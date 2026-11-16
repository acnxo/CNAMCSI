#!/usr/bin/env python3
"""公开行情接口封装（纯标准库，可直接跑在 Lambda / 函数计算里）。

四个数据源：
  1. 腾讯批量快照 qt.gtimg.cn：价格 / 总市值 / 流通市值 / 成交量 / 简称（900 只/请求，默认主路径）；
  2. 东财 clist push2.eastmoney.com：可按总市值升序翻页（无状态备选，注意限频）；
  3. 东财指数日线：取交易日历（避免打包节假日表）；
  4. 交易所 / 巨潮清单：代码 + 上市日期（每周刷新一次即可）。

所有请求都经过 `httpGet`，统一记录到 `STATS`（成功请求数 / 发出次数 / 字节数 / 域名明细），
云函数返回值里会带上这些数字，便于核对「每天到底打了多少次接口」。
"""

import datetime
import gzip
import json
import os
import sys
import time

import YamlLite
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from Universe import eligibility, listedTradingDays, normalizeDate

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
}

DEFAULT_TIMEOUT = 12
DEFAULT_RETRIES = 4


class HttpStats:
    """累计 HTTP 请求数与下行字节数（gzip 解压前的原始字节）。"""

    def __init__(self):
        self.requests = 0   # 成功拿到响应的请求数
        self.attempts = 0   # 实际发出的请求数（含重试与失败）
        self.bytes = 0
        self.hosts = {}

    def add(self, host, nbytes):
        self.requests += 1
        self.bytes += nbytes
        item = self.hosts.setdefault(host, {"requests": 0, "bytes": 0})
        item["requests"] += 1
        item["bytes"] += nbytes

    def as_dict(self):
        return {
            "requests": self.requests,
            "attempts": self.attempts,
            "bytes": self.bytes,
            "bytes_mb": round(self.bytes / 1024 / 1024, 3),
            "hosts": self.hosts,
        }


STATS = HttpStats()


def resetStats():
    global STATS
    STATS = HttpStats()
    return STATS


def _getRaw(url, headers=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES):
    h = dict(UA)
    h["Accept-Encoding"] = "gzip"
    if headers:
        h.update(headers)
    host = urllib.parse.urlsplit(url).netloc
    last = None
    for i in range(retries):
        STATS.attempts += 1
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                encoding = (r.headers.get("Content-Encoding") or "").lower()
                if "gzip" in encoding:
                    raw = gzip.decompress(raw)
            STATS.add(host, len(raw))
            return raw
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(0.5 * (2 ** i), 5))
    raise RuntimeError(f"GET failed {url}: {last}")


def httpGet(url, headers=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, encoding="utf-8"):
    raw = _getRaw(url, headers=headers, timeout=timeout, retries=retries)
    return raw.decode(encoding, "ignore")


# --------------------------------------------------------------------------- #
# 1. 东方财富 clist：全市场快照（可按总市值升序翻页）
# --------------------------------------------------------------------------- #

# 主域名 push2 经常 502，数字子域是同一集群的镜像，按顺序轮换
EM_HOSTS = [
    "https://82.push2.eastmoney.com",
    "https://1.push2.eastmoney.com",
    "https://7.push2.eastmoney.com",
    "https://push2.eastmoney.com",
]
# f2 最新价 / f5 成交量(手) / f6 成交额 / f12 代码 / f13 市场(0深1沪) / f14 名称
# f20 总市值 / f21 流通市值 / f26 上市日期 / f124 行情时间戳
EM_FIELDS = "f2,f5,f6,f12,f13,f14,f17,f20,f21,f26,f124"
# 沪深主板：沪 m:1+t:2，深 m:0+t:6（不含创业板 t:80、科创板 t:23、北交所）
EM_FS_MAIN = "m:0+t:6,m:1+t:2"
EM_FS_ALL_A = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"


