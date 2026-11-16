# CNAMCI · 中证微盘股基线指数体系

本仓库根即 `CNAMCI`。同一选样口径（沪深主板、非 ST、上市满 60 交易日、总市值最小 400 只、等权），
按**调仓频率**分成三个基线指数，各自独立工作流、独立状态与产物：

基日为 **2004-12-31**（中证全指基日，基点 1000），全量数据自 2004-12-31 起（5310 个交易日）。

| 代码 | 名称 | 调仓频率 | 2004-12-31 起全量回放 |
| --- | --- | --- | --- |
| `CNAMCSDEWI` | 中证微盘股日频等权指数 | 每个交易日 | 91915.93（+9091.59%），5310 次调仓 |
| `CNAMCSWEWI` | 中证微盘股周频等权指数 | 每周最后一个交易日 | 93019.16（+9201.92%），1115 次调仓 |
| `CNAMCSMEWI` | 中证微盘股月频等权指数 | 每月最后一个交易日 | 52988.88（+5198.89%），263 次调仓 |

业绩比较基准：**国证2000（399303）**、**中证2000（932000）**、**中证全指（000985）**，与三只自建指数
同图展示（`Statistic/Comparison.svg`），基期同为 2023-08-11 = 1000 点。

备选业绩参考（未接入同图）：

| 参考 | 代码 | 数据源 | 状态 |
| --- | --- | --- | --- |
| 万得微盘股指数 | `868008.WI` | Wind 官网 `index_api` | 60 条已入库（`Benchmarks/868008.WI.mvsv`） |
| 万得微盘股日频等权指数 | `8841431.WI` | Wind 官网 `index_api` | 60 条已入库（`Benchmarks/8841431.WI.mvsv`） |
| 东方财富 微盘股板块 | `90.BK1158` | 东方财富 | 可参考（<https://quote.eastmoney.com/bk/90.BK1158.html>，非万得编制） |

万得官网接口（只返回最近 60 个交易日）：

```
GET https://index_api.wind.com.cn/indexofficialwebsite/Kline?indexId=<ID>&lan=cn
  868008.WI   万得微盘股指数          indexId = tShUKnLO4cLQ8v+TnUd2uQ==
  8841431.WI  万得微盘股日频等权指数  indexId = 2O5u8vTCa4KiLTLQ7DT/9w==
```

- 抓取：`python Tool/FetchWindIndex.py`（零依赖，按 Date 合并去重，历史保留）
- 定时：`.github/workflows/WindIndex.yml` 每个交易日 15:45（北京时间）自动运行并提交

| 指数 | 起点 | 末值 | 区间收益 | 年化 | 最大回撤 |
| --- | --- | ---: | ---: | ---: | ---: |
| CNAMCSDEWI 日频 | 2004-12-31 | 91915.93 | +9091.59% | +23.93% | -69.94% |
| CNAMCSWEWI 周频 | 2004-12-31 | 93019.16 | +9201.92% | +24.00% | -69.69% |
| CNAMCSMEWI 月频 | 2004-12-31 | 52988.88 | +5198.89% | +20.73% | -70.39% |
| 国证2000 | 2014-03-31 | 2106.51 | +110.65% | +6.31% | -66.75% |
| 中证2000 | 2023-08-11 | 1209.57 | +20.96% | +6.29% | -36.40% |
| 中证全指 | 2004-12-31 | 5207.16 | +420.72% | +8.15% | -71.48% |

> 中证2000 于 2023-08 发布，2023 年以前无数据；国证2000 发布更早，可覆盖更长的比较区间。

调仓日收盘重算 400 只并等权；非调仓日沿用上次成分，权重随价格漂移（买入并持有到下次调仓）。

- 编制方案：[`Methodology.md`](Methodology.md)（逐条规则）
- 输出契约：[`Spec.md`](Spec.md)
- 日志：`Index/<代码>/Latest/Run.log`

## 行情数据（三层）

```
CNAMCSI/
  Archive/<Code>.mvsv         L1 归档：每证券 1 个（≤2025，冻结）
  Data/<yyyy>/<yyyyMM>.mvsv   L2 当年：按月横截面
  Latest.mvsv               L3 当前：最近 5 个交易日
```

```
Date|Code|Name|Open|Close|High|Low|Volume|Amount|SharesTotal|SharesFloat|MvTotal|MvFloat
20041231|000001|平安银行|6.58|6.59|6.66|6.58|1752143.0|11568524.157|4716909683.848|4716869407.796|31084434816.556|31084169397.374
```

