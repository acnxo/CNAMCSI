#!/usr/bin/env python3
"""配置文件（Config.yaml）解析与键映射的单测。"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "Core"))

import Index  # noqa: E402
import YamlLite  # noqa: E402


class TestYamlLite(unittest.TestCase):
    def test_scalars_and_comments(self):
        data = YamlLite.loads(
            "# 注释\n"
            'N: 400          # 数量\n'
            'Capital: 4000000\n'
            'BaseDate: "2004-12-31"   # 基日\n'
            "Enabled: true\n"
            "Ratio: 0.10\n"
            'Name: "含 # 号"\n'
        )
        self.assertEqual(data["N"], 400)
        self.assertEqual(data["Capital"], 4000000)
        self.assertEqual(data["BaseDate"], "2004-12-31")
        self.assertIs(data["Enabled"], True)
        self.assertAlmostEqual(data["Ratio"], 0.10)
        self.assertEqual(data["Name"], "含 # 号")

    def test_list(self):
        data = YamlLite.loads('Boards: ["600", "601", "000"]\n')
        self.assertEqual(data["Boards"], ["600", "601", "000"])


class TestConfigFile(unittest.TestCase):
    def test_default_config_maps(self):
        cfg = YamlLite.load(os.path.join(ROOT, "Config.yaml"))
        mapped = {Index.CONFIG_KEYS.get(k, k): v for k, v in cfg.items()}
        self.assertEqual(mapped["n"], 400)
        self.assertEqual(mapped["base_date"], "2004-12-31")
        self.assertEqual(mapped["base_point"], 1000)
        self.assertIsInstance(mapped["boards"], list)
        self.assertEqual(len(mapped["boards"]), 8)
        self.assertIn("min_amount20", mapped)
        self.assertIn("liquidity_first", mapped)


    def test_loads_block_list(self):
        text = ('Count: 2\nStocks:\n  - Code: "000001"\n    Name: 平安银行\n'
                '    ListDate: "1991-04-03"\n  - Code: "600000"\n    Name: 浦发银行\n'
                '    ListDate: "1999-11-10"\n')
        data = YamlLite.loads(text)
        self.assertEqual(data["Count"], 2)
        self.assertEqual(len(data["Stocks"]), 2)
        self.assertEqual(data["Stocks"][0]["Code"], "000001")
        self.assertEqual(data["Stocks"][1]["Name"], "浦发银行")


if __name__ == "__main__":
    unittest.main()