def emClistPage(pn, pz=100, fid="f20", po=0, fs=EM_FS_MAIN):
    """东财行情列表单页。po=0 升序、1 降序；fid=f20 为总市值排序。

    主域名偶发 502，这里做域名轮换；每页都是独立请求，轮换不影响排序一致性。
    """
    last = None
    for host in EM_HOSTS:
        url = (
            f"{host}/api/qt/clist/get?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2"
            f"&fid={fid}&fs={urllib.parse.quote(fs, safe=':,+')}&fields={EM_FIELDS}"
        )
        try:
            txt = httpGet(url, {"Referer": "https://quote.eastmoney.com/"}, retries=1, timeout=8)
            data = (json.loads(txt) or {}).get("data") or {}
            return data.get("diff") or [], data.get("total") or 0
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"东财 clist 全部分片域名不可用: {last}")


def _num(v):
    """东财的 '-' / '' 一律当缺失。"""
    if v in (None, "", "-"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parseEmRow(r):
    """东财 clist 一行 → 统一 Quote 字典；无行情（退市/长期停牌）返回 None。"""
    code = str(r.get("f12") or "").strip()
    if not code:
        return None
    price = _num(r.get("f2"))
    mcap = _num(r.get("f20"))
    if price is None or mcap is None or price <= 0 or mcap <= 0:
        return None
    return {
        "code": code,
        "name": str(r.get("f14") or "").strip(),
        "price": price,
        "open": _num(r.get("f17")) or 0.0,   # 今开（T+1 成交价）
        "volume": _num(r.get("f5")) or 0.0,  # 手
        "amount": _num(r.get("f6")) or 0.0,
        "total_mv": mcap,
        "float_mv": _num(r.get("f21")) or 0.0,
        "list_date": str(r.get("f26") or ""),
        "ts": _num(r.get("f124")),
        "market": r.get("f13"),
        "source": "eastmoney",
    }


def emScanByMcap(accept, need, max_pages=60, window=1, fs=EM_FS_MAIN, workers=2, delay=1.0):
    """按总市值升序翻页，边翻边过滤，凑满 `need` 只合格股即停。

    因为排序是市值升序，凑满时「已选中的第 need 名」就是最终边界，无需全市场扫描。
    返回 (accepted, scanned_rows, pages_used, total)。
    """
    accepted, scanned = [], []
    page, total, used, done = 1, 0, 0, False
    failed_pages = []
    idle_rounds = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        while not done and page <= max_pages:
            pages = list(range(page, min(page + window, max_pages + 1)))
            futs = {ex.submit(emClistPage, pn, 100, "f20", 0, fs): pn for pn in pages}
            got = {}
            for fu in as_completed(futs):
                pn = futs[fu]
                try:
                    rows, tot = fu.result()
                except Exception:  # noqa: BLE001  单页失败不拖垮整体，稍后按缺口判断
                    failed_pages.append(pn)
                    continue
                got[pn] = rows
                total = total or tot
            if len(got) == 0:  # 整轮全失败：连续两轮就放弃，避免把时间耗在限流上
                idle_rounds += 1
                if idle_rounds >= 2:
                    raise RuntimeError(f"东财 clist 连续两轮全部失败（限流/被封），已放弃：{failed_pages}")
            else:
                idle_rounds = 0
            for pn in pages:  # 严格按页序处理，保证市值升序
                used += 1
                for r in got.get(pn) or []:
                    q = parseEmRow(r)
                    if q is None:
                        scanned.append({"code": str(r.get("f12") or ""), "reason": "no_quote"})
                        continue
                    scanned.append(q)
                    if accept(q):
                        accepted.append(q)
                        if len(accepted) >= need:
                            done = True
                            break
                if done:
                    break
            page += len(pages)
            if not done:
                time.sleep(delay)  # 东财对连续翻页有限频，稍作间隔
    if accepted and len(accepted) < need:
        raise RuntimeError(f"东财翻页不足：{len(accepted)}/{need}，失败页 {failed_pages}")
    return accepted, scanned, used, total


# --------------------------------------------------------------------------- #
# 2. 交易日历（上证指数日线，一年约 250 根）
# --------------------------------------------------------------------------- #

def emTradingCalendar(limit=260, end="20500101"):
    """返回升序交易日列表 ['2025-01-02', ...]，最后一根即最近交易日。"""
    start = time.strftime("%Y0101", time.localtime(time.time() - 3 * 365 * 86400))
    url = (
        "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=1.000001"
        "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53&klt=101&fqt=0"
        f"&beg={start}&end={end}&lmt={limit}"
    )
    txt = httpGet(url, {"Referer": "https://quote.eastmoney.com/"})
    klines = (((json.loads(txt) or {}).get("data") or {}).get("klines") or [])
    days = [k.split(",")[0] for k in klines]
    return sorted(d for d in days if len(d) == 10)


# --------------------------------------------------------------------------- #
# 3. 腾讯批量快照（一次最多 800~900 只，URL 再长会 414）
# --------------------------------------------------------------------------- #

# 实测：900 只（URL 约 8.1 KB）可用，950 只触发 414；超限时自动二分降级
TENCENT_BATCH = 900


def _tencentChunk(codes):
    q = ",".join(("sh" if c[0] == "6" else "sz") + c for c in codes)
    url = "https://qt.gtimg.cn/q=" + q
    try:
        return httpGet(url, {"Referer": "https://finance.qq.com/"}, encoding="gbk", retries=2)
    except Exception:  # noqa: BLE001
        if len(codes) <= 100:
            raise
        mid = len(codes) // 2  # URL 过长（414）或临时失败 → 二分重试
        return _tencentChunk(codes[:mid]) + ";" + _tencentChunk(codes[mid:])


def parseTencentText(txt):
    out = []
    for line in txt.split(";"):
        line = line.strip()
        if '="' not in line:
            continue
        val = line.split('="', 1)[1].rstrip('"')
        f = val.split("~")
        if len(f) < 46 or not f[2]:
            continue
        price = _num(f[3])
        mcap = _num(f[45])
        if price is None or price <= 0 or mcap is None:
            continue
        ts = f[30] or ""
        out.append({
            "code": f[2],
            "name": f[1].strip(),
            "price": price,
            "open": _num(f[5]) or 0.0,    # 今开（T+1 成交价）
            "volume": _num(f[6]) or 0.0,  # 手
            "amount": (_num(f[37]) or 0.0) * 1e4,
            "total_mv": mcap * 1e8,   # 腾讯返回「亿元」
            "float_mv": (_num(f[44]) or 0.0) * 1e8,
            "list_date": "",          # 腾讯快照无上市日期，需配合交易所列表
            "ts": ts[:8],
            "market": 1 if f[2][0] == "6" else 0,
            "source": "tencent",
        })
    return out


def tencentSnapshot(codes, batch=TENCENT_BATCH, workers=4):
    """按批并发拉腾讯快照，返回 Quote 列表。"""
    chunks = [codes[i:i + batch] for i in range(0, len(codes), batch)]
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_tencentChunk, ch): ch for ch in chunks}
        for fu in as_completed(futs):
            ch = futs[fu]
            try:
                out.extend(parseTencentText(fu.result()))
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(f"腾讯快照批次失败（{len(ch)} 只）: {e}") from e
    return out


