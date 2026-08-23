# 圆弧底股票扫描器 V1 设计规格

## 1. V1 目标

V1 是一个仅监听本机地址、通过浏览器访问的 Web 应用。它从 `watchlist.txt` 读取股票池，使用 LongPort API 增量同步前复权且包含盘前、盘中、盘后交易的周线、日线和 4 小时 K 线，运行 `CORE_SCANNING_ALGORITHM.md` 定义的扫描算法，并保存每日正式扫描结果以支持后续回测。

V1 的首要目标是：

1. 一键完成行情同步和全股票池扫描；
2. 按评分快速定位候选股票；
3. 清楚解释每个周期、每个因子的触发原因和评分来源；
4. 保存可追溯的每日扫描历史；
5. 保证行情获取、缓存、算法和 GUI 相互解耦。

V1 暂不实现自动交易、云端部署、多用户权限、移动端适配和复杂策略回测引擎。

## 2. 技术选型

| 层次 | V1 选择 |
|---|---|
| 后端 | Python、FastAPI |
| 前端 | React、TypeScript、Vite |
| 图表 | TradingView Lightweight Charts |
| 任务进度 | SSE（Server-Sent Events） |
| 元数据及扫描结果 | DuckDB |
| K 线缓存 | Parquet，由 DuckDB 查询 |
| 行情来源 | LongPort API |
| 启动方式 | 本地启动脚本，自动打开浏览器 |

服务默认只监听 `127.0.0.1`，不得默认暴露到局域网或互联网。LongPort 凭据只由后端读取，不得写入前端代码、浏览器存储、日志或 API 响应。

## 3. 总体结构

```text
浏览器（React）
    │ REST + SSE
    ▼
FastAPI
    ├─ WatchlistService      读取和校验股票池
    ├─ MarketDataService     编排 LongPort 增量同步
    ├─ ScanService           创建任务并调用核心算法
    ├─ ResultService         查询当前和历史扫描结果
    └─ SettingsService       管理非敏感设置
          │
          ├─ LongPort API
          ├─ DuckDB：任务、结果、诊断、元数据
          └─ Parquet：标准化 OHLCV
```

核心算法是纯计算模块，只接收标准化 DataFrame 和配置，不访问 LongPort、DuckDB、Parquet 或 Web 状态。

## 4. 主界面

主界面采用三栏结构，顶部保留窄状态栏：左侧是紧凑股票池，中间是主要图表及功能页面，右侧常驻分析摘要。

```text
┌─────────────────────────────────────────────────────────────────┐
│ 圆弧底扫描器  LongPort 状态  数据截止时间  同步并扫描  设置     │
├──────────────────────┬──────────────────────────────────────────┤
│ 左：股票池 │ 中：图表与功能 Tab                    │ 右：分析摘要 │
│ 搜索/筛选  │ K线、详细诊断、评分、历史、数据管理   │ 评分/因子矩阵│
│ 代码/评分  │ 当前选中股票的主要工作区              │ 多周期概况   │
└──────────────────────┴──────────────────────────────────────────┘
```

默认列宽为 `260px minmax(560px, 1fr) 320px`。V1 以桌面浏览器为目标；窗口过窄时应优先折叠右侧分析栏。

### 4.1 顶部状态栏

顶部状态栏包含：

- LongPort 连接状态；
- 最近成功同步时间；
- 当前数据是否过期；
- “同步并扫描”主按钮；
- 当前任务进度入口；
- 设置入口。

同一时间只允许一个全股票池同步或扫描任务运行。重复点击应返回现有任务状态，不得创建重复任务。

### 4.2 左侧股票池与评分

左侧从 `E:\我的程序\圆弧底股票扫描\watchlist.txt` 读取股票池，提供：

- 代码搜索；
- 按总分、代码或数据时间排序；
- 最低分过滤；
- 仅显示圆弧底；
- 仅显示多周期共振；
- 按 F1～F6 和触发周期过滤；
- 隐藏数据异常或扫描失败项目。

每只股票使用紧凑列表项：

```text
AAPL.US                                      82
F1-A  F3  F5                         周 日 4H
数据截至 2026-08-21 20:00 UTC              正常
```

总分是最醒目的信息；因子、周期和数据状态不得只用颜色表达。选中股票后，右侧所有 Tab 切换到该股票。扫描结果更新时保持当前选择；若当前股票不再符合过滤条件，则显示提示而不是强制跳到其他股票。

### 4.3 右侧 Tab

#### 行情图表