| 项 | 值 |
| --- | --- |
| Archive | 3,171 个证券文件，1.41 GB |
| Data 2026 | 11 个月度文件，88 MB |
| Current | 2 MB（5 个交易日） |
| 补采 | 单只只改 `Archive/Security/<Code>.mvsv` 1 个文件 |

三层由日频工作流（`CNAMCSDEWI`）每天自动更新：拉完快照后由 `Core/Market.py` 落盘——
L3 保留最近 5 个交易日、L2 追加当月横截面、跨年后自动把往年 `Data/` 合并进 `Archive/Security/`（幂等）。
周频/月频工作流用 `--no-market` 跳过，避免三个工作流同时改写同一批文件。
单独补采：`python Tool/UpdateMarket.py [--date YYYY-MM-DD]`。

`Amount` 取自腾讯快照（万元精度，约万级近似），`SharesTotal`/`SharesFloat` 由市值 ÷ 收盘反推。

## 目录

```
CNAMCI/                        本仓库
  Spec.md                      指数规则（三指数、调仓频率、权重漂移、点位、输出契约）
  Readme.md                    本文件
  Index.py                     入口（7 步流程；--index 选择指数，默认 CNAMCSDEWI）
  Compare.py                   基准对比：拉取国证2000/中证2000/中证全指并生成 SVG
  Cnamci.py                    指数元数据/点位/权重漂移/报告（纯函数）
  Logger.py                    统一日志（北京时间 + 主题前缀 + 步骤耗时）
  Config.yaml                  公共参数（大驼峰字段：N/Capital/BaseDate/...）
  SeedStockList.yaml           代码清单兜底快照（大驼峰 YAML）
  requirements.txt             云端零依赖；回放需 pandas
  Tool/ExportPanel.py         导出回放面板（Input/Panel-YYYY.csv.gz，仓库自包含）
  Input/Panel-YYYY.csv.gz      回放数据（2004-2026 共 23 个分片，约 207 MB）
  Core/                        零依赖基础模块
    Sources.py                 行情抓取（腾讯/东财/交易所/巨潮）+ 请求计数
    Universe.py                股票池过滤、市值排序、400 只、等权权重
    Portfolio.py               目标股数、调仓计划、成交回填、费用
    Store.py                   本地存储（Cache 前缀映射）
    LocalData.py               历史回放数据源（pandas，仅回放）
  tests/                       TestCnamci / TestIndexFlow / TestRebalance
  .github/workflows/
    CNAMCSDEWI.yml              日频：15:35 自动运行
    CNAMCSWEWI.yml              周频：15:35 自动运行（仅周五调仓）
    CNAMCSMEWI.yml              月频：15:35 自动运行（仅月末调仓）
    Compare.yml                15:55 生成 6 指数对比图与统计
    Replay.yml                 手动触发：用 Input/ 回放三只指数并重建对比图
  Data/
    CNAMCSDEWI/  CNAMCSWEWI/  CNAMCSMEWI/   各自的 Index/Nav/State/Portfolio/Latest/Daily
    Cache/                              交易日历/代码清单缓存（三个指数共享，不提交）
    Benchmarks/                         基准收盘价缓存（国证2000/中证2000/中证全指）
    Comparison.svg / .md / .json        6 指数对比图与统计
```

## 运行

### Actions（默认）

每个工作流各自 `python Index.py --index <代码>`，无需其他参数。启用方式：
Settings → Actions → General → Workflow permissions 选 **Read and write**，
fork 仓库在 Actions 页面点一次 **Enable workflow**。

每个交易日 15:35 触发，三个工作流并行的流程一致：

```
1. 数据就绪检查   上证指数快照时间戳 = 今日且 ≥ 15:00:00（最多等 40 分钟）
2. 拉取全市场快照 腾讯 900 只/批 + 代码清单（周级缓存）+ 东财交易日历
3. 成分与权重     调仓日：重算 400 只并等权；非调仓日：沿用并漂移
4. 计算指数点位   用上一交易日收盘权重算当日收益，累积点位
5. 组合与调仓     回填昨日计划（今日开盘价）→ 估值 → 调仓日生成明日计划
6. 写出结果       Index/<代码>/{Index.json,Nav.mvsv,State.json,Portfolio.json,Latest,Daily}
7. 汇总           请求数、耗时、点位、是否调仓、计划与成交笔数
```

首次运行自动初始化：基期 **2023-08-11**（中证2000 发布日）、基点 **1000**，`Nav.mvsv` 补基期行。

### 本地

