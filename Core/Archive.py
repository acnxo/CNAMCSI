#!/usr/bin/env python3
"""三层存储：Current（当日/未完整）→ Pending（当年待归档）→ Archive（已归档）。

分层规则（见 SPEC §7）：
- L3 ``Current/<Kind>.jsonl``：最近 5 个交易日，每天覆盖写，便于快速调整；
- L2 ``Pending/<年>/<Kind>.mvsv``：已确认完整、当年内可修正；**次年 3 月 1 日**整体转 L1；
- L1 ``Archive/<年>/<Kind>.mvsv``：只读归档，支持批量 patch（按年长表，加列只需改 23 个文件/指数）。

Kinds：Components / Holdings / Trades / Summary（不混存）。
"""

import csv
import glob
import json
import os
import shutil

import Mvsv

COMP_COLS = ["date", "rank", "code", "name", "close", "total_mv", "float_mv", "weight",
             "listed_days"]
HOLD_COLS = ["date", "code", "name", "shares", "cost", "close", "market_value", "weight",
             "unrealized_pnl"]
TRADE_COLS = ["kind", "date", "signal_date", "exec_date", "side", "code", "name", "shares",
              "price", "amount", "fee", "cash_after", "realized_pnl", "reason"]
SUMMARY_COLS = ["date", "point", "ret", "cum_return", "holdings_count", "total_asset",
                "cash", "rebalance"]
KINDS = {"Components": COMP_COLS, "Holdings": HOLD_COLS, "Trades": TRADE_COLS,
         "Summary": SUMMARY_COLS}
KEEP_DAYS = 5          # Current 保留的交易日数
ARCHIVE_MONTH_DAY = "-03-01"   # 次年 3 月 1 日归档


