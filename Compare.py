#!/usr/bin/env python3
"""业绩比较基准与走势图：三只 CNAMCI 指数 + 国证2000 / 中证2000 / 中证全指。

- 三个基准均以 2025-01-02 收盘归一为 1000 点（与 CNAMCI 基点一致），可直接比较；
- 中证2000 推出较晚（2023-08），2023 年以前无数据，故同时保留推出更早的国证2000；
- 输出（`Statistic/`）：
    Comparison.svg   6 条净值曲线（3 只 CNAMCI 实线 + 3 个基准虚线）
    Comparison.md    区间收益 / 年化 / 最大回撤对比表
    Comparison.json  结构化结果（供下游使用）
    Benchmarks/*.mvsv 基准收盘价缓存（与 Data/ 平级，Date|Close，带 # 元数据头部）

数据源：腾讯日线（国证2000 sz399303、中证全指 sh000985）、中证指数官网（中证2000 932000）。
用法：
    python3 Compare.py                 # 拉取基准并生成图表（失败时用缓存）
    python3 Compare.py --no-fetch      # 只用本地缓存
"""

import argparse
import csv
import datetime
import json
import math
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "Core"))

import Sources as sources  # noqa: E402
import Mvsv  # noqa: E402
import Universe as universe  # noqa: E402

BASE_DATE = "2004-12-31"      # 基日（中证全指基日）
ALL_START = "2004-12-31"      # 全时段图起点
CSI2000_START = "2023-08-11"  # 中证2000 发布日
BASE_POINT = 1000.0

# 三只自建指数（实线）
INDICES = [
    ("CNAMCSDEWI", "CNAMCSDEWI 日频", "#1f77b4"),
    ("CNAMCSWEWI", "CNAMCSWEWI 周频", "#ff7f0e"),
    ("CNAMCSMEWI", "CNAMCSMEWI 月频", "#2ca02c"),
]
# 三个业绩比较基准（虚线）
BENCHMARKS = [
    {"key": "GZ2000", "label": "国证2000", "color": "#d62728",
     "kind": "sina", "symbol": "sz399303"},          # 新浪：2014-03 起
    {"key": "CSI2000", "label": "中证2000", "color": "#9467bd",
     "kind": "csindex", "code": "932000", "since": CSI2000_START},  # 发布日 2023-08-11 起
    {"key": "CSIAllShare", "label": "中证全指", "color": "#17becf",
     "kind": "csindex", "code": "000985"},           # 官网：2013 起
]


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
def parseDate(value):
    """``20250102`` / ``2025-01-02`` → ``2025-01-02``。"""
    return universe.normalizeDate(value)


def fetchTencent(symbol, begin, end):
    """腾讯日线（前复权，指数无差别）：返回 [(date, close)]。"""
    url = ("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param="
           f"{symbol},day,{begin},{end},1000,qfq")
    text = sources.httpGet(url, {"Referer": "https://finance.qq.com/"})
    node = (json.loads(text) or {}).get("data", {}).get(symbol, {}) or {}
    rows = node.get("qfqday") or node.get("day") or []
    points = []
    for row in rows:
        if len(row) < 3:
            continue
        points.append((parseDate(row[0]), float(row[2])))
    return points


def fetchSina(symbol, count=4000):
    """新浪日线（长历史）：返回 [(date, close)]。"""
    url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           "CN_MarketData.getKLineData"
           f"?symbol={symbol}&scale=240&ma=no&datalen={count}")
    text = sources.httpGet(url, {"Referer": "https://finance.sina.com.cn/"})
    rows = json.loads(text) or []
    return [(parseDate(row["day"]), float(row["close"])) for row in rows
            if row.get("day") and row.get("close")]


def fetchCsindex(code, begin, end):
    """中证指数官网：返回 [(date, close)]（中证2000 等）。"""
    url = ("https://www.csindex.com.cn/csindex-home/perf/index-perf"
           f"?indexCode={code}&startDate={begin.replace('-', '')}&endDate={end.replace('-', '')}")
    text = sources.httpGet(url, {"Referer": "https://www.csindex.com.cn/"})
    rows = (json.loads(text) or {}).get("data") or []
    points = {}
    for row in rows:
        day = parseDate(row.get("tradeDate"))
        close = row.get("close")
        if day and close:
            points[day] = float(close)   # 官网偶有非交易日重复行，按日期去重
    return sorted(points.items())


