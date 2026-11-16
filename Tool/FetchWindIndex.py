#!/usr/bin/env python3
"""抓取万得官网指数日线，增量更新 ``Benchmarks/<windCode>.mvsv``。

数据源（只返回最近 60 个交易日，故按 Date 合并去重，历史保留）：

    GET https://index_api.wind.com.cn/indexofficialwebsite/Kline?indexId=<ID>&lan=cn

零第三方依赖，可直接在 GitHub Actions 里跑。
"""

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_DIR = os.path.join(BASE, "Benchmarks")
SEP = "|"
API = "https://index_api.wind.com.cn/indexofficialwebsite/Kline?indexId={}&lan=cn"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"

# windCode -> (indexId, 中文名)
INDICES = {
    "868008.WI": ("tShUKnLO4cLQ8v+TnUd2uQ==", "万得微盘股指数"),
    "8841431.WI": ("2O5u8vTCa4KiLTLQ7DT/9w==", "万得微盘股日频等权指数"),
}

COLUMNS = ["Date", "Close", "Open", "High", "Low", "Volume", "Amount", "ChangeRatio"]
FIELD_CN = ["日期", "收盘价", "开盘价", "最高价", "最低价", "成交量", "成交额", "涨跌幅"]
FIELD_TYPE = ["Int", "Decimal", "Decimal", "Decimal", "Decimal", "Int", "Int", "Decimal"]


def fetch(indexId):
    """拉取最近 60 个交易日，返回 [(date, close, open, high, low, volume, amount, ratio)]。"""
    url = API.format(urllib.parse.quote(indexId, safe=""))
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    # 对方证书是 *.wind.com.cn，与 index_api.wind.com.cn 不匹配：只跳过主机名校验，保留链校验
    context = ssl.create_default_context()
    context.check_hostname = False
    with urllib.request.urlopen(request, timeout=30, context=context) as resp:
        payload = json.load(resp)
    rows = payload.get("Result") or []
    points = []
    for row in rows:
        day = datetime.fromtimestamp(row["tradeDate"] / 1000 + 8 * 3600,
                                     tz=timezone.utc).strftime("%Y%m%d")
        points.append((day, row["close"], row["open"], row["hight"], row["low"],
                       int(row["volume"]), int(row["amount"]), row.get("pctChange")))
    return points


def readExisting(path):
    """读已有 .mvsv 的数据行（跳过 ``#`` 与空行）。"""
    rows = {}
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(SEP)
            if len(parts) >= 8:
                rows[parts[0]] = parts[1:]
    return rows


def write(path, rows, windCode, name):
    meta = [
        ("标题", f"{name} {windCode} 日线"),
        ("字段名称", '"' + SEP.join(FIELD_CN) + '"'),
        ("DataProvider", "Wind 官网 index_api"),
        ("Field", '"' + SEP.join(COLUMNS) + '"'),
        ("FieldName", '"' + SEP.join(COLUMNS) + '"'),
        ("FieldType", '"' + SEP.join(FIELD_TYPE) + '"'),
        ("TypeKLine", "Day"),
        ("Count", str(len(rows))),
        ("Symbol", windCode),
        ("SecuCode", windCode.split(".")[0]),
        ("USC", windCode.split(".")[0]),
        ("Name", f'"{name}"'),
        ("Region", "CN"),
        ("Market", "WI"),
        ("TimeZone", "Asia/Shanghai"),
        ("Currency", "CNY"),
    ]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        file.write("".join(f"# {key} : {value}\n" for key, value in meta) + "\n")
        for day in sorted(rows):
            file.write(SEP.join([day, *rows[day]]) + "\n")


def main():
    failed = 0
    for windCode, (indexId, name) in INDICES.items():
        path = os.path.join(BENCH_DIR, f"{windCode}.mvsv")
        try:
            fresh = fetch(indexId)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {windCode} 抓取失败：{exc}", file=sys.stderr)
            failed += 1
            continue
        if not fresh:
            print(f"[warn] {windCode} 返回空数据，跳过", file=sys.stderr)
            failed += 1
            continue
        merged = readExisting(path)
        before = len(merged)
        for point in fresh:
            merged[point[0]] = [str(v) if v is not None else "" for v in point[1:]]
        write(path, merged, windCode, name)
        print(f"{windCode}: 抓取 {len(fresh)} 条（{fresh[0][0]} ~ {fresh[-1][0]}），"
              f"合并后 {len(merged)} 条（新增 {len(merged) - before}）")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