# --------------------------------------------------------------------------- #
# 通用读写
# --------------------------------------------------------------------------- #
def readJsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def writeJsonl(path, rows):
    """写 .jsonl：一行一个 JSON 对象，紧凑格式（无多余空格，separators=(",", ":")）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def readCsv(path, columns=None):
    """读 .mvsv（保留旧函数名，内部走 Mvsv）。"""
    return Mvsv.read(path, columns)


def writeCsv(path, rows, columns):
    """写 .mvsv（保留旧函数名，内部走 Mvsv）。"""
    Mvsv.write(path, rows, columns, title=os.path.basename(path))


def appendCsv(path, rows, columns):
    if not rows:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    newFile = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        if newFile:
            writer.writeheader()
        writer.writerows(rows)


# --------------------------------------------------------------------------- #
# 每日写入：Current（L3）
# --------------------------------------------------------------------------- #
def buildDayRows(date, components, holdings, plan, execution, record):
    """把一天的明细整理成 4 类行（Kinds）。"""
    trades = []
    for order in plan:
        trades.append({"kind": "plan", "date": date, "signal_date": date, "exec_date": "",
                       "side": order.get("side"), "code": order.get("code"),
                       "name": order.get("name"), "shares": order.get("shares"),
                       "price": order.get("ref_price"), "amount": order.get("est_amount"),
                       "fee": order.get("est_fee"), "reason": order.get("reason")})
    for fill in execution:
        trades.append({"kind": "exec", "date": date, **fill})
    summary = {
        "date": date, "point": record.get("point"), "ret": record.get("ret"),
        "cum_return": record.get("cum_return"),
        "holdings_count": (record.get("portfolio") or {}).get("holdings_count", 0),
        "total_asset": (record.get("portfolio") or {}).get("total_asset"),
        "cash": (record.get("portfolio") or {}).get("cash"),
        "rebalance": (record.get("scan") or {}).get("rebalance", ""),
    }
    return {
        "Components": [{"date": date, **item} for item in components],
        "Holdings": [{"date": date, **item} for item in holdings],
        "Trades": trades,
        "Summary": [summary],
    }


def writeCurrentDay(root, date, dayRows):
    """把当日写入 Current/<Kind>.jsonl（追加/替换，不裁剪；裁剪由 mergeToPending 负责）。"""
    current = os.path.join(root, "Current")
    for kind in KINDS:
        path = os.path.join(current, f"{kind}.jsonl")
        rows = [row for row in readJsonl(path) if row.get("date") != date]
        rows.extend(dayRows.get(kind) or [])
        rows.sort(key=lambda row: (row.get("date") or "", row.get("rank") or ""))
        writeJsonl(path, rows)


def writeCurrentRange(root, dayRowsList):
    """一次性写入 Current（回放收尾用，等价于逐日 writeCurrentDay）。"""
    current = os.path.join(root, "Current")
    for kind in KINDS:
        rows = [row for dayRows in dayRowsList for row in (dayRows.get(kind) or [])]
        writeJsonl(os.path.join(current, f"{kind}.jsonl"), rows)


# --------------------------------------------------------------------------- #
# 合并：Current 中已确认的 → Pending（L2）
# --------------------------------------------------------------------------- #
def mergeToPending(root, keepDays=KEEP_DAYS):
    """把 Current 中超出最近 ``keepDays`` 天的记录合并进 Pending/<年>/*.mvsv（按日期去重）。"""
    current = os.path.join(root, "Current")
    allDates = set()
    for kind in KINDS:
        allDates |= {row.get("date") for row in readJsonl(os.path.join(current, f"{kind}.jsonl"))
                     if row.get("date")}
    if not allDates:
        return 0
    keep = set(sorted(allDates, reverse=True)[:keepDays])
    old = sorted(allDates - keep)
    if not old:
        return 0
    byYear = {}
    for day in old:
        byYear.setdefault(day[:4], set()).add(day)
    merged = 0
    for year, days in byYear.items():
        for kind, columns in KINDS.items():
            source = [row for row in readJsonl(os.path.join(current, f"{kind}.jsonl"))
                      if row.get("date") in days]
            if not source:
                continue
            path = os.path.join(root, "Pending", year, f"{kind}.mvsv")
            rows = [row for row in readCsv(path) if row.get("date") not in days]
            rows.extend(source)
            rows.sort(key=lambda row: (row.get("date") or "", row.get("rank") or ""))
            writeCsv(path, rows, columns)
            merged += len(source)
    # 从 Current 删除已合并的日期
    for kind in KINDS:
        path = os.path.join(current, f"{kind}.jsonl")
        writeJsonl(path, [row for row in readJsonl(path) if row.get("date") not in old])
    return merged


# --------------------------------------------------------------------------- #
# 归档：Pending/<年> → Archive/<年>（次年 3 月 1 日）
# --------------------------------------------------------------------------- #
def archivePending(root, today=None, monthDay=ARCHIVE_MONTH_DAY):
    """把满足归档时点的 Pending/<年> 整体移动到 Archive/<年>。返回已归档年份列表。"""
    import datetime

    today = today or datetime.date.today().isoformat()
    archived = []
    for path in sorted(glob.glob(os.path.join(root, "Pending", "*"))):
        year = os.path.basename(path)
        if not year.isdigit():
            continue
        if today >= f"{int(year) + 1}{monthDay}":
            target = os.path.join(root, "Archive", year)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target):
                shutil.rmtree(target)
            shutil.move(path, target)
            archived.append(year)
    return archived


# --------------------------------------------------------------------------- #
# 回放/迁移：直接写 Archive 或 Pending
# --------------------------------------------------------------------------- #
def writeYear(root, year, dayRowsList, currentYear):
    """把某年的全部日记录写入 Archive（历史）或 Pending（当年）。"""
    layer = "Pending" if str(year) == str(currentYear) else "Archive"
    base = os.path.join(root, layer, str(year))
    for kind, columns in KINDS.items():
        rows = []
        for dayRows in dayRowsList:
            rows.extend(dayRows.get(kind) or [])
        if rows:
            rows.sort(key=lambda row: (row.get("date") or "", row.get("rank") or ""))
            writeCsv(os.path.join(base, f"{kind}.mvsv"), rows, columns)


def readLayer(root, year):
    """读取某年数据（先 Pending 后 Archive）。"""
    for layer in ("Pending", "Archive"):
        base = os.path.join(root, layer, str(year))
        if os.path.isdir(base):
            return {kind: readCsv(os.path.join(base, f"{kind}.mvsv"), columns)
                    for kind, columns in KINDS.items()}
    return {kind: [] for kind in KINDS}
