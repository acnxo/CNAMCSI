#!/usr/bin/env python3
"""行情三层（L1 Archive / L2 Data / L3 Latest）的读写与每日更新。

三层结构（``.mvsv``：头部元数据 + 竖线分隔的数据区）::

    Archive/Security/<Code>.mvsv   L1 归档：每证券 1 个，跨年后冻结
    Data/<yyyy>/<yyyyMM>.mvsv      L2 当年：按月横截面
    Latest/Latest.mvsv             L3 当前：最近 5 个交易日横截面

数据行（13 列，无表头，字段名见 ``# Field``）::

    Code|Name|Date|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat

- ``Volume`` 单位为**股**（行情接口多为「手」，此处乘 100 换算）；
- ``High``/``Low`` 缺失时用 ``max/min(Open, Close)`` 兜底，避免整行被丢弃；
- ``SharesTotal``/``SharesFloat`` 由「市值 ÷ 收盘」反推（与历史文件口径一致）。

更新是幂等的：同一天重复执行只会覆盖当日横截面，不会产生重复行。
"""

import glob
import json
import os

FIELD = ("Code|Name|Date|Open|Close|High|Low|Volume|Amount"
         "|SharesTotal|SharesFloat|MvTotal|MvFloat")
FIELD_CN = ("代码|名称|日期|开盘价|收盘价|最高价|最低价"
            "|成交量|成交额|总股本|流通股本|总市值|流通市值")
FIELD_TYPE = ("String|String|Int|Decimal|Decimal|Decimal|Decimal"
              "|Decimal|Decimal|Decimal|Decimal|Decimal|Decimal")
PROVIDER = "Sina/Tencent"
KEEP_DAYS = 5                    # L3 保留的交易日数
ARCHIVED_FILE = ".ArchivedYears.json"
BOARDS = ("600", "601", "603", "605", "000", "001", "002", "003")


# --------------------------------------------------------------------------- #
# 行格式化
# --------------------------------------------------------------------------- #
def _trim(value, digits):
    """定点小数后去掉多余的 0（12.10 → 12.1，11.00 → 11）。"""
    text = f"{float(value):.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _price(value):
    return _trim(value, 4)


def _amount(value):
    return _trim(value, 3)


def _mv(value):
    return f"{float(value):.2f}"


def _shares(value):
    return str(int(round(float(value))))


def rowsFromQuotes(quotes, date):
    """行情快照（``Sources`` 的 Quote 列表）→ 13 列数据行（按代码升序）。

    ``date`` 为 ``YYYYMMDD``。缺价格/市值、非沪深主板的一律跳过。
    """
    rows = []
    for q in quotes:
        code = str(q.get("code") or "").strip()
        close = q.get("price") or 0.0
        totalMv = q.get("total_mv") or 0.0
        if not code.startswith(BOARDS) or close <= 0 or totalMv <= 0:
            continue
        if (q.get("volume") or 0.0) <= 0:      # 停牌/退市：当日无 K 线，历史文件同样不含
            continue
        openPrice = q.get("open") or close
        high = q.get("high") or 0.0
        low = q.get("low") or 0.0
        high = max(high, openPrice, close)
        low = min(low, openPrice, close) if low > 0 else min(openPrice, close)
        volume = int(round((q.get("volume") or 0.0) * 100))     # 手 → 股
        amount = float(q.get("amount") or 0.0)
        floatMv = q.get("float_mv") or totalMv
        rows.append("|".join([
            code, str(q.get("name") or "").strip() or code, date,
            _price(openPrice), _price(close), _price(high), _price(low),
            str(volume), _amount(amount),
            _shares(totalMv / close), _shares(floatMv / close),
            _mv(totalMv), _mv(floatMv),
        ]))
    rows.sort(key=lambda line: line.split("|", 1)[0])
    return rows


# --------------------------------------------------------------------------- #
# 底层读写
# --------------------------------------------------------------------------- #
def readRows(path):
    """读数据区（跳过 ``#`` 元数据行与空行），返回原始行字符串列表。"""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            rows.append(line)
    return rows


def readHeaders(path):
    """读 ``# Key : Value`` 元数据，返回 [(key, value)]（保持原顺序）。"""
    headers = []
    if not os.path.exists(path):
        return headers
    with open(path, encoding="utf-8") as file:
        for line in file:
            if not line.startswith("#"):
                break
            key, _, value = line[1:].partition(":")
            headers.append((key.strip(), value.strip()))
    return headers