# --------------------------------------------------------------------------- #
# 4. 交易所股票列表（含上市日期；仅腾讯模式需要，可缓存 7 天）
# --------------------------------------------------------------------------- #

def sseList():
    url = (
        "http://query.sse.com.cn/sseQuery/commonQuery.do?sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GP_L"
        "&type=inParams&STOCK_TYPE=1&COMPANY_STATUS=2,4,5,7,8"
        "&pageHelp.pageSize=3000&pageHelp.pageNo=1&pageHelp.beginPage=1&pageHelp.cacheSize=1&pageHelp.endPage=1"
    )
    rows = (json.loads(httpGet(url, {"Referer": "http://www.sse.com.cn/"})) or {}).get("result") or []
    out = []
    for r in rows:
        code = (r.get("A_STOCK_CODE") or "").strip()
        name = (r.get("SEC_NAME_CN") or "").strip()
        ld = (r.get("LIST_DATE") or "").strip()
        if not code or not name or not ld.isdigit():
            continue
        out.append({"code": code, "name": name, "list_date": ld})
    return out


def _szsePage(pageno):
    url = (
        "http://www.szse.cn/api/report/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1110"
        f"&TABKEY=tab1&PAGENO={pageno}"
    )
    d = json.loads(httpGet(url, {"Referer": "http://www.szse.cn/"}))[0]
    meta = d.get("metadata") or {}
    data = d.get("data") or []
    out = []
    for r in data:
        code = (r.get("agdm") or "").strip()
        if not code:
            continue
        name = (r.get("agjc") or "").replace("<u>", " ").replace("</u>", " ")
        name = name.split("'>")[-1].split("<")[0].strip()
        out.append({"code": code, "name": name, "list_date": (r.get("agssrq") or "").strip()})
    return int(meta.get("pagecount") or 0), out


