#!/usr/bin/env python3
"""CNAMCI 基线指数体系入口（GitHub Actions，仓库根即本目录）。

三个指数共用同一选样口径，仅调仓频率不同：

    CNAMCSDEWI  日频等权（每个交易日调仓）
    CNAMCSWEWI  周频等权（每周最后一个交易日调仓）
    CNAMCSMEWI  月频等权（每月最后一个交易日调仓）

每日流程（7 步，日志逐步骤反馈）::

    1. 数据就绪检查   上证指数快照时间戳 = 今日且 ≥ 15:00:00，最多等待 max_wait_min 分钟
    2. 拉取全市场快照 腾讯 900 只/批 + 交易所/巨潮代码清单（周级缓存）+ 东财交易日历
    3. 成分与权重     调仓日：按总市值升序取 400 只并等权；非调仓日：沿用上次成分，权重漂移
    4. 指数点位       用上一交易日收盘权重计算当日收益，累积点位（基点 1000）
    5. 组合与调仓     回填昨日计划（今日开盘价）→ 估值 → 调仓日生成明日计划
    6. 写出结果       Index/<代码>/{Index.json,Nav.mvsv,State.json,Portfolio.json,Latest}
    7. 汇总           请求数、耗时、点位、计划与成交笔数

规则见 ``Spec.md``；行情/选样/组合实现见 ``Core/``。
"""

import argparse
import csv
import datetime
import io
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))   # 仓库根（CNAMCI）
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "Core"))

import Archive  # noqa: E402  按年归档（每天只改 4 个文件）
import Cnamci  # noqa: E402  指数点位与报告（纯函数）
import Logger  # noqa: E402  统一日志（北京时间 + 主题前缀 + 步骤耗时）
import Mvsv  # noqa: E402  .mvsv 读写（头部元数据 + 竖线分隔）
import Portfolio  # noqa: E402  目标股数/调仓计划/成交回填/费用
import Sources as sources  # noqa: E402  行情抓取与请求计数
import Store  # noqa: E402  本地存储
import YamlLite  # noqa: E402  极简 YAML 配置读取
import Universe  # noqa: E402  股票池过滤与 400 只选样

TOTAL_STEPS = 7
# Config.yaml 使用大驼峰字段，读取后映射为内部小写键
CONFIG_KEYS = {
    "N": "n", "Capital": "capital", "Mode": "mode", "WeightMode": "weight_mode",
    "MinListedDays": "min_listed_days", "FeeModel": "fee_model", "Lot": "lot",
    "BasePoint": "base_point", "BaseDate": "base_date", "Source": "source",
    "Boards": "boards", "MaxWaitMin": "max_wait_min", "RetrySeconds": "retry_seconds",
    "ExcludeTop": "exclude_top", "MinAmount20": "min_amount20",
    "Amount20Pct": "amount20_pct", "LiquidityFirst": "liquidity_first",
}
FILE_TITLES = {"holdings": "Holdings", "plan": "Plan", "execution": "Execution"}
DEFAULT_CONFIG = {
    "n": 400, "capital": 4_000_000.0, "mode": "changes", "weight_mode": "equal",
    "min_listed_days": 60, "fee_model": "rate", "lot": 100,
    "base_point": 1000.0, "base_date": "2004-12-31",
    "source": "tencent", "boards": list(Universe.MAIN_BOARD_PREFIX),
    "exclude_top": 0,             # 剔除市值前 N 名（需全市场数据；主板数据下保持 0）
    "max_wait_min": 40, "retry_seconds": 60,
}
COMP_COLS = ["rank", "code", "name", "close", "total_mv", "float_mv", "weight", "listed_days"]
HOLD_COLS = ["code", "name", "shares", "cost", "close", "market_value", "weight", "unrealized_pnl"]
PLAN_COLS = ["side", "code", "name", "shares", "ref_price", "est_amount", "est_fee", "reason"]
EXEC_COLS = ["signal_date", "exec_date", "side", "code", "name", "shares", "price",
             "amount", "fee", "cash_after", "realized_pnl", "reason"]
NAV_COLS = ["date", "point", "ret", "cum_return", "holdings_count", "total_asset", "cash"]


