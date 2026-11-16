#!/usr/bin/env python3
"""微盘 400 名单（Micro400）与候选池（Candidates）的查询 / 生成。

查看某一天的 400 只成分股::

    python Tool/Micro400.py --date 2026-11-16              # 打印全部 400 只
    python Tool/Micro400.py --date 2026-11-16 --top 20     # 只看市值最小的 20 只
    python Tool/Micro400.py --date 2026-11-16 --candidates # 只看收盘价 <10 元的候选
    python Tool/Micro400.py --code 600000                  # 查某只股票是否在名单内
    python Tool/Micro400.py --date 2004-12-31 --bottom 10  # 市值最大的 10 只（边界观察）

生成并入库（Spec 7.3 格式）::

    python Tool/Micro400.py --build-year 2026 --candidates   # 当年 → Data/2026/Micro400/
    python Tool/Micro400.py --build-all --candidates         # 全部年份 → Archive/Micro400/

选样规则（与 AGENTS.md §3 一致）：沪深主板、非 ST、当日有成交、上市满 60 个交易日，
按总市值升序取前 400 只；候选池 = 这 400 只里收盘价 < 10 元的。

字段：``Code|Name|Date|Rank|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat``
（与 `Archive/Security`、`Data` 行情同格式，头部 ``# Key : Value``）。
"""

import argparse
import bisect
import glob
import json
import os
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys  # noqa: E402

sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "Core"))

import Market  # noqa: E402
import Universe  # noqa: E402

FIELD = "Code|Name|Date|Rank|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat"
FIELD_CN = ("代码|名称|日期|排名|开盘价|收盘价|最高价|最低价|成交量|成交额"
            "|总股本|流通股本|总市值|流通市值")
FIELD_TYPE = ("String|String|Int|Int|Decimal|Decimal|Decimal|Decimal|Decimal|Decimal"
              "|Decimal|Decimal|Decimal|Decimal")
CACHE_DIR = os.path.join(BASE, "Index", "Cache", "Panel")
FIRST_FILE = "first.json"
DAYS_FILE = "days.json"


# --------------------------------------------------------------------------- #
# 面板缓存：把 Archive/Security + Data/<年> 的行按年份切分，供快速查询
# --------------------------------------------------------------------------- #
def panelDir(root):
    return CACHE_DIR


def _newest(root, paths):
    newest = 0.0
    for path in paths:
        try:
            newest = max(newest, os.path.getmtime(path))
        except OSError:
            pass
    return newest


def ensurePanel(root, force=False, log=print):
    """构建/刷新按年面板缓存（``Index/Cache/Panel/<年>.mvsv`` + ``first.json``）。"""
    folder = panelDir(root)
    sources = glob.glob(os.path.join(root, "Archive", "Security", "*.mvsv")) + \
        glob.glob(os.path.join(root, "Data", "*", "*.mvsv"))
    stampPath = os.path.join(folder, "stamp.json")
    stamp = _newest(root, sources)
    if not force and os.path.exists(stampPath):
        try:
            with open(stampPath, encoding="utf-8") as file:
                cached = float(json.load(file).get("source_mtime") or 0)
        except (ValueError, OSError):
            cached = 0.0
        if cached >= stamp and os.path.exists(os.path.join(folder, FIRST_FILE)) \
                and os.path.exists(os.path.join(folder, DAYS_FILE)):
            return folder
    log(f"重建面板缓存（{len(sources)} 个文件）…")
    os.makedirs(folder, exist_ok=True)
    handles = {}
    first = {}
    days = set()
    rows = 0
    try:
        for path in sources:
            for row in Market.readRows(path):
                field = row.split("|")
                if len(field) < 14 - 1:      # 13 列
                    continue
                year = field[2][:4]
                handle = handles.get(year)
                if handle is None:
                    handle = open(os.path.join(folder, f"{year}.mvsv"), "w", encoding="utf-8")
                    handles[year] = handle
                handle.write(row + "\n")
                rows += 1
                days.add(field[2])
                code = field[0]
                if code not in first or field[2] < first[code]:
                    first[code] = field[2]
    finally:
        for handle in handles.values():
            handle.close()
    with open(os.path.join(folder, FIRST_FILE), "w", encoding="utf-8") as file:
        json.dump(first, file, ensure_ascii=False, separators=(",", ":"))
    with open(os.path.join(folder, DAYS_FILE), "w", encoding="utf-8") as file:
        json.dump(sorted(days), file, separators=(",", ":"))
    with open(stampPath, "w", encoding="utf-8") as file:
        json.dump({"source_mtime": stamp, "rows": rows,
                   "built": time.strftime("%Y-%m-%d %H:%M:%S")}, file)
    log(f"面板缓存完成：{rows:,} 行 · {len(handles)} 个年份文件")
    return folder


