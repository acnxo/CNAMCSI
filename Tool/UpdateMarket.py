#!/usr/bin/env python3
"""独立更新行情三层（不做指数计算），用于补跑或本地验证。

流程：交易日历 → 沪深主板代码清单 → 腾讯全市场快照 → ``Core/Market.update()``
落盘 ``Latest/Latest.mvsv``、``Data/<yyyy>/<yyyyMM>.mvsv``、跨年时归档 ``Archive/Security/``。

用法::

    python Tool/UpdateMarket.py                     # 抓当日快照并更新三层
    python Tool/UpdateMarket.py --date 2026-11-16   # 指定日期（仍用当前快照数据）
    python Tool/UpdateMarket.py --source eastmoney  # 备用数据源
    python Tool/UpdateMarket.py --dry-run           # 只打印摘要，不写盘
"""

import argparse
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "Core"))

import Market  # noqa: E402
import Sources  # noqa: E402
from Store import LocalStore  # noqa: E402
from Universe import MAIN_BOARD_PREFIX  # noqa: E402


class CacheStore(LocalStore):
    """``state/*`` 缓存落到 ``Index/Cache/`` 根（与 Index.py 的 DataStore 一致）。"""

    def __init__(self, cacheDir):
        super().__init__(cacheDir)
        self.cacheDir = cacheDir

    def _path(self, key):
        if key.startswith("state/"):
            path = os.path.join(self.cacheDir, key[len("state/"):])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path
        return super()._path(key)


def fetchQuotes(source="tencent", dataDir=None):
    """抓取沪深主板全市场快照，返回 (quotes, 交易日历, 数据日期)。"""
    store = CacheStore(os.path.join(dataDir or os.path.join(BASE, "Index"), "Cache"))
    cfg = dict(Sources.DEFAULT_CFG)
    calendar = Sources.fetchCalendar(store, float(cfg["calendar_ttl_hours"]))
    if source == "eastmoney":
        quotes, _ = Sources.quotesFromEastmoney(cfg, calendar, MAIN_BOARD_PREFIX)
    else:
        quotes, _ = Sources.quotesFromTencent(cfg, calendar, MAIN_BOARD_PREFIX, store)
    date = Sources.quoteDate(quotes)
    return quotes, calendar, date


def main():
    parser = argparse.ArgumentParser(description="更新行情三层（Latest / Data / Archive）")
    parser.add_argument("--date", default=None, help="数据日期 YYYY-MM-DD（默认取快照时间戳）")
    parser.add_argument("--source", default="tencent", choices=("tencent", "eastmoney"))
    parser.add_argument("--data-dir", default=None, help="缓存目录（默认 Index/）")
    parser.add_argument("--dry-run", action="store_true", help="只打印摘要，不写盘")
    args = parser.parse_args()

    quotes, calendar, date = fetchQuotes(args.source, args.data_dir)
    if not quotes:
        raise SystemExit("未取到任何行情")
    day = args.date or date
    if not day:
        raise SystemExit("无法确定数据日期，请用 --date 指定")
    print(f"快照 {len(quotes)} 只 · 交易日历 {len(calendar)} 天 · 数据日期 {day}")
    if args.dry_run:
        rows = Market.rowsFromQuotes(quotes, str(day).replace("-", "")[:8])
        print(f"[dry-run] 将写入 {len(rows)} 行，示例：{rows[0] if rows else '-'}")
        return
    info = Market.update(BASE, quotes, day)
    print(f"完成：{info}")


if __name__ == "__main__":
    main()
