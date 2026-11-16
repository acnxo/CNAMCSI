#!/usr/bin/env python3
"""微盘 400 名单：**按需现场计算，不落盘**。

数据源：``Archive/Security``（历史年）+ ``Data/<年>``（当年）+ ``Latest``（最近 5 个交易日，
同日与 Data 重复时以 Latest 为准，避免重复行）。

名单 = 每日横截面上「主板 + 非 ST + 有成交 + 上市满 60 交易日」按总市值升序取前 400，
所以不需要再存一份：直接算即得。

查看某一天的 400 只成分股::

    python Tool/Micro400.py --date 2026-11-16              # 打印全部 400 只
    python Tool/Micro400.py --date 2026-11-16 --top 20     # 只看市值最小的 20 只
    python Tool/Micro400.py --date 2026-11-16 --candidates # 只看收盘价 <10 元的候选
    python Tool/Micro400.py --code 600000                  # 查某只股票是否在名单内
    python Tool/Micro400.py --date 2004-12-31 --bottom 10  # 市值最大的 10 只（边界观察）

选样规则（与 AGENTS.md §3 一致）：沪深主板、非 ST、当日有成交、上市满 60 个交易日，
按总市值升序取前 400 只；候选池 = 这 400 只里收盘价 < 10 元的。

只读：不写 `Archive/`、`Data/`、`Latest/`；仅在 `Index/Cache/Panel/`（.gitignore）建查询缓存。
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
    """构建/刷新按年面板缓存（``Index/Cache/Panel/<年>.mvsv`` + ``first.json``/``days.json``）。

    源 = ``Archive/Security`` + ``Data/<年>`` + ``Latest``（最近 5 日）；同一 ``Code+Date``
    在 Latest 与 Data 都出现时以 Latest 为准（先收集 Latest 的键，写盘时跳过）。
    """
    folder = panelDir(root)
    latestPath = os.path.join(root, "Latest", "Latest.mvsv")
    sources = glob.glob(os.path.join(root, "Archive", "Security", "*.mvsv")) + \
        glob.glob(os.path.join(root, "Data", "*", "*.mvsv"))
    if os.path.exists(latestPath):
        sources.append(latestPath)
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
    latestRows, latestKeys = [], set()
    if os.path.exists(latestPath):
        for row in Market.readRows(latestPath):
            field = row.split("|")
            latestRows.append(row)
            latestKeys.add(f"{field[0]}|{field[2]}")
    log(f"重建面板缓存（{len(sources)} 个文件，Latest {len(latestRows)} 行）…")
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
                if latestKeys and f"{field[0]}|{field[2]}" in latestKeys:
                    continue                 # 该日已由 Latest 提供，避免重复行
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
        for row in latestRows:               # Latest 的行（多数已跳过，这里兜底补缺）
            field = row.split("|")
            year = field[2][:4]
            handle = handles.get(year)
            if handle is None:
                handle = open(os.path.join(folder, f"{year}.mvsv"), "w", encoding="utf-8")
                handles[year] = handle
            handle.write(row + "\n")
            rows += 1
            days.add(field[2])
            if field[0] not in first or field[2] < first[field[0]]:
                first[field[0]] = field[2]
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
def main():
    parser = argparse.ArgumentParser(description="微盘 400 名单查询 / 生成")
    parser.add_argument("--date", default=None, help="交易日 YYYY-MM-DD（默认最新）")
    parser.add_argument("--top", type=int, default=None, help="只看市值最小的 N 只")
    parser.add_argument("--bottom", type=int, default=None, help="只看市值最大的 N 只")
    parser.add_argument("--code", default=None, help="查某只股票在名单中的排名")
    parser.add_argument("--candidates", action="store_true", help="只看收盘价 <10 元的候选")
    parser.add_argument("--csv", default=None, metavar="PATH",
                        help="把查询结果导出为 CSV（Excel 可直接打开）")
    parser.add_argument("--rebuild-cache", action="store_true", help="强制重建面板缓存")
    args = parser.parse_args()

    root = BASE
    if args.rebuild_cache:
        ensurePanel(root, force=True)
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