def szseList(workers=8):
    """深交所列表：每页 20 条，总页数约 150，需并发翻页（只在缓存过期时执行）。"""
    pagecount, first = _szsePage(1)
    out = list(first)
    if pagecount > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_szsePage, p) for p in range(2, pagecount + 1)]
            for fu in as_completed(futs):
                out.extend(fu.result()[1])
    return out


# 巨潮资讯的深市股票清单：一次请求拿到深市全部代码（含创业板/B 股，按前缀过滤）
CNINFO_SZSE = "http://www.cninfo.com.cn/new/data/szse_stock.json"


def cninfoSzseList():
    txt = httpGet(CNINFO_SZSE, {"Referer": "http://www.cninfo.com.cn/"})
    obj = json.loads(txt) or {}
    rows = obj.get("stockList") if isinstance(obj, dict) else obj
    out = []
    for r in rows or []:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        out.append({"code": code, "name": str(r.get("zwjc") or "").strip(), "list_date": ""})
    return out


def emNewListings(limit=100):
    """按上市日期降序取最近上市的 limit 只（用于识别「上市不满 60 个交易日」的次新股）。

    A 股 IPO 最近 3 年每年不足 100 家，取 100 只即可覆盖 2~3 年，远超 60 个交易日。
    """
    rows, page = [], 1
    while len(rows) < limit and page <= 5:
        diff, _ = emClistPage(page, 100, "f26", 1, EM_FS_MAIN)
        rows.extend(diff)
        if len(diff) < 100:
            break
        page += 1
    out = {}
    for r in rows:
        code = str(r.get("f12") or "").strip()
        ld = str(r.get("f26") or "").strip()
        if code and ld.isdigit():
            out[code] = ld
    return out


SEED_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "SeedStockList.yaml")


def _seedStockList():
    """兜底：部署包内的代码清单快照（构建时从 data/raw/stock_list.json 拷入）。

    即使行情/交易所接口全挂也能算出名单；清单若滞后，只会漏掉新股（新股本就要剔除
    60 个交易日），不会把次新股错纳进来。
    """
    if not os.path.exists(SEED_FILE):
        return []
    data = YamlLite.load(SEED_FILE)
    stocks = data.get("Stocks") or []
    # 大驼峰（Code/Name/ListDate）→ 下游使用的 snake_case
    return [{"code": str(item.get("Code", "")), "name": item.get("Name", ""),
             "list_date": item.get("ListDate", "")} for item in stocks]


def seedListDates():
    """SeedStockList.yaml 中的 {code: list_date}（只取带日期的条目）。"""
    return {str(r["code"]): r["list_date"] for r in _seedStockList()
            if r.get("code") and r.get("list_date")}


