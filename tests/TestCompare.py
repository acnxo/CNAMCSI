#!/usr/bin/env python3
"""业绩比较基准与 SVG 渲染的单测（不需要网络）。"""

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import Compare  # noqa: E402

SVG = "{http://www.w3.org/2000/svg}"


def series6():
    """3 只指数 + 3 个基准的合成序列。"""
    labels = [("CNAMCSDEWI 日频", False), ("CNAMCSWEWI 周频", False), ("CNAMCSMEWI 月频", False),
              ("国证2000", True), ("中证2000", True), ("中证全指", True)]
    out = []
    for i, (label, dashed) in enumerate(labels):
        base = 1000 + i * 20
        out.append({"label": label, "color": f"#0000{i}0", "dashed": dashed,
                    "points": [("2025-01-02", base), ("2025-07-01", base * 1.1),
                               ("2026-01-02", base * 0.95), ("2026-11-13", base * 1.2)]})
    return out


class TestNormalize(unittest.TestCase):
    def test_base_point(self):
        points = [("2024-12-01", 500.0), ("2025-01-02", 1000.0), ("2025-01-03", 1100.0)]
        out = Compare.normalize(points, baseDate="2025-01-02")
        # 保留全历史：基期之前的值按基期锚点折算
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(out[0][1], 500.0, places=6)
        self.assertEqual(out[1], ("2025-01-02", 1000.0))
        self.assertAlmostEqual(out[2][1], 1100.0, places=6)

    def test_missing_base_returns_empty(self):
        self.assertEqual(Compare.normalize([("2024-12-01", 500.0)], baseDate="2025-01-02"), [])


class TestSummarize(unittest.TestCase):
    def test_return_and_drawdown(self):
        points = [("2025-01-02", 1000.0), ("2025-06-30", 1200.0), ("2026-01-02", 900.0)]
        s = Compare.summarize(points)
        self.assertEqual(s["last"], 900.0)
        self.assertAlmostEqual(s["return"], -0.10, places=8)
        self.assertAlmostEqual(s["max_drawdown"], -0.25, places=8)
        self.assertLess(s["annualized"], 0)

    def test_empty(self):
        self.assertIsNone(Compare.summarize([])["last"])


class TestRenderSvg(unittest.TestCase):
    def test_six_paths_and_labels(self):
        svg = Compare.renderSvg(series6(), {"title": "对比", "begin": "2025-01-02",
                                           "as_of": "2026-11-13"})
        root = ET.fromstring(svg)                       # XML 合法
        # 6 条净值曲线 + 6 个回撤面积 + 6 条回撤线
        self.assertEqual(svg.count('class="series"'), 6)
        self.assertEqual(svg.count('class="drawdown"'), 6)
        self.assertEqual(svg.count('class="drawdown-line"'), 6)
        self.assertIn("回撤", svg)
        self.assertIn("国证2000", svg)
        self.assertIn("中证2000", svg)
        self.assertIn("中证全指", svg)
        self.assertIn("CNAMCSDEWI 日频", svg)
        self.assertIn("对比", svg)
        self.assertEqual(root.get("width"), "2360")          # 默认 2 倍画布
        self.assertGreater(int(root.get("height")), 2000)    # 主图 + 6 个回撤子图
        self.assertIn('transform="scale(2)"', svg)
        # 图例固定在绘图区左上角（left=78, top=66），带半透明白底
        self.assertIn('<rect x="78" y="66" width="214" height="192" rx="6"', svg)
        # X 轴月份与年份分两行（y=566 / y=586），年份带分隔线
        self.assertIn('y="566"', svg)
        self.assertIn('y="586"', svg)
        self.assertIn(">2025</text>", svg)
        self.assertIn(">2026</text>", svg)
        # 全部实线（无 dasharray），线宽 1（原 2 的一半）
        self.assertEqual(svg.count("stroke-dasharray"), 0)
        self.assertIn('stroke-width="1"', svg)

    def test_no_points_series_skipped_by_caller(self):
        # 空序列由 main 过滤；renderSvg 要求非空，这里只验证单序列可渲染
        svg = Compare.renderSvg([{"label": "A", "color": "#000", "dashed": False,
                                  "points": [("2025-01-02", 1000.0), ("2025-06-30", 1050.0)]}],
                                {"title": "t", "begin": "2025-01-02", "as_of": "2025-06-30"})
        self.assertEqual(svg.count('class="series"'), 1)
        self.assertEqual(svg.count('class="drawdown"'), 1)


class TestDrawdown(unittest.TestCase):
    def test_series_and_peak(self):
        dd = Compare.drawdown([("2025-01-02", 1000.0), ("2025-02-03", 1200.0),
                               ("2025-03-03", 900.0), ("2025-04-01", 1100.0)])
        self.assertEqual(dd[0][1], 0.0)
        self.assertEqual(dd[1][1], 0.0)
        self.assertAlmostEqual(dd[2][1], -0.25, places=8)
        self.assertAlmostEqual(dd[3][1], -1 + 1100 / 1200, places=6)


class TestSliceWindow(unittest.TestCase):
    def test_anchor_and_window(self):
        pts = [("2013-01-01", 500.0), ("2023-08-11", 1000.0), ("2024-01-02", 1200.0)]
        out = Compare.sliceWindow(pts, "2023-01-01", "2023-08-11")
        self.assertEqual(out[0], ("2023-08-11", 1000.0))
        self.assertAlmostEqual(out[1][1], 1200.0, places=6)

    def test_window_first_point_anchor(self):
        pts = [("2013-01-01", 500.0), ("2024-01-02", 1000.0), ("2025-01-02", 900.0)]
        out = Compare.sliceWindow(pts, "2024-01-01")
        self.assertEqual(out[0], ("2024-01-02", 1000.0))
        self.assertAlmostEqual(out[1][1], 900.0, places=6)

    def test_empty_window(self):
        self.assertEqual(Compare.sliceWindow([("2013-01-01", 500.0)], "2024-01-01"), [])


class TestBenchmarkFallback(unittest.TestCase):
    def test_uses_cache_when_fetch_fails(self):
        tmp = tempfile.mkdtemp(prefix="bench_")
        dataDir = os.path.join(tmp, "Data")       # 缓存与 Data 平级：tmp/Benchmarks
        os.makedirs(dataDir, exist_ok=True)
        os.makedirs(os.path.join(tmp, "Benchmarks"), exist_ok=True)
        with open(os.path.join(tmp, "Benchmarks", "GZ2000.mvsv"), "w",
                  encoding="utf-8") as f:
            f.write("# 标题 : 国证2000 日线收盘价\n# Count : 2\n\n"
                    "20250102|7369.04\n20250103|7106.75\n")
        spec = {"key": "GZ2000", "label": "国证2000", "kind": "tencent",
                "symbol": "sz399303"}
        with mock.patch.object(Compare, "fetchTencent", side_effect=RuntimeError("boom")):
            points = Compare.loadBenchmark(spec, dataDir, "2025-01-02", "2026-11-13")
        self.assertEqual(points[0], ("2025-01-02", 7369.04))
        self.assertEqual(len(points), 2)


class TestParse(unittest.TestCase):
    def test_parse_date(self):
        self.assertEqual(Compare.parseDate("20250102"), "2025-01-02")
        self.assertEqual(Compare.parseDate("2025-01-02"), "2025-01-02")


if __name__ == "__main__":
    unittest.main()
