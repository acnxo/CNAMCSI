#!/usr/bin/env python3
"""``.mvsv``（metadata + vertical-separated values）读写。

格式（与 ``Benchmarks/*.mvsv``、行情数据一致）::

    # 标题 : ...
    # Field : "date|point|ret|..."
    # Count : 5310
    <空行>
    20041231|1000.0|0.0|...

- 元数据行为 ``# Key : Value``；
- 数据区**无表头**，字段名由 ``# Field`` 定义；
- 分隔符为 ``|``。
"""

import os

SEP = "|"
FIELD_CN = {
    "date": "日期", "point": "点位", "ret": "收益率", "cum_return": "累计收益",
    "holdings_count": "持仓只数", "total_asset": "总资产", "cash": "现金",
    "rank": "排名", "code": "代码", "name": "名称", "close": "收盘价",
    "total_mv": "总市值", "float_mv": "流通市值", "weight": "权重",
    "listed_days": "上市交易日数", "shares": "股数", "cost": "成本",
    "market_value": "市值", "unrealized_pnl": "浮动盈亏", "side": "方向",
    "ref_price": "参考价", "est_amount": "预估金额", "est_fee": "预估费用",
    "reason": "原因", "signal_date": "信号日", "exec_date": "成交日",
    "price": "成交价", "amount": "金额", "fee": "费用", "cash_after": "成交后现金",
    "realized_pnl": "已实现盈亏", "kind": "类型", "rebalance": "是否调仓",
}


def render(rows, columns, title="", extra=None):
    """渲染成 .mvsv 文本（``rows`` 为 dict 列表）。"""
    meta = [("标题", title), ("Field", '"' + SEP.join(columns) + '"'),
            ("字段名称", '"' + SEP.join(FIELD_CN.get(c, c) for c in columns) + '"'),
            ("Count", str(len(rows)))]
    for key, value in (extra or {}).items():
        meta.append((key, value))
    lines = [f"# {key} : {value}" for key, value in meta]
    lines.append("")
    lines.extend(SEP.join(str(row.get(col, "") if row.get(col) is not None else "")
                          for col in columns) for row in rows)
    return "\n".join(lines) + "\n"


def write(path, rows, columns, title="", extra=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write(render(rows, columns, title, extra))


def read(path, columns=None):
    """读 .mvsv，返回 dict 列表；未给 ``columns`` 时用 ``# Field`` 解析。"""
    if not os.path.exists(path):
        return []
    rows = []
    fields = columns
    with open(path, encoding="utf-8") as file:
        for line in file:
            if line.startswith("#"):
                key, _, value = line[1:].partition(":")
                if key.strip() == "Field" and fields is None:
                    fields = value.strip().strip('"').split(SEP)
                continue
            line = line.rstrip("\n")
            if not line.strip():
                continue
            parts = line.split(SEP)
            rows.append(dict(zip(fields or [], parts)))
    return rows
