#!/usr/bin/env python3
"""把 Data/<指数>/ 下已有的 .csv 转成 .mvsv（头部元数据 + 竖线分隔），并删除旧文件。"""

import csv
import glob
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "Core"))

import Mvsv  # noqa: E402


def main():
    count = 0
    for path in sorted(glob.glob(os.path.join(BASE, "Data", "**", "*.mvsv"), recursive=True)):
        with open(path, encoding="utf-8", newline="") as file:
            reader = csv.reader(file)
            columns = next(reader, [])
            rows = [dict(zip(columns, row)) for row in reader if row]
        target = path[:-4] + ".mvsv"
        Mvsv.write(target, rows, columns, title=os.path.basename(target)[:-5])
        os.remove(path)
        count += 1
        if count % 50 == 0:
            print(f"已转换 {count} 个文件…", flush=True)
    print(f"完成：{count} 个 .csv → .mvsv")


if __name__ == "__main__":
    main()