def yearRows(root, year):
    """读某年的面板行（依赖 ``ensurePanel``）。"""
    path = os.path.join(panelDir(root), f"{year}.mvsv")
    return Market.readRows(path)


def firstDays(root):
    with open(os.path.join(panelDir(root), FIRST_FILE), encoding="utf-8") as file:
        return json.load(file)


def calendarDays(root):
    """全部交易日（升序），用于「上市满 60 个交易日」判定。"""
    with open(os.path.join(panelDir(root), DAYS_FILE), encoding="utf-8") as file:
        return json.load(file)


# --------------------------------------------------------------------------- #
# 选样
# --------------------------------------------------------------------------- #
def pick(rows, calendarDays, first, n=400, minListed=60):
    """单日横截面 → 微盘 n 只（总市值升序）。``rows`` 为当日全部交易行。"""
    day = rows[0].split("|")[2] if rows else ""
    pos = bisect.bisect_left(calendarDays, day)
    candidates = []
    for row in rows:
        field = row.split("|")
        code, name = field[0], field[1]
        if not Universe.inMainBoard(code) or Universe.isSt(name):
            continue
        try:
            volume = float(field[7])
            close = float(field[4])
            mcap = float(field[11])
        except ValueError:
            continue
        if volume <= 0 or close <= 0 or mcap <= 0:
            continue
        start = first.get(code) or field[2]
        listed = pos - bisect.bisect_left(calendarDays, start)
        if listed < minListed:
            continue
        candidates.append((mcap, code, row, listed))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [(item[0], item[1], item[2], item[3], rank)
            for rank, item in enumerate(candidates[:n], 1)]


def dayRows(root, day):
    """某交易日的全部行（来自面板缓存）。"""
    rows = yearRows(root, day[:4])
    return [row for row in rows if row.split("|")[2] == day]


