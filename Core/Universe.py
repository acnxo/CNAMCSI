#!/usr/bin/env python3
"""微盘 400 成分与权重的纯函数实现（不依赖网络，方便单测）。

规则（与项目 AGENTS.md §3 一致）：
  1. 基础池：沪深主板、当日有成交、非 ST/退、上市满 60 个交易日；
  2. 按总市值升序取前 400 只；
  3. 权重默认等权（万得微盘股指数为等权口径），也支持按市值加权。
"""

import bisect

MAIN_BOARD_PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003")
ST_MARKS = ("ST", "退", "PT")


def normalizeDate(v):
    """'20261113' / '2026-11-13' / '2026/11/13' → '2026-11-13'。"""
    s = str(v or "").strip()
    if not s:
        return ""
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return s


def inMainBoard(code):
    return str(code).startswith(MAIN_BOARD_PREFIX)


def isSt(name):
    up = str(name or "").upper()
    return any(m.upper() in up for m in ST_MARKS)


def listedTradingDays(list_date, calendar):
    """自上市日（不含）到日历最后一天的交易日数。

    上市日早于日历窗口 → 10**6（早已上市）；
    上市日缺失 / 日历为空 → 0（视为不可选，避免把缺失上市日的新股误当老股）。
    日期缺失不再逐条打印：调用方（Sources.quotesFromTencent / fillMissingListDates）汇总一条 warn。
    """
    d = normalizeDate(list_date)
    if not d:
        return 0
    if not calendar:
        return 0
    if d < calendar[0]:
        return 10 ** 6
    i = bisect.bisect_left(calendar, d)
    if i >= len(calendar):
        return 0
    return len(calendar) - 1 - i


def eligibility(q, calendar, min_listed_days=60, boards=MAIN_BOARD_PREFIX):
    """返回 (是否合格, 不合格原因)。原因用于统计，便于排查。"""
    if not str(q.get("code") or "").startswith(boards):
        return False, "not_main_board"
    if isSt(q.get("name")):
        return False, "st"
    if (q.get("price") or 0) <= 0 or (q.get("total_mv") or 0) <= 0:
        return False, "no_quote"
    if (q.get("volume") or 0) <= 0:
        return False, "halted"
    listed = q.get("listed_days")
    if listed is None:
        listed = listedTradingDays(q.get("list_date"), calendar)
    if listed < min_listed_days:
        return False, "new_listing"
    return True, ""


def computeWeights(components, mode="equal"):
    """就地写入 weight 字段（占比，合计 1）。"""
    n = len(components)
    if n == 0:
        return components
    if mode == "equal":
        w = [1.0 / n] * n
    elif mode in ("mcap", "float_mv"):
        key = "total_mv" if mode == "mcap" else "float_mv"
        vals = [max(float(c.get(key) or 0.0), 0.0) for c in components]
        s = sum(vals)
        w = [v / s if s > 0 else 1.0 / n for v in vals]
    elif mode in ("mcap_inverse", "inverse_mcap"):
        vals = [1.0 / float(c["total_mv"]) if c.get("total_mv") else 0.0 for c in components]
        s = sum(vals)
        w = [v / s if s > 0 else 1.0 / n for v in vals]
    else:
        raise ValueError(f"未知权重口径: {mode}")
    total = 0.0
    for i, c in enumerate(components):
        c["weight"] = round(w[i], 8)
        total += c["weight"]
    # 归一化误差补到最后一只，保证合计严格为 1
    if components:
        components[-1]["weight"] = round(components[-1]["weight"] + (1.0 - total), 8)
    return components


def buildUniverse(quotes, calendar, n=400, min_listed_days=60,
                   weight_mode="equal", boards=MAIN_BOARD_PREFIX, exclude_top=0):
    """从全量快照里挑出 n 只微盘股，返回成分列表与统计。"""
    reasons = {}
    eligible = []
    for q in quotes:
        listed = q.get("listed_days")
        if listed is None:
            listed = listedTradingDays(q.get("list_date"), calendar)
        q["listed_days"] = listed
        ok, why = eligibility(q, calendar, min_listed_days, boards)
        if ok:
            eligible.append(q)
        else:
            reasons[why] = reasons.get(why, 0) + 1

    eligible.sort(key=lambda x: (x["total_mv"], x["code"]))
    # excludeTop：剔除市值前 N 名（中证 A500/800/1000/2000 覆盖区），再取微盘 n 只
    pool = eligible[int(exclude_top):] if exclude_top else eligible
    picked = pool[:n]
    components = []
    for i, q in enumerate(picked, 1):
        components.append({
            "rank": i,
            "code": q["code"],
            "name": q.get("name", ""),
            "close": round(float(q["price"]), 3),
            "total_mv": round(float(q["total_mv"]), 0),
            "float_mv": round(float(q.get("float_mv") or 0.0), 0),
            "listed_days": int(q["listed_days"]),
        })
    computeWeights(components, weight_mode)
    return {
        "components": components,
        "eligible_count": len(eligible),
        "skipped": reasons,
        "border_mcap": components[-1]["total_mv"] if components else None,
        "next_mcap": round(eligible[n]["total_mv"], 0) if len(eligible) > n else None,
    }


def diffComponents(prev, cur):
    """前后两版成分对比。prev/cur 为 {code: name} 或 [{'code','name'}]。"""
    def to_map(items):
        if isinstance(items, dict):
            return dict(items)
        return {c.get("code"): c.get("name", "") for c in (items or []) if c.get("code")}

    p, c = to_map(prev), to_map(cur)
    enter = [{"code": k, "name": v} for k, v in c.items() if k not in p]
    exit_ = [{"code": k, "name": v} for k, v in p.items() if k not in c]
    enter.sort(key=lambda x: x["code"])
    exit_.sort(key=lambda x: x["code"])
    return {"enter": enter, "exit": exit_, "enter_count": len(enter), "exit_count": len(exit_)}
