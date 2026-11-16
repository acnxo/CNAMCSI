#!/usr/bin/env python3
"""本地回放数据源：把 data/panel.csv 变成「每日一份快照」（仅本地使用，依赖 pandas）。

与线上 `sources.py` 产出同一套 quote 契约：
    {"code","name","price"(收盘),"open","volume","total_mv","float_mv","list_date","ts"}

口径差异（见 SPEC §3）：本地市值来自复权还原股本 × 不复权收盘价；线上来自实时快照总市值。
"""

import glob
import os

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根（CNAMCI）
INPUT_DIR = os.path.join(BASE, "Input")
# 回放输入：优先 Input/Panel-*.mvsv.gz（仓库自包含），其次项目数据目录（本地开发）
PANEL_FILES = sorted(glob.glob(os.path.join(INPUT_DIR, "Panel-*.mvsv.gz")))
PANEL = next((path for path in (
    os.path.join(INPUT_DIR, "panel.csv"),
    os.path.join(BASE, "..", "data", "panel.csv"),
    os.path.join(BASE, "data", "panel.csv")) if os.path.exists(path)),
    os.path.join(INPUT_DIR, "panel.csv"))
CALENDAR = next((path for path in (
    os.path.join(BASE, "Input", "calendar.json"),
    os.path.join(BASE, "..", "data", "raw", "calendar.json")) if os.path.exists(path)),
    os.path.join(BASE, "Input", "calendar.json"))
CACHE_DIR = os.path.join(BASE, "ReplayCache")

COLS = ["date", "code", "name", "list_date", "open", "close", "volume", "mcap"]
DTYPES = {"date": "string", "code": "string", "name": "string", "list_date": "string",
          "open": "float64", "close": "float64", "volume": "float64", "mcap": "float64"}


def loadCalendar(path=CALENDAR):
    import json

    with open(path, encoding="utf-8") as f:
        return json.load(f)


def panelFiles(start=None, end=None):
    """按年份过滤分片文件（Panel-YYYY.csv.gz）。"""
    files = []
    for path in sorted(glob.glob(os.path.join(INPUT_DIR, "Panel-*.mvsv.gz"))):
        year = os.path.basename(path)[6:10]
        if start and year < start[:4]:
            continue
        if end and year > end[:4]:
            continue
        files.append(path)
    return files


def loadPanel(start=None, end=None, use_cache=True):
    """读取面板（默认整表），返回 DataFrame。可缓存为 pickle 加速二次回放。"""
    cache = None
    if use_cache:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache = os.path.join(CACHE_DIR, f"replay_panel_{start or 'all'}_{end or 'all'}.pkl")
        if os.path.exists(cache):
            return pd.read_pickle(cache)

    files = panelFiles(start, end)
    if files:
        frames = [pd.read_csv(path, dtype=DTYPES) for path in files]
        df = pd.concat(frames, ignore_index=True)
    elif os.path.exists(PANEL):
        df = pd.read_csv(PANEL, usecols=COLS, dtype=DTYPES)
    else:
        raise RuntimeError("缺少回放数据：请运行 Tools/ExportPanel.py 生成 Input/Panel-*.mvsv.gz，"
                           "或把 data/panel.csv 放到 Input/ 或 ../data/")
    df["code"] = df["code"].str.zfill(6)
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    df = df.reset_index(drop=True)
    if use_cache and cache:
        df.to_pickle(cache)
    return df


def iterDays(df, calendar):
    """按交易日升序产出 (date, quotes, opens, closes, cal_upto)。

    quotes 为当日全量主板快照（含停牌/ST，由 universe 负责过滤）；
    opens/closes 为 {code: 价格}，用于 T+1 开盘成交与收盘估值。
    """
    cal_idx = {d: i for i, d in enumerate(calendar)}
    for date, g in df.groupby("date", sort=True):
        quotes, opens, closes = [], {}, {}
        for r in g.itertuples(index=False):
            price = float(r.close)
            if price <= 0:
                continue
            quotes.append({
                "code": r.code, "name": r.name or "", "price": price,
                "open": float(r.open) if r.open and r.open > 0 else 0.0,
                "volume": float(r.volume or 0.0),
                "total_mv": float(r.mcap or 0.0), "float_mv": float(r.mcap or 0.0),
                "list_date": r.list_date or "", "ts": date,
            })
            closes[r.code] = price
            if r.open and r.open > 0:
                opens[r.code] = float(r.open)
        idx = cal_idx.get(date)
        cal_upto = calendar[: idx + 1] if idx is not None else calendar
        yield date, quotes, opens, closes, cal_upto
