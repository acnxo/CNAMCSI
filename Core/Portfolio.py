#!/usr/bin/env python3
"""组合模拟：目标持仓、调仓计划、成交回填、费用、估值（纯函数，零依赖）。

口径见 `Spec.md` §5：
  - 目标市值 = 总资产 / N，股数按 100 股向下取整；
  - 成交价 = 信号日次一交易日开盘价；
  - 费用 = rate 模型（佣金万 1 最低 5 元 + 过户费 0.001% + 印花税卖出 0.05%/0.1%）；
  - 调仓模式：index（只算权重）/ changes（只处理进出）/ full（完全对齐等权）。

不变式（测试会断言）：
  - shares % 100 == 0；
  - cash >= 0；
  - 金额、费用、现金变动可手工复核；
  - 同一标的同一计划里不同时出现买入与卖出。
"""

import math

LOT = 100
COMMISSION_RATE = 0.0001      # 佣金万分之一
COMMISSION_MIN = 5.0          # 单笔最低 5 元
TRANSFER_RATE = 0.00001       # 过户费十万分之一（双向）
STAMP_RATE_NOW = 0.0005       # 印花税（卖出）2023-08-28 起 0.05%
STAMP_RATE_OLD = 0.001        # 2023-08-28 之前 0.1%
STAMP_SWITCH = "2023-08-28"
FEE_BUY_FIXED = 5.1           # 旧模型（仅复现旧结果）
FEE_SELL_FIXED = 5.6

MODES = ("index", "changes", "full")


def calcFee(amount, side, date="2026-01-01", model="rate"):
    """返回 (总费用, 佣金, 印花税, 过户费)，金额单位元，费用四舍五入到分。"""
    amount = float(amount)
    if amount <= 0:
        return 0.0, 0.0, 0.0, 0.0
    if model == "fixed":
        total = FEE_BUY_FIXED if side == "BUY" else FEE_SELL_FIXED
        return round(total, 2), round(total, 2), 0.0, 0.0
    commission = max(amount * COMMISSION_RATE, COMMISSION_MIN)
    transfer = amount * TRANSFER_RATE
    stamp = amount * (STAMP_RATE_NOW if date >= STAMP_SWITCH else STAMP_RATE_OLD) \
        if side == "SELL" else 0.0
    return round(commission + transfer + stamp, 2), round(commission, 2), \
        round(stamp, 2), round(transfer, 2)


def _floorLot(value, price, lot=LOT):
    """按价格与最小交易单位向下取整股数。"""
    if price <= 0:
        return 0
    return int(math.floor(value / price / lot + 1e-9)) * lot


def targetShares(components, total_asset, lot=LOT):
    """等权目标持仓股数：每只目标市值 = 总资产 / N。"""
    n = len(components)
    if n == 0 or total_asset <= 0:
        return {}
    budget = total_asset / n
    out = {}
    for c in components:
        price = float(c.get("close") or 0.0)
        shares = _floorLot(budget, price, lot)
        if shares > 0:
            out[c["code"]] = shares
    return out


def _order(side, code, name, shares, price, reason, date, model):
    amount = round(shares * price, 2)
    fee = calcFee(amount, side, date, model)[0]
    return {
        "signal_date": date, "side": side, "code": code, "name": name,
        "shares": int(shares), "ref_price": round(float(price), 3),
        "est_amount": amount, "est_fee": fee, "reason": reason,
    }


def makePlan(holdings, components, total_asset, cash, mode="changes",
              date="2026-01-01", model="rate", lot=LOT, prices=None):
    """生成调仓计划（信号日收盘 → 次日开盘执行）。

    holdings: {code: {"code","name","shares","cost"}}
    prices:   信号日收盘价 {code: price}（用于给「退出成分」的持仓定价）
    返回 (orders, meta)
    """
    if mode not in MODES:
        raise ValueError(f"未知调仓模式: {mode}")
    if mode == "index":
        return [], {"mode": mode, "entries": 0, "exits": 0}

    comp = {c["code"]: c for c in components}
    rank = {c["code"]: c["rank"] for c in components}
    prices = prices or {}
    orders = []

    # ---- 卖出：不在新成分里的持仓（changes / full 都需要） ----
    exits = [h for code, h in holdings.items() if code not in comp and h["shares"] > 0]
    for h in sorted(exits, key=lambda x: x["code"]):
        ref = prices.get(h["code"]) or h.get("last_price") or h.get("cost") or 0.0
        orders.append(_order("SELL", h["code"], h.get("name", ""), h["shares"],
                             float(ref), "exit", date, model))

    # ---- full：存量与目标股数的差额也要调 ----
    targets = targetShares(components, total_asset, lot) if mode == "full" else {}
    if mode == "full":
        for code, h in list(holdings.items()):
            if code not in comp or h["shares"] <= 0:
                continue
            diff = targets.get(code, 0) - h["shares"]
            if diff < 0:
                orders.append(_order("SELL", code, h.get("name", ""), -diff,
                                     float(comp[code]["close"]), "weight_down", date, model))
    # 卖出后可用现金（预估）
    cash_after = cash + sum(o["est_amount"] - o["est_fee"] for o in orders if o["side"] == "SELL")

    # ---- 买入候选 ----
    if mode == "changes":
        entries = [c for c in components if c["code"] not in holdings]
        entries.sort(key=lambda c: rank[c["code"]])
        budget = min(cash_after / len(entries), total_asset / len(components)) if entries else 0.0
        buys = []
        for c in entries:
            shares = _floorLot(min(budget, cash_after), float(c["close"]), lot)
            if shares > 0:
                buys.append((c, shares))
    else:  # full
        buys = []
        for c in components:
            code = c["code"]
            cur = holdings.get(code, {}).get("shares", 0)
            diff = targets.get(code, 0) - cur
            if diff > 0:
                buys.append((c, diff))

    # 先卖后买：按可用现金逐只成交（计划阶段就做现金约束，保证可执行）
    available = cash_after
    for c, shares in buys:
        price = float(c["close"])
        shares = min(shares, _floorLot(available - 6.0, price, lot))  # 预留最低费用
        if shares <= 0:
            continue
        order = _order("BUY", c["code"], c.get("name", ""), shares, price, "enter", date, model)
        # 校验真实费用（最低 5 元），不足则回退一手
        while shares > 0 and order["est_amount"] + order["est_fee"] > available + 1e-9:
            shares -= lot
            order = _order("BUY", c["code"], c.get("name", ""), shares, price,
                           "enter", date, model)
        if shares <= 0:
            continue
        orders.append(order)
        available -= order["est_amount"] + order["est_fee"]

    meta = {
        "mode": mode,
        "entries": sum(1 for o in orders if o["side"] == "BUY"),
        "exits": sum(1 for o in orders if o["side"] == "SELL" and o["reason"] == "exit"),
        "cash_after_est": round(available, 2),
    }
    return orders, meta


