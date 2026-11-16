#!/usr/bin/env python3
"""CNAMCI 基线的纯函数单测：点位计算、记录组装、报告渲染、时间戳解析。"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import Cnamci  # noqa: E402
import Index as gh  # noqa: E402


class TestDailyReturn(unittest.TestCase):
    def test_equal_weight_two_stocks(self):
        prev = [{"code": "600001", "name": "A", "close": 10.0, "weight": 0.5},
                {"code": "600002", "name": "B", "close": 20.0, "weight": 0.5}]
        ret, detail = Cnamci.computeDailyReturn(prev, {"600001": 11.0, "600002": 20.0})
        # (0.5*10% + 0.5*0%) = 5%
        self.assertAlmostEqual(ret, 0.05, places=8)
        self.assertEqual(len(detail), 2)

    def test_suspended_keeps_weight(self):
        prev = [{"code": "600001", "name": "A", "close": 10.0, "weight": 0.5},
                {"code": "600002", "name": "B", "close": 20.0, "weight": 0.5}]
        # B 停牌（无价格）→ 收益 0，权重保留
        ret, detail = Cnamci.computeDailyReturn(prev, {"600001": 11.0})
        self.assertAlmostEqual(ret, 0.05, places=8)
        self.assertEqual(detail[1]["note"], "no_quote")

    def test_weights_normalized(self):
        prev = [{"code": "600001", "name": "A", "close": 10.0, "weight": 0.25},
                {"code": "600002", "name": "B", "close": 20.0, "weight": 0.25}]
        ret, _ = Cnamci.computeDailyReturn(prev, {"600001": 11.0, "600002": 20.0})
        # 权重合计 0.5，归一后仍是 5%
        self.assertAlmostEqual(ret, 0.05, places=8)

    def test_empty_prev(self):
        ret, detail = Cnamci.computeDailyReturn([], {"600001": 11.0})
        self.assertEqual(ret, 0.0)
        self.assertEqual(detail, [])


class TestPoint(unittest.TestCase):
    def test_next_point(self):
        self.assertAlmostEqual(Cnamci.nextPoint(1000.0, 0.0123), 1012.3, places=6)
        self.assertAlmostEqual(Cnamci.nextPoint(1012.3, 0.0), 1012.3, places=6)

    def test_cum_return_consistent(self):
        point = Cnamci.nextPoint(Cnamci.nextPoint(1000.0, 0.05), -0.02)
        rec = Cnamci.buildIndexRecord("CNAMCSDEWI", "日频等权", "2026-11-13", point, 1000.0,
                                      0.029, "2026-11-11", 1000.0, [{"rank": 1}], {}, {}, {},
                                      "changes", {}, "2026-11-13T15:05:00+0800")
        self.assertAlmostEqual(rec["cum_return"], round(point / 1000.0 - 1, 8), places=8)


class TestIndexRecord(unittest.TestCase):
    def test_required_fields(self):
        rec = Cnamci.buildIndexRecord(
            "CNAMCSDEWI", "日频等权", "2026-11-13", 1234.5, 1200.0, 0.02875, "2026-01-05", 1000.0,
            [{"rank": 1, "code": "600001"}], {"eligible_count": 3043, "border_mcap": 2.8e9,
                                             "next_mcap": 2.9e9, "skipped": {"st": 1}},
            {"pages": 0}, {"requests": 4, "attempts": 4, "bytes_mb": 1.6},
            "changes", {"total_asset": 1.0}, "2026-11-13T15:05:00+0800")
        for k in ("code", "name", "trade_date", "point", "prev_point", "ret", "cum_return",
                  "base_date", "base_point", "components", "eligible_count", "http"):
            self.assertIn(k, rec)
        self.assertEqual(rec["code"], "CNAMCSDEWI")


class TestReport(unittest.TestCase):
    def test_report_contains_key_parts(self):
        idx = Cnamci.buildIndexRecord(
            "CNAMCSDEWI", "日频等权", "2026-11-13", 1012.3, 1000.0, 0.0123, "2026-11-11", 1000.0,
            [{"rank": 1, "code": "600001", "name": "A", "close": 10.0,
              "total_mv": 1e9, "float_mv": 1e9, "weight": 0.0025, "listed_days": 999}],
            {"eligible_count": 3043, "border_mcap": 2.868e9, "next_mcap": 2.87e9,
             "skipped": {"st": 376, "halted": 69}},
            {"pages": 0, "stock_list": "cache", "codes": 3499},
            {"requests": 4, "attempts": 4, "bytes_mb": 1.617},
            "changes",
            {"cash": 1000.0, "market_value": 2000.0, "total_asset": 3000.0,
             "holdings_count": 1},
            "2026-11-13T15:05:00+0800")
        plan = [{"side": "SELL", "code": "600009", "name": "旧", "shares": 100,
                 "ref_price": 10.0, "est_amount": 1000.0, "est_fee": 5.1, "reason": "exit"}]
        md = Cnamci.renderReport(idx, plan, [], idx["portfolio"])
        self.assertIn("# CNAMCSDEWI 每日速览 · 2026-11-13", md)
        self.assertIn("点位", md)
        self.assertIn("明日卖出", md)
        self.assertIn("600009", md)
        self.assertIn("数据：", md)


class TestIndexTs(unittest.TestCase):
    def test_parse_ts(self):
        f = [""] * 88
        f[1], f[2], f[30] = "上证指数", "000001", "20261113161427"
        text = 'v_sh000001="' + "~".join(f) + '";'
        with mock.patch.object(gh.sources, "httpGet", return_value=text):
            self.assertEqual(gh.indexTimestamp(), "20261113161427")

    def test_parse_ts_failure(self):
        with mock.patch.object(gh.sources, "httpGet", side_effect=RuntimeError("boom")):
            self.assertEqual(gh.indexTimestamp(), "")


if __name__ == "__main__":
    unittest.main()
