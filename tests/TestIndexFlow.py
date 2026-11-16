#!/usr/bin/env python3
"""CNAMCSDEWI 流程测试：首次点位、次日收益累积、同日幂等、调仓回填。"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import Index as gh  # noqa: E402
import Sources as sources  # noqa: E402
import Mvsv  # noqa: E402

NAV_COLS = ["date", "point", "ret", "cum_return", "holdings_count", "total_asset", "cash"]

START = datetime.date(2026, 8, 19)
CAL = [(START + datetime.timedelta(days=i)).isoformat() for i in range(90)]
CAL[-2:] = ["2026-11-13", "2026-11-16"]


def q(code, close, open_p, mcap, ts):
    return {"code": code, "name": code, "price": close, "open": open_p, "volume": 1000.0,
            "total_mv": mcap, "float_mv": mcap, "list_date": "2000-01-01", "ts": ts}


def quotes(ts, mul=1.0):
    return [q("600001", 10 * mul, 10, 1e8, ts), q("600002", 20 * mul, 20, 2e8, ts),
            q("600003", 5 * mul, 5, 3e8, ts), q("600004", 8 * mul, 8, 4e8, ts)]


def args(data_dir, **kw):
    base = {"data_dir": data_dir, "config": "", "wait_close": False, "date": None,
            "n": 3, "capital": 300_000.0, "mode": "changes", "source": "tencent",
            "max_wait_min": 1, "dry_run": False, "replay_start": None,
            "no_market": True,          # 测试不得写仓库根的行情三层
            "index": "CNAMCSDEWI"}
    base.update(kw)
    return SimpleNamespace(**base)


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cnamci_")
        self.patches = [
            mock.patch.object(sources, "fetchCalendar", return_value=list(CAL)),
            mock.patch.object(sources, "quotesFromTencent", side_effect=[
                (quotes("2026-11-13"), {"stock_list": "cache", "codes": 4}),
                (quotes("2026-11-16", 1.1), {"stock_list": "cache", "codes": 4}),
                (quotes("2026-11-16", 1.1), {"stock_list": "cache", "codes": 4}),
            ]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_first_point_and_next_day_return(self):
        r1 = gh.run(args(self.tmp, date="2026-11-13"))
        self.assertEqual(r1["point"], 1000.0)      # 首次：基点
        self.assertEqual(r1["components"], 3)
        self.assertEqual(r1["executed"], 0)

        r2 = gh.run(args(self.tmp, date="2026-11-13"))  # 次日：全部 +10%
        self.assertAlmostEqual(r2["point"], 1100.0, places=6)
        self.assertAlmostEqual(r2["ret"], 0.10, places=8)
        self.assertEqual(r2["executed"], 3)        # 回填昨日建仓计划

        r3 = gh.run(args(self.tmp, date="2026-11-13"))  # 同日重跑：幂等
        self.assertAlmostEqual(r3["point"], 1100.0, places=6)
        self.assertEqual(r3["executed"], 0)

        nav = Mvsv.read(os.path.join(self.tmp, "CNAMCSDEWI", "Nav.mvsv"), NAV_COLS)
        # 基期行（默认 2004-12-31）+ 2 个交易日
        self.assertEqual(len(nav), 3)
        self.assertEqual(nav[0]["date"], "2004-12-31")
        self.assertEqual(float(nav[0]["point"]), 1000.0)

        with open(os.path.join(self.tmp, "CNAMCSDEWI", "Portfolio.json"), encoding="utf-8") as f:
            pf = json.load(f)
        self.assertGreaterEqual(pf["cash"], 0)
        self.assertEqual(len(pf["holdings"]), 3)
        for h in pf["holdings"]:
            self.assertEqual(h["shares"] % 100, 0)

        with open(os.path.join(self.tmp, "CNAMCSDEWI", "Latest", "Report.md"), encoding="utf-8") as f:
            self.assertIn("CNAMCSDEWI 每日速览", f.read())

    def test_non_trading_day_skips(self):
        r = gh.run(args(self.tmp))  # 今日不是 11-13（运行日），且未传 --date
        self.assertTrue(r.get("skipped"))

    def test_first_run_writes_base_row(self):
        """基期早于首个交易日时，Nav.mvsv 自动补一行基期 1000 点。"""
        cfgPath = os.path.join(self.tmp, "Config.yaml")
        with open(cfgPath, "w", encoding="utf-8") as f:
            f.write('BaseDate: "2026-11-10"\nBasePoint: 1000\n')
        r = gh.run(args(self.tmp, config=cfgPath, date="2026-11-13"))
        self.assertEqual(r["point"], 1000.0)
        nav = Mvsv.read(os.path.join(self.tmp, "CNAMCSDEWI", "Nav.mvsv"), NAV_COLS)
        self.assertEqual(len(nav), 2)                      # 基期 + 首个交易日
        self.assertEqual(nav[0]["date"], "2026-11-10")
        self.assertEqual(float(nav[0]["point"]), 1000.0)
        self.assertEqual(nav[1]["date"], "2026-11-13")
        with open(os.path.join(self.tmp, "CNAMCSDEWI", "Index.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["base_date"], "2026-11-10")

    def test_index_mode(self):
        r = gh.run(args(self.tmp, mode="index", date="2026-11-13"))
        self.assertEqual(r["point"], 1000.0)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "CNAMCSDEWI", "Portfolio.json")))


if __name__ == "__main__":
    unittest.main()