class DataStore(Store.LocalStore):
    """产物写 ``Data/<代码>/``；Sources 的 ``state/`` 缓存统一放 ``Data/Cache/``（共享、不提交）。"""

    def __init__(self, root, cacheDir):
        super().__init__(root)
        self.cacheDir = cacheDir

    def _path(self, key):
        if key.startswith("state/"):
            path = os.path.join(self.cacheDir, key[len("state/"):])
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return path
        return super()._path(key)


def saveFile(root, key, payload):
    """按相对 key 写文件：``str`` 原样写，其余按 JSON 写；目录自动创建。"""
    path = os.path.join(root, *key.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        if isinstance(payload, str):
            file.write(payload)
        else:
            json.dump(payload, file, ensure_ascii=False, indent=1)
            file.write("\n")


def csvText(rows, columns, title=""):
    """把行列表渲染成 ``.mvsv`` 文本（头部元数据 + 竖线分隔、无表头）。"""
    return Mvsv.render(rows, columns, title)


def updateNav(path, row):
    """写入 Nav.mvsv：同一交易日只保留一行，按日期升序；返回总行数。"""
    rows = [item for item in Mvsv.read(path, NAV_COLS) if item.get("date") != row["date"]]
    rows.append({key: row[key] for key in NAV_COLS})
    rows.sort(key=lambda item: item["date"])
    Mvsv.write(path, rows, NAV_COLS, title="指数点位序列")
    return len(rows)


def isRebalanceDay(dateStr, frequency, calendar):
    """判断某日是否为调仓日。

    daily：每天；weekly/monthly：当日之后没有同周/同月交易日。
    回放时日历含未来交易日，按日历精确判断；线上日历只到当日，按自然日近似（周五/月末最后工作日）。
    """
    if frequency == "daily":
        return True
    day = datetime.date.fromisoformat(dateStr)
    future = next((item for item in calendar if item > dateStr), None)
    if future:
        nxt = datetime.date.fromisoformat(future)
        if frequency == "weekly":
            return nxt.isocalendar()[:2] != day.isocalendar()[:2]
        return (nxt.year, nxt.month) != (day.year, day.month)
    probe = day + datetime.timedelta(days=1)
    while probe.weekday() >= 5:
        probe += datetime.timedelta(days=1)
    if frequency == "weekly":
        return probe.isocalendar()[:2] != day.isocalendar()[:2]
    return (probe.year, probe.month) != (day.year, day.month)


def componentsView(indexComponents, quotes):
    """非调仓日：用当日快照补全总市值等字段，按市值升序赋 rank（仅展示用）。"""
    byCode = {item["code"]: item for item in quotes}
    rows = []
    for item in indexComponents:
        quote = byCode.get(item["code"]) or {}
        rows.append({"code": item["code"], "name": item.get("name") or quote.get("name", ""),
                     "close": item["close"], "total_mv": quote.get("total_mv") or 0.0,
                     "float_mv": quote.get("float_mv") or 0.0, "weight": item["weight"],
                     "listed_days": quote.get("listed_days") or 0})
    rows.sort(key=lambda row: row["total_mv"])
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank
    return rows


def indexTimestamp():
    """1 次请求取上证指数快照的行情时间戳（``YYYYMMDDHHMMSS``），失败返回空串。"""
    try:
        text = sources.httpGet("https://qt.gtimg.cn/q=sh000001",
                               {"Referer": "https://finance.qq.com/"}, encoding="gbk")
    except Exception as exc:  # noqa: BLE001  网络异常只降级，不中断等待循环
        Logger.warn(f"指数快照请求失败：{exc}", topic="DATA")
        return ""
    for line in text.split(";"):
        fields = line.split('="', 1)[1].rstrip('"').split("~") if '="' in line else []
        if len(fields) > 30 and fields[2] == "000001":
            return fields[30]
    return ""


def waitUntilClose(cfg, step):
    """等待当日收盘数据就绪（时间戳日期 = 今天且时间 ≥ 15:00:00）。"""
    deadline = time.time() + float(cfg["max_wait_min"]) * 60
    attempt = 0
    while True:
        attempt += 1
        timestamp = indexTimestamp()
        if timestamp[:8] == Logger.beijingNow().strftime("%Y%m%d") and timestamp[8:] >= "150000":
            return True, timestamp
        if time.time() >= deadline:
            step.warn(f"等待超时（{cfg['max_wait_min']:g} 分钟），最后时间戳 {timestamp or '无'}")
            return False, timestamp
        step.note(f"第 {attempt} 次检查：时间戳 {timestamp or '无'} 未更新到今日收盘，"
                  f"{cfg['retry_seconds']:g} 秒后重试")
        time.sleep(float(cfg["retry_seconds"]))


def replayHistory(cfg, code):
    """用本地 ``Input/panel.csv``（或 ../data/panel.csv）从基期逐日回放，写入 Data/<code>/。"""
    try:
        import LocalData  # 惰性导入：仅回放需要 pandas
    except ImportError as exc:
        raise RuntimeError("缺少 pandas / LocalData，无法历史回放") from exc

    topic = code
    root = os.path.join(cfg["data_dir"], code)
    frequency = Cnamci.INDICES[code]["rebalance"]
    calendar = LocalData.loadCalendar()
    nominalStart = cfg.get("base_date") or calendar[0]   # 名义基日（如 2004-12-31）
    start = max(nominalStart, calendar[0])               # 实际数据起点（2009-06-01）
    df = LocalData.loadPanel(start, None)
    totalDays = int(df["date"].nunique())
    archived = 0
    Logger.info(f"回放 {Cnamci.INDICES[code]['name']}（{frequency}）：{start} 起，"
                f"共 {totalDays} 个交易日", topic=topic)

    holdings, cash = {}, float(cfg["capital"])
    pending, prevComponents = None, []
    basePoint = float(cfg["base_point"])
    point, navRows, totalFee = basePoint, [], 0.0
    lastRecord, lastPlan, lastExecution, lastSnapshot = {}, [], [], {}
    lastComponents, lastCloses = [], {}
    rebalanceDays, tradeRows = 0, []
    yearRows, recentRows, prevYear = {}, [], None
    currentYear = str(datetime.date.today().year)

    for index, (date, quotes, opens, closes, calUpto) in enumerate(
            LocalData.iterDays(df, calendar), 1):
        # 1) 执行昨日计划（今日开盘价）
        execution = []
        if pending and cfg["mode"] != "index":
            holdings, cash, execution = Portfolio.execute(
                holdings, cash, pending["orders"], opens, date,
                model=cfg["fee_model"], lot=int(cfg["lot"]))
            totalFee += sum(fill["fee"] for fill in execution)
            tradeRows.extend(execution)
        # 2) 成分与权重：调仓日重算并等权，非调仓日沿用并漂移
        rebalance = isRebalanceDay(date, frequency, calendar) or not prevComponents
        built = {}
        if rebalance:
            built = Universe.buildUniverse(quotes, calUpto, n=int(cfg["n"]),
                                           min_listed_days=int(cfg["min_listed_days"]),
                                           weight_mode=cfg["weight_mode"],
                                           boards=tuple(cfg["boards"]),
                                           exclude_top=int(cfg.get("exclude_top") or 0))
            components = built["components"]
            if len(components) < int(cfg["n"]):
                raise RuntimeError(f"{date} 合格标的不足 {len(components)} < {cfg['n']}")
            rebalanceDays += 1
        else:
            components = prevComponents
        # 3) 指数点位：用上一交易日收盘权重计算今日收益
        prevPoint = point
        ret, drifted, _ = Cnamci.updateComponents(prevComponents, closes)
        point = Cnamci.nextPoint(prevPoint, ret) if prevComponents else basePoint
        cumulative = point / basePoint - 1.0
        # 4) 组合估值与调仓计划（仅调仓日）
        holdingsRows, marketValue, _ = Portfolio.markToMarket(holdings, closes)
        totalAsset = round(cash + marketValue, 2)
        for row in holdingsRows:
            row["weight"] = round(row["market_value"] / totalAsset, 8) if totalAsset else 0.0
        plan, planMeta = [], {}
        if rebalance and cfg["mode"] != "index":
            plan, planMeta = Portfolio.makePlan(
                holdings, components, totalAsset, cash, cfg["mode"], date,
                model=cfg["fee_model"], lot=int(cfg["lot"]), prices=closes)
        snapshot = {"cash": round(cash, 2), "market_value": marketValue,
                    "total_asset": totalAsset, "holdings_count": len(holdingsRows),
                    "plan_orders": len(plan), "plan_meta": planMeta,
                    "executed_orders": len(execution)}
        # 5) 逐日落盘
        view = components if rebalance else componentsView(drifted, quotes)
        record = Cnamci.buildIndexRecord(
            code, Cnamci.INDICES[code]["name"], date, point, prevPoint, ret,
            nominalStart, basePoint, view, built,
            {"replay": True, "days": totalDays, "rebalance": rebalance},
            {"requests": 0, "attempts": 0, "bytes": 0, "bytes_mb": 0.0, "hosts": {}},
            cfg["mode"], snapshot, f"{date}T15:00:00+0800", dataStart=start)
        dayRows = Archive.buildDayRows(date, view, holdingsRows, plan, execution, record)
        yearRows.setdefault(date[:4], []).append(dayRows)
        if prevYear and date[:4] != prevYear:
            Archive.writeYear(root, prevYear, yearRows.pop(prevYear), currentYear)
        prevYear = date[:4]
        recentRows.append(dayRows)
        if len(recentRows) > Archive.KEEP_DAYS:
            recentRows.pop(0)
        archived += 1
        navRows.append({"date": date, "point": point, "ret": ret,
                        "cum_return": round(cumulative, 8),
                        "holdings_count": len(holdingsRows), "total_asset": totalAsset,
                        "cash": round(cash, 2)})
        prevComponents = components if rebalance else drifted
        pending = {"signal_date": date, "exec_date": sources.nextBusinessDay(date),
                   "orders": plan} if plan else None
        lastRecord, lastPlan, lastExecution = record, plan, execution
        lastSnapshot, lastComponents, lastCloses = snapshot, view, closes
        if index % 50 == 0:
            Logger.info(f"回放进度 {index}/{totalDays}（{date}，点位 {point:.4f}，"
                        f"累计 {cumulative:+.2%}）", topic=topic)

    if not navRows:
        raise RuntimeError("回放区间内没有交易日数据")
    for year in sorted(yearRows):            # 收尾：剩余年份落盘
        Archive.writeYear(root, year, yearRows[year], currentYear)
    Archive.writeCurrentRange(root, recentRows)
    saveFile(root, "State.json", {
        "base_date": nominalStart, "data_start": start, "base_point": basePoint,
        "index": {"date": lastRecord["trade_date"], "point": point,
                  "ret": lastRecord["ret"], "components": prevComponents}})
    saveFile(root, "Nav.mvsv", csvText(navRows, NAV_COLS, "指数点位序列"))
    if cfg["mode"] != "index":                 # 全量调仓流水（不依赖 Daily 归档）
        saveFile(root, "Trades.mvsv", csvText(tradeRows, EXEC_COLS, "全量调仓流水"))
    saveFile(root, "Index.json", lastRecord)
    saveFile(root, "Latest/Index.json", lastRecord)
    saveFile(root, "Latest/Components.mvsv", csvText(lastComponents, COMP_COLS, "当日成分"))
    saveFile(root, "Latest/Components.json", {**lastRecord, "components": lastComponents})
    if cfg["mode"] != "index":
        saveFile(root, "Portfolio.json", {
            "as_of": lastRecord["trade_date"], "cash": round(cash, 2),
            "holdings": sorted(holdings.values(), key=lambda item: item["code"]),
            "pending": pending})
        holdingsRows, marketValue, _ = Portfolio.markToMarket(holdings, lastCloses)
        totalAsset = round(cash + marketValue, 2)
        for row in holdingsRows:
            row["weight"] = round(row["market_value"] / totalAsset, 8) if totalAsset else 0.0
        saveFile(root, "Latest/Holdings.mvsv", csvText(holdingsRows, HOLD_COLS, "当日持仓"))
        saveFile(root, "Latest/Plan.mvsv", csvText(lastPlan, PLAN_COLS, "次日调仓计划"))
        saveFile(root, "Latest/Execution.mvsv", csvText(lastExecution, EXEC_COLS, "当日成交"))
        lastSnapshot.update({"holdings_count": len(holdingsRows), "market_value": marketValue,
                             "total_asset": totalAsset, "cash": round(cash, 2)})
    saveFile(root, "Latest/Report.md",
             Cnamci.renderReport(lastRecord, lastPlan, lastExecution, lastSnapshot))
    Logger.ok(f"回放完成：{start} → {lastRecord['trade_date']}，调仓 {rebalanceDays} 次，"
              f"成交 {len(tradeRows)} 笔，Daily 归档 {archived} 天（按年文件），"
              f"点位 {point:.4f}（累计 {lastRecord['cum_return']:+.2%}），"
              f"费用 {totalFee:,.2f} 元", topic=topic)
    return {"ok": True, "skipped": False, "mode": "replay", "code": code,
            "base_date": start, "trade_date": lastRecord["trade_date"],
            "days": len(navRows), "rebalance_days": rebalanceDays, "point": point,
            "cum_return": lastRecord["cum_return"], "total_fee": round(totalFee, 2)}


def run(args):
    """执行一次完整的每日计算，返回结果字典（供日志与 CI 判定使用）。"""
    code = args.index
    if code not in Cnamci.INDICES:
        raise SystemExit(f"未知指数代码: {code}")
    frequency = Cnamci.INDICES[code]["rebalance"]
    title = Cnamci.INDICES[code]["name"]

    cfg = dict(DEFAULT_CONFIG)
    if args.config and os.path.exists(args.config):
        raw = YamlLite.load(args.config)
        cfg.update({CONFIG_KEYS.get(key, key): value for key, value in raw.items()
                    if value is not None})
    for key in ("n", "capital", "mode", "source", "max_wait_min"):
        if getattr(args, key, None) is not None:
            cfg[key] = getattr(args, key)
    dataDir = os.path.abspath(args.data_dir)
    cfg["data_dir"] = dataDir
    root = os.path.join(dataDir, code)

    Logger.setTopic(code)
    Logger.setLogFile(os.path.join(root, "Latest", "Run.log"))
    archivedYears = Archive.archivePending(root)      # 次年 3 月 1 日自动归档
    if archivedYears:
        Logger.info(f"已归档年份：{', '.join(archivedYears)}（Pending → Archive）", topic=code)
    storage = DataStore(root, os.path.join(dataDir, "Cache"))
    startedAt = time.time()

    if args.replay_start:
        Logger.section(f"{code} 历史回放（{args.replay_start} 起）")
        cfg["base_date"] = args.replay_start
        try:
            return replayHistory(cfg, code)
        except Exception as exc:  # noqa: BLE001
            Logger.error(f"历史回放失败：{exc}")
            return {"ok": False, "skipped": False, "reason": "replay_failed"}

    Logger.section(f"{code} 每日计算（{frequency}）")
    Logger.info(f"Python {sys.version.split()[0]}｜Actions="
                f"{'是' if os.environ.get('GITHUB_ACTIONS') else '否'}｜{title}｜"
                f"数据源={cfg['source']}｜成分={cfg['n']}｜资金={cfg['capital']:,.0f}｜"
                f"模式={cfg['mode']}｜等待收盘={'是' if args.wait_close and not args.date else '否'}")

    # ---------------- 1) 数据就绪 ----------------
    with Logger.Step(1, TOTAL_STEPS, "数据就绪检查", topic=f"{code}/DATA") as step:
        if args.wait_close and not args.date:
            step.note(f"条件：上证指数时间戳=今日且 ≥15:00:00，最多等 {cfg['max_wait_min']:g} 分钟")
            ready, timestamp = waitUntilClose(cfg, step)
            if not ready:
                step.skip(f"未就绪（{timestamp or '无'}）")
                Logger.warn("非交易日或数据源异常：不写盘，退出码 0")
                return {"ok": False, "skipped": True, "reason": "not_ready"}
            step.result(f"时间戳 {timestamp}")
        else:
            step.result("跳过等待（--no-wait/--date）")

    # ---------------- 2) 全市场快照 ----------------
    with Logger.Step(2, TOTAL_STEPS, "拉取全市场快照", topic=f"{code}/DATA") as step:
        calendar = sources.fetchCalendar(storage, float(sources.DEFAULT_CFG["calendar_ttl_hours"]))
        step.note(f"交易日历 {len(calendar)} 天，最近交易日 {calendar[-1]}")
        lamCfg = dict(sources.DEFAULT_CFG)
        lamCfg.update({key: cfg[key] for key in lamCfg if key in cfg})
        lamCfg.update({"n": cfg["n"], "min_listed_days": cfg["min_listed_days"],
                       "weight_mode": cfg["weight_mode"]})
        boards = tuple(cfg["boards"])
        if cfg["source"] == "tencent":
            quotes, scan = sources.quotesFromTencent(lamCfg, calendar, boards, storage)
            step.note(f"代码清单 {scan.get('stock_list')}（{scan.get('codes')} 只），"
                      f"腾讯 {sources.TENCENT_BATCH} 只/批")
        else:
            quotes, scan = sources.quotesFromEastmoney(lamCfg, calendar, boards)
        if not quotes:
            step.fail("未取到任何行情")
            return {"ok": False, "skipped": False, "reason": "no_quotes"}
        tradeDate = sources.quoteDate(quotes) or Logger.beijingNow().strftime("%Y-%m-%d")
        step.result(f"{len(quotes)} 条行情 · 日期 {tradeDate} · {sources.STATS.requests} 次请求 / "
                    f"{sources.STATS.bytes / 1048576:.2f} MB")

    today = Logger.beijingNow().strftime("%Y-%m-%d")
    if not args.date and tradeDate != today:
        Logger.warn(f"数据日期 {tradeDate} ≠ 今日 {today}，视为非交易日，不写盘")
        return {"ok": False, "skipped": True, "reason": "non_trading_day", "trade_date": tradeDate}
    if args.date and args.date != tradeDate:
        Logger.warn(f"--date {args.date} 与实际数据日期 {tradeDate} 不一致，以数据日期为准")

    closes = {item["code"]: item["price"] for item in quotes}
    opens = {item["code"]: item["open"] for item in quotes if (item.get("open") or 0) > 0}

    # ---------------- 3) 成分与权重 ----------------
    state = storage.readJson("State.json") or {}
    prev = state.get("index") or {}
    prevComponents = prev.get("components") or []
    basePoint = float(state.get("base_point") or cfg["base_point"])
    baseDate = state.get("base_date") or cfg.get("base_date") or tradeDate
    dataStart = state.get("data_start") or tradeDate
    rebalance = isRebalanceDay(tradeDate, frequency, calendar) or not prevComponents

    with Logger.Step(3, TOTAL_STEPS, "成分与权重", topic=f"{code}/INDEX") as step:
        step.note(f"调仓频率={frequency}；今日{'是' if rebalance else '不是'}调仓日；"
                  f"上次定权 {prev.get('date') or '无'}")
        if rebalance:
            built = Universe.buildUniverse(quotes, calendar, n=int(cfg["n"]),
                                           min_listed_days=int(cfg["min_listed_days"]),
                                           weight_mode=cfg["weight_mode"], boards=boards,
                                           exclude_top=int(cfg.get("exclude_top") or 0))
            components = built["components"]
            if len(components) < int(cfg["n"]):
                step.fail(f"合格标的不足 {len(components)} < {cfg['n']}")
                return {"ok": False, "skipped": False, "reason": "insufficient_universe"}
            view = components
            skipped = scan.get("skip_reasons") or built["skipped"]
            step.note(f"候选 {built['eligible_count']}；跳过 ST {skipped.get('st', 0)} / "
                      f"停牌 {skipped.get('halted', 0)} / 次新 {skipped.get('new_listing', 0)}")
            step.result(f"{len(components)} 只 · 等权 {1 / len(components):.8f} · 边界 "
                        f"{built['border_mcap'] / 1e8:.2f} 亿（下一名 "
                        f"{(built['next_mcap'] or 0) / 1e8:.2f} 亿）")
        else:
            built, view = {}, componentsView(prevComponents, quotes)
            step.result(f"沿用上次成分 {len(view)} 只（权重漂移，不调仓）")

    # ---------------- 4) 指数点位 ----------------
    with Logger.Step(4, TOTAL_STEPS, "计算指数点位", topic=f"{code}/INDEX") as step:
        prevPoint = float(prev.get("point") or basePoint)
        ret, drifted, _ = Cnamci.updateComponents(prevComponents, closes)
        point = Cnamci.nextPoint(prevPoint, ret) if prevComponents else basePoint
        if not prevComponents:
            ret = 0.0
            step.note(f"首次计算：基期 {baseDate}，基点 {basePoint:g}")
        else:
            step.note(f"用 {prev.get('date')} 收盘权重计算（无行情 "
                      f"{sum(1 for item in (Cnamci.computeDailyReturn(prevComponents, closes)[1]) if item['note'])} 只）")
        cumulative = point / basePoint - 1.0
        step.result(f"{prevPoint:.4f} → {point:.4f}（{ret:+.2%}）· 累计 {cumulative:+.2%}")

    # ---------------- 5) 组合与调仓 ----------------
    holdingsRows, plan, execution, snapshot = [], [], [], {}
    portfolioState = storage.readJson("Portfolio.json") or {}
    holdings = {item["code"]: item for item in (portfolioState.get("holdings") or [])
                if item.get("code")}
    cash = float(portfolioState.get("cash", cfg["capital"]))
    pending = portfolioState.get("pending")
    with Logger.Step(5, TOTAL_STEPS, "组合与调仓", topic=f"{code}/PORT") as step:
        if cfg["mode"] == "index":
            step.skip("mode=index，不模拟资金")
        else:
            step.note(f"现金 {cash:,.2f} · 持仓 {len(holdings)} 只 · pending="
                      f"{pending.get('signal_date') if pending else '无'}")
            if pending and pending.get("signal_date") and pending["signal_date"] < tradeDate:
                holdings, cash, execution = Portfolio.execute(
                    holdings, cash, pending.get("orders") or [], opens, tradeDate,
                    model=cfg["fee_model"], lot=int(cfg["lot"]))
                step.note(f"回填成交 {len(execution)} 笔，费用 "
                          f"{sum(fill['fee'] for fill in execution):,.2f} 元")
            holdingsRows, marketValue, _ = Portfolio.markToMarket(holdings, closes)
            totalAsset = round(cash + marketValue, 2)
            for row in holdingsRows:
                row["weight"] = round(row["market_value"] / totalAsset, 8) if totalAsset else 0.0
            if rebalance:
                plan, planMeta = Portfolio.makePlan(
                    holdings, components, totalAsset, cash, cfg["mode"], tradeDate,
                    model=cfg["fee_model"], lot=int(cfg["lot"]), prices=closes)
            else:
                plan, planMeta = [], {}
            snapshot = {"cash": round(cash, 2), "market_value": marketValue,
                        "total_asset": totalAsset, "holdings_count": len(holdingsRows),
                        "plan_orders": len(plan), "plan_meta": planMeta,
                        "executed_orders": len(execution)}
            step.result(f"持仓 {len(holdingsRows)} 只 · 总资产 {totalAsset:,.2f} · "
                        f"{'调仓日计划' if rebalance else '非调仓日无计划'} "
                        f"{len(plan)} 笔 · 今日成交 {len(execution)} 笔")

    # ---------------- 6) 写出结果 ----------------
    httpStats = sources.STATS.as_dict()
    indexRecord = Cnamci.buildIndexRecord(
        code, title, tradeDate, point, prevPoint, ret, baseDate, basePoint, view, built,
        scan, httpStats, cfg["mode"], snapshot,
        Logger.beijingNow().strftime("%Y-%m-%dT%H:%M:%S%z"), dataStart=dataStart)
    with Logger.Step(6, TOTAL_STEPS, "写出结果", topic=f"{code}/OUT") as step:
        if args.dry_run:
            step.skip("--dry-run")
        else:
            saveFile(root, "State.json", {
                "base_date": baseDate, "data_start": dataStart, "base_point": basePoint,
                "index": {"date": tradeDate, "point": point, "ret": ret,
                          "rebalance": rebalance,
                          "components": components if rebalance else drifted}})
            saveFile(root, "Index.json", indexRecord)
            navPath = os.path.join(root, "Nav.mvsv")
            if not os.path.exists(navPath) and baseDate < tradeDate:
                updateNav(navPath, {"date": baseDate, "point": basePoint, "ret": 0.0,
                                    "cum_return": 0.0, "holdings_count": 0,
                                    "total_asset": cfg["capital"], "cash": cfg["capital"]})
            navCount = updateNav(navPath, {
                "date": tradeDate, "point": point, "ret": ret,
                "cum_return": round(cumulative, 8),
                "holdings_count": snapshot.get("holdings_count", 0),
                "total_asset": snapshot.get("total_asset", cfg["capital"]),
                "cash": snapshot.get("cash", cfg["capital"])})
            saveFile(root, "Latest/Index.json", indexRecord)
            saveFile(root, "Latest/Components.mvsv", csvText(view, COMP_COLS))
            saveFile(root, "Latest/Components.json", {**indexRecord, "components": view})
            dayRows = Archive.buildDayRows(tradeDate, view, holdingsRows, plan, execution,
                                           indexRecord)
            Archive.writeCurrentDay(root, tradeDate, dayRows)
            Archive.mergeToPending(root)
            fileCount = 3
            if cfg["mode"] != "index":
                saveFile(root, "Portfolio.json", {
                    "as_of": tradeDate, "cash": round(cash, 2),
                    "holdings": sorted(holdings.values(), key=lambda item: item["code"]),
                    "pending": {"signal_date": tradeDate,
                                "exec_date": sources.nextBusinessDay(tradeDate),
                                "orders": plan} if plan else None})
                for name, rows, columns in (("holdings", holdingsRows, HOLD_COLS),
                                            ("plan", plan, PLAN_COLS),
                                            ("execution", execution, EXEC_COLS)):
                    saveFile(root, f"Latest/{FILE_TITLES[name]}.csv", csvText(rows, columns))
                    fileCount += 1
                fileCount += 1
            report = Cnamci.renderReport(indexRecord, plan, execution, snapshot)
            saveFile(root, "Latest/Report.md", report)
            fileCount += 1
            step.result(f"{fileCount} 个文件 · Nav.mvsv {navCount} 行 · Daily/{tradeDate[:4]}/")
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as file:
                    file.write(report + "\n")
                step.note("已写入 Actions 步骤摘要")

    # ---------------- 7) 汇总 ----------------
    elapsed = round(time.time() - startedAt, 2)
    with Logger.Step(7, TOTAL_STEPS, "汇总", topic=code) as step:
        step.result(f"{tradeDate} 点位 {point:.4f}（{ret:+.2%}）· 成分 {len(view)} · "
                    f"{'调仓日' if rebalance else '非调仓日'} 计划 {len(plan)} 笔 · "
                    f"成交 {len(execution)} 笔 · {httpStats['requests']} 次请求 / "
                    f"{httpStats['bytes_mb']} MB · {elapsed}s")
    result = {"ok": True, "skipped": False, "code": code, "trade_date": tradeDate,
              "point": point, "ret": ret, "cum_return": round(cumulative, 8),
              "components": len(view), "rebalance": rebalance, "plan_orders": len(plan),
              "executed": len(execution), "http": httpStats, "elapsed_sec": elapsed}
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return result


def main():
    """解析命令行参数并执行；返回结果字典。"""
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="CNAMCI 基线指数每日计算")
    parser.add_argument("--index", default=Cnamci.INDEX_CODE, choices=list(Cnamci.INDICES))
    parser.add_argument("--data-dir", default=os.path.join(here, "Index"))
    parser.add_argument("--config", default=os.path.join(here, "Config.yaml"))
    parser.add_argument("--wait-close", dest="wait_close", action="store_true", default=True)
    parser.add_argument("--no-wait", dest="wait_close", action="store_false")
    parser.add_argument("--date", default=None, help="期望交易日（本地校验用）")
    parser.add_argument("--n", type=int, default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--mode", default=None, choices=Portfolio.MODES)
    parser.add_argument("--source", default=None, choices=("tencent", "eastmoney"))
    parser.add_argument("--max-wait-min", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--replay-start", default=None,
                        help="历史回放起始日（如 2004-12-31），覆盖 Data/<代码>/")
    return run(parser.parse_args())


if __name__ == "__main__":
    result = main()
    sys.exit(0 if (result.get("ok") or result.get("skipped")) else 1)
