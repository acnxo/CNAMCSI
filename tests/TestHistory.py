#!/usr/bin/env python3
"""历史行情采集（History）的解析 / 合并 / 断点状态单测（不需要网络）。"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "Core"))

import History  # noqa: E402
import Market  # noqa: E402


def xqPayload(items, columns=None):
    columns = columns or ["timestamp", "volume", "open", "high", "low", "close",
                          "chg", "percent", "turnoverrate", "amount"]
    return json.dumps({"data": {"column": columns, "item": items}})


def ts(day):
    """``20041231`` → 雪球毫秒时间戳（北京时间 00:00）。"""
    import calendar
    import time as _time
    stamp = _time.strptime(day, "%Y%m%d")
    return (calendar.timegm(stamp) - 8 * 3600) * 1000


class TestParse(unittest.TestCase):

    def test_norm_date(self):
        self.assertEqual(History.normDate("2004-12-01"), "20041201")
        self.assertEqual(History.normDate("20041201"), "20041201")
        self.assertEqual(len(History.normDate(None)), 8)

    def test_codes(self):
        self.assertEqual(History.xqSymbol("600000"), "SH600000")
        self.assertEqual(History.xqSymbol("000001"), "SZ000001")
        self.assertEqual(History.emSecid("600000"), "1.600000")
        self.assertEqual(History.exchangeOf("000001"), "SZSE")

    def test_parse_xueqiu_units(self):
        # volume 已是股、amount 已是元，不换算
        payload = xqPayload([[ts("20041231"), 2410280, 7.0, 7.06, 6.99, 7.0, 0, 0, 0.27,
                              16965807.0]])
        rows = History.parseXueqiu(payload)
        self.assertEqual(len(rows), 1)
        day, open_, close, high, low, volume, amount = rows[0]
        self.assertEqual(day, "20041231")
        self.assertEqual((open_, close, high, low), (7.0, 7.0, 7.06, 6.99))
        self.assertEqual(volume, 2410280)
        self.assertEqual(amount, 16965807.0)

    def test_parse_xueqiu_skips_bad_and_sorts(self):
        payload = xqPayload([
            [ts("20050104"), 100, 7.0, 7.1, 6.9, 7.05, 0, 0, 0, 1.0],
            [ts("20041231"), 100, 7.0, 7.1, 6.9, 0, 0, 0, 0, 1.0],   # close=0 → 丢弃
            [ts("20041230"), 100, 7.0, 7.1, 6.9, 7.02, 0, 0, 0, 1.0],
        ])
        rows = History.parseXueqiu(payload)
        self.assertEqual([row[0] for row in rows], ["20041230", "20050104"])

    def test_parse_eastmoney_units(self):
        # f56 手 → 股（×100），f57 元
        payload = json.dumps({"data": {"klines": [
            "2004-12-31,7.00,7.00,7.06,6.99,24102,16965807.00"]}})
        rows = History.parseEastmoney(payload)
        self.assertEqual(rows[0][0], "20041231")
        self.assertEqual(rows[0][5], 2410200)
        self.assertEqual(rows[0][6], 16965807.0)


class TestRows(unittest.TestCase):

    def setUp(self):
        self.old = [
            "600000|浦发银行|20041231|7|7|7.06|6.99|2410280|16902088.5"
            "|8121068845|8121068845|56847481915.00|56847481915.00",
            "600000|浦发银行|20251231|12.44|12.44|12.5|12.4|1000|12440"
            "|33305865385|33305865385|414324965389.00|414324965389.00",
        ]

    def test_shares_index_and_nearest(self):
        index = History.sharesIndex(self.old)
        dates = sorted(index)
        self.assertEqual(dates, ["20041231", "20251231"])
        # 2005 年取 2004 年的股本（往前最近）
        self.assertEqual(History.nearestShares(index, dates, "20050601")[0], "8121068845")
        # 2026 年取 2025 年的股本
        self.assertEqual(History.nearestShares(index, dates, "20260105")[0], "33305865385")

    def test_build_rows_format(self):
        klines = [("20041231", 7.0, 7.0, 7.06, 6.99, 2410280.0, 16965807.0)]
        index = History.sharesIndex(self.old)
        rows = History.buildRows("600000", "浦发银行", klines, index, sorted(index))
        self.assertEqual(len(rows), 1)
        cols = rows[0].split("|")
        self.assertEqual(len(cols), 13)
        self.assertEqual(cols[2], "20041231")
        self.assertEqual(cols[3:7], ["7", "7", "7.06", "6.99"])
        self.assertEqual(cols[7], "2410280")
        self.assertEqual(cols[8], "16965807")
        self.assertEqual(cols[9], "8121068845")
        self.assertEqual(cols[11], "56847481915.00")   # 7 × 8121068845

    def test_merge_keeps_outside_rows(self):
        new = ["600000|浦发银行|20050104|7|7.05|7.1|6.9|100|705|8121068845"
               "|8121068845|57269266360.00|57269266360.00"]
        merged = History.mergeRows(self.old, new, "20041201", "20251231")
        days = [row.split("|")[2] for row in merged]
        self.assertEqual(days, ["20050104"])     # 区间内旧行被替换
        merged = History.mergeRows(self.old, new, "20050101", "20050131")
        days = [row.split("|")[2] for row in merged]
        self.assertEqual(days, ["20041231", "20050104", "20251231"])

    def test_security_headers(self):
        headers = dict(History.securityHeaders("600000", "浦发银行", 2))
        self.assertEqual(headers["Symbol"], "CN.600000")
        self.assertEqual(headers["Market"], "SSE")
        self.assertEqual(headers["Name"], '"浦发银行"')
        self.assertEqual(headers["Count"], "2")
        self.assertEqual(headers["TypeKLine"], "Day")


class TestSplitAndPending(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rowsFor(self, code, days):
        return [f"{code}|测试|{day}|1|1|1|1|100|100|1000|1000|1000.00|1000.00"
                for day in days]

    def test_split_rows_by_year(self):
        rows = self.rowsFor("000001", ["20251231", "20260105", "20261116"])
        hist, current = History.splitRows(rows, "2026")
        self.assertEqual([r.split("|")[2] for r in hist], ["20251231"])
        self.assertEqual([r.split("|")[2] for r in current], ["20260105", "20261116"])

    def test_archive_only_keeps_history(self):
        os.makedirs(os.path.join(self.tmp, "Archive", "Security"))
        rows = self.rowsFor("000001", ["20251231", "20260105"])
        History.saveSecurity(self.tmp, "000001", "测试", rows)
        moved = History.splitArchive(self.tmp, "2026", log=lambda *_: None)
        self.assertEqual(moved, 1)
        kept = Market.readRows(History.archivePath(self.tmp, "000001"))
        self.assertEqual([r.split("|")[2] for r in kept], ["20251231"])
        pending = Market.readRows(History.pendingPath(self.tmp, "2026", "000001"))
        self.assertEqual([r.split("|")[2] for r in pending], ["20260105"])

    def test_apply_pending_merges_into_month_file(self):
        months = os.path.join(self.tmp, "Data", "2026")
        os.makedirs(months, exist_ok=True)
        Market.writeMonthly(self.tmp, self.rowsFor("600000", ["20260105"]))
        History.savePending(self.tmp, "2026", "000001", self.rowsFor("000001", ["20260105"]))
        History.savePending(self.tmp, "2026", "000001", self.rowsFor("000001", ["20260106"]))
        written = History.applyPending(self.tmp, "2026", log=lambda *_: None)
        self.assertEqual(written, {"202601": 3})     # 600000 一行 + 000001 两行
        rows = Market.readRows(os.path.join(months, "202601.mvsv"))
        self.assertEqual([r.split("|")[:1] + r.split("|")[2:3] for r in rows],
                         [["000001", "20260105"], ["000001", "20260106"],
                          ["600000", "20260105"]])
        self.assertFalse(os.path.exists(os.path.join(months, ".pending")))

    def test_rebuild_latest_from_data(self):
        for day in ("20260105", "20260106", "20260107", "20260108", "20260109", "20260112"):
            Market.writeMonthly(self.tmp, self.rowsFor("000001", [day]))
        count = History.rebuildLatest(self.tmp, "2026", log=lambda *_: None)
        self.assertEqual(count, 5)
        latest = Market.readRows(os.path.join(self.tmp, "Latest", "Latest.mvsv"))
        days = sorted({r.split("|")[2] for r in latest})
        self.assertEqual(days, ["20260106", "20260107", "20260108", "20260109", "20260112"])

    def test_archive_year_appends_without_overwrite(self):
        os.makedirs(os.path.join(self.tmp, "Archive", "Security"))
        History.saveSecurity(self.tmp, "000001", "测试",
                             self.rowsFor("000001", ["20251231"]))
        Market.writeMonthly(self.tmp, self.rowsFor("000001", ["20251231", "20251230"]))
        Market.archiveYear(self.tmp, 2025, log=lambda *_: None)
        rows = Market.readRows(History.archivePath(self.tmp, "000001"))
        self.assertEqual([r.split("|")[2] for r in rows], ["20251230", "20251231"])
        self.assertEqual(rows[1], self.rowsFor("000001", ["20251231"])[0])   # 原值未被覆盖


class TestState(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_state_roundtrip_and_done(self):
        state = History.stateLoad(self.tmp)
        self.assertEqual(state["version"], History.STATE_VERSION)
        state["securities"]["000001"] = {"status": "ok", "from": "20041201",
                                         "first": "20041231", "last": "20261116",
                                         "rows": 5300}
        History.stateSave(self.tmp, state)
        again = History.stateLoad(self.tmp)
        self.assertEqual(again["securities"]["000001"]["rows"], 5300)
        self.assertTrue(History.isDone(again["securities"]["000001"], "20041201", "20261116"))
        self.assertFalse(History.isDone(again["securities"]["000001"], "20041201", "20261117"))
        self.assertFalse(History.isDone({"status": "failed"}, "20041201", "20261116"))

    def test_security_codes_and_save(self):
        os.makedirs(os.path.join(self.tmp, "Archive", "Security"))
        rows = ["600000|浦发银行|20041231|7|7|7.06|6.99|2410280|16965807"
                "|8121068845|8121068845|56847481915.00|56847481915.00"]
        path = History.saveSecurity(self.tmp, "600000", "浦发银行", rows)
        self.assertTrue(path.endswith(os.path.join("Archive", "Security", "600000.mvsv")))
        self.assertEqual(Market.readRows(path), rows)
        self.assertEqual(History.securityCodes(self.tmp, includeData=False), ["600000"])
        self.assertEqual(History.nameOf(self.tmp, "600000"), "浦发银行")
        # 300/688 不进主板全集
        with open(os.path.join(self.tmp, "Archive", "Security", "300001.mvsv"), "w",
                  encoding="utf-8") as file:
            file.write("\n".join(rows) + "\n")
        self.assertEqual(History.securityCodes(self.tmp, includeData=False), ["600000"])


if __name__ == "__main__":
    unittest.main()