```bash
# 单测（零依赖）
python3 -m unittest discover -s tests -p 'Test*.py' -v

# 与 Actions 一致：零参数（默认 CNAMCSDEWI，等收盘）
python3 Index.py

# 指定指数 / 调试
python3 Index.py --index CNAMCSWEWI --no-wait
python3 Index.py --index CNAMCSMEWI --no-wait --date 2026-11-13
```

## 业绩比较基准与走势图

```bash
python3 Compare.py            # 拉取基准并生成 Statistic/Comparison.svg / .md / .json
python3 Compare.py --no-fetch # 只用本地缓存（Benchmarks/）
```

- 基准数据：国证2000、中证全指走腾讯日线；中证2000 走中证指数官网（腾讯无 932000 行情）；
- 归一化：均以 2025-01-02 收盘 = 1000 点，与 CNAMCI 基点一致；
- 输出**六张**时间窗口的 SVG（纯手写，2360×2940，含主图 + 6 个回撤子图）：
  `Comparison-All.svg`（全时段）、`Comparison-10Y.svg`、`Comparison-5Y.svg`、
  `Comparison-3Y.svg`、`Comparison-1Y.svg`、
  `Comparison-CSI2000.svg`（2023-08-11 中证2000 发布以来）；`Comparison.svg` 为全时段副本；
- 全时段图以 2023-08-11 = 1000 锚定（中证2000 从发布日接入，国证2000/中证全指覆盖更长历史）；
  三年/一年窗口以窗口首日归一为 1000；
- 同时输出 `Comparison.md`（六窗口统计表，含各自起点）与 `Comparison.json`。

## 部署到 GitHub（仓库自包含）

1. **生成回放数据**（在父项目有 `data/panel.csv` 的机器上，只需一次）：

   ```bash
   pip install pandas
   python Tool/ExportPanel.py         # 生成 Input/Panel-2004.csv.gz … Panel-2026.csv.gz（共约 207 MB）
   ```

2. 提交并推送：`git add Input Tools && git commit -m "data: 回放面板" && git push`
   （`Input/` 不在 `.gitignore` 中，随仓库提交；单文件 10–13 MB，符合 GitHub 限制）

3. GitHub → Actions 启用 workflow，Settings → Workflow permissions 选 **Read and write**。

4. **历史回放**：Actions → **CNAMCIReplay** → Run workflow
   - `start`：默认 `2004-12-31`
   - `index`：`all` 或单个代码
   - `daily_since`：留空=最近 1 年+年末；填 `2004-12-31`=逐日全量归档（体积约 1.1 GB）
   - 回放后自动 `python Compare.py` 重建六张对比图并提交 `Statistic/`

5. **每日增量**：`CNAMCSDEWI` / `CNAMCSWEWI` / `CNAMCSMEWI` 三个工作流每交易日 15:35 自动运行。

> 若不想把 207 MB 放进 git，可改用 GitHub Release 存 `Input/*.csv.gz`，在 `Replay.yml` 的「检查回放数据」步骤前加一步 `gh release download`。

## 历史回放（本地）

```bash
python3 Index.py --index CNAMCSDEWI --replay-start 2004-12-31
python3 Index.py --index CNAMCSWEWI --replay-start 2004-12-31
python3 Index.py --index CNAMCSMEWI --replay-start 2004-12-31
```

回放写入与线上一致的 `Index/<代码>/` 结构（5310 个交易日）。三个指数的对比：

| 指数 | 调仓次数 | 末值 | 累计 | 年化 | 交易费用 |
| --- | ---: | ---: | ---: | ---: | ---: |
| CNAMCSDEWI（日频） | 5310 | 91915.93 | +9091.59% | +23.93% | 5,485,949 元 |
| CNAMCSWEWI（周频） | 1115 | 93019.16 | +9201.92% | +24.00% | 1,836,724 元 |
| CNAMCSMEWI（月频） | 263 | 52988.88 | +5198.89% | +20.73% | 619,529 元 |

同期基准：中证全指 +420.72%（2004-12-31 起，年化 8.15%）、国证2000 +110.65%（2014 起）、
中证2000 +20.96%（2023-08 起）。

## 产物（`Index/<代码>/`）