def fillMissingListDates(rows, quiet=False):
    """用部署包快照补齐清单中缺失的上市日期。

    巨潮的深市清单只有代码/简称，不带上市日期；不补的话深市股票 listed_days 全为 0，
    会被整批判为「上市不满 60 个交易日」。快照滞后只会漏掉新股（新股本就该剔除），
    属于安全方向的保守降级。

    rows 可为 dict{code: row} 或 list[row]，就地修改；返回 (已补齐数, 仍缺失数)。
    """
    items = list(rows.values()) if isinstance(rows, dict) else list(rows)
    missing = [r for r in items if isinstance(r, dict) and not normalizeDate(r.get("list_date"))]
    if not missing:
        return 0, 0
    dates = seedListDates()
    filled = 0
    for r in missing:
        ld = dates.get(str(r.get("code") or ""))
        if ld:
            r["list_date"] = ld
            filled += 1
    left = len(missing) - filled
    if not quiet and filled:
        print(f"[info] 上市日期补齐 {filled} 只（来源 SeedStockList.yaml）", flush=True)
    if not quiet and left:
        print(f"[warn] 上市日期仍缺失 {left} 只，按不可选处理（多为快照之后的新股）", flush=True)
    return filled, left


def exchangeStockList(only_main_board=True, append_new=True):
    """沪深主板代码清单（含上市日期）。

    主路径：上交所 1 次 + 巨潮深市 1 次（代码/简称）+ 东财新股 1~2 次（深市上市日期）。
    东财被限流时降级到深交所官方列表（约 146 页，慢但准确）；全挂则用部署包内快照。
    """
    main = ("600", "601", "603", "605", "000", "001", "002", "003")
    rows, ok = {}, True
    for fn in (sseList, cninfoSzseList):  # 两个来源任一失败都退回部署包快照，避免只有半个市场
        try:
            for r in fn():
                code = r["code"]
                if only_main_board and not code.startswith(main):
                    continue
                rows[code] = r
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"[warn] 代码清单源失败 {fn.__name__}: {e}", flush=True)

    if append_new and ok and rows:
        try:
            for code, ld in emNewListings(100).items():
                if code in rows:  # 深市新股补上市日期；沪市已有
                    rows[code]["list_date"] = ld
                elif code.startswith(main):
                    rows[code] = {"code": code, "name": "", "list_date": ld}
        except Exception as e:  # noqa: BLE001  东财限流/故障 → 用部署包快照的上市日期
            print(f"[warn] 东财新股接口失败（{e}），改用部署包快照的上市日期", flush=True)
            dates = {r["code"]: r.get("list_date") for r in _seedStockList() if r.get("list_date")}
            if dates:
                for code, ld in dates.items():
                    if code in rows and ld:
                        rows[code]["list_date"] = ld
            else:  # 没有快照才走深交所官方列表（约 146 页，慢但准确）
                try:
                    for r in szseList():
                        code = r["code"]
                        if code in rows and r.get("list_date"):
                            rows[code]["list_date"] = r["list_date"]
                except Exception:  # noqa: BLE001
                    pass

    if not ok or len(rows) < 1000:
        seed = _seedStockList()
        if seed:
            print(f"[warn] 代码清单退回部署包快照（{len(seed)} 只），请检查交易所接口", flush=True)
            rows = {r["code"]: r for r in seed
                    if not only_main_board or str(r["code"]).startswith(main)}
    if not rows:
        raise RuntimeError("代码清单获取失败：交易所接口与部署包快照都不可用")

    fillMissingListDates(rows)

    return sorted(rows.values(), key=lambda x: x["code"])


def nowDate():
    """本地日期（云函数默认 UTC，部署时请把 TZ 设为 Asia/Shanghai）。"""
    return time.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 数据抓取编排（供 Index.py 调用）
# --------------------------------------------------------------------------- #
DEFAULT_CFG = {
    "workers": 4,
    "stock_list_ttl_days": 7,
    "calendar_ttl_hours": 3,
    "scan_buffer": 100,
    "page_window": 1,
    "page_delay": 1.0,
    "max_pages": 60,
}


