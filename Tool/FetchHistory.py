#!/usr/bin/env python3
"""历史行情全量采集（断点续采）——命令行入口。

典型用法::

    python3 Tool/FetchHistory.py --status                 # 看进度（已完成/待采/失败）
    python3 Tool/FetchHistory.py --limit 5                # 先跑 5 只验证
    python3 Tool/FetchHistory.py                          # 全量：2004-12-01 至今
    python3 Tool/FetchHistory.py --workers 6              # 提高并发（雪球限流时调低）
    python3 Tool/FetchHistory.py --verify                 # 校验覆盖（2004-12 天数等）
    python3 Tool/FetchHistory.py --rebuild 2026           # 从 Archive 切出 Data/Latest
    python3 Tool/FetchHistory.py --source eastmoney       # 换备源重采失败的

采集状态写在 ``Archive/.FetchState.json``：中途失败后**直接重跑同一条命令**即可，
已完成的证券会被跳过；``--force`` 忽略状态强制重采。
"""

import argparse
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "Core"))

import History  # noqa: E402


def showStatus(root):
    state = History.stateLoad(root)
    entries = state.get("securities") or {}
    counts = {}
    for entry in entries.values():
        counts[entry.get("status", "?")] = counts.get(entry.get("status", "?"), 0) + 1
    total = len(History.securityCodes(root))
    rows = sum(entry.get("rows", 0) for entry in entries.values())
    print(f"状态文件 {History.statePath(root)}")
    print(f"证券总数 {total} · 已记录 {len(entries)} · 累计新采 {rows:,} 行")
    print(f"状态分布 {counts} · 区间 {state.get('start', '-')}~{state.get('end', '-')}"
          f" · 源 {state.get('source', '-')} · 更新 {state.get('updated', '-')}")
    stamp = state.get("updated") or ""
    try:
        idle = (time.time() - time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")))
    except ValueError:
        idle = None
    if idle is not None and idle < 300:
        print(f"后台采集中：状态 {idle:.0f} 秒前更新（勿重复启动，查看进度用 --status）")
    elif idle is not None:
        print(f"当前无采集在跑（状态 {idle / 60:.0f} 分钟前更新），重跑本命令即续采")
    failed = [(code, entry.get("error", "")) for code, entry in entries.items()
              if entry.get("status") == "failed"]
    for code, error in failed[:10]:
        print(f"  失败 {code}: {error[:120]}")
    if len(failed) > 10:
        print(f"  … 另有 {len(failed) - 10} 只失败，重跑即可续采")
    end = state.get("end") or History.normDate(None)
    todo = [code for code in History.securityCodes(root)
            if not History.isDone(entries.get(code), state.get("start")
                                  or History.DEFAULT_START, end)]
    print(f"待采 {len(todo)} 只（区间 {state.get('start', History.DEFAULT_START)}~{end}）；"
          f"重跑本命令即续采")


def main():
    parser = argparse.ArgumentParser(description="历史行情全量采集（断点续采）")
    parser.add_argument("--start", default=History.DEFAULT_START,
                        help="起始日期（默认 20041201，即 2004 年 12 月）")
    parser.add_argument("--end", default=None, help="结束日期（默认今天）")
    parser.add_argument("--source", default="xueqiu", choices=("xueqiu", "eastmoney"))
    parser.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    parser.add_argument("--delay", type=float, default=0.15, help="每只之间的停顿秒数")
    parser.add_argument("--force", action="store_true", help="忽略状态，强制重采")
    parser.add_argument("--only", default=None, help="只采这些代码（逗号分隔）")
    parser.add_argument("--limit", type=int, default=None, help="只采前 N 只（试跑）")
    parser.add_argument("--status", action="store_true", help="只打印进度，不采集")
    parser.add_argument("--verify", action="store_true", help="校验历史覆盖（不采集）")
    parser.add_argument("--rebuild", default=None,
                        help="把 Data/<年>/.pending/ 落到 Data，并由 Data 重建 Latest（如 --rebuild 2026）")
    parser.add_argument("--split", default=None, metavar="YEAR",
                        help="把 Archive 里 >= YEAR 的行搬到 Data/<YEAR>/.pending/（一次性迁移）")
    args = parser.parse_args()

    root = BASE
    if args.status:
        showStatus(root)
        return
    if args.verify:
        History.verify(root)
        return
    if args.split:
        History.splitArchive(root, args.split)
        return
    if args.rebuild:
        History.rebuild(root, args.rebuild)
        return

    only = [item.strip() for item in args.only.split(",") if item.strip()] if args.only else None
    History.run(root, start=args.start, end=args.end, source=args.source,
                workers=args.workers, delay=args.delay, force=args.force,
                only=only, limit=args.limit,
                log=lambda *items: print(*items, flush=True))
    showStatus(root)


if __name__ == "__main__":
    main()