def toLine(item, day, rank=None):
    mcap, code, row, _, original = item
    field = row.split("|")
    return "|".join([code, field[1], day, str(rank or original)] + field[3:])


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def exportCsv(picked, day, path):
    """导出 CSV（UTF-8 BOM，Excel 可直接双击打开）。"""
    import csv
    cols = ["Rank", "Code", "Name", "Date", "Open", "Close", "High", "Low", "Volume",
            "Amount", "SharesTotal", "SharesFloat", "MvTotal", "MvFloat", "ListedDays"]
    with open(path, "w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(cols)
        for item in picked:
            field = item[2].split("|")
            writer.writerow([item[4], field[0], field[1], day] + field[3:] + [item[3]])
    return path


def show(root, day, top=None, bottom=None, candidates=False, code=None, log=print,
         csvPath=None):
    ensurePanel(root, log=log)
    rows = dayRows(root, day)
    if not rows:
        log(f"{day} 无行情数据（非交易日或该日未采集）")
        return []
    picked = pick(rows, calendarDays(root), firstDays(root))
    if candidates:
        picked = [item for item in picked if float(item[2].split("|")[4]) < 10]
    if code:
        found = [item for item in picked if item[1] == str(code)]
        if not found:
            log(f"{code} 不在 {day} 的名单内（{len(picked)} 只）")
            return []
        item = found[0]
        field = item[2].split("|")
        log(f"{day} · 第 {item[4]}/{len(picked)} 名 · {field[0]} {field[1]} "
            f"收盘 {field[4]} · 总市值 {float(field[11]) / 1e8:.2f} 亿")
        return [item]
    if top:
        picked = picked[:top]
    elif bottom:
        picked = picked[-bottom:]
    if csvPath:
        exportCsv(picked, day, csvPath)
        log(f"已导出 {len(picked)} 只 → {csvPath}")
    log(f"{day} · {'候选池（<10 元）' if candidates else '微盘名单'} {len(picked)} 只")
    log(f"{'排名':>4}  {'代码':<8}{'名称':<10}{'收盘':>8}{'总市值(亿)':>12}{'上市天数':>8}")
    for item in picked:
        field = item[2].split("|")
        name = field[1].replace("\u3000", " ")[:8]
        log(f"{item[4]:>4}  {field[0]:<8}{name:<10}{float(field[4]):>8.2f}"
            f"{float(field[11]) / 1e8:>12.2f}{item[3]:>8}")
    return picked


# --------------------------------------------------------------------------- #
# 生成（Spec 7.3 格式）
# --------------------------------------------------------------------------- #
def headers(title, count):
    return [("标题", title), ("字段名称", f'"{FIELD_CN}"'), ("DataProvider", "CNAMCSI"),
            ("Field", f'"{FIELD}"'), ("FieldName", f'"{FIELD}"'),
            ("FieldType", f'"{FIELD_TYPE}"'), ("TypeKLine", "Day"),
            ("Count", str(count)), ("Symbol", ""), ("SecuCode", ""), ("USC", ""),
            ("Name", '""'), ("Region", "CN"), ("Market", "SSE|SZSE"),
            ("TimeZone", "Asia/Shanghai"), ("Currency", "CNY")]


def buildYear(root, year, withCandidates=False, log=print):
    """生成 ``<年>`` 的 Micro400（历史进 Archive、当年进 Data）。"""
    ensurePanel(root, log=log)
    year = str(year)
    rows = yearRows(root, year)
    if not rows:
        log(f"{year} 年无面板数据")
        return {}
    days = calendarDays(root)
    first = firstDays(root)
    byDay = {}
    for row in rows:
        byDay.setdefault(row.split("|")[2], []).append(row)
    micro, cand = {}, {}
    for day in sorted(byDay):
        picked = pick(byDay[day], days, first)
        for rank, item in enumerate(picked, 1):
            line = toLine(item, day, rank)
            micro.setdefault(day[:6], []).append(line)
            if withCandidates and float(item[2].split("|")[4]) < 10:
                cand.setdefault(day[:6], []).append(line)
    isCurrent = year == max(days)[:4] or int(year) > int(max(days)[:4])
    written = {}
    for kind, data in (("Micro400", micro), ("Candidates", cand)):
        if not data:
            continue
        if isCurrent:                       # 当年 → Data/<年>/<kind>/<yyyyMM>.mvsv
            for month, lines in sorted(data.items()):
                lines.sort(key=lambda line: (line.split("|")[0], line.split("|")[2]))
                path = os.path.join(root, "Data", year, kind, f"{month}.mvsv")
                title = f"中证微盘股{kind} {year} 年 {int(month[4:])} 月（{len(lines):,} 行）"
                Market._write(path, headers(title, len(lines)), lines)
                written[f"{kind}/{month}"] = len(lines)
        else:                               # 历史年 → Archive/<kind>/<年>.mvsv（全年一个文件）
            lines = [line for month in sorted(data) for line in data[month]]
            lines.sort(key=lambda line: (line.split("|")[0], line.split("|")[2]))
            path = os.path.join(root, "Archive", kind, f"{year}.mvsv")
            title = f"中证微盘股{kind} {year} 年（{len(lines):,} 行）"
            Market._write(path, headers(title, len(lines)), lines)
            written[kind] = len(lines)
    total_micro = sum(len(v) for v in micro.values())
    log(f"{year} 年：Micro400 {total_micro:,} 行 · Candidates "
        f"{sum(len(v) for v in cand.values()):,} 行 · 写入 {len(written)} 个文件")
    return written


def buildDay(root, day, withCandidates=True, log=print):
    """只生成某一天的 Micro400（写当年 Data 目录），供每日流水线调用。"""
    ensurePanel(root, log=log)
    rows = dayRows(root, day)
    if not rows:
        log(f"{day} 无行情数据，跳过 Micro400")
        return {}
    picked = pick(rows, calendarDays(root), firstDays(root))
    year = day[:4]
    lines = [toLine(item, day) for item in picked]
    lines.sort(key=lambda line: int(line.split("|")[3]))
    path = os.path.join(root, "Data", year, "Micro400", f"{day[:6]}.mvsv")
    merged = {(row.split("|")[0], row.split("|")[2]): row for row in Market.readRows(path)}
    merged.update({(row.split("|")[0], row.split("|")[2]): row for row in lines})
    out = [merged[key] for key in sorted(merged)]
    Market._write(path, headers(f"中证微盘股Micro400 {year} 年 {int(day[4:6])} 月"
                                f"（{len(out):,} 行）", len(out)), out)
    log(f"{day} · Micro400 {len(lines)} 只 → Data/{year}/Micro400/{day[:6]}.mvsv")
    if withCandidates:
        cand = [line for line in lines if float(line.split("|")[5]) < 10]
        cpath = os.path.join(root, "Data", year, "Candidates", f"{day[:6]}.mvsv")
        cmerged = {(row.split("|")[0], row.split("|")[2]): row
                   for row in Market.readRows(cpath)}
        cmerged.update({(row.split("|")[0], row.split("|")[2]): row for row in cand})
        cout = [cmerged[key] for key in sorted(cmerged)]
        Market._write(cpath, headers(f"中证微盘股Candidates {year} 年 {int(day[4:6])} 月"
                                     f"（{len(cout):,} 行）", len(cout)), cout)
        log(f"{day} · Candidates {len(cand)} 只 → Data/{year}/Candidates/{day[:6]}.mvsv")
    return {"micro400": len(lines), "candidates": len(cand) if withCandidates else 0}


def buildAll(root, withCandidates=False, log=print):
    ensurePanel(root, log=log)
    years = sorted(os.path.basename(path)[:-5] for path
                   in glob.glob(os.path.join(panelDir(root), "*.mvsv")))
    done = {}
    for year in years:
        done[year] = buildYear(root, year, withCandidates, log=log)
    return done


def main():
    parser = argparse.ArgumentParser(description="微盘 400 名单查询 / 生成")
    parser.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认最新）")
    parser.add_argument("--top", type=int, default=None, help="只看市值最小的 N 只")
    parser.add_argument("--bottom", type=int, default=None, help="只看市值最大的 N 只")
    parser.add_argument("--code", default=None, help="查某只股票在名单中的排名")
    parser.add_argument("--candidates", action="store_true", help="只看收盘价 <10 元的候选")
    parser.add_argument("--build-year", default=None, metavar="YEAR")
    parser.add_argument("--build-day", default=None, metavar="DATE",
                        help="只生成某一天（供每日流水线调用）")
    parser.add_argument("--build-all", action="store_true")
    parser.add_argument("--csv", default=None, metavar="PATH",
                        help="把查询结果导出为 CSV（Excel 可直接打开）")
    parser.add_argument("--rebuild-cache", action="store_true", help="强制重建面板缓存")
    args = parser.parse_args()

    root = BASE
    if args.rebuild_cache:
        ensurePanel(root, force=True)
    if args.build_day:
        buildDay(root, args.build_day.replace("-", "")[:8])
        return
    if args.build_year:
        buildYear(root, args.build_year, args.candidates)
        return
    if args.build_all:
        buildAll(root, args.candidates)
        return
    day = (args.date or "").replace("-", "")[:8] or None
    if not day:
        ensurePanel(root, log=print)
        years = sorted(os.path.basename(path)[:-5] for path
                       in glob.glob(os.path.join(panelDir(root), "*.mvsv")))
        day = max(row.split("|")[2] for row in yearRows(root, years[-1]))
    show(root, day, top=args.top, bottom=args.bottom,
         candidates=args.candidates, code=args.code, csvPath=args.csv)


if __name__ == "__main__":
    main()