- 内部切换周线、日线和 4 小时线；
- 显示 K 线、成交量、EMA12、EMA144、EMA169、EMA576、EMA676；
- 在主图下方显示标准 MACD（12/26/9）独立副图；MACD 仅供观察，不参与 F1～F6 或评分；
- 显示黄色 Vegas 通道和绿色长期通道；
- 使用 `trendline_indicator.py` 在完整本地历史上计算分形趋势支撑线和压力线，并叠加到 K 线主图；该画线指标不参与评分；
- F3 触发时显示圆弧拟合曲线和 60 根拟合窗口；
- F4 触发时显示上下趋势线；
- 显示每个信号的触发位置；
- 固定展示“前复权、包含盘前盘后、UTC 数据截止时间”。

图表数据必须从本地 Parquet 缓存读取到后端内存，再按显示区间计算/装配 EMA 后返回浏览器。切换股票、周期或缩放图表不得直接触发 LongPort 请求；只有显式的同步操作才能访问行情 API。

#### 因子诊断

按周期展示 F1～F6。默认展开已触发因子，折叠未触发因子。每项必须显示：

- 是否触发；
- 信号名称及 F1 等级；
- 实际计算值；
- 对应阈值；
- 未触发原因；
- 使用的最后一根 K 线时间。

#### 评分明细

展示单因子基础分、跨周期倍数、各周期共振分、六因子覆盖倍数以及最终公式。不得把总分显示为概率或百分比。

#### 历史表现

V1 展示当前股票的历史正式扫描记录：

- 每日总分曲线；
- F1～F6 触发时间线；
- 算法版本与参数版本；
- 对应交易日的行情截止时间。

未来收益、胜率、最大回撤等统计保留接口位置，但不作为 V1 验收要求，避免在收益口径尚未确认前展示误导性结果。

#### 数据管理

- 三个周期的缓存根数、最早和最新时间；
- K 线口径：前复权、包含盘前盘后；
- 单只股票同步；
- 单只股票重建缓存；
- 最近同步错误；
- LongPort 和本地缓存状态。

## 5. 核心业务流程

### 5.1 启动

1. 加载环境变量和非敏感配置；
2. 初始化 DuckDB schema；
3. 校验 Parquet 根目录；
4. 读取并规范化 `watchlist.txt`；
5. 启动 FastAPI 并监听 `127.0.0.1`；
6. 自动打开默认浏览器；
7. GUI 显示最近一次正式扫描，不自动发起全量同步。

启动时不自动扫描，避免每次打开界面都产生无法区分的正式结果。用户点击“同步并扫描”或调度器触发后才创建正式任务。

### 5.2 同步并扫描

```text
创建 scan_run（running）
    ↓
读取股票池快照并保存
    ↓
逐证券同步 weekly / daily / 4hour
    ↓
校验前复权、全交易时段、UTC、OHLCV、重复项
    ↓
写入 Parquet 并更新 DuckDB 缓存元数据
    ↓
仅读取已收盘 K 线并运行纯算法
    ↓
以事务写入汇总结果和全部因子诊断
    ↓
scan_run 标记 completed 或 completed_with_errors
```

单只证券失败不应中止整批扫描；错误写入任务记录并继续。数据库中只有结果和因子诊断都写入成功后，该证券结果才视为成功。

### 5.3 行情同步

- 首次为每个证券、每个周期请求最近 1000 根；
- 后续只请求最近 3 根数据，并按时间戳覆盖刷新缓存尾部；
- 请求前先判断周期缓存新鲜度：周线按已完成交易周、日线按已完成交易日、4小时按3小时刷新窗口；新鲜缓存不访问LongPort；
- 三个周期均从 LongPort 获取，不用 1000 根日线临时合成周线；
- 合并后按唯一键覆盖、去重、升序排序；
- 正式扫描排除未收盘 K 线；
- 缓存口径不一致或校验失败时重建该证券、该周期缓存；
- LongPort 暂时不可用时，V1 默认不生成新的正式扫描结果；可查看旧结果和旧缓存。

## 6. 数据目录

建议目录结构：

```text
data/
├─ scanner.duckdb
├─ market/
│  ├─ timeframe=weekly/
│  ├─ timeframe=daily/
│  └─ timeframe=4hour/
└─ fixtures/
   ├─ ohlcv/
   └─ expected_results/
```

Parquet 可继续按 `symbol` 分区。文件和分区名称需要对证券代码做安全编码，数据库字段中仍保存原始规范代码。

## 7. DuckDB 数据模型

