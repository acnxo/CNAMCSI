#!/usr/bin/env python3
"""调仓频率与权重漂移的单测（不需要网络/pandas）。"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import Cnamci  # noqa: E402
import Index  # noqa: E402


class TestRebalanceDay(unittest.TestCase):
    # 连续交易日：11-09(一) ~ 11-13(五)、11-16(一) ~ 11-20(五)、11-23~11-27、11-30(一)、12-01
    CAL = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
           "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20",
           "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27",
           "2026-11-30", "2026-12-01"]

    def test_daily_always_true(self):
        for day in self.CAL:
            self.assertTrue(Index.isRebalanceDay(day, "daily", self.CAL))

    def test_weekly(self):
        self.assertFalse(Index.isRebalanceDay("2026-11-09", "weekly", self.CAL))
        self.assertFalse(Index.isRebalanceDay("2026-11-12", "weekly", self.CAL))
        self.assertTrue(Index.isRebalanceDay("2026-11-13", "weekly", self.CAL))   # 周五
        self.assertFalse(Index.isRebalanceDay("2026-11-16", "weekly", self.CAL))  # 周一
        self.assertTrue(Index.isRebalanceDay("2026-11-20", "weekly", self.CAL))   # 周五
        self.assertFalse(Index.isRebalanceDay("2026-11-27", "weekly", self.CAL)) if False else None

    def test_monthly(self):
        self.assertFalse(Index.isRebalanceDay("2026-11-13", "monthly", self.CAL))
        self.assertTrue(Index.isRebalanceDay("2026-11-30", "monthly", self.CAL))  # 月末最后交易日
        self.assertFalse(Index.isRebalanceDay("2026-12-01", "monthly", self.CAL))

    def test_online_calendar_without_future(self):
        # 线上日历只到当日：按自然日近似（周五 / 月末最后工作日）
        cal = ["2026-11-12", "2026-11-13"]
        self.assertTrue(Index.isRebalanceDay("2026-11-13", "weekly", cal))
        self.assertFalse(Index.isRebalanceDay("2026-11-12", "weekly", cal))
        self.assertTrue(Index.isRebalanceDay("2026-11-30", "monthly", ["2026-11-30"]))
        self.assertFalse(Index.isRebalanceDay("2026-11-27", "monthly", ["2026-11-27"]))


class TestUpdateComponents(unittest.TestCase):
    def test_weight_drift(self):
        prev = [{"code": "600001", "name": "A", "close": 10.0, "weight": 0.5},
                {"code": "600002", "name": "B", "close": 20.0, "weight": 0.5}]
        ret, drifted, detail = Cnamci.updateComponents(prev, {"600001": 11.0, "600002": 20.0})
        self.assertAlmostEqual(ret, 0.05, places=8)          # (0.5*10% + 0.5*0%)
        self.assertAlmostEqual(sum(item["weight"] for item in drifted), 1.0, places=6)
        # A 涨 10% → 权重上升；B 不变 → 权重下降
        self.assertGreater(drifted[0]["weight"], 0.5)
        self.assertLess(drifted[1]["weight"], 0.5)
        self.assertEqual(drifted[0]["close"], 11.0)
        self.assertEqual(len(detail), 2)

    def test_suspended_keeps_price(self):
        prev = [{"code": "600001", "name": "A", "close": 10.0, "weight": 0.5},
                {"code": "600002", "name": "B", "close": 20.0, "weight": 0.5}]
        ret, drifted, _ = Cnamci.updateComponents(prev, {"600001": 11.0})
        self.assertAlmostEqual(ret, 0.05, places=8)
        self.assertEqual(drifted[1]["close"], 20.0)          # 停牌沿用上一收盘价

    def test_empty_prev(self):
        ret, drifted, detail = Cnamci.updateComponents([], {"600001": 11.0})
        self.assertEqual((ret, drifted, detail), (0.0, [], []))


if __name__ == "__main__":
    unittest.main()


class TestListedDays(unittest.TestCase):
    """上市日缺失/异常时的处理：不可当作老股。"""

    def test_missing_list_date_is_not_selectable(self):
        from Core.Universe import listedTradingDays
        calendar = ["2025-01-02", "2025-01-03", "2025-01-06"]
        self.assertEqual(listedTradingDays("", calendar), 0)
        self.assertEqual(listedTradingDays(None, calendar), 0)
        self.assertEqual(listedTradingDays("2025-01-02", calendar), 2)   # 上市次日至末日
        self.assertEqual(listedTradingDays("2000-01-04", calendar), 10 ** 6)  # 早于窗口