| 文件 | 内容 |
| --- | --- |
| `Index.json` | 点位、日收益、累计、成分数、边界市值、调仓频率、请求统计 |
| `Nav.mvsv` | `date,point,ret,cum_return,holdings_count,total_asset,cash` |
| `State.json` | 基期、上一交易日成分与漂移权重（次日收益依赖） |
| `Portfolio.json` | 现金、持仓、pending 计划 |
| `Latest/Components.mvsv` | 当前成分与权重（非调仓日为漂移权重） |
| `Latest/Holdings.mvsv` | 持仓数量明细 |
| `Latest/Plan.mvsv` | 次日调仓计划（仅调仓日生成） |
| `Latest/Execution.mvsv` | 当日回填成交 |
| `Latest/Report.md` | 人读摘要（Actions 页面同步展示） |
| `Latest/Run.log` | 本次运行日志 |
| `Trades.mvsv` | 回放生成的全量调仓流水（所有 Plan + Execution 成交，不依赖 Daily 归档） |
| `Archive/<年>/*.mvsv` | **L1 已归档**（只读）：Components/Holdings/Trades/Summary 按年长表 |
| `Pending/<年>/*.mvsv` | **L2 待归档**：当年数据，可修正；次年 3 月 1 日整体转 L1 |
| `Current/*.jsonl` | **L3 当日/未完整**：最近 5 个交易日，分类型 4 个文件，每天覆盖写 |
| `Statistic/Comparison-All.svg` | 全时段（2013 起）6 指数走势 + 回撤 |
| `Statistic/Comparison-10Y.svg` | 最近十年 |
| `Statistic/Comparison-5Y.svg` | 最近五年 |
| `Statistic/Comparison-3Y.svg` | 最近三年 |
| `Statistic/Comparison-1Y.svg` | 最近一年 |
| `Statistic/Comparison-CSI2000.svg` | 中证2000 发布以来（2023-08-11 起） |
| `Statistic/Comparison.md` | 六窗口统计表（收益/年化/回撤，含各自起点） |
| `Statistic/Comparison.json` | 结构化对比结果 |

> 缓存统一放在 `Cache/`（三个指数共享，`.gitignore` 已排除），不进入各指数产物目录。

## 三层数据存储

| 层 | 范围 | 形式 | 更新频率 | 说明 |
| --- | --- | --- | --- | --- |
| **L1 Archive** | 去年及以前（已过次年 3 月 1 日） | `Archive/<年>/{Components,Holdings,Trades,Summary}.mvsv` | 冻结 | 只读；批量 patch（加列/修数）只改 23 个文件/指数 |
| **L2 Pending** | 当年、已确认完整但未归档 | `Pending/<年>/…csv` | 追加/修正 | 次年 3 月 1 日 `mv` 到 `Archive/<年>` |
| **L3 Current** | 最近 5 个交易日（含当日） | `Current/{Kind}.jsonl`（4 个文件） | 每天覆盖写 | 未完整/待复核，单文件、便于快速调整 |

每日运行：写 `Current` → 把超出 5 个交易日的记录合并进 `Pending/<当年>`（按日期去重）→
若已到次年 3 月 1 日，自动把 `Pending/<年>` 移到 `Archive/<年>`。

- 加指标：`Tools/PatchArchive.py`（L1 批量）、`Pending` 直接改 CSV（L2）；
- 单股查询：按年长表过滤 `code` 即可，不需按个股拆文件；
- 全部为明文 `.mvsv` / `.jsonl`，开发阶段可直接编辑。

## 配置（`Config.yaml`，大驼峰字段）

```yaml
N: 400
Capital: 4000000
Mode: changes
WeightMode: equal
MinListedDays: 60
FeeModel: rate
Lot: 100
BasePoint: 1000
BaseDate: "2004-12-31"
Source: tencent
Boards: ["600", "601", "603", "605", "000", "001", "002", "003"]
MaxWaitMin: 40
RetrySeconds: 60
ExcludeTop: 0          # 剔除市值前 N 名（宽基覆盖区）
MinAmount20: 0         # 20 日日均成交额下限（元）
Amount20Pct: 0         # 剔除成交额最低分位
LiquidityFirst: true   # 先过滤流动性再取市值 400
```

> 解析器为仓库自带的 `Core/YamlLite.py`（零依赖，支持注释/标量/行内列表），无需 PyYAML；
> 读取后映射为内部键（`BaseDate` → `base_date`）。

## 注意

- GitHub cron **不保证准点**；脚本会轮询就绪后才计算，非交易日/超时退出码 0、不写盘。
- 周频/月频工作流仍每天运行（更新点位与持仓估值），只是非调仓日不产生调仓计划。
- 公共 runner 在海外，行情接口延迟高于国内；连续失败可换 self-hosted runner。
- 产物自动提交（`git add Index/<代码>`），每次运行产生一个提交。