### 7.1 `scan_runs`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | UUID | 主键 |
| `run_type` | VARCHAR | `official` 或 `preview` |
| `status` | VARCHAR | `queued/running/completed/completed_with_errors/failed` |
| `started_at` | TIMESTAMPTZ | 开始时间 |
| `completed_at` | TIMESTAMPTZ | 完成时间 |
| `market_data_cutoff` | TIMESTAMPTZ | 本次扫描可见数据截止时间 |
| `algorithm_version` | VARCHAR | 算法版本 |
| `config_version` | VARCHAR | 参数版本 |
| `config_json` | JSON | 完整参数快照 |
| `config_hash` | VARCHAR | 参数内容哈希 |
| `watchlist_json` | JSON | 股票池有序快照 |
| `symbols_total` | INTEGER | 股票总数 |
| `symbols_succeeded` | INTEGER | 成功数 |
| `symbols_failed` | INTEGER | 失败数 |
| `error_summary` | JSON | 批次错误摘要 |

### 7.2 `scan_results`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | UUID | 关联扫描批次 |
| `symbol` | VARCHAR | 规范证券代码 |
| `total_score` | DOUBLE | 最终总分 |
| `base_total` | DOUBLE | 基础贡献合计 |
| `confluence_total` | DOUBLE | 共振分合计 |
| `pre_multiplier_score` | DOUBLE | 乘覆盖倍数之前的分数 |
| `coverage_multiplier` | DOUBLE | 六因子覆盖倍数 |
| `triggered_factors_json` | JSON | 已触发因子及周期 |
| `weekly_score` | DOUBLE | 周线共振分 |
| `daily_score` | DOUBLE | 日线共振分 |
| `four_hour_score` | DOUBLE | 4 小时共振分 |
| `weekly_bar_timestamp` | TIMESTAMPTZ | 周线最后已收盘时间 |
| `daily_bar_timestamp` | TIMESTAMPTZ | 日线最后已收盘时间 |
| `four_hour_bar_timestamp` | TIMESTAMPTZ | 4 小时最后已收盘时间 |
| `data_status` | VARCHAR | 正常、过期或异常 |
| `created_at` | TIMESTAMPTZ | 写入时间 |

主键为 `(run_id, symbol)`。

### 7.3 `factor_results`

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | UUID | 关联扫描批次 |
| `symbol` | VARCHAR | 证券代码 |
| `timeframe` | VARCHAR | `weekly/daily/4hour` |
| `factor_id` | VARCHAR | `F1`～`F6` |
| `triggered` | BOOLEAN | 是否触发 |
| `signal_name` | VARCHAR | 信号名称 |
| `factor_tier` | VARCHAR | F1 的 A/B/C，其他为空 |
| `base_score` | DOUBLE | 基础分 |
| `timeframe_multiplier` | DOUBLE | 跨周期倍数 |
| `score_contribution` | DOUBLE | 该因子贡献 |
| `bar_timestamp` | TIMESTAMPTZ | 判定 K 线时间 |
| `reason` | VARCHAR | 未触发或错误原因 |
| `details_json` | JSON | 完整诊断数据 |

主键为 `(run_id, symbol, timeframe, factor_id)`。未触发因子也必须保存。

### 7.4 `market_cache_manifest`

记录每个证券、周期缓存的路径、行数、最早/最新时间、复权口径、交易时段口径、同步状态和更新时间。它是缓存索引，不替代 Parquet 中的 OHLCV 数据。

### 7.5 `scan_errors`

保存 `run_id`、`symbol`、`timeframe`、处理阶段、错误代码、可安全展示的错误信息和发生时间。敏感凭据和完整环境信息不得写入。

