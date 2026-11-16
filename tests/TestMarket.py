#!/usr/bin/env python3
"""行情三层（Market）读写与幂等更新的单测（不需要网络）。"""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import Market  # noqa: E402


def quote(code="000001", name="平安银行", open_=10.0, close=11.0, high=11.5, low=9.8,
          volume=1234.0, amount=1.5e7, totalMv=2.2e10, floatMv=2.0e10):
    """构造一条腾讯/东财统一格式的行情（volume 单位为手）。"""
    return {"code": code, "name": name, "open": open_, "price": close,
            "high": high, "low": low, "volume": volume, "amount": amount,
            "total_mv": totalMv, "float_mv": floatMv}


class TestRowsFromQuotes(unittest.TestCase):

    def test_fields_and_units(self):
        rows = Market.rowsFromQuotes([quote()], "20261116")
        self.assertEqual(len(rows), 1)
        cols = rows[0].split("|")
        self.assertEqual(len(cols), 13)
        code, name, date, open_, close, high, low, volume, amount = cols[:9]
        self.assertEqual((code, name, date), ("000001", "平安银行", "20261116"))
        self.assertEqual((open_, close, high, low), ("10", "11", "11.5", "9.8"))
        self.assertEqual(volume, "123400")           # 手 → 股
        self.assertEqual(amount, "15000000")         # 1.5e7
        self.assertEqual(cols[9], "2000000000")      # 总股本 = 220亿 / 11
        self.assertEqual(cols[10], "1818181818")     # 流通股本 = 200亿 / 11
        self.assertEqual(cols[11], "22000000000.00")

    def test_high_low_fallback(self):
        rows = Market.rowsFromQuotes([quote(open_=10.0, close=11.0, high=0.0, low=0.0)],
                                     "20261116")
        cols = rows[0].split("|")
        self.assertEqual((cols[5], cols[6]), ("11", "10"))

    def test_skip_non_main_board_and_bad_quotes(self):
        quotes = [quote(code="300001"), quote(code="688001"),
                  quote(code="000002", close=0.0), quote(code="600000", totalMv=0)]
        self.assertEqual(Market.rowsFromQuotes(quotes, "20261116"), [])

    def test_sorted_by_code(self):
        rows = Market.rowsFromQuotes([quote(code="600000"), quote(code="000001")], "20261116")
        self.assertEqual([r.split("|")[0] for r in rows], ["000001", "600000"])


class TestLayers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_latest_keeps_five_days(self):
        for day in ("20261109", "20261110", "20261111", "20261112", "20261113", "20261116"):
            rows = Market.rowsFromQuotes([quote()], day)
            Market.writeLatest(self.tmp, rows)
        path = os.path.join(self.tmp, "Latest", "Latest.mvsv")
        rows = Market.readRows(path)
        self.assertEqual(sorted({r.split("|")[2] for r in rows}),
                         ["20261110", "20261111", "20261112", "20261113", "20261116"])
        with open(path, encoding="utf-8") as file:
            self.assertIn("20261110 ~ 20261116", file.read())

    def test_latest_idempotent(self):
        rows = Market.rowsFromQuotes([quote()], "20261116")
        Market.writeLatest(self.tmp, rows)
        first = Market.readRows(os.path.join(self.tmp, "Latest", "Latest.mvsv"))
        Market.writeLatest(self.tmp, rows)
        second = Market.readRows(os.path.join(self.tmp, "Latest", "Latest.mvsv"))
        self.assertEqual(first, second)              # 同日重复写入不会产生重复行

    def test_monthly_merge_and_header(self):
        Market.writeMonthly(self.tmp, Market.rowsFromQuotes([quote()], "20261113"))
        path, count = Market.writeMonthly(self.tmp, Market.rowsFromQuotes(
            [quote(code="000001", close=12.0)], "20261116"))
        self.assertEqual(count, 2)
        self.assertEqual(os.path.basename(path), "202611.mvsv")
        with open(path, encoding="utf-8") as file:
            text = file.read()
        self.assertIn("# 字段名称", text)
        self.assertIn("# DataProvider : Sina/Tencent", text)
        self.assertIn("# Field : \"Code|Name|Date", text)
        rows = Market.readRows(path)
        self.assertEqual([r.split("|")[2] for r in rows], ["20261113", "20261116"])
        self.assertEqual(rows[-1].split("|")[4], "12")     # 同日以最新数据为准

    def test_archive_year_merges_into_security_files(self):
        for month, day in (("202611", "20261113"), ("202612", "20261215")):
            rows = Market.rowsFromQuotes([quote(code="000001"),
                                          quote(code="600000", name="浦发银行")], day)
            Market.writeMonthly(self.tmp, rows)
        # 归档后 Data 仍保留，Archive/Security 每只一个文件、按日期升序
        self.assertEqual(Market.archiveYear(self.tmp, 2026, log=lambda *_: None), 2)
        path = os.path.join(self.tmp, "Archive", "Security", "000001.mvsv")
        rows = Market.readRows(path)
        self.assertEqual([r.split("|")[2] for r in rows], ["20261113", "20261215"])
        with open(path, encoding="utf-8") as file:
            text = file.read()
        self.assertIn("# TypeKLine : Day", text)
        self.assertIn("# Count : 2", text)
        # 幂等：再归档一次结果不变
        Market.archiveYear(self.tmp, 2026, log=lambda *_: None)
        self.assertEqual(Market.readRows(path), rows)
        self.assertEqual(Market.archivedYears(self.tmp), ["2026"])

    def test_update_writes_all_layers(self):
        info = Market.update(self.tmp, [quote(code="000001"), quote(code="600000")],
                             "2026-11-16", log=lambda *_: None, archive=False)
        self.assertTrue(info["ok"])
        self.assertEqual(info["rows"], 2)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Latest", "Latest.mvsv")))
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "Data", "2026", "202611.mvsv")))

    def test_update_no_rows(self):
        info = Market.update(self.tmp, [], "2026-11-16", log=lambda *_: None)
        self.assertFalse(info["ok"])
        self.assertEqual(info["reason"], "no_rows")


if __name__ == "__main__":
    unittest.main()
