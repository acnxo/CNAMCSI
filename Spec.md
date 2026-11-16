> 编制方案（逐条规则）：[`Methodology.md`](Methodology.md)

# CNAMCI 指数规则说明书（SPEC）

> 适用：本仓库 **CNAMCI**（仓库根目录）的每日指数计算，GitHub Actions 北京时间 15:05 触发。
> **编制规则（选样/加权/调仓/费用/数据口径）以 [`Methodology.md`](Methodology.md) 为准**；
> 本文件只定义**输出契约、运行约定与验收标准**，两者冲突时以 Methodology.md 为准。
>
> 变更规则：先改本文件，再改代码，并在文末「变更记录」登记。

## 1. 指数标识

三只指数共用同一选样口径，仅调仓频率不同：

| 代码 | 名称 | 调仓频率 | 工作流 |
| --- | --- | --- | --- |
| `CNAMCSDEWI` | 中证微盘股日频等权指数 | 每个交易日 | `.github/workflows/CNAMCSDEWI.yml` |
| `CNAMCSWEWI` | 中证微盘股周频等权指数 | 每周最后一个交易日 | `.github/workflows/CNAMCSWEWI.yml` |
| `CNAMCSMEWI` | 中证微盘股月频等权指数 | 每月最后一个交易日 | `.github/workflows/CNAMCSMEWI.yml` |

业绩比较基准（同图展示，各序列以自身数据起点归一为 1000）：

| 基准 | 代码 | 数据源 | 调样频率 | 说明 |
| --- | --- | --- | --- | --- |
| 国证2000 | 399303 | 腾讯日线 | 半年（6/12 月） | 发布较早，可覆盖更长的比较区间 |
| 中证2000 | 932000 | 中证指数官网 | 半年（6/12 月）+ 新股快速纳入 | 2023-08-11 发布，以其发布日为基期 |
| 中证全指 | 000985 | 腾讯日线 | 半年（6/12 月）+ 新股快速纳入 | 全市场宽基参照 |

> 基准均按「自由流通市值加权、半年定期调样」编制，与本体系的「400 只小市值等权、日/周/月调仓」
> 在成分数量、加权方式、调仓频率上均不同；对比仅用于业绩参照，不做同口径归因。

### 备选业绩参考（未接入同图）

| 参考 | 代码 | 数据源 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 万得微盘股指数 | `868008.WI` | Wind 官网 `index_api` | 已入库 60 条（20260814~20261113） | `Benchmarks/868008.WI.mvsv`；接口只返回最近 60 个交易日 |
| 万得微盘股日频等权指数 | `8841431.WI` | Wind 官网 `index_api` | 已入库 60 条（20260814~20261113） | `Benchmarks/8841431.WI.mvsv`；与本体系日频等权指数可比性最强 |
| 东方财富 微盘股板块 | `90.BK1158` | 东方财富 | 可参考 | <https://quote.eastmoney.com/bk/90.BK1158.html>；东财自编概念板块（非万得），仅作走势参考 |

> 备选参考的定位：`868008.WI` / `8841431.WI` 是权威对标；`90.BK1158` 只是东财「微盘股」概念板块，
> 成分与编制规则与万得不同，不能当作万得微盘股指数。

### 万得官网指数接口（`index_api`）

```
GET https://index_api.wind.com.cn/indexofficialwebsite/Kline?indexId=<ID>&lan=cn
```

| 指数 | windCode | indexId（URL 编码前） |
| --- | --- | --- |
| 万得微盘股指数 | `868008.WI` | `tShUKnLO4cLQ8v+TnUd2uQ==` |
| 万得微盘股日频等权指数 | `8841431.WI` | `2O5u8vTCa4KiLTLQ7DT/9w==` |

