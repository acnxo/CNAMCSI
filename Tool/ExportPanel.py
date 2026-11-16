#!/usr/bin/env python3
"""把回放所需的全市场面板导出为按年分片的 gzip（仓库自包含，供 GitHub Actions 回放）。

用法（在有 data/panel.csv 的机器上运行一次）：
    python Tools/ExportPanel.py              # 输出 Input/Panel-YYYY.csv.gz
    python Tools/ExportPanel.py --src ../data/panel.csv --out Input

产物列：date,code,name,list_date,open,close,volume,mcap（回放所需最小集合）。
按年分片避免单文件过大；配合 Core/LocalData.py 直接读取。
"""

import argparse
import os

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLS = ["date", "code", "name", "list_date", "open", "close", "volume", "mcap"]
DTYPES = {"date": "string", "code": "string", "name": "string", "list_date": "string",
          "open": "float64", "close": "float64", "volume": "float64", "mcap": "float64"}


def main():
    ap = argparse.ArgumentParser(description="导出回放面板（按年 gzip 分片）")
    ap.add_argument("--src", default=os.path.join(BASE, "..", "data", "panel.csv"))
    ap.add_argument("--out", default=os.path.join(BASE, "Input"))
    args = ap.parse_args()
    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    if not os.path.exists(src):
        raise SystemExit(f"找不到面板文件：{src}")
    os.makedirs(out, exist_ok=True)

    df = pd.read_csv(src, usecols=COLS, dtype=DTYPES)
    df["code"] = df["code"].str.zfill(6)
    df["year"] = df["date"].str[:4]
    total = 0
    for year, group in df.groupby("year", sort=True):
        path = os.path.join(out, f"Panel-{year}.csv.gz")
        group.drop(columns=["year"]).to_csv(path, index=False, compression="gzip")
        size = os.path.getsize(path)
        total += size
        print(f"{path}  {len(group):>8,} 行  {size/1048576:6.1f} MB")
    print(f"合计 {len(df):,} 行，{total/1048576:.1f} MB")


if __name__ == "__main__":
    main()
