#!/usr/bin/env python3
"""把旧的 ``Daily/<年>/`` 迁移到三层结构（Archive / Pending / Current）。

归档规则：年份 <= 当年-1 或已过次年 3 月 1 日 → Archive；当年 → Pending；
最近 5 个交易日从 Pending 生成 Current/<Kind>.jsonl。
"""

import datetime
import glob
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "Core"))

import Archive  # noqa: E402

DATA = os.path.join(BASE, "Data")
INDICES = ("CNAMCSDEWI", "CNAMCSWEWI", "CNAMCSMEWI")
FILES = ("Components.mvsv", "Holdings.mvsv", "Trades.mvsv", "Summary.mvsv")


def main():
    today = datetime.date.today().isoformat()
    currentYear = str(datetime.date.today().year)
    for code in INDICES:
        root = os.path.join(DATA, code)
        legacy = os.path.join(root, "Daily")
        if not os.path.isdir(legacy):
            print(f"{code}: 无 Daily 目录，跳过")
            continue
        moved = 0
        for yearPath in sorted(glob.glob(os.path.join(legacy, "*"))):
            year = os.path.basename(yearPath)
            if not year.isdigit():
                continue
            layer = "Pending" if year == currentYear else "Archive"
            target = os.path.join(root, layer, year)
            os.makedirs(target, exist_ok=True)
            for name in FILES:
                source = os.path.join(yearPath, name)
                if os.path.exists(source):
                    shutil.copy2(source, os.path.join(target, name))
                    moved += 1
        # Current：当年最后 5 个交易日
        pending = os.path.join(root, "Pending", currentYear)
        if os.path.isdir(pending):
            dates = sorted({row["date"] for row in
                            Archive.readCsv(os.path.join(pending, "Summary.mvsv"))
                            if row.get("date")})[-Archive.KEEP_DAYS:]
            for kind in Archive.KINDS:
                rows = [row for row in Archive.readCsv(os.path.join(pending, f"{kind}.csv"))
                        if row.get("date") in dates]
                Archive.writeJsonl(os.path.join(root, "Current", f"{kind}.jsonl"), rows)
        shutil.rmtree(legacy)
        print(f"{code}: 迁移 {moved} 个文件，Current 保留 {len(dates) if 'dates' in dir() else 0} 天")
        dates = []


if __name__ == "__main__":
    main()