- 返回 `Result[]`：`windCode / tradeDate(毫秒) / open / hight / low / close / volume / amount / pctChange / swing / shortName`
- **只返回最近 60 个交易日**（`startDate`/`endDate`/`count`/`limit`/`page` 等 30+ 种参数均无效，POST 405）
- 可作日更增量；全历史（2004 起）需 Wind 终端导出，导出后转 `Benchmarks/<windCode>.mvsv`
- 抓取脚本：`Tool/FetchWindIndex.py`（零依赖，按 `Date` 合并去重）
- 定时任务：`.github/workflows/WindIndex.yml`（交易日 15:45 北京时间自动运行并提交）

| 项目 | 取值 |
| --- | --- |
| 指数代码 | `CNAMCSDEWI` / `CNAMCSWEWI` / `CNAMCSMEWI` |
| 英文名 | China A-share Micro Cap Index |
| 中文名 | 见上表（日频/周频/月频等权指数） |
| 成分数量 | 400 |
| 加权方式 | 等权（1/400） |
| 调样频率 | 调仓日：日频=每天，周频=每周最后一个交易日，月频=每月最后一个交易日 |
| 基点 / 基日 | **1000 点 / 2004-12-31**（`Config.yaml` 的 `BasePoint` / `BaseDate`） |
| 计算公式 | 等权价格指数（逐条规则见 [`Methodology.md`](Methodology.md) §5） |
| 数据区间 | 2004-12-31 ~ 最新交易日（5,310 个交易日） |

## 2. 成分与权重（实现：`Core/Universe.py`）

