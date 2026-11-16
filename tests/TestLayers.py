#!/usr/bin/env python3
"""三层存储（Current / Pending / Archive）的单测。"""

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "Core"))

import Archive  # noqa: E402


def dayRows(date, code="000001", point=1000.0):
    return {
        "Components": [{"date": date, "rank": 1, "code": code, "name": "A", "close": 10.0,
                        "total_mv": 1e9, "float_mv": 1e9, "weight": 0.0025, "listed_days": 999}],
        "Holdings": [{"date": date, "code": code, "name": "A", "shares": 100, "cost": 10.0,
                      "close": 10.0, "market_value": 1000.0, "weight": 0.001, "unrealized_pnl": 0.0}],
        "Trades": [{"kind": "exec", "date": date, "signal_date": date, "exec_date": date,
                    "side": "BUY", "code": code, "name": "A", "shares": 100, "price": 10.0,
                    "amount": 1000.0, "fee": 5.1, "cash_after": 0.0, "realized_pnl": 0.0,
                    "reason": "enter"}],
        "Summary": [{"date": date, "point": point, "ret": 0.0, "cum_return": 0.0,
                     "holdings_count": 1, "total_asset": 1000.0, "cash": 0.0, "rebalance": True}],
    }


class TestLayers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="layers_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_current_keeps_five_days(self):
        for i, date in enumerate(("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12",
                                  "2026-11-13", "2026-11-16")):
            Archive.writeCurrentDay(self.tmp, date, dayRows(date, point=1000 + i))
        Archive.mergeToPending(self.tmp)        # 合并后 Current 只留最近 5 天
        rows = Archive.readJsonl(os.path.join(self.tmp, "Current", "Summary.jsonl"))
        self.assertEqual(len(rows), 5)
        self.assertEqual([row["date"] for row in rows],
                         ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13", "2026-11-16"])
        self.assertNotIn("2026-11-09", {row["date"] for row in rows})

    def test_merge_to_pending(self):
        for i, date in enumerate(("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12",
                                  "2026-11-13", "2026-11-16")):
            Archive.writeCurrentDay(self.tmp, date, dayRows(date, point=1000 + i))
        merged = Archive.mergeToPending(self.tmp)
        self.assertGreater(merged, 0)
        pending = os.path.join(self.tmp, "Pending", "2026", "Summary.mvsv")
        rows = Archive.readCsv(pending)
        self.assertEqual([row["date"] for row in rows], ["2026-11-09"])
        # Current 只剩 5 天
        current = Archive.readJsonl(os.path.join(self.tmp, "Current", "Summary.jsonl"))
        self.assertEqual(len(current), 5)

    def test_merge_is_idempotent(self):
        for date in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
                     "2026-11-16"):
            Archive.writeCurrentDay(self.tmp, date, dayRows(date))
        Archive.mergeToPending(self.tmp)
        Archive.mergeToPending(self.tmp)          # 再跑一次不应重复写入
        rows = Archive.readCsv(os.path.join(self.tmp, "Pending", "2026", "Summary.mvsv"))
        self.assertEqual(len(rows), 1)

    def test_archive_pending_by_march_first(self):
        Archive.writeCsv(os.path.join(self.tmp, "Pending", "2025", "Summary.mvsv"),
                         dayRows("2025-12-31")["Summary"], Archive.SUMMARY_COLS)
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "Pending", "2025")))
        archived = Archive.archivePending(self.tmp, today="2026-02-28")
        self.assertEqual(archived, [])
        archived = Archive.archivePending(self.tmp, today="2026-03-01")
        self.assertEqual(archived, ["2025"])
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "Archive", "2025")))

    def test_write_year_and_read_layer(self):
        Archive.writeYear(self.tmp, "2005", [dayRows("2005-01-04")], currentYear="2026")
        self.assertTrue(os.path.isdir(os.path.join(self.tmp, "Archive", "2005")))
        data = Archive.readLayer(self.tmp, "2005")
        self.assertEqual(len(data["Summary"]), 1)
        self.assertEqual(data["Summary"][0]["date"], "2005-01-04")


if __name__ == "__main__":
    unittest.main()