def execute(holdings, cash, orders, exec_prices, date="2026-01-01", model="rate", lot=LOT):
    """按次日开盘价回填成交，返回 (holdings, cash, fills)。

    exec_prices: {code: 开盘价}；缺失或 <= 0（停牌/无行情）视为未成交，保留到下次。
    """
    h = {code: dict(v) for code, v in holdings.items()}
    fills = []
    cash = float(cash)

    def _sell(o, price):
        nonlocal cash
        cur = h.get(o["code"])
        if not cur or cur["shares"] <= 0:
            return
        shares = min(int(o["shares"]), cur["shares"])
        if shares <= 0:
            return
        amount = round(shares * price, 2)
        fee = calcFee(amount, "SELL", date, model)[0]
        avg_cost = cur["cost"]
        realized = round((price - avg_cost) * shares - fee, 2)
        cash = round(cash + amount - fee, 6)
        cur["shares"] -= shares
        cur["last_price"] = price
        if cur["shares"] == 0:
            del h[o["code"]]
        fills.append({
            "signal_date": o.get("signal_date", ""), "exec_date": date,
            "side": "SELL", "code": o["code"], "name": o.get("name", ""),
            "shares": shares, "price": round(price, 3), "amount": amount,
            "fee": fee, "cash_after": round(cash, 2), "realized_pnl": realized,
            "reason": o.get("reason", ""),
        })

    def _buy(o, price):
        nonlocal cash
        shares = int(o["shares"])
        if shares <= 0:
            return
        while shares > 0:
            amount = round(shares * price, 2)
            fee = calcFee(amount, "BUY", date, model)[0]
            if amount + fee <= cash + 1e-9:
                break
            shares -= lot
        if shares <= 0:
            return
        amount = round(shares * price, 2)
        fee = calcFee(amount, "BUY", date, model)[0]
        cash = round(cash - amount - fee, 6)
        total_cost = amount + fee
        cur = h.get(o["code"])
        if cur:
            prev_cost_basis = cur["cost"] * cur["shares"]
            cur["shares"] += shares
            cur["cost"] = round((prev_cost_basis + total_cost) / cur["shares"], 6)
            cur["last_price"] = price
        else:
            h[o["code"]] = {"code": o["code"], "name": o.get("name", ""),
                            "shares": shares, "cost": round(total_cost / shares, 6),
                            "last_price": price}
        fills.append({
            "signal_date": o.get("signal_date", ""), "exec_date": date,
            "side": "BUY", "code": o["code"], "name": o.get("name", ""),
            "shares": shares, "price": round(price, 3), "amount": amount,
            "fee": fee, "cash_after": round(cash, 2), "realized_pnl": 0.0,
            "reason": o.get("reason", ""),
        })

    for o in orders:
        price = exec_prices.get(o["code"])
        if not price or price <= 0:
            continue
        if o["side"] == "SELL":
            _sell(o, float(price))
        else:
            _buy(o, float(price))
    return h, round(cash, 6), fills


def markToMarket(holdings, prices, total_asset=None):
    """按收盘价估值，返回 (rows, market_value, total)。

    rows 字段：code,name,shares,cost,close,market_value,weight,unrealized_pnl
    """
    rows = []
    mv = 0.0
    for code, h in holdings.items():
        price = prices.get(code) or h.get("last_price") or h.get("cost") or 0.0
        value = round(h["shares"] * price, 2)
        mv += value
        rows.append({
            "code": code, "name": h.get("name", ""), "shares": int(h["shares"]),
            "cost": round(float(h.get("cost") or 0.0), 4), "close": round(float(price), 3),
            "market_value": value,
            "unrealized_pnl": round(value - h["shares"] * float(h.get("cost") or 0.0), 2),
        })
    rows.sort(key=lambda r: r["code"])
    total = total_asset if total_asset is not None else mv
    for r in rows:
        r["weight"] = round(r["market_value"] / total, 8) if total > 0 else 0.0
    return rows, round(mv, 2), round(total, 2)


def portfolioSummary(holdings, cash):
    return {
        "holdings_count": len(holdings),
        "holdings_shares": int(sum(h["shares"] for h in holdings.values())),
        "cash": round(float(cash), 2),
    }
