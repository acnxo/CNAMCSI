#!/usr/bin/env python3
"""历史行情全量采集：断点续采 + 状态/进度记录（雪球主源，东财备源）。

目标：把 2004-12 起的**真实**日线（不复权）重新采一遍，替换历史上由转换/缺省值
占位的数据；产物与现有三层格式完全一致：

    Archive/Security/<Code>.mvsv   按证券日线（2004-12 起，含 16 行头部元数据）
    Data/<yyyy>/<yyyyMM>.mvsv      当年横截面（``rebuild`` 从 Archive 切出）
    Latest/Latest.mvsv             最近 5 个交易日（``rebuild``）

数据源（都用**不复权**）：

- 雪球 ``stock/chart/kline.json``（``type=normal``）：一次返回全历史（6,400+ 根），
  字段 ``volume`` 单位已是**股**、``amount`` 单位**元**，无需换算；
- 东财 ``push2his ... klt=101&fqt=0``：备源，``f56`` 为「手」（×100 换算成股），
  大区间请求容易被限流，自动退避。

断点续采状态写在 ``Archive/.FetchState.json``（不提交，见 ``.gitignore``）：每只股票
记录 ``status``/``rows``/``first``/``last``/``source``，重跑只补未完成的，中途失败
不会从头开始。``--force`` 忽略状态全量重采。
"""

import bisect
import concurrent.futures
import glob
import http.cookiejar
import json
import os
import random
import threading
import time
import urllib.request

import Market

DEFAULT_START = "20041201"          # 2004 年 12 月（≥20 个交易日）作为选样评估起点
XQ_COUNT = 8000                     # 单次请求最多往前取的根数（覆盖 1999 至今）
UA_XQ = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://xueqiu.com/",
}
UA_EM = dict(UA_XQ, Referer="https://quote.eastmoney.com/")
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"
EM_HOSTS = ["https://push2his.eastmoney.com", "https://1.push2his.eastmoney.com",
            "https://82.push2his.eastmoney.com"]
STATE_NAME = ".FetchState.json"
STATE_VERSION = 1


# --------------------------------------------------------------------------- #
# 基础：日期 / 代码
# --------------------------------------------------------------------------- #
def normDate(value):
    """``2004-12-01`` / ``20041201`` / ``None`` → ``20041201``（None → 今天）。"""
    if value is None:
        return time.strftime("%Y%m%d")
    text = str(value).strip().replace("-", "").replace("/", "")
    return text[:8]


def dayOf(ms):
    """雪球时间戳（毫秒，UTC）→ 北京日期 ``YYYYMMDD``。"""
    return time.strftime("%Y%m%d", time.gmtime(ms / 1000 + 8 * 3600))


def xqSymbol(code):
    return ("SH" if code[0] == "6" else "SZ") + code


def emSecid(code):
    return ("1." if code[0] == "6" else "0.") + code


def exchangeOf(code):
    return "SSE" if code[0] == "6" else "SZSE"