def _write(path, headers, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        for key, value in headers:
            file.write(f"# {key} : {value}\n")
        file.write("\n")
        for row in rows:
            file.write(row + "\n")


DEFAULT_HEADERS = [("字段名称", f'"{FIELD_CN}"'), ("DataProvider", PROVIDER),
                   ("Field", f'"{FIELD}"'), ("FieldName", f'"{FIELD}"'),
                   ("FieldType", f'"{FIELD_TYPE}"'), ("TypeKLine", "Day"),
                   ("Count", "0"), ("Symbol", ""), ("SecuCode", ""), ("USC", ""), ("Name", '""'),
                   ("Region", "CN"), ("Market", "SSE|SZSE"),
                   ("TimeZone", "Asia/Shanghai"), ("Currency", "CNY")]


def _headers(path, title, count):
    """沿用原文件头（保留 TypeKLine/Symbol/Region 等），只替换标题与 Count。"""
    headers = readHeaders(path)
    if not headers:
        headers = [("标题", title)] + [(key, str(count) if key == "Count" else value)
                                       for key, value in DEFAULT_HEADERS]
    out, seen = [], False
    for key, value in headers:
        if key == "标题":
            out.append((key, title)); seen = True
        elif key == "Count":
            out.append((key, str(count)))
        else:
            out.append((key, value))
    if not seen:
        out.insert(0, ("标题", title))
    return out


def _merge(rows, newRows, date, keepDays=None):
    """按日期替换后合并，按 (Code, Date) 排序；``keepDays`` 只保留最近 N 个交易日。"""
    merged = [row for row in rows if row.split("|")[2] != date] + list(newRows)
    merged.sort(key=lambda line: (line.split("|")[0], line.split("|")[2]))
    if keepDays:
        dates = sorted({row.split("|")[2] for row in merged})
        keep = set(dates[-keepDays:])
        merged = [row for row in merged if row.split("|")[2] in keep]
    return merged


def _dateSpan(rows):
    dates = sorted({row.split("|")[2] for row in rows})
    return (dates[0], dates[-1]) if dates else ("", "")


# --------------------------------------------------------------------------- #
# 三层落盘
# --------------------------------------------------------------------------- #
def writeLatest(root, rows):
    """L3：最近 ``KEEP_DAYS`` 个交易日横截面。"""
    path = os.path.join(root, "Latest", "Latest.mvsv")
    merged = _merge(readRows(path), rows, _dateOf(rows), KEEP_DAYS)
    first, last = _dateSpan(merged)
    title = f"中证微盘股行情 最近 {KEEP_DAYS} 个交易日横截面（{first} ~ {last}）"
    _write(path, _headers(path, title, len(merged)), merged)
    return path, len(merged)


def writeMonthly(root, rows):
    """L2：``Data/<yyyy>/<yyyyMM>.mvsv`` 追加/覆盖当日横截面。"""
    date = _dateOf(rows)
    year, month = date[:4], date[4:6]
    path = os.path.join(root, "Data", year, f"{year}{month}.mvsv")
    merged = _merge(readRows(path), rows, date)
    title = f"中证微盘股行情 {year} 年 {month} 月 日线横截面（{len(merged):,} 行）"
    _write(path, _headers(path, title, len(merged)), merged)
    return path, len(merged)


def _dateOf(rows):
    """取数据行的日期（同一批必然同一天）。"""
    return rows[0].split("|")[2] if rows else ""


def archivedYears(root):
    path = os.path.join(root, "Archive", ARCHIVED_FILE)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as file:
            return [str(y) for y in (json.load(file) or {}).get("years", [])]
    except (ValueError, OSError):
        return []


def _saveArchived(root, years):
    path = os.path.join(root, "Archive", ARCHIVED_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump({"years": sorted(years)}, file, ensure_ascii=False, separators=(",", ":"))


def archiveYear(root, year, log=print):
    """把 ``Data/<year>/`` 的月度横截面合并进 ``Archive/Security/<Code>.mvsv``（幂等）。"""
    files = sorted(glob.glob(os.path.join(root, "Data", str(year), "*.mvsv")))
    if not files:
        return 0
    byCode = {}
    for path in files:
        for row in readRows(path):
            byCode.setdefault(row.split("|")[0], {}).update({row.split("|")[2]: row})
    saved = 0
    for code, dated in sorted(byCode.items()):
        path = os.path.join(root, "Archive", "Security", f"{code}.mvsv")
        existing = {row.split("|")[2]: row for row in readRows(path)}
        existing.update(dated)               # 同一天以 Data 为准
        merged = [existing[date] for date in sorted(existing)]
        name = merged[-1].split("|")[1]
        title = f"中证微盘股行情 {code} {name} 日线"
        _write(path, _headers(path, title, len(merged)), merged)
        saved += 1
    years = archivedYears(root)
    if str(year) not in years:
        years.append(str(year))
        _saveArchived(root, years)
    log(f"行情归档：{year} 年 {saved} 只证券 → Archive/Security")
    return saved


def update(root, quotes, date, log=print, archive=True):
    """用当日快照更新行情三层（幂等），返回摘要字典。

    ``root`` 为仓库根（含 ``Latest``/``Data``/``Archive``）；
    ``date`` 为 ``YYYY-MM-DD`` 或 ``YYYYMMDD``。
    """
    day = str(date).replace("-", "")[:8]
    rows = rowsFromQuotes(quotes, day)
    if not rows:
        return {"ok": False, "reason": "no_rows", "date": day}
    latestPath, latestCount = writeLatest(root, rows)
    dataPath, dataCount = writeMonthly(root, rows)
    info = {"ok": True, "date": day, "rows": len(rows),
            "latest": os.path.relpath(latestPath, root), "latest_rows": latestCount,
            "data": os.path.relpath(dataPath, root), "data_rows": dataCount}
    if archive:                                   # 跨年后把往年 L2 并入 L1（幂等）
        year = int(day[:4])
        done = archivedYears(root)
        for path in sorted(glob.glob(os.path.join(root, "Data", "*"))):
            name = os.path.basename(path)
            if name.isdigit() and int(name) < year and name not in done:
                info.setdefault("archived", []).append(
                    {"year": name, "codes": archiveYear(root, name, log=log)})
    log(f"行情三层更新：{day} · {len(rows)} 只 · "
        f"Latest {latestCount} 行 · {os.path.basename(dataPath)} {dataCount} 行")
    return info