## 8. API V1

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/status` | 系统、LongPort、缓存和任务状态 |
| GET | `/api/v1/watchlist` | 读取规范化股票池及校验错误 |
| POST | `/api/v1/scans` | 创建同步并扫描任务 |
| GET | `/api/v1/scans/{run_id}` | 查询任务进度和摘要 |
| GET | `/api/v1/scans/{run_id}/events` | SSE 推送进度 |
| GET | `/api/v1/results/latest` | 最近一次正式扫描的结果列表 |
| GET | `/api/v1/results/{run_id}` | 指定扫描批次结果 |
| GET | `/api/v1/symbols/{symbol}/diagnostics` | 当前股票因子和评分详情 |
| GET | `/api/v1/symbols/{symbol}/history` | 历史正式评分 |
| GET | `/api/v1/symbols/{symbol}/bars` | 图表 K 线及指标 |
| POST | `/api/v1/symbols/{symbol}/sync` | 同步单只股票 |
| POST | `/api/v1/symbols/{symbol}/cache/rebuild` | 重建单只股票缓存 |
| GET | `/api/v1/settings` | 获取非敏感设置 |
| PUT | `/api/v1/settings` | 更新允许修改的设置 |

所有列表接口应支持分页。股票代码放入 URL 时必须进行编码和白名单校验。

### 8.1 SSE 事件

至少包含：

```text
run_started
symbol_sync_started
symbol_sync_completed
symbol_scan_completed
symbol_failed
run_completed
heartbeat
```

每个事件包含 `run_id`、当前阶段、已完成数、总数和安全的简短消息。浏览器断线重连后，应能先通过任务查询接口恢复当前状态。

## 9. 正式结果与回测准备

每日正式扫描必须写入 DuckDB，不能只保存最终总分。必须同时保存：

- 全部触发和未触发因子诊断；
- 完整评分拆解；
- 算法版本和参数快照；
- 股票池快照；
- 三个周期最后一根已收盘 K 线时间；
- 前复权和全交易时段口径；
- 数据错误及缺失状态。

正式扫描不可覆盖旧批次。相同 `market_data_cutoff + algorithm_version + config_hash + watchlist` 的重复正式任务应提示用户，并由后端避免意外重复写入。

V1 的“历史表现”只读取当时实际保存的扫描结果，不使用当前算法重算后冒充历史结果。以后实现收益回测时，必须严格按当时可见数据计算，防止未来数据污染。

## 10. 配置与版本

非敏感配置可保存在项目配置文件或 DuckDB 中，包括：

- 股票池路径；
- DuckDB 和 Parquet 路径；
- 可选的每周期保留策略；默认不按根数裁剪；
- LongPort 请求并发数和重试次数；
- 扫描周期；
- 是否允许预览未收盘 K 线；
- 算法参数和版本。

LongPort 密钥只通过环境变量或本机安全配置提供。每次正式扫描将最终生效的算法参数固化到 `scan_runs.config_json`。

## 11. 错误和状态原则

- “行情同步失败”“数据过期”“算法未触发”是三种不同状态，GUI 不得混为一谈；
- 数据不足时显示 `insufficient_history`，不能显示为普通未触发；
- 单只股票失败时继续处理其他股票；
- 整批任务失败后保留错误记录，但不得产生看似完整的正式结果；
- 破坏性操作（如重建缓存）必须在 GUI 二次确认，并明确目标证券和周期；
- 后端日志使用结构化格式，GUI 只展示经过清理的错误信息。

## 12. V1 实施阶段

### 阶段一：项目骨架与数据层

- FastAPI 和 React 项目骨架；
- 配置加载与本地启动；
- 股票池解析；
- DuckDB schema 迁移；
- Parquet 行情仓库接口；
- LongPort Provider 接口及增量同步。

### 阶段二：算法与持久化

- 将六因子及评分拆成纯计算模块；
- 固定回归样本；
- 保存扫描批次、结果和因子诊断；
- 实现任务状态和错误隔离。

### 阶段三：两栏 GUI

- 顶部状态栏；
- 左侧股票池、评分、搜索、排序和过滤；
- 右侧行情图表、因子诊断和评分明细；
- SSE 扫描进度。

### 阶段四：历史与数据管理

- 历史评分曲线；
- 缓存状态；
- 单股同步和缓存重建；
- 完整错误提示和空状态。

## 13. V1 验收标准

V1 完成时应满足：

1. 一条本地命令能够启动后端、前端并打开浏览器；
2. GUI 严格采用左侧股票池评分、右侧功能 Tab 的两栏结构；
3. 能读取并规范化既有 `watchlist.txt`；
4. 能从 LongPort 同步三个周期各最多 1000 根前复权、含盘前盘后的 K 线；
5. 后续运行能够增量更新而不是重新下载全部历史；
6. 正式扫描只使用已收盘 K 线；
7. 能运行六因子及跨周期评分并显示完整诊断；
8. 每日正式扫描结果、版本和评分拆解完整写入 DuckDB；
9. 页面刷新后仍能查看最近结果和历史评分；
10. 单股失败不会导致整批扫描中止；
11. LongPort 凭据不会出现在浏览器、数据库结果或日志中；
12. 固定回归样本可以验证核心算法的兼容性。

## 14. V1 尚待实现前确认的决策

以下问题会直接改变扫描结果，应在编写算法实现前确认：

1. F1 在不同周期触发不同等级时采用哪个等级计分；
2. 共振规则继续使用精确集合匹配，还是改为子集匹配并取最高档；
3. 六因子全覆盖是否继续使用 `×10`；
4. 盘前盘后数据在 LongPort 的周线、日线和 4 小时接口中的准确语义与可用性；
5. 已确认当前 LongPort Python SDK 提供原生 `Period.Min_240`；V1 直接请求该周期并使用 `TradeSessions.All`，不从小时线自行聚合；
6. “每日正式扫描”的固定触发时间和所对应的市场交易日。

这些决策必须版本化，不能在已有历史结果后静默修改。