def fetchCalendar(store, ttlHours=None):
    """交易日历：优先读缓存（默认 3 小时），未命中则拉东财上证指数日线。"""
    ttl = float(ttlHours if ttlHours is not None else DEFAULT_CFG["calendar_ttl_hours"])
    cached = store.readJson("state/calendar.json") or {}
    if cached.get("days") and time.time() - float(cached.get("updated_at") or 0) <= ttl * 3600:
        return cached["days"]
    try:
        days = emTradingCalendar(limit=260)
    except Exception as exc:  # noqa: BLE001  接口失败时使用过期缓存，避免当日整体失败
        if cached.get("days"):
            print(f"[warn] 交易日历拉取失败（{exc}），使用过期缓存 {len(cached['days'])} 天",
                  file=sys.stderr)
            return cached["days"]
        raise
    if len(days) < 80:
        raise RuntimeError(f"交易日历异常，仅 {len(days)} 天")
    store.writeJson("state/calendar.json", {"updated_at": time.time(), "days": days})
    return days


def quoteDate(quotes):
    """快照里最新的行情时间戳 → ``YYYY-MM-DD``。"""
    values = [item.get("ts") or "" for item in quotes if item.get("ts")]
    return normalizeDate(max(values)) if values else ""


def nextBusinessDay(dateStr):
    """下一个工作日（仅用于 pending.exec_date 展示，不排除节假日）。"""
    day = datetime.date.fromisoformat(dateStr) + datetime.timedelta(days=1)
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day.isoformat()


def loadStockList(cfg, store):
    """代码清单（含上市日期）：缓存 ``stock_list_ttl_days`` 天，未命中则重建。"""
    cached = store.readJson("state/stock_list.json")
    if cached and cached.get("rows"):
        age = time.time() - float(cached.get("updated_at") or 0)
        if age <= float(cfg.get("stock_list_ttl_days", 7)) * 86400:
            rows = cached["rows"]
            filled, _ = fillMissingListDates(rows, quiet=True)
            if filled:  # 老缓存可能缺上市日期 → 补齐后回写，避免一直带着缺口
                print(f"[info] 缓存清单补齐上市日期 {filled} 只", flush=True)
                store.writeJson("state/stock_list.json", {"updated_at": time.time(), "rows": rows})
            return rows, "cache"
    rows = exchangeStockList(only_main_board=True)
    store.writeJson("state/stock_list.json", {"updated_at": time.time(), "rows": rows})
    return rows, "refreshed"


def quotesFromTencent(cfg, calendar, boards, store):
    """腾讯批量快照（900 只/请求）：返回统一 quote 列表与扫描摘要。"""
    rows, how = loadStockList(cfg, store)
    infoMap = {row["code"]: row for row in rows}
    codes = [row["code"] for row in rows if row["code"].startswith(boards)]
    quotes = tencentSnapshot(codes, workers=int(cfg.get("workers", 4)))
    for quote in quotes:
        meta = infoMap.get(quote["code"]) or {}
        quote["name"] = quote.get("name") or meta.get("name", "")
        quote["list_date"] = meta.get("list_date", "") or quote.get("list_date", "")
        quote["listed_days"] = listedTradingDays(quote["list_date"], calendar)
    missing = sum(1 for q in quotes if not normalizeDate(q.get("list_date")))
    if missing:
        print(f"[warn] 快照中 {missing}/{len(quotes)} 只上市日缺失，按不可选处理", flush=True)
    return quotes, {"pages": 0, "stock_list": how, "codes": len(codes),
                    "scanned": len(quotes)}


def quotesFromEastmoney(cfg, calendar, boards):
    """东财按总市值升序翻页（备选数据源，有限频风险）。"""
    skipped = {}

    def accept(quote):
        quote["listed_days"] = listedTradingDays(quote.get("list_date"), calendar)
        ok, reason = eligibility(quote, calendar, int(cfg.get("min_listed_days", 60)), boards)
        if not ok:
            skipped[reason] = skipped.get(reason, 0) + 1
        return ok

    accepted, scanned, pages, total = emScanByMcap(
        accept,
        need=int(cfg["n"]) + int(cfg.get("scan_buffer", 0)),
        max_pages=int(cfg.get("max_pages", 60)),
        window=int(cfg.get("page_window", 1)),
        workers=int(cfg.get("workers", 4)),
        delay=float(cfg.get("page_delay", 1.0)))
    return accepted, {"pages": pages, "market_total": total, "scanned": len(scanned),
                      "skip_reasons": skipped}