def readBenchmark(path):
    """读 .mvsv 基准缓存：跳过 ``#`` 元数据与空行，数据行为 ``yyyyMMdd|close``。"""
    points = []
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 2:
                continue
            day, close = parts[0], parts[1]
            points.append((f"{day[:4]}-{day[4:6]}-{day[6:]}", float(close)))
    return points


def writeBenchmark(points, path, spec):
    """写 .mvsv 基准缓存：``# Key : Value`` 头部 + 空行 + ``yyyyMMdd|close``。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    code = str(spec.get("code") or spec.get("symbol") or "")
    meta = [
        ("标题", f"{spec.get('label', '基准')} 日线收盘价"),
        ("字段名称", '"日期|收盘价"'),
        ("DataProvider", spec.get("provider", "CSIndex/Sina/Tencent")),
        ("Field", '"Date|Close"'),
        ("FieldName", '"Date|Close"'),
        ("FieldType", '"Int|Decimal"'),
        ("TypeKLine", "Day"),
        ("Count", str(len(points))),
        ("Symbol", code),
        ("SecuCode", code),
        ("USC", code),
        ("Name", f'"{spec.get("label", "")}"'),
        ("Region", "CN"),
        ("Market", "CSI"),
        ("TimeZone", "Asia/Shanghai"),
        ("Currency", "CNY"),
    ]
    with open(path, "w", encoding="utf-8") as file:
        file.write("".join(f"# {key} : {value}\n" for key, value in meta) + "\n")
        for day, close in points:
            file.write(f"{day.replace('-', '')}|{close}\n")


def loadBenchmark(spec, dataDir, begin, end, allowFetch=True):
    """优先实时拉取，失败回退本地缓存（Benchmarks/<Key>.mvsv）。"""
    # 缓存与 Index/ 平级：<仓库根>/Benchmarks/<windCode>.mvsv
    cachePath = os.path.join(os.path.dirname(dataDir), "Benchmarks", f"{spec['key']}.mvsv")
    points = []
    if allowFetch:
        try:
            if spec["kind"] == "tencent":
                points = fetchTencent(spec["symbol"], begin, end)
            elif spec["kind"] == "sina":
                points = fetchSina(spec["symbol"])
            else:
                points = fetchCsindex(spec["code"], begin, end)
            points = [(day, close) for day, close in points if begin <= day <= end]
            if points:
                writeBenchmark(points, cachePath, spec)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {spec['label']} 拉取失败：{exc}", file=sys.stderr)
    if not points and os.path.exists(cachePath):
        points = readBenchmark(cachePath)
    since = spec.get("since")
    if since:
        points = [(day, close) for day, close in points if day >= since]
    return points


def loadIndexNav(code, dataDir):
    """读取 Index/<代码>/Nav.mvsv 的 (date, point)。"""
    path = os.path.join(dataDir, code, "Nav.mvsv")
    if not os.path.exists(path):
        print(f"[warn] 缺少 {path}，跳过 {code}", file=sys.stderr)
        return []
    return [(row["date"], float(row["point"])) for row in Mvsv.read(path, ["date", "point"])
            if row.get("date") and row.get("point")]


def normalize(points, baseDate=BASE_DATE, basePoint=BASE_POINT):
    """以基期收盘归一为 ``basePoint``（基准指数用；CNAMCI 点位已是 1000 基点）。"""
    base = next((close for day, close in points if day >= baseDate), None)
    if not base:
        return []
    return [(day, close / base * basePoint) for day, close in points]


# --------------------------------------------------------------------------- #
# 统计与渲染
# --------------------------------------------------------------------------- #
def sliceWindow(points, start, anchor=None):
    """截取窗口并按 ``anchor``（缺省为窗口首点）归一为 1000。"""
    rows = [(day, value) for day, value in points if day >= start]
    if not rows:
        return []
    baseDay, baseValue = rows[0]
    if anchor:
        hit = [(day, value) for day, value in rows if day >= anchor]
        if hit:
            baseDay, baseValue = hit[0]
    if not baseValue:
        return []
    return [(day, round(value / baseValue * BASE_POINT, 6)) for day, value in rows]


def summarize(points):
    """末值、区间收益（相对窗口首值）、年化、最大回撤。"""
    if not points:
        return {"begin": None, "last": None, "return": None, "annualized": None,
                "max_drawdown": None}
    values = [value for _, value in points]
    last, base = values[-1], values[0]
    total = last / base - 1.0 if base else 0.0
    days = max(len(values), 1)
    annualized = (last / base) ** (252.0 / days) - 1.0 if base > 0 and last > 0 else 0.0
    peak, drawdown = -1e18, 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            drawdown = min(drawdown, value / peak - 1.0)
    return {"begin": points[0][0], "last": round(last, 4), "return": round(total, 8),
            "annualized": round(annualized, 8), "max_drawdown": round(drawdown, 8)}


def _nextMonth(day):
    """返回下个月 1 日（用于季度刻度迭代）。"""
    month, year = day.month + 1, day.year
    if month > 12:
        month, year = 1, year + 1
    return day.replace(year=year, month=month, day=1)


def drawdown(points):
    """逐日回撤（相对历史最高净值，≤ 0）。"""
    peak, out = -1e18, []
    for day, value in points:
        peak = max(peak, value)
        out.append((day, round(value / peak - 1.0, 6) if peak > 0 else 0.0))
    return out


TICK_MIN_PX = 22          # 相邻 X 轴月份标签的最小水平间距
YEAR_TICK_MIN_PX = 44     # 相邻年份标签的最小水平间距（长跨度时自动隔年标注）


def niceStep(span, maxTicks, floorStep=0.0):
    """挑选 1/2/2.5/5×10^k 的“整齐”步长，使刻度数不超过 ``maxTicks``。"""
    count = max(1, int(maxTicks))
    raw = max(span / count, floorStep, 1e-12)
    exponent = math.floor(math.log10(raw) + 1e-9)
    for mult in (1.0, 2.0, 2.5, 5.0, 10.0):
        step = mult * 10.0 ** exponent
        if step >= raw - 1e-12:
            return step
    return 10.0 ** (exponent + 1)


def thinTicks(ticks, width, minPx):
    """按最小像素间距等间隔抽稀刻度（至少保留 1 个）。"""
    if not ticks:
        return []
    limit = max(1, int(width / max(minPx, 1.0)))
    stride = max(1, math.ceil(len(ticks) / limit))
    return ticks[::stride]


def buildYearTicks(begin, end, xMap, left, right):
    """每年 1 月 1 日一个年份分界点（裁剪到 [left, right]），用于画逐年分界竖线。"""
    beginDate = datetime.date.fromisoformat(begin)
    endDate = datetime.date.fromisoformat(end)
    return [(year, max(left, min(right, xMap(datetime.date(year, 1, 1).isoformat()))))
            for year in range(beginDate.year, endDate.year + 1)]


def thinYearTicks(marks, width, left, minPx=YEAR_TICK_MIN_PX):
    """年份标签抽稀（分界竖线仍每年一条）：首年贴左边界且与次年太近时略去首年。"""
    marks = list(marks)
    if len(marks) > 1 and marks[0][1] < left + 24 and marks[1][1] - marks[0][1] < minPx * 1.4:
        marks.pop(0)
    return thinTicks(marks, width, minPx)


def renderSvg(series, meta, scale=2, baseWidth=1180, mainHeight=640):
    """生成 SVG：上方 6 条净值曲线，下方 6 个时间轴对齐的回撤子图。

    画布按 ``scale`` 放大（默认 2 倍），内容用 ``<g transform="scale()">`` 等比缩放。
    所有曲线均为实线、以颜色区分；回撤子图与主图共用同一时间轴（x 映射一致）。
    series: [{"label","color","points":[(date, value)]}]
    """
    width, height = baseWidth, mainHeight
    left, right, top, bottom = 78, width - 250, 66, height - 92
    ordinals = [datetime.date.fromisoformat(day).toordinal()
                for item in series for day, _ in item["points"]]
    values = [value for item in series for _, value in item["points"]]
    xMin, xMax = min(ordinals), max(ordinals)
    yMin, yMax = min(values), max(values)
    pad = (yMax - yMin) * 0.08 or 50.0
    yMin, yMax = yMin - pad, yMax + pad

    def xMap(day):
        return left + (datetime.date.fromisoformat(day).toordinal() - xMin) / (xMax - xMin) * (right - left)

    def yMap(value):
        return bottom - (value - yMin) / (yMax - yMin) * (bottom - top)

    # Y 轴：按可用高度自适应（约 34px 一个刻度），取整齐步长
    maxYTicks = max(2, int((bottom - top) / 34))
    yStep = niceStep(yMax - yMin, maxYTicks)
    yDigits = 0 if abs(yStep - round(yStep)) < 1e-9 else 2
    yTicks = []
    tick = math.ceil(yMin / yStep - 1e-9) * yStep
    while tick <= yMax + 1e-9:
        yTicks.append(round(tick, 10))
        tick += yStep

    # X 轴刻度：先按季度（1/4/7/10 月）取候选，再按可用宽度抽稀，避免标签重叠
    xCandidates = []
    cursor = datetime.date.fromisoformat(meta["begin"]).replace(day=1)
    while cursor.month not in (1, 4, 7, 10):
        cursor = _nextMonth(cursor)
    while cursor.toordinal() <= xMax:
        xCandidates.append((cursor.isoformat(), cursor.month, cursor.year))
        cursor = _nextMonth(cursor)
        while cursor.month not in (1, 4, 7, 10):
            cursor = _nextMonth(cursor)
    xTicks = thinTicks(xCandidates, right - left, TICK_MIN_PX)
    # 年份较多的图：季度刻度只画淡线不写月份文字（写满会很密），空格留白即可
    compactX = len(xTicks) < len(xCandidates)
    gridTicks = xCandidates if compactX else xTicks
    # 分界竖线逐年一条（每年 1 月 1 日），年份文字再按宽度抽稀
    yearLines = buildYearTicks(meta["begin"], meta["as_of"], xMap, left, right)
    yearTicks = thinYearTicks(yearLines, right - left, left)

    # 回撤子图布局
    ddHeight, ddGap = 112, 16
    ddStart = height + 18
    canvasHeight = ddStart + len(series) * (ddHeight + ddGap) + 44
    canvasWidth = int(width * scale)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvasWidth}" height="{int(canvasHeight * scale)}" '
        f'viewBox="0 0 {canvasWidth} {int(canvasHeight * scale)}" '
        f'font-family="Segoe UI, Microsoft YaHei, sans-serif">',
        f'<rect width="{canvasWidth}" height="{int(canvasHeight * scale)}" fill="#ffffff"/>',
        f'<g transform="scale({scale})">',
        f'<text x="{left}" y="34" font-size="20" fill="#1f2937" font-weight="600">'
        f'{meta["title"]}</text>',
        f'<text x="{left}" y="56" font-size="12" fill="#6b7280">'
        f'起点 {meta["begin"]}；截至 {meta["as_of"]}；各序列以自身数据起点归一为 '
        f'{int(BASE_POINT)}；下方为逐日回撤</text>',
    ]
    # 主图：水平网格 + Y 刻度
    for tick in yTicks:
        y = yMap(tick)
        parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" stroke="#e5e7eb"/>')
        parts.append(f'<text x="{left - 10}" y="{y + 4:.2f}" font-size="11" fill="#6b7280" '
                     f'text-anchor="end">{tick:,.{yDigits}f}</text>')
    # 主图：季度网格（每年年初 / 3 / 6 / 9 月底四个点）；年份少时才写月份文字
    for day, _month, _year in gridTicks:
        x = xMap(day)
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="#f3f4f6"/>')
    if not compactX:
        for day, month, _year in xTicks:
            x = xMap(day)
            parts.append(f'<text x="{x:.2f}" y="{bottom + 18}" font-size="11" fill="#6b7280" '
                         f'text-anchor="middle">{month:02d}</text>')
    yearLabelY = bottom + (20 if compactX else 38)
    # 年份分界线：每年一条（上一年年末与下一年年初之间），比季度线更明显
    for _year, x in yearLines:
        if x <= left + 0.5:            # 起点年 1 月 1 日在绘图区外，不重复画在 Y 轴上
            continue
        parts.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{bottom}" stroke="#cbd5e1"/>')
    # 年份文字：按可用宽度抽稀（长跨度隔年标注）
    for year, x in yearTicks:
        anchor = "start" if x < left + 24 else "middle"
        parts.append(f'<text x="{x + (2 if anchor == "start" else 0):.2f}" y="{yearLabelY}" '
                     f'font-size="12" fill="#374151" text-anchor="{anchor}" '
                     f'font-weight="600">{year}</text>')
    # 主图：坐标轴
    parts.append(f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#9ca3af"/>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#9ca3af"/>')

    # 主图：净值曲线（全部实线）
    for item in series:
        points = item["points"]
        if len(points) > 700:
            stride = math.ceil(len(points) / 700)
            points = points[::stride] + [points[-1]]
        path = " ".join(f"{'M' if i == 0 else 'L'}{xMap(day):.2f},{yMap(value):.2f}"
                        for i, (day, value) in enumerate(points))
        parts.append(f'<path class="series" d="{path}" fill="none" stroke="{item["color"]}" '
                     f'stroke-width="1" stroke-linejoin="round">'
                     f'<title>{item["label"]}</title></path>')

    # 图例（左上角，半透明白底）：与 Y 轴（left）留出间隙，避免贴边遮住刻度值
    legendGap = 16
    legendX, legendY = left + legendGap + 10, top + 16
    legendW, legendH = 214, len(series) * 30 + 12
    parts.append(f'<rect x="{legendX - 10}" y="{legendY - 16}" width="{legendW}" '
                 f'height="{legendH}" rx="6" fill="#ffffff" fill-opacity="0.95" '
                 f'stroke="#e5e7eb"/>')
    for i, item in enumerate(series):
        y = legendY + i * 30
        parts.append(f'<line x1="{legendX}" y1="{y}" x2="{legendX + 26}" y2="{y}" '
                     f'stroke="{item["color"]}" stroke-width="1.2"/>')
        parts.append(f'<text x="{legendX + 34}" y="{y + 4}" font-size="12" fill="#374151">'
                     f'{item["label"]}</text>')
        parts.append(f'<text x="{legendX + legendW - 22}" y="{y + 4}" font-size="12" '
                     f'fill="#111827" text-anchor="end" font-weight="600">'
                     f'{item["points"][-1][1]:.1f}</text>')

    # 回撤子图：与主图共用时间轴；6 个面板共用统一 Y 轴深度（便于横向比较）
    ddSeries = [(item, drawdown(item["points"])) for item in series]
    depth = max(max(abs(min(value for _, value in dd)) for _, dd in ddSeries), 0.05)
    depth = round(math.ceil(depth / 0.05) * 0.05, 4)
    # 回撤子图的高度有限，横线数量同样自适应（约 14px 一条）
    ddStep = niceStep(depth, max(2, int(ddHeight / 14)), floorStep=0.01)
    for index, (item, dd) in enumerate(ddSeries):
        worst = min(value for _, value in dd)
        current = dd[-1][1]          # 最新交易日的回撤（相对历史峰值）
        pTop = ddStart + index * (ddHeight + ddGap)
        pBottom = pTop + ddHeight

        def ddY(value, _top=pTop, _depth=depth):
            return _top + (abs(value) / _depth) * ddHeight

        parts.append(f'<text x="{left}" y="{pTop - 5}" font-size="12" fill="#111827" '
                     f'font-weight="600">{item["label"]} 回撤</text>')
        # 右侧：最大回撤 + 当前回撤（当前值用系列颜色加粗，便于横向对比）
        parts.append(f'<text x="{right}" y="{pTop - 5}" font-size="11" text-anchor="end">'
                     f'<tspan fill="#6b7280">最大回撤 {worst:.2%}</tspan>'
                     f'<tspan dx="12" fill="{item["color"]}" font-weight="600">'
                     f'当前回撤 {current:.2%}</tspan></text>')
        # 水平网格 + 百分比刻度
        for levelIndex in range(int(depth / ddStep + 1e-9) + 1):
            level = levelIndex * ddStep
            y = pTop + level / depth * ddHeight
            parts.append(f'<line x1="{left}" y1="{y:.2f}" x2="{right}" y2="{y:.2f}" '
                         f'stroke="#f3f4f6"/>')
            label = f"{-level:.0%}" if level else "0%"
            parts.append(f'<text x="{left - 10}" y="{y + 4:.2f}" font-size="10" '
                         f'fill="#9ca3af" text-anchor="end">{label}</text>')
        # 季度竖线（与主图对齐，每年四个季度分界点）
        for day, _month, _year in gridTicks:
            x = xMap(day)
            parts.append(f'<line x1="{x:.2f}" y1="{pTop}" x2="{x:.2f}" y2="{pBottom}" '
                         f'stroke="#f9fafb"/>')
        # 零线与边框
        parts.append(f'<line x1="{left}" y1="{pTop}" x2="{right}" y2="{pTop}" stroke="#d1d5db"/>')
        parts.append(f'<line x1="{left}" y1="{pBottom}" x2="{right}" y2="{pBottom}" stroke="#e5e7eb"/>')
        # 回撤面积 + 折线
        coords = [(xMap(day), ddY(value)) for day, value in dd]
        line = " ".join(f"{'M' if i == 0 else 'L'}{x:.2f},{y:.2f}"
                        for i, (x, y) in enumerate(coords))
        area = (f"M{coords[0][0]:.2f},{pTop:.2f} " +
                " ".join(f"L{x:.2f},{y:.2f}" for x, y in coords) +
                f" L{coords[-1][0]:.2f},{pTop:.2f} Z")
        parts.append(f'<path class="drawdown" d="{area}" fill="{item["color"]}" '
                     f'fill-opacity="0.16" stroke="none"/>')
        parts.append(f'<path class="drawdown-line" d="{line}" fill="none" '
                     f'stroke="{item["color"]}" stroke-width="1"/>')

    # 底部时间轴（与主图一致：年份少时月份一行、年份一行，年份多时只留年份）
    axisTop = canvasHeight - 42
    if not compactX:
        for day, month, _year in xTicks:
            x = xMap(day)
            parts.append(f'<text x="{x:.2f}" y="{axisTop + 16}" font-size="11" fill="#6b7280" '
                         f'text-anchor="middle">{month:02d}</text>')
    axisYearY = axisTop + (16 if compactX else 34)
    for year, x in yearTicks:
        anchor = "start" if x < left + 24 else "middle"
        parts.append(f'<text x="{x + (2 if anchor == "start" else 0):.2f}" y="{axisYearY}" '
                     f'font-size="12" fill="#374151" text-anchor="{anchor}" '
                     f'font-weight="600">{year}</text>')
    parts.append("</g>")
    parts.append("</svg>")
    return "\n".join(parts)


def renderMarkdown(windowStats):
    """四个时间窗口的统计表。"""
    lines = ["# 中证微盘股基线指数 vs 业绩比较基准", ""]
    for data in windowStats.values():
        lines += [f"## {data['title']}（{data['begin']} ~ {data['end']}）", "",
                  "| 指数 | 起点 | 末值 | 区间收益 | 年化 | 最大回撤 |",
                  "| --- | --- | ---: | ---: | ---: | ---: |"]
        for row in data["rows"]:
            if row["last"] is None:
                lines.append(f"| {row['label']} | - | - | - | - | - |")
                continue
            lines.append(f"| {row['label']} | {row['begin']} | {row['last']:.2f} | "
                         f"{row['return']:+.2%} | {row['annualized']:+.2%} | "
                         f"{row['max_drawdown']:.2%} |")
        lines.append("")
    lines.append("说明：各序列以自身数据起点归一为 1000；中证全指基日 2004-12-31（基日点位 1000），"
                 "CNAMCI 受本地面板数据限制自 2009-06-01 起算，中证2000 自 2023-08-11 发布日起。")
    lines.append("")
    return "\n".join(lines)


def buildWindows(end):
    """六个时间窗口：全时段 / 最近十年 / 五年 / 三年 / 一年 / 中证2000 发布以来。"""
    endDate = datetime.date.fromisoformat(end)

    def yearsAgo(n):
        try:
            return datetime.date(endDate.year - n, endDate.month, endDate.day).isoformat()
        except ValueError:                       # 2/29
            return datetime.date(endDate.year - n, endDate.month, 28).isoformat()

    return [
        {"key": "All", "title": "全时段", "start": ALL_START, "anchor": None},
        {"key": "10Y", "title": "最近十年", "start": yearsAgo(10), "anchor": None},
        {"key": "5Y", "title": "最近五年", "start": yearsAgo(5), "anchor": None},
        {"key": "3Y", "title": "最近三年", "start": yearsAgo(3), "anchor": None},
        {"key": "1Y", "title": "最近一年", "start": yearsAgo(1), "anchor": None},
        {"key": "CSI2000", "title": "中证2000 发布以来", "start": CSI2000_START, "anchor": None},
    ]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="CNAMCI 与基准指数走势对比（六张窗口图）")
    parser.add_argument("--data-dir", default=os.path.join(here, "Index"))
    parser.add_argument("--statistic-dir", default=os.path.join(here, "Statistic"))
    parser.add_argument("--begin", default=ALL_START)
    parser.add_argument("--end", default=datetime.date.today().isoformat())
    parser.add_argument("--no-fetch", action="store_true")
    args = parser.parse_args()
    dataDir = os.path.abspath(args.data_dir)
    statDir = os.path.abspath(args.statistic_dir)
    os.makedirs(statDir, exist_ok=True)

    series = []
    for code, label, color in INDICES:
        points = loadIndexNav(code, dataDir)
        if points:
            series.append({"label": label, "color": color, "points": points})
    for spec in BENCHMARKS:
        raw = loadBenchmark(spec, dataDir, args.begin, args.end, allowFetch=not args.no_fetch)
        points = normalize(raw)
        if points:
            series.append({"label": spec["label"], "color": spec["color"], "points": points})
    if len(series) < 2:
        print("[error] 有效序列不足，无法生成图表", file=sys.stderr)
        return 1

    end = max(item["points"][-1][0] for item in series)
    outputs, windowStats = [], {}
    for window in buildWindows(end):
        winSeries = []
        for item in series:
            points = sliceWindow(item["points"], window["start"], window.get("anchor"))
            if len(points) >= 2:
                winSeries.append({"label": item["label"], "color": item["color"],
                                  "points": points})
        if len(winSeries) < 2:
            continue
        begin = min(item["points"][0][0] for item in winSeries)
        meta = {"title": f"中证微盘股基线指数 vs 业绩比较基准（{window['title']}）",
                "begin": begin, "as_of": end, "window": window["title"]}
        svg = renderSvg(winSeries, meta)
        name = f"Comparison-{window['key']}.svg"
        with open(os.path.join(statDir, name), "w", encoding="utf-8") as file:
            file.write(svg)
        if window["key"] == "All":               # 兼容旧路径
            with open(os.path.join(statDir, "Comparison.svg"), "w", encoding="utf-8") as file:
                file.write(svg)
        outputs.append(name)
        windowStats[window["key"]] = {
            "title": window["title"], "begin": begin, "end": end,
            "rows": [{"label": item["label"], **summarize(item["points"])}
                     for item in winSeries]}

    with open(os.path.join(statDir, "Comparison.md"), "w", encoding="utf-8") as file:
        file.write(renderMarkdown(windowStats))
    with open(os.path.join(statDir, "Comparison.json"), "w", encoding="utf-8") as file:
        json.dump({"base_date": BASE_DATE, "base_point": BASE_POINT, "as_of": end,
                   "windows": windowStats}, file, ensure_ascii=False, indent=1)
        file.write("\n")

    print(json.dumps({"ok": True, "as_of": end, "series": len(series),
                      "windows": list(windowStats), "outputs": outputs},
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