# --------------------------------------------------------------------------- #
# 数据源：雪球（主）
# --------------------------------------------------------------------------- #
class Session:
    """每线程一个：先访问首页拿 cookie（``xq_a_token``），再请求 kline。"""

    def __init__(self, timeout=25):
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self.warmed = False
        self.lock = threading.Lock()

    def warm(self):
        if self.warmed:
            return
        with self.lock:
            if not self.warmed:
                self.opener.open(urllib.request.Request("https://xueqiu.com/",
                                                        headers=UA_XQ),
                                 timeout=self.timeout).read()
                self.warmed = True

    def get(self, url, headers=None):
        self.warm()
        req = urllib.request.Request(url, headers=headers or UA_XQ)
        with self.opener.open(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", "ignore")


_LOCAL = threading.local()


def sessionOf():
    """线程本地 Session：cookie 只 warm 一次，避免每只股票重复访问首页。"""
    session = getattr(_LOCAL, "session", None)
    if session is None:
        session = Session()
        _LOCAL.session = session
    return session


def fetchXueqiu(session, code, start, end, retries=3):
    """雪球日线（不复权）→ ``[(date, open, close, high, low, volume, amount)]``。"""
    last = None
    for attempt in range(retries):
        url = (f"https://stock.xueqiu.com/v5/stock/chart/kline.json?symbol={xqSymbol(code)}"
               f"&begin={int(time.time() * 1000)}&period=day&count=-{XQ_COUNT}"
               f"&indicator=kline&type=normal")
        try:
            rows = parseXueqiu(session.get(url))
            return [r for r in rows if start <= r[0] <= end]
        except Exception as e:  # noqa: BLE001
            last = e
            session.warmed = False
            _LOCAL.session = None            # 下只股票重新建会话（token 可能失效）
            time.sleep(min(1.0 * (2 ** attempt), 8))
    raise RuntimeError(f"xueqiu {code}: {last}")


def parseXueqiu(payload):
    """雪球 kline 响应 → 统一行（``volume`` 已是股，``amount`` 已是元）。"""
    data = json.loads(payload).get("data") or {}
    items = data.get("item") or []
    if not items:
        return []
    col = {name: i for i, name in enumerate(data.get("column") or [])}

    def cell(item, name, default=0.0):
        i = col.get(name)
        if i is None or i >= len(item):
            return default
        value = item[i]
        return default if value is None else value

    rows = []
    for item in items:
        close = float(cell(item, "close") or 0)
        if close <= 0:
            continue
        day = dayOf(cell(item, "timestamp"))
        open_ = float(cell(item, "open") or close)
        high = float(cell(item, "high") or max(open_, close))
        low = float(cell(item, "low") or min(open_, close))
        rows.append((day, open_, close, high, low,
                     cell(item, "volume"), cell(item, "amount")))
    rows.sort(key=lambda r: r[0])
    return rows


# --------------------------------------------------------------------------- #
# 数据源：东财（备）
# --------------------------------------------------------------------------- #
def fetchEastmoney(code, start, end, retries=3):
    last = None
    for attempt in range(retries):
        host = random.choice(EM_HOSTS)
        url = (f"{host}/api/qt/stock/kline/get?secid={emSecid(code)}&ut={EM_UT}"
               f"&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57"
               f"&klt=101&fqt=0&beg={start}&end={end}&lmt=1000000")
        try:
            req = urllib.request.Request(url, headers=UA_EM)
            with urllib.request.urlopen(req, timeout=25) as resp:
                return [r for r in parseEastmoney(resp.read().decode("utf-8", "ignore"))
                        if start <= r[0] <= end]
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(min(1.5 * (2 ** attempt), 10))
    raise RuntimeError(f"eastmoney {code}: {last}")


def parseEastmoney(payload):
    """东财 kline 响应 → 统一行（``f56`` 手 → 股，``f57`` 元）。"""
    data = json.loads(payload).get("data") or {}
    rows = []
    for line in data.get("klines") or []:
        field = line.split(",")
        if len(field) < 7:
            continue
        close = float(field[2] or 0)
        if close <= 0:
            continue
        open_ = float(field[1] or close)
        rows.append((field[0].replace("-", ""), open_, close,
                     float(field[3] or max(open_, close)),
                     float(field[4] or min(open_, close)),
                     float(field[5] or 0) * 100, float(field[6] or 0)))
    rows.sort(key=lambda r: r[0])
    return rows


# --------------------------------------------------------------------------- #
# 股本：从既有 Archive 取（最近邻填充），市值 = 收盘 × 股本
# --------------------------------------------------------------------------- #
def sharesIndex(rows):
    """既有数据行 → ``{date: (sharesTotal, sharesFloat)}``。"""
    index = {}
    for row in rows:
        field = row.split("|")
        if len(field) >= 13 and field[9] not in ("", "0"):
            index[field[2]] = (field[9], field[10])
    return index


def nearestShares(index, dates, day):
    """取与 ``day`` 最近（优先不晚于）的股本，股本只在送转/增发时变化。"""
    if not dates:
        return None
    pos = bisect.bisect_right(dates, day) - 1
    if pos < 0:
        pos = 0
    return index[dates[pos]]


def buildRows(code, name, klines, index, dates, fallback=None):
    """统一行 + 股本 → 13 列数据行（与既有文件同样的数值格式）。"""
    rows = []
    for day, open_, close, high, low, volume, amount in klines:
        shares = nearestShares(index, dates, day) or fallback
        if not shares:
            continue
        total, floating = shares
        total = float(total)
        floating = float(floating or total)
        rows.append("|".join([
            code, name, day, Market._price(open_), Market._price(close),
            Market._price(high), Market._price(low),
            Market._shares(volume), Market._amount(amount),
            Market._shares(total), Market._shares(floating),
            Market._mv(close * total), Market._mv(close * floating)]))
    return rows


# --------------------------------------------------------------------------- #
# 读写既有文件
# --------------------------------------------------------------------------- #
def archivePath(root, code):
    return os.path.join(root, "Archive", "Security", f"{code}.mvsv")


def securityCodes(root, includeData=True):
    """证券全集 = 已有 Archive 文件 ∪ 当年 Data 里出现的代码（新股）。"""
    codes = {os.path.basename(path)[:-len(".mvsv")]
             for path in glob.glob(os.path.join(root, "Archive", "Security", "*.mvsv"))}
    if includeData:
        for path in glob.glob(os.path.join(root, "Data", "*", "*.mvsv")):
            codes |= {row.split("|")[0] for row in Market.readRows(path)}
    return sorted(code for code in codes if code.startswith(Market.BOARDS))


def nameOf(root, code):
    """名称：优先既有 Archive 头部 / 数据行，其次当年 Data。"""
    path = archivePath(root, code)
    for key, value in Market.readHeaders(path):
        if key == "Name" and value.strip('"'):
            return value.strip('"')
    rows = Market.readRows(path)
    if rows:
        return rows[-1].split("|")[1]
    for month in sorted(glob.glob(os.path.join(root, "Data", "*", "*.mvsv")), reverse=True):
        for row in Market.readRows(month):
            field = row.split("|")
            if field[0] == code:
                return field[1]
    return code


def securityHeaders(code, name, count):
    """与既有 Archive 文件一致的 16 行头部。"""
    return [
        ("标题", f"中证微盘股行情 {code} {name} 日线"),
        ("字段名称", f'"{Market.FIELD_CN}"'),
        ("DataProvider", Market.PROVIDER),
        ("Field", f'"{Market.FIELD}"'),
        ("FieldName", f'"{Market.FIELD}"'),
        ("FieldType", f'"{Market.FIELD_TYPE}"'),
        ("TypeKLine", "Day"),
        ("Count", str(count)),
        ("Symbol", f"CN.{code}"),
        ("SecuCode", code),
        ("USC", code),
        ("Name", f'"{name}"'),
        ("Region", "CN"),
        ("Market", exchangeOf(code)),
        ("TimeZone", "Asia/Shanghai"),
        ("Currency", "CNY"),
    ]


def mergeRows(oldRows, newRows, start, end):
    """采集区间 ``[start, end]`` 内以新数据为准，区间外的既有行保留。"""
    keep = [row for row in oldRows if not (start <= row.split("|")[2] <= end)]
    merged = keep + list(newRows)
    merged.sort(key=lambda row: row.split("|")[2])
    return merged


def saveSecurity(root, code, name, rows):
    path = archivePath(root, code)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Market._write(path, securityHeaders(code, name, len(rows)), rows)
    return path


# --------------------------------------------------------------------------- #
# 断点续采状态
# --------------------------------------------------------------------------- #
def statePath(root):
    return os.path.join(root, "Archive", STATE_NAME)


def stateLoad(root):
    try:
        with open(statePath(root), encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        data = {}
    if data.get("version") != STATE_VERSION:
        data = {"version": STATE_VERSION}
    data.setdefault("securities", {})
    return data


def stateSave(root, state):
    state["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path = statePath(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return path


def isDone(entry, start, end):
    """已完成 = 状态 ok 且覆盖 [start, end]；``from`` 是当初请求的起点（非数据首日）。"""
    return bool(entry) and entry.get("status") == "ok" \
        and entry.get("from", "99999999") <= start \
        and entry.get("last", "00000000") >= end


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def oneSecurity(root, code, start, end, source, delay):
    """采一只：读既有股本 → 抓取 → 合并 → 落盘。返回状态条目。"""
    old = Market.readRows(archivePath(root, code))
    name = nameOf(root, code)
    index = sharesIndex(old)
    dates = sorted(index)
    fallback = index.get(dates[-1]) if dates else None

    if source == "eastmoney":
        klines = fetchEastmoney(code, start, end)
    else:
        klines = fetchXueqiu(sessionOf(), code, start, end)
    if not klines:
        return {"status": "empty", "rows": 0, "from": start, "source": source,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S")}

    rows = buildRows(code, name, klines, index, dates, fallback)
    if not rows:
        return {"status": "no_shares", "rows": 0, "from": start, "source": source,
                "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
    merged = mergeRows(old, rows, start, end)
    saveSecurity(root, code, name, merged)
    if delay:
        time.sleep(delay)
    return {"status": "ok", "rows": len(rows), "total": len(merged), "from": start,
            "first": rows[0].split("|")[2], "last": rows[-1].split("|")[2],
            "source": source, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def run(root, start=DEFAULT_START, end=None, source="xueqiu", workers=4, delay=0.15,
        force=False, only=None, limit=None, saveEvery=20, log=print):
    """全量/增量采集入口，返回汇总统计。"""
    start, end = normDate(start), normDate(end)
    state = stateLoad(root)
    codes = securityCodes(root)
    if only:
        want = {str(code) for code in only}
        codes = [code for code in codes if code in want]
    todo = [code for code in codes
            if force or not isDone(state["securities"].get(code), start, end)]
    if limit:
        todo = todo[:limit]
    log(f"证券 {len(codes)} 只 · 待采 {len(todo)} 只（已完成 {len(codes) - len(todo)}）"
        f" · 区间 {start}~{end} · 源 {source} · 并发 {workers}")

    stats = {"ok": 0, "failed": 0, "empty": 0, "no_shares": 0, "rows": 0}
    started = time.time()
    lock = threading.Lock()

    def work(code):
        try:
            return code, oneSecurity(root, code, start, end, source, delay), None
        except Exception as e:  # noqa: BLE001
            return code, {"status": "failed", "error": str(e)[:200],
                          "source": source,
                          "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, e

    with concurrent.futures.ThreadPoolExecutor(workers) as pool:
        futures = [pool.submit(work, code) for code in todo]
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            code, entry, error = future.result()
            with lock:
                stats["rows"] += entry.get("rows", 0)
                stats[entry.get("status", "failed")] = stats.get(entry.get("status", "failed"), 0) + 1
                state["securities"][code] = entry
                if error:
                    log(f"[{done}/{len(todo)}] {code} 失败：{entry['error']}")
                elif done % 20 == 0 or entry.get("status") != "ok":
                    log(f"[{done}/{len(todo)}] {code} {entry.get('status')} "
                        f"{entry.get('rows', 0)} 行 · 累计 {stats['rows']} 行 · "
                        f"失败 {stats['failed']}")
                if done % saveEvery == 0:
                    state.update({"start": start, "end": end, "source": source,
                                  "stats": stats})
                    stateSave(root, state)

    state.update({"start": start, "end": end, "source": source, "stats": stats})
    stateSave(root, state)
    stats["seconds"] = round(time.time() - started, 1)
    stats["todo"] = len(todo)
    log(f"完成：{stats}")
    return stats


# --------------------------------------------------------------------------- #
# 校验 / 重建
# --------------------------------------------------------------------------- #
def verify(root, log=print, sample=5):
    """检查历史覆盖：交易日总数、每月天数、每只行数与缺口。"""
    days = set()
    perCode = {}
    for path in sorted(glob.glob(os.path.join(root, "Archive", "Security", "*.mvsv"))):
        rows = Market.readRows(path)
        if not rows:
            continue
        stamp = [row.split("|")[2] for row in rows]
        perCode[os.path.basename(path)[:-5]] = (stamp[0], stamp[-1], len(stamp))
        days.update(stamp)
    months = {}
    for day in days:
        months[day[:6]] = months.get(day[:6], 0) + 1
    ordered = sorted(months)
    log(f"证券 {len(perCode)} 只 · 交易日 {len(days)} 天 · "
        f"区间 {min(days) if days else '-'} ~ {max(days) if days else '-'}")
    log(f"2004-12 交易日 {months.get('200412', 0)} 天"
        f"{'（≥20 ✓）' if months.get('200412', 0) >= 20 else '（不足 20 ✗）'}")
    thin = [(month, months[month]) for month in ordered if months[month] < 15]
    if thin:
        log(f"交易日偏少的月份（<15 天）：{thin[:12]}{' …' if len(thin) > 12 else ''}")
    gaps = sorted(perCode.items(), key=lambda item: item[1][2])[:sample]
    log(f"行数最少的 {len(gaps)} 只：{gaps}")
    return {"codes": len(perCode), "days": len(days), "months": months}


def rebuild(root, year, keepDays=Market.KEEP_DAYS, log=print):
    """从 Archive 切出 ``year`` 年的横截面 → ``Data/<year>/<yyyyMM>.mvsv`` + Latest。"""
    year = str(year)
    months = {}
    days = set()
    for path in sorted(glob.glob(os.path.join(root, "Archive", "Security", "*.mvsv"))):
        for row in Market.readRows(path):
            day = row.split("|")[2]
            if day[:4] != year:
                continue
            months.setdefault(day[:6], []).append(row)
            days.add(day)
    if not months:
        log(f"{year} 年无数据，跳过重建")
        return {}
    written = {}
    for month, rows in sorted(months.items()):
        rows.sort(key=lambda row: (row.split("|")[0], row.split("|")[2]))
        path = os.path.join(root, "Data", year, f"{month}.mvsv")
        Market._write(path, Market._headers(path, f"中证微盘股行情 {year} 年 "
                                                   f"{int(month[4:])} 月 日线横截面"
                                                   f"（{len(rows):,} 行）", len(rows)), rows)
        written[month] = len(rows)
    last = sorted(days)[-keepDays:]
    latest = [row for month in months for row in months[month]
              if row.split("|")[2] in last]
    latest.sort(key=lambda row: (row.split("|")[0], row.split("|")[2]))
    path = os.path.join(root, "Latest", "Latest.mvsv")
    Market._write(path, Market._headers(
        path, f"中证微盘股行情 最近 {keepDays} 个交易日横截面（{last[0]} ~ {last[-1]}）",
        len(latest)), latest)
    log(f"重建 {year} 年：{len(written)} 个月度文件（{sum(written.values()):,} 行）· "
        f"Latest {len(latest):,} 行（{last[0]} ~ {last[-1]}）")
    return written