1. 股票池：沪深主板（600/601/603/605/000/001/002/003）、非 ST/*ST/退/PT、当日有成交、上市满 60 个交易日、收盘价与总市值有效；
2. 按**当日总市值升序**取前 400 只（同市值按代码升序，稳定排序）；
3. 权重 `w_i = 1/N`，保留 8 位小数，误差补到最后一只。

数据来源：腾讯批量快照（`qt.gtimg.cn`，900 只/请求，约 4 次）+ 交易所/巨潮代码清单（周级缓存）
+ 东财上证指数日线（交易日历）。实现见 `Core/Sources.py`。

## 3. 指数点位算法

设 `t` 为交易日，`P_i,t` 为不复权收盘价，`w_i,t-1` 为**上一交易日收盘确定**的权重：

```
ret_t = Σ_i w_i,t-1 × (P_i,t / P_i,t-1 − 1) / Σ_i w_i,t-1
point_t = point_{t-1} × (1 + ret_t)
```

约定：

| 情形 | 处理 |
| --- | --- |
| 首次运行（无上一期） | `point = base_point`（1000），`ret = 0`；若基日（2004-12-31）早于首个交易日，`Nav.mvsv` 自动补一行基日净值 |
| 历史初始化 | `--replay-start 2004-12-31` 用回放数据逐日重算（2004-12-31 起，5,310 个交易日），当年写 `Pending/<年>/`、历史年份写 `Archive/<年>/`（均为 `.mvsv`） |
| 成分股当日停牌（快照无记录或成交量 0） | `P_i,t = P_i,t-1`，收益记 0，权重保留 |
| 成分股退市/长期无数据 | 同上，价格沿用最后收盘（等价于停牌） |
| 上一期权重合计 ≠ 1（异常数据） | 按 `Σw` 归一后再计算 |
| 非交易日触发 | 数据日期 ≠ 今日 → 不计算、不写盘（退出码 0，标记 skipped） |

说明：指数用**昨日成分**计算今日收益（避免用今日收盘才知道的名单去算今日收益），
今日收盘重排出的名单用于**下一交易日**的指数与调仓。

## 4. 调仓日判定与权重漂移

**调仓日**（实现：`Index.isRebalanceDay`）：

| 频率 | 判定 |
| --- | --- |
| `daily` | 每个交易日 |
| `weekly` | 当日之后没有同一 ISO 周的交易日（回放用日历精确判断；线上日历只到当日，按自然日近似到周五） |
| `monthly` | 当日之后没有同一自然月的交易日（线上近似到月末最后一个工作日） |

**权重**：

- 调仓日收盘：重算 400 只并重置为等权 `1/N`；
- 非调仓日：成分不变，权重随价格漂移（买入并持有到下次调仓）：

```
w_i,t = w_i,t-1 × (1 + r_i,t) / (1 + ret_t)
```

- 停牌 / 无行情按价格不变处理，权重经归一化自然变化；
- 组合（持仓）只在调仓日生成新的调仓计划，非调仓日不交易；上一调仓日的计划仍按 T+1 开盘回填。

### 4.1 动态规则的适用性（决策记录）

宽基剔除（`ExcludeTop`）与 20 日流动性过滤（`MinAmount20` / `Amount20Pct`）**暂不启用**：

- 中证2000 于 2023-08-11 发布、中证A500 于 2024-09 发布，2004–2023 年对应指数不存在，无法回溯剔除；
- 早期合格样本远少于现在，取 400 只已是较大比例：

| 日期 | 合格样本 | 400 只占比 | 第 400 名市值 |
| --- | ---: | ---: | ---: |
| 2005-01-04 | 1,083 | 36.9% | 17.0 亿 |
| 2006-01-04 | 959 | 41.7% | 15.8 亿 |
| 2008-01-02 | 1,132 | 35.3% | 61.9 亿 |
| 2015-01-05 | 1,697 | 23.6% | 48.5 亿 |
| 2026-11-13 | 3,043 | 13.1% | 28.7 亿 |

- 若在早期强行叠加"剔除宽基 + 流动性门槛"，候选会不足 400 且引入政策时代的外推误差；
- 因此历史回放只用一套跨期一致的规则：沪深主板、非 ST、上市满 60 交易日、有成交 → 总市值最小 400 只、等权；
- 后续如需按时代启用，可新增 `RulesByEra`（例如自 2023-08-11 起剔除中证2000 成分），不影响历史段。

## 5. 运行时间与数据就绪

| 项目 | 约定 |
| --- | --- |
| A 股收盘 | 15:00（Asia/Shanghai） |
| 触发时间 | 15:05（GitHub Actions cron `5 7 * * 1-5`，UTC） |
| 实际延迟 | GitHub 官方说明：cron 在高峰期可能延迟数分钟到十几分钟，**不保证准点** |
| 就绪校验 | 用 1 次请求查上证指数快照 `qt.gtimg.cn/q=sh000001`，要求 `f30` 时间戳 **日期 = 今日** 且 **时间 ≥ 15:00:00** |
| 等待策略 | 未就绪则每 60 s 重试，最多 `--max-wait-min`（默认 40 分钟，到 15:45） |
| 超时处理 | 视为非交易日/数据源异常：不写盘，输出 `skipped` 并退出码 0（避免 Actions 红叉） |
| 运行参数 | **无需任何命令行参数**：Actions 直接执行 `python Index.py`，参数全部来自 `Config.yaml` 与内置默认值 |
| 交易日历 | 东财上证指数日线；最后交易日 ≠ 今日 时同样视为非交易日 |

运行日判定以**接口返回的数据日期**为准，不以 runner 的本地日期为准（runner 为 UTC）。

## 6. 组合模拟（实现：`Core/Portfolio.py`）

| 参数 | 默认 |
| --- | --- |
| 初始资金 | 4,000,000 元 |
| 调仓模式 | `changes`（只处理成分进出；`index` 仅指数不模拟资金；`full` 每日完全等权） |
| 成交价 | 信号日次一交易日开盘价（15:05 运行时回填**昨日**计划用今日开盘价） |
| 成交单位 | 100 股整数倍，向下取整 |
| 费用 | 佣金万 1 最低 5 元 + 过户费 0.001% + 印花税卖出 0.05%（2023-08-28 前 0.1%） |
| 现金约束 | 先卖后买，买入金额 + 费用 ≤ 现金，不融资 |

## 7. 输出契约

提交回仓库的目录 `Index/<代码>/`（每只指数独立；数据文件为 `.mvsv`，字段名沿用 snake_case 契约）：

| 文件 | 内容 |
| --- | --- |
| `Index.json` | 最新指数摘要（点位、日收益、累计收益、成分数、边界市值、请求数） |
| `Nav.mvsv` | 指数序列：`date,point,ret,cum_return,holdings_count,total_asset,cash` |
| `State.json` | 指数状态：`base_date/base_point/index{date,point,ret,components}` |
| `Portfolio.json` | 组合状态：`cash/holdings/pending`（次日开盘回填用） |
| `Latest/Components.mvsv` | `rank,code,name,close,total_mv,float_mv,weight,listed_days` |
| `Latest/Holdings.mvsv` | `code,name,shares,cost,close,market_value,weight,unrealized_pnl` |
| `Latest/Plan.mvsv` | `side,code,name,shares,ref_price,est_amount,est_fee,reason` |
| `Latest/Execution.mvsv` | 当日开盘回填的成交（`signal_date,exec_date,...`） |
| `Latest/Report.md` | 人读摘要（点位、涨跌、明日买卖清单） |
| `Latest/Run.log` | 本次运行日志（每次覆盖） |
| `Archive/<年>/*.mvsv` | L1 已归档（只读，跨年冻结） |
| `Pending/<年>/*.mvsv` | L2 待归档（当年可修正，次年 3 月 1 日转 L1） |
| `Current/*.jsonl` | L3 最近 5 个交易日（分类型 4 文件，每天覆盖；单行紧凑 JSON，无多余空格） |
| `Trades.mvsv` | 全量调仓流水（Plan + Execution 成交） |

对比产物（`Compare.py` 生成，每日 15:35 由 `Compare.yml` 运行）：

| 文件 | 内容 |
| --- | --- |
| `Statistic/Comparison-All.svg` | 全时段 6 指数走势 + 6 个回撤子图 |
| `Statistic/Comparison-10Y.svg` | 最近十年窗口 |
| `Statistic/Comparison-5Y.svg` | 最近五年窗口 |
| `Statistic/Comparison-3Y.svg` | 最近三年窗口 |
| `Statistic/Comparison-1Y.svg` | 最近一年窗口 |
| `Statistic/Comparison-CSI2000.svg` | 中证2000 发布以来（2023-08-11 起） |
| `Statistic/Comparison.svg` | 全时段副本（兼容旧路径） |
| `Statistic/Comparison.md` | 六窗口收益 / 年化 / 最大回撤对比表（含各自起点） |
| `Statistic/Comparison.json` | 结构化对比结果（按窗口） |
| `Benchmarks/<windCode>.mvsv` | 基准收盘价缓存（与 `Data/` 平级，`Date|Close`），拉取失败时作降级 |

回放数据 `Input/Panel-YYYY.csv.gz`（2004–2026 共 23 个分片，207 MB）随仓库提交，保证 Actions 可独立回放；
未提交时自动回退读取父项目 `../data/panel.csv`。`ReplayCache/`（回放缓存）已加入 `.gitignore`。

## 7.1 三层存储与归档时点

| 层 | 判定 | 写入 | 归档 |
| --- | --- | --- | --- |
| L3 Current | 最近 5 个交易日 | 每天覆盖 4 个 JSONL | 超出 5 日 → 合并进 Pending |
| L2 Pending | 当年（已确认） | 按日期去重合并 | 次年 3 月 1 日 → `mv` 到 Archive |
| L1 Archive | 已过次年 3 月 1 日 | 只读 | 需批量改数据时按年重写（每年 4 个文件） |

- 全部明文存储（`.mvsv` / `.jsonl`，竖线分隔，不压缩），便于开发期直接调整；
  `.jsonl` 为一行一个 JSON 对象、紧凑格式（`separators=(",", ":")`，无多余空格）；
- 按年长表设计保证「加一列指标」只需改 23 个文件/指数，而按个股则要改 400+ 个文件。

## 7.2 行情数据层（Archive / Data / Current）

> 三层按**数据稳定度**划分，写入边界与采集粒度一致（采集按单证券 → 归档按单证券）。

| 层 | 路径 | 粒度 | 更新 | 说明 |
| --- | --- | --- | --- | --- |
| L1 归档 | `Archive/Security/<Code>.mvsv` | **单证券 1 个文件** | 冻结，仅修正时改 | 补采一只只改 1 个文件，避免按月拆分的写放大 |
| L2 当年 | `Data/<yyyy>/<yyyyMM>.mvsv` | 月横截面 | 每天 append（当月 1 个文件） | 当年数据，次年 3 月 1 日分发进 L1 |
| L3 当前 | `Latest/Latest.mvsv` | 最近 5 个交易日横截面 | 每天覆盖 | 未确认完整，便于快速调整 |

- 明文、竖线分隔（`|`）、扩展名 `.mvsv`，不压缩
- 自动采集：日频工作流每天调用 `Core/Market.update()` 写三层（周频/月频传 `--no-market`）；
  同一天重复执行只覆盖当日横截面，不会产生重复行；跨年后自动把 `Data/<旧年>/` 合并进
  `Archive/Security/<Code>.mvsv`（幂等，已归年份记在 `Archive/.ArchivedYears.json`）
- 字段列顺序统一为
  `Code|Name|Date|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat`
  （`Archive/Security/` 按证券；`Data/` 为横截面）；

`Archive/Security/<Code>.mvsv`（按证券）：

```
Code|Name|Date|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat
000001|平安银行|20041231|6.58|6.59|6.66|6.58|1752143|11568524.157|4716909684|4716869408|31084434817.56|31084169398.72
```

`Data/<yyyy>/<yyyyMM>.mvsv`、`Latest/Latest.mvsv`（横截面）：

```
Code|Name|Date|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat
000001|平安银行|20261102|11.6|11.54|11.74|11.47|159436145|1847466330.188|19405882353|19405716653|223943882353.62|223941970175.62
```

- `Date` 为 `yyyyMMdd`、文件名 `yyyyMM`（均不含 `-`）

| 字段 | 含义 | 单位 | 口径 |
| --- | --- | --- | --- |
| `Volume` | 成交量 | 股 | 新浪日线 |
| `Amount` | 成交额 | 元 | **临时近似** `Volume×(Open+Close+High+Low)/4`，待增量采集替换 |
| `SharesTotal` | 总股本 | 股 | 快照总市值/价 反推 + 除权回溯 |
| `SharesFloat` | 流通股本 | 股 | 快照流通市值/价 反推 + 除权回溯（限售解禁无法还原） |
| `MvTotal` | 总市值 | 元 | `Close × SharesTotal` |
| `MvFloat` | 流通市值 | 元 | `Close × SharesFloat` |

- 规模：`Archive/Security` 3,171 个文件 / 约 1.2 GB；`Data/2026` 11 个月度文件 / 约 80 MB；`Latest/Latest.mvsv` 约 2 MB
- 补采单只证券：只改 `Archive/Security/<Code>.mvsv` 1 个文件
- 年末分发：`Data/<yyyy>/` 的月份文件追加进各 `Archive/Security/<Code>.mvsv`（一年一次）
- 生成工具（父仓库 `WindMicroCapIdx/tools/BuildCnamcsi.py`）：`python tools/BuildCnamcsi.py --force`

### 7.3 微盘选样数据（Archive/Micro400、Archive/Candidates）

| 层 | 路径 | 内容 |
| --- | --- | --- |
| L1 归档 | `Archive/Micro400/<年>.mvsv` | 每日微盘 400 名单（含 `Rank`、行情、股本、市值） |
| L1 归档 | `Archive/Candidates/<年>.mvsv` | 微盘 400 中收盘价 < 10 元的候选池 |
| L2 当年 | `Data/<yyyy>/Micro400/<yyyyMM>.mvsv` | 当年按月切分 |
| L2 当年 | `Data/<yyyy>/Candidates/<yyyyMM>.mvsv` | 当年按月切分 |

- 字段：`Code|Name|Date|Rank|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat`
- 规模：Micro400 2,124,000 行（5,310 天 × 400）、Candidates 1,234,319 行（日均 232.5 只）
- 生成工具（父仓库）：`python tools/BuildMicro.py`

## 8. 复现步骤

```bash
# 单测（纯标准库）
python3 -m unittest discover -s tests -p 'Test*.py' -v

# 与 Actions 一致：零参数（默认 CNAMCSDEWI，会等到 15:00 后数据就绪，最多 40 分钟）
python3 Index.py

# 指定指数 / 本地调试（不等收盘）
python3 Index.py --index CNAMCSWEWI --no-wait
python3 Index.py --index CNAMCSMEWI --no-wait --date 2026-11-13

# 历史回放（基日 2004-12-31，数据自 2004-12-31 起）
python3 Index.py --index CNAMCSDEWI --replay-start 2004-12-31

# 查看结果
cat Index/CNAMCSDEWI/Index.json
cat Index/CNAMCSDEWI/Latest/Report.md
```

验收标准：

1. 单测全绿；
2. 首次运行 `Index.json` 的 `point = 1000`、`base_date = 2004-12-31`、`components = 400`；
   回放模式下 `Nav.mvsv` 从 2004-12-31（基日，1000 点）开始，跨至最新交易日；
   周频/月频回放的调仓次数应显著少于交易日数（约 1/5、1/20）；
3. 连续运行两次（同一交易日）`point` 不变、`execution` 不重复；
4. 次日运行 `Nav.mvsv` 新增一行，且 `cum_return` 与 `point/1000 - 1` 一致（误差 < 1e-9）；
5. `Latest/Plan.mvsv` 的股数全部为 100 的整数倍。

## 9. 已知近似

- 用滚动「昨收成分 + 今日收盘」计算收益，与官方指数的成分生效日可能存在 1 日错位；
- 停牌/退市按价格不变处理（无法模拟长期停牌后的复牌跳空）；
- 分红除息不还原（不复权价格），指数收益在除息日会有小幅低估；
- 成分筛选依赖最新简称判断 ST，历史状态不可还原；
- GitHub Actions 的 cron 不保证准点，且公共 runner 出口在海外，接口延迟可能高于国内。

## 10. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-15 | v1.0 | 初版：CNAMCI 等权 400、BASE 1000、15:05 触发 + 就绪等待、changes 组合 |
| 2026-11-15 | v1.1 | 仓库根即 CNAMCI；基期改为 2025-01-02；新增历史回放初始化；目录/文件大驼峰 |
| 2026-11-15 | v2.0 | 基线指数体系：CNAMCSDEWI / CNAMCSWEWI / CNAMCSMEWI（日/周/月频等权），调仓日判定与权重漂移，产物按代码分目录 |
| 2026-11-15 | v2.1 | 新增业绩比较基准（国证2000 / 中证2000 / 中证全指）与 6 指数对比 SVG |
| 2026-11-15 | v2.2 | 基期改为 2023-08-11（中证2000 发布日），重算全部历史 |
| 2026-11-15 | v3.0 | 基日改为 2004-12-31（中证全指基日），回放 2009-06 起全量数据（4242 个交易日） |
| 2026-11-16 | v3.1 | 数据扩至 2004-12-31 起（5,310 个交易日）；全部导出改为 `.mvsv`（头部元数据 + 竖线分隔）；三层存储（Current/Pending/Archive）；新增行情数据层（`Archive/Security`、`Data/<yyyy>/`、`Latest/Latest.mvsv`）与微盘选样数据（`Archive/Micro400`、`Archive/Candidates`）；万得两指数接入（60 条增量）；文档改名大驼峰（`Spec.md`/`Readme.md`/`Methodology.md`/`License`）；新增 `Methodology.md` |
