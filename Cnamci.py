#!/usr/bin/env python3
"""CNAMCI 基线指数体系：点位、权重漂移与报告渲染（纯函数，零依赖）。

三个指数共用同一选样口径（400 只微盘等权），仅调仓频率不同：

| 代码 | 名称 | 调仓频率 |
| --- | --- | --- |
| `CNAMCSDEWI` | 中证微盘股日频等权指数 | 每个交易日 |
| `CNAMCSWEWI` | 中证微盘股周频等权指数 | 每周最后一个交易日 |
| `CNAMCSMEWI` | 中证微盘股月频等权指数 | 每月最后一个交易日 |

点位规则见 ``Spec.md`` §3::

    ret_t   = Σ w_i,t-1 × (P_i,t / P_i,t-1 − 1) / Σ w_i,t-1
    point_t = point_{t-1} × (1 + ret_t)

调仓日收盘重置权重为 1/N；非调仓日权重随价格漂移（买入并持有到下次调仓）。
对外字段沿用 snake_case 契约，模块内变量与函数使用小驼峰。
"""

INDICES = {
    "CNAMCSDEWI": {"name": "中证微盘股日频等权指数", "rebalance": "daily"},
    "CNAMCSWEWI": {"name": "中证微盘股周频等权指数", "rebalance": "weekly"},
    "CNAMCSMEWI": {"name": "中证微盘股月频等权指数", "rebalance": "monthly"},
}
INDEX_CODE = "CNAMCSDEWI"           # 默认指数
INDEX_NAME = INDICES[INDEX_CODE]["name"]


def computeDailyReturn(prevComponents, closes):
    """用「上一交易日收盘权重」计算今日等权组合收益。

    返回 ``(ret, detail)``；停牌/无行情/上一收盘价缺失按价格不变处理（收益 0，权重保留）。
    """
    ret, totalWeight = 0.0, 0.0
    detail = []
    for item in prevComponents or []:
        code = item.get("code")
        weight = float(item.get("weight") or 0.0)
        prevClose = float(item.get("close") or 0.0)
        close = closes.get(code)
        if close is None or close <= 0 or prevClose <= 0:
            stockRet, note = 0.0, "no_quote"
        else:
            stockRet, note = float(close) / prevClose - 1.0, ""
        ret += weight * stockRet
        totalWeight += weight
        detail.append({"code": code, "name": item.get("name", ""), "weight": weight,
                       "prevClose": prevClose, "close": close,
                       "ret": round(stockRet, 8), "note": note})
    if totalWeight > 0:   # 权重合计异常时归一，避免收益被放大或缩小
        ret /= totalWeight
    return round(ret, 8), detail


def updateComponents(prevComponents, closes):
    """非调仓日：计算今日收益，并把权重按价格漂移、收盘价更新。

    返回 ``(ret, components, detail)``；``components`` 的 ``weight`` 为今日收盘后的漂移权重，
    可直接作为下一交易日的收益计算输入，也可写入 ``State.json``。
    """
    ret, detail = computeDailyReturn(prevComponents, closes)
    growth = 1.0 + ret
    components, totalWeight = [], 0.0
    for item in prevComponents or []:
        code = item.get("code")
        close = closes.get(code) or float(item.get("close") or 0.0)
        weight = float(item.get("weight") or 0.0)
        if growth > 0 and close > 0 and item.get("close"):
            weight = weight * (float(close) / float(item["close"])) / growth
        weight = round(weight, 10)
        totalWeight += weight
        components.append({"code": code, "name": item.get("name", ""),
                           "close": round(float(close), 3), "weight": weight})
    if totalWeight > 0:   # 归一化，抵消舍入误差
        for item in components:
            item["weight"] = round(item["weight"] / totalWeight, 10)
    return ret, components, detail


def nextPoint(prevPoint, ret):
    """由前一日点位与当日收益推出新的指数点位。"""
    return round(float(prevPoint) * (1.0 + float(ret)), 6)


def buildIndexRecord(code, name, tradeDate, point, prevPoint, ret, baseDate, basePoint,
                     components, built, scan, http, mode, portfolioSnapshot, generatedAt,
                     dataStart=None):
    """组装 ``Index.json`` 记录（对外契约，字段名固定）。"""
    cumulative = round(point / float(basePoint) - 1.0, 8) if basePoint else 0.0
    return {
        "code": code,
        "name": name,
        "trade_date": tradeDate,
        "point": point,
        "prev_point": prevPoint,
        "ret": ret,
        "cum_return": cumulative,
        "base_date": baseDate,
        "data_start": dataStart or tradeDate,
        "base_point": basePoint,
        "components": len(components),
        "eligible_count": built.get("eligible_count"),
        "weight_mode": "equal",
        "rebalance": INDICES[code]["rebalance"],
        "border_mcap": built.get("border_mcap"),
        "next_mcap": built.get("next_mcap"),
        "skipped": built.get("skipped"),
        "mode": mode,
        "portfolio": portfolioSnapshot,
        "scan": scan,
        "http": http,
        "generated_at": generatedAt,
    }


def _formatYi(value):
    """把「元」换算成「亿元」显示。"""
    return f"{value / 1e8:.2f} 亿" if value else "-"


def renderReport(idx, plan, execution, portfolioSnapshot, topN=20):
    """生成 Markdown 报告（写入 ``Latest/Report.md``，也用于 Actions 步骤摘要）。"""
    lines = [
        f"# {idx['code']} 每日速览 · {idx['trade_date']}", "",
        f"{idx['name']}（调仓频率：{idx['rebalance']}）", "",
        "| 指标 | 数值 |", "| --- | ---: |",
        f"| 点位 | **{idx['point']:.4f}** |",
        f"| 日收益 / 累计 | {idx['ret']:+.2%} / {idx['cum_return']:+.2%} |",
        f"| 成分 / 合格 | {idx['components']} / {idx['eligible_count']} |",
        f"| 边界市值 | {_formatYi(idx['border_mcap'])}（下一名 {_formatYi(idx['next_mcap'])}） |",
        f"| 基期 | {idx['base_date']}（{idx['base_point']:g}） |", "",
    ]
    if portfolioSnapshot:
        lines += [
            f"组合：总资产 {portfolioSnapshot.get('total_asset', 0):,.2f} 元，"
            f"现金 {portfolioSnapshot.get('cash', 0):,.2f} 元，"
            f"持仓 {portfolioSnapshot.get('holdings_count', 0)} 只，"
            f"今日成交 {len(execution)} 笔，明日计划 {len(plan)} 笔", "",
        ]
    for side, title in (("SELL", "卖出"), ("BUY", "买入")):
        rows = [order for order in plan if order.get("side") == side]
        if not rows:
            continue
        lines += [f"## 明日{title}（{len(rows)} 只，列前 {min(len(rows), topN)}）", "",
                  "| 代码 | 名称 | 股数 | 参考价 | 预估金额 |",
                  "| --- | --- | ---: | ---: | ---: |"]
        lines += [f"| {order['code']} | {order.get('name', '')} | {order['shares']} | "
                  f"{order['ref_price']:.2f} | {order['est_amount']:,.0f} |"
                  for order in rows[:topN]]
        lines.append("")
    lines.append(f"数据：{idx['http'].get('requests', 0)} 次请求 / "
                 f"{idx['http'].get('bytes_mb', 0)} MB · {idx['generated_at']}")
    return "\n".join(lines)
