# 核心扫描算法规格（由当前实现提取）

本文档主要描述 `technical-state-scanner` 当前版本的核心技术扫描算法，供后续重新设计程序时使用。除第 11 节给出的行情缓存与同步方案外，它不涵盖 LongPort 连接实现、调度器、FastAPI、WebSocket、React UI、CSV/JSON 写出等外围功能。

> 重要：本文记录的是“源码当前实际执行的行为”，不代表这些规则都合理。文末单独列出了重构时需要重新确认的问题。

本地 Web 应用第一版的 GUI、API、存储模型和实施范围见 `V1_APPLICATION_DESIGN.md`。

## 1. 算法目标

对单个证券在三个时间周期上分别检测六个相互独立的技术因子，然后综合：

1. 单因子基础分；
2. 同一因子的跨周期覆盖倍数；
3. 同一周期内的因子共振分；
4. 六因子跨周期全覆盖倍数。

核心算法不设置主状态，不要求因子按阶段依次出现，也不对因子设置优先级。一个周期内所有因子都独立运行，所有触发结果均被保留。

## 2. 输入数据契约

### 2.1 证券代码

- 已包含市场后缀的代码保持不变，例如 `700.HK`、`AAPL.US`。
- 不含后缀的代码统一补为 `.US`，例如 `AAPL` 转为 `AAPL.US`。

扫描股票池固定从以下文件读取：

```text
E:\我的程序\圆弧底股票扫描\watchlist.txt
```

`watchlist.txt` 使用 UTF-8 编码，每行一个证券代码。读取时应去除行首、行尾空白并忽略空行，然后按照上述规则补全市场后缀。应在保持首次出现顺序的前提下删除重复代码；单个代码无效时记录错误并跳过，不应中止整个股票池的扫描。股票池文件不存在或读取失败时，应停止本次扫描并报告明确错误，不得静默使用内置默认股票池。

### 2.2 时间周期

每只证券扫描以下三个周期：

- `weekly`：周线；
- `daily`：日线；
- `4hour`：4 小时线。

三个周期运行完全相同的六因子检测器。

### 2.3 K 线字段

标准输入是按时间升序排列、以 UTC `DatetimeIndex` 为索引的 OHLCV 表：

所有周期的 K 线必须包含盘前、盘中和盘后交易时段，并统一使用前复权价格。数据提供层不得将仅常规交易时段或其他复权口径的数据传入核心算法。

| 字段 | 含义 |
|---|---|
| `Open` | 开盘价 |
| `High` | 最高价 |
| `Low` | 最低价 |
| `Close` | 收盘价 |
| `Volume` | 成交量 |

默认请求每个周期最多 `700` 根 K 线。指标计算与因子判定均使用序列最后一根 K 线作为“当前 K 线”。

## 3. 指标预处理

对每个周期分别按收盘价计算指数移动平均线：

```text
EMA12  = EWM(Close, span=12,  adjust=False)
EMA144 = EWM(Close, span=144, adjust=False)
EMA169 = EWM(Close, span=169, adjust=False)
EMA576 = EWM(Close, span=576, adjust=False)
EMA676 = EWM(Close, span=676, adjust=False)
```

Pandas 对应计算为：

```python
Close.ewm(span=period, adjust=False).mean()
```

GUI 行情图表另外计算标准 MACD（12/26/9）作为观察用副图。MACD 不属于 F1～F6，也不参与当前扫描触发或评分：

```text
DIF  = EMA12 - EMA26
DEA  = EWM(DIF, span=9, adjust=False)
MACD = 2 × (DIF - DEA)
```

短期 Vegas 通道定义为：

```text
VegasLower = min(EMA144, EMA169)
VegasUpper = max(EMA144, EMA169)
```

只要数据少于 `676` 根，指标状态会标记为 `insufficient_history`。其中 F1、F2 会直接不触发；其他因子仍可使用它们各自需要的较短窗口。

## 4. 六个独立因子

每个检测器统一返回：

```json
{
  "triggered": true,
  "timestamp": "最后一根K线时间",
  "signal_name": "信号名称",
  "details": {}
}
```

未触发时，`details.reason` 记录失败原因。

### 4.1 F1 — Vegas Alignment

信号名：`Vegas Alignment`

最低数据量：`676` 根。

在最后一根 K 线上定义：

```text
yellow_low  = min(EMA144, EMA169)
yellow_high = max(EMA144, EMA169)
green_low   = min(EMA576, EMA676)
green_high  = max(EMA576, EMA676)

overall_spread_pct =
    (max(yellow_high, green_high) - min(yellow_low, green_low))
    / Close * 100

overlap = 两个通道区间发生重叠

distance_between_tunnels =
    max(green_low - yellow_high, yellow_low - green_high, 0)

distance_pct = distance_between_tunnels / Close * 100
```

按以下顺序匹配模式，首次匹配即停止：

| 优先级 | 条件 | 模式 | F1 等级 |
|---:|---|---|---|
| 1 | 通道重叠且 `overall_spread_pct < 0.6` | `full_overlap` | A |
| 2 | `overall_spread_pct < 1.5` | `tight_compression` | B |
| 3 | `distance_pct <= 0.8` | `parallel_close` | C |
| 4 | 通道重叠 | `nested_interlaced` | A |

任一模式成立即触发 F1。

同时记录 EMA12 相对完整通道的位置：

- `INSIDE_TUNNEL`；
- `ABOVE_TUNNEL`；
- `BELOW_TUNNEL`。

还会记录黄、绿通道中线过去 5 根的平均斜率，但当前斜率不参与触发判定：

```text
slope = (current_midline - midline_5_bars_ago) / 5
```

可调参数：

| 参数 | 默认值 |
|---|---:|
| `compression_threshold_pct` | `1.5` |
| `close_parallel_threshold_pct` | `0.8` |

### 4.2 F2 — EMA12 Lift-Off

信号名：`EMA12 Lift-Off`

最低数据量：`676` 根。

完整 Vegas 区间使用四条长期 EMA：

```text
lower[t] = min(EMA144, EMA169, EMA576, EMA676)
upper[t] = max(EMA144, EMA169, EMA576, EMA676)
```

EMA12 到区间的归一化距离：

```text
若 EMA12 > upper：distance = (EMA12 - upper) / Close
若 EMA12 < lower：distance = (lower - EMA12) / Close
若位于区间内：   distance = 0
```

必须同时满足四个条件：

1. 最近 `10` 根内至少一次 `distance <= 0.005`；
2. 对最近 `5` 个 EMA12 值拟合二次曲线，二次项系数 `a > 0`；
3. 当前 `EMA12 > upper`；
4. 当前距离大于 `3` 根之前的距离。

拟合模型：

```text
EMA12(x) ≈ a*x² + b*x + c
```

代码会计算拟合的 `R²`、当前 EMA12 斜率和距离，但 `R²` 与斜率不参与触发判定。

可调参数：

| 参数 | 默认值 |
|---|---:|
| `attachment_lookback` | `10` |
| `attached_distance_pct` | `0.005` |
| `curvature_window` | `5` |
| `distance_compare_bars` | `3` |

### 4.3 F3 — Round Bottom

信号名：`Round Bottom`

分别取最近 `60`、`120`、`180` 根收盘价进行多窗口拟合：

```text
Close(x) ≈ a*x² + b*x + c
```

计算：

```text
R² = 1 - SS_res / SS_tot
vertex_x = -b / (2a)
```

必须同时满足：

1. `a > 0`，曲线开口向上；
2. `R² >= 0.7`；
3. 顶点位于窗口的中间 60%，即 `0.2*window <= vertex_x <= 0.8*window`。

任意一个窗口满足条件即触发 F3；若多个窗口同时满足，则选择 `R²` 最高的窗口作为本次诊断结果。同时保存全部候选窗口、实际采用窗口、拟合顶点时间、顶点价格、距顶点 K 线数以及当前价格相对拟合底部的涨幅。这样既能识别短期小型圆弧，也能识别已经超出最近 60 根范围的中大型 U 型底。

可调参数：

| 参数 | 默认值 |
|---|---:|
| `windows` | `60, 120, 180` |
| `min_r_squared` | `0.7` |

### 4.4 F4 — Triangle Consolidation

信号名：`Triangle Consolidation`

在最近 `30` 根 K 线上寻找局部高低点。默认 `pivot=3`，即中心高点必须严格高于左右各 3 根的所有高点，中心低点必须严格低于左右各 3 根的所有低点。

高点和低点各至少需要两个。随后分别进行一元线性拟合：

```text
upper(x) = slope_high*x + intercept_high
lower(x) = slope_low*x + intercept_low
```

类型判定：

| 类型 | 条件 |
|---|---|
| 对称三角形 | `slope_high < -epsilon` 且 `slope_low > epsilon` |
| 上升三角形 | `abs(slope_high) < epsilon` 且 `slope_low > epsilon` |
| 下降三角形 | `slope_high < -epsilon` 且 `abs(slope_low) < epsilon` |

收缩率：

```text
start_range = max(upper(x_start) - lower(x_start), 0)
end_range   = max(upper(x_end)   - lower(x_end),   0)

contraction_ratio = end_range / start_range
```

当三角形类型有效且 `contraction_ratio < 0.6` 时触发。

可调参数：

| 参数 | 默认值 |
|---|---:|
| `window` | `30` |
| `pivot` | `3` |
| `epsilon` | `0.01` |

### 4.5 F5 — Big Bullish Candle

信号名：`Big Bullish Candle`

基于最后一根 K 线，必须同时满足：

1. `Close > Open`；
2. `(Close - Open) / Open > 0.025`；
3. 当前实体大小相对前 20 根实体绝对值平均数的比例 `> 1.5`；
4. `Close > max(EMA12, EMA144, EMA169) * 1.015`。

定义：

```text
body_size = Close - Open
gain_pct = body_size / Open
avg_body_size_20 = mean(abs(Close[i] - Open[i]))，不包含当前 K 线
body_ratio = body_size / avg_body_size_20
```

可调参数：

| 参数 | 默认值 |
|---|---:|
| `min_body_pct` | `0.025` |
| `body_ratio_threshold` | `1.5` |
| `ema_clearance_pct` | `0.015` |

### 4.6 F6 — Volume Surge

信号名：`Volume Surge`

定义：

```text
avg_volume_20 = 前 20 根成交量平均值，不包含当前 K 线
actual_ratio = current_volume / avg_volume_20
```

当 `actual_ratio > 1.5` 时触发。

可调参数：

| 参数 | 默认值 |
|---|---:|
| `surge_ratio` | `1.5` |

### 4.7 F7 — Cup and Handle V2（观察型结构）

F7 在 `60`、`120`、`180` 根窗口中搜索茶杯柄结构，检查左右杯沿、杯底深度、杯身拟合度、杯柄长度与回撤、颈线位置及突破成交量。主要约束：

- 杯身二次拟合开口向上，`R² >= 0.55`；
- 杯深位于 `12%～33%`；
- 左右杯沿差异不超过 `10%`，右杯沿恢复至左杯沿的 `90%～105%`；
- 左右两侧持续时间比例至少为 `0.5`，且价格位于杯底上方 8% 杯深以内的低位区至少覆盖杯身的 10%，用于排除尖锐 V 型；
- 杯柄长度候选为 `5、10、15、20、25、30` 根；
- 杯柄回撤不超过 `15%`，且不超过杯深的 `1/3`；
- 杯柄平均成交量不超过杯柄前均量的 `85%`；
- 使用杯柄高点拟合上轨，突破线取右杯沿/左杯沿颈线与杯柄上轨中的较高值；
- 放量突破确认要求收盘价高于突破线 `1%`，成交量达到前 20 根均量的 `1.5` 倍。

输出阶段包括 `cup_complete`、`handle_forming`、`breakout_ready`、`breakout_confirmed`，并给出 `0～100` 的结构置信度。置信度描述图形符合程度，不是上涨概率。F7 仅用于诊断、左侧筛选和展示，基础分、贡献分均为 `0`，不进入 F1～F6 共振及全覆盖倍数。

### 4.8 P1～P6 — PatternPy 经典形态观察器

使用固定版本 PatternPy 对本地已闭合 OHLCV 数据识别头肩、复合顶底、三角形、楔形、通道和双顶底。部分上游算法需要后一根 K 线确认，因此最后一根不作为形态发生点，只展示最近 3 根内已经确认的结果。P1～P6 仅用于左侧“经典形态”筛选和因子诊断，不参与任何评分、共振或覆盖倍数。

| ID | 形态族 |
|---|---|
| P1 | Head & Shoulders |
| P2 | Multiple Tops & Bottoms |
| P3 | Triangle |
| P4 | Wedge |
| P5 | Channel |
| P6 | Double Top & Bottom |

## 5. 单周期聚合

在每个周期上执行六个评分因子以及一个独立观察型结构：

```text
F1 Vegas Alignment
F2 EMA12 Lift-Off
F3 Round Bottom
F4 Triangle Consolidation
F5 Big Bullish Candle
F6 Volume Surge
F7 Cup and Handle（不参与评分）
P1～P6 PatternPy 经典形态（不参与评分）
```

结果包含：

- `triggered_signals`：触发的信号名称；
- `triggered_factors`：触发的因子 ID；
- `details`：F1～F7 与 P1～P6 的完整诊断结果，包括未触发观察器；评分仍只读取 F1～F6。

## 6. 跨周期评分

### 6.1 单因子基础分

| 因子 | 基础分 |
|---|---:|
| F1 Tier A | 4 |
| F1 Tier B | 3 |
| F1 Tier C | 2 |
| F2 | 3 |
| F3 | 4 |
| F4 | 1 |
| F5 | 4 |
| F6 | 1 |

### 6.2 同因子跨周期倍数

对每个因子汇总其触发周期集合，并只计算一次基础分：

| 触发周期集合 | 倍数 |
|---|---:|
| 周线 + 日线 + 4 小时 | 6.0 |
| 周线 + 日线 | 4.5 |
| 周线 + 4 小时 | 3.5 |
| 日线 + 4 小时 | 3.0 |
| 仅周线 | 2.5 |
| 仅日线 | 1.5 |
| 仅 4 小时 | 1.0 |

单因子贡献：

```text
factor_score = base_signal_score * timeframe_multiplier
```

F1 跨周期触发时，当前实现从触发周期集合中取到的第一个 F1 等级作为基础分等级。

### 6.3 单周期因子共振分

每个周期根据该周期触发因子的集合计算一次共振分，只应用一个匹配等级：

| 等级 | 要求的精确因子集合 | F1 等级 | 分数 |
|---|---|---|---:|
| S | F1+F2+F3+F4+F5+F6 | 任意 | 30 |
| A+ | F3+F4+F5+F6 | — | 24 |
| A | F3+F5+F6 | — | 22 |
| A | F1+F2+F3+F4+F5 | A | 22 |
| B+ | F1+F2+F3+F4+F5 | B | 18 |
| B | F1+F2+F3+F4+F5 | C | 15 |
| C+ | F1+F2+F3+F4 | A | 12 |
| C | F1+F2+F3+F4 | B | 10 |
| C- | F1+F2+F3+F4 | C | 8 |
| D+ | F1+F2+F3 或 F1+F2+F4 | A | 6 |
| D | F1+F2+F3 或 F1+F2+F4 | B | 5 |
| D- | F1+F2+F3 或 F1+F2+F4 | C | 4 |
| Early | F1+F2 | 任意 | 3 |

这里使用的是集合严格相等，不是“至少包含”。例如 `F3+F4+F5+F6` 得 24 分，但再多触发一个 F2 后，如果不满足其他精确规则，反而可能得 0 分。

### 6.4 六因子跨周期全覆盖倍数

将三个周期触发的因子取并集：

```text
all_factors = union(weekly_factors, daily_factors, 4hour_factors)
```

若并集严格等于 `{F1,F2,F3,F4,F5,F6}`，最终分数乘以 `10.0`，否则乘以 `1.0`。六个因子不需要在同一个周期触发。

### 6.5 最终公式

```text
base_total = Σ(每个已触发因子的基础分 × 该因子的跨周期倍数)

confluence_total = Σ(周线共振分, 日线共振分, 4小时共振分)

pre_multiplier_score = base_total + confluence_total

total_score = pre_multiplier_score × 六因子跨周期全覆盖倍数
```

## 7. 核心扫描流程伪代码

```text
function scan(symbol, weekly, daily, four_hour):
    timeframe_results = {}

    for timeframe, bars in [weekly, daily, four_hour]:
        bars = add_ema_12_144_169_576_676(bars)
        factor_results = {}

        for factor in [F1, F2, F3, F4, F5, F6]:
            factor_results[factor.id] = factor.detect(bars)

        timeframe_results[timeframe] = aggregate(factor_results)

    scoring = calculate_score(timeframe_results)

    return {
        symbol,
        triggered_signals,
        timeframe_results,
        scoring,
        total_score
    }
```

## 8. 建议的新核心接口

重新设计时，可以让算法核心完全不依赖 LongPort、文件系统、数据库或 Web 框架：

```python
scan_symbol(
    symbol: str,
    timeframe_bars: dict[str, DataFrame],
    config: ScannerConfig,
) -> ScanResult
```

推荐分层：

```text
MarketDataProvider  -> 只负责提供标准 OHLCV
IndicatorEngine     -> 只负责派生指标
FactorEngine        -> 只负责独立因子判定
ScoringEngine       -> 只接收因子结果并评分
ScanApplication     -> 编排批量、并发、缓存和持久化
API/UI              -> 只消费扫描结果
```

其中 `FactorEngine` 和 `ScoringEngine` 应是纯函数，便于回测、单元测试和替换数据源。

## 9. 重设计时必须重新确认的问题

以下是当前实现中会显著影响结果或运行效率的行为，不宜未经确认直接继承。

### 9.1 数据与指标

1. 默认只取 700 根数据，而最长 EMA 为 676；EMA576/676 的预热余量很小，初始值会显著影响结果。
2. 当前 EMA 使用 `adjust=False`，新实现必须明确是否保持一致。
3. 所有因子都检查最后一根 K 线，但未统一约束它必须是已收盘 K 线；盘中扫描可能使用未完成 K 线。
4. 不同周期的交易时段、复权方式和时间边界需要在数据层形成明确契约。

V1 数据口径补充：美股周线在美东周五 20:00（盘后结束）之后视为已收盘，周六和周日同样视为已收盘。该修正从配置版本 `v2-weekly-close` 开始生效；更早版本在周末可能错误排除当周周线。

### 9.2 因子定义

1. F1 中 A/B/C 的判定有顺序依赖；`nested_interlaced` 只有前三条都未匹配时才会到达。
2. F1 记录通道斜率但不参与触发。
3. F2 计算 `R²` 和当前斜率但不参与触发。
4. F2 的“最近贴合”窗口包含当前 K 线，需确认这是否符合“先贴合、后起飞”的语义。
5. F3 直接拟合绝对价格，二次项系数会受价格尺度影响；虽然当前只判断正负，但跨标的可比性有限。
6. F4 的 `epsilon=0.01` 是绝对价格斜率，不是百分比斜率，对不同价格水平的股票不等价。
7. F4 的趋势线起止点取高低 pivot 的联合边界，不一定对应相同时间点。
8. F5/F6 的注释要求“前 20 根”，但最低长度判断是 `len >= 20`；严格取得 20 根历史再加当前根实际上需要至少 21 根。
9. F5/F6 都使用严格大于阈值；恰好等于阈值不触发。
10. F6 不要求价格方向，因此放量下跌也会触发。

### 9.3 评分

1. F1 若在多个周期以不同等级触发，当前代码从 Python `set` 中取第一个等级，结果可能不稳定；新设计应明确取最高、最低、主周期或分别计分。
2. 共振规则使用精确集合匹配，多触发一个因子可能降低共振分；需要确认是否改为子集包含匹配并取最高档。
3. 六因子跨周期全覆盖直接乘 10，会造成分数跳变，需要确认是否符合排序目标。
4. 当前 `calculate_score` 参数 `all_triggered_signals` 实际没有参与计算，应从新接口删除或赋予明确职责。
5. 当前总分没有上限，也没有归一化，不适合直接解释为概率或百分制。
6. 顶层信号去重通过 `set` 完成，输出顺序不稳定。

### 9.4 执行效率

1. 当前智能加载路径可能为每只股票的每个周期分别创建一个 `QuoteContext`，导致大量重复连接。
2. 新设计应在数据提供层复用连接，并将“取得数据”和“运行算法”完全分开。
3. 批量扫描应先批量准备数据，再把只读数据交给纯算法并行计算，避免在线程中重复建立外部连接。

## 10. 建议保留的兼容性测试

重新实现前，应固定一组历史 OHLCV 样本并保存旧实现输出，至少覆盖：

- F1 的 A/B/C 三个等级和未触发情形；
- F2 四个条件逐一失败的情形；
- F3 顶点、曲率、R² 边界；
- F4 三种三角形和 pivot 不足；
- F5/F6 阈值恰好相等以及略高/略低；
- 每一种跨周期倍数组合；
- 每一种共振等级；
- 六因子跨周期并集全覆盖；
- 同一因子在不同周期拥有不同 F1 等级。

新算法若有意改变结果，应通过版本化配置或结果 schema 明确标记，而不是静默改变。

## 11. 建议的行情缓存与同步方案

### 11.1 设计结论

LongPort API 是行情数据的唯一权威来源，本地使用 **DuckDB + Parquet** 建立可随时重建的增量缓存。程序每次运行时仍应联系 LongPort 检查行情更新，但不需要为每只证券、每个周期重新下载完整的 1000 根 K 线。

```text
首次运行：
LongPort 请求最近 1000 根 -> 标准化 -> 写入 Parquet/DuckDB -> 扫描

后续运行：
读取本地缓存 -> 从 LongPort 获取新增及近期 K 线
             -> 按时间戳覆盖合并、去重、排序
             -> 更新本地缓存 -> 扫描
```

本地缓存不是第二个权威数据源。缓存缺失、损坏、版本不兼容或数据校验失败时，应丢弃对应缓存并从 LongPort 重新构建。

### 11.2 存储职责

- **Parquet**：保存标准化后的 OHLCV 历史数据，适合按市场、证券和周期分区存储。
- **DuckDB**：保存缓存元数据并负责查询、合并、去重和批量读取 Parquet 数据。
- **扫描结果库**：与行情缓存分开保存，记录算法版本、参数版本和行情截止时间，以支持结果复现。
- **固定测试样本**：另存不可变的 Parquet 样本及旧算法输出，不随生产缓存更新，用于兼容性和回归测试。

### 11.3 缓存数据契约

每根 K 线至少保存以下字段：

| 字段 | 含义 |
|---|---|
| `symbol` | 含市场后缀的证券代码 |
| `timeframe` | `weekly`、`daily` 或 `4hour` |
| `timestamp_utc` | K 线起始或结束时间，语义必须统一 |
| `open` / `high` / `low` / `close` | OHLC 价格 |
| `volume` | 成交量 |
| `is_closed` | K 线是否已经收盘 |
| `adjustment_type` | 复权方式，固定为前复权 |
| `trade_session` | 交易时段，能够区分盘前、盘中和盘后 |
| `data_source` | 固定标记为 LongPort |
| `updated_at` | 本地最后更新时间 |

推荐使用 `(symbol, timeframe, timestamp_utc, adjustment_type)` 作为唯一键。所有缓存数据必须采用前复权，并包含盘前、盘中和盘后成交；不得在同一缓存分区中混入仅常规交易时段的数据。传入核心算法前，再统一转换为第 2.3 节规定的 UTC `DatetimeIndex` 和大写 OHLCV 列名。

### 11.4 增量同步规则

1. 首次没有缓存时，从 LongPort 为每个证券和周期请求最近 1000 根前复权 K 线，请求范围必须包含盘前、盘中和盘后交易时段。首次历史回填不受美东周末和完整休市节假日限制；休市日禁用规则仅适用于已有缓存的增量同步。
2. 后续运行以相同的前复权及全时段口径请求最近 3 根 K 线，并覆盖刷新缓存尾部，以吸收未收盘 K 线变化及数据商可能的修订。
3. 合并时按唯一键覆盖旧记录、删除重复项，并按时间升序排列。
4. 正式扫描默认只使用 `is_closed = true` 的 K 线；若允许盘中预览，必须在结果中明确标记。
5. 同一批扫描只同步一次数据，因子计算阶段不得再次请求 LongPort。
6. 批量扫描复用同一个 LongPort 连接，并对请求并发和重试进行统一控制。
7. 数据过期但 LongPort 暂时不可用时，可以选择使用缓存完成降级扫描，但必须在结果中记录缓存截止时间和“数据未更新”状态。
8. 每次同步后校验复权方式和交易时段范围；口径与缓存不一致时不得直接合并，应重建对应证券和周期的缓存。

### 11.5 保留数量与清理

LongPort 单次可以返回最多 1000 根 K 线。生产缓存默认不再按根数裁剪，应保留首次历史回填和后续增量同步得到的全部数据，以支持回测及机器学习。若未来需要清理，必须采用显式、可审计的保留策略。

生产缓存允许按保留策略清理并可从 LongPort 重建，但固定回归测试样本和已经关联扫描结果的行情快照不得随缓存清理而静默改变。

### 11.6 与核心算法的边界

核心算法不得直接读取 DuckDB、Parquet 或调用 LongPort。数据层完成同步与标准化后，通过以下接口把只读数据交给算法：

```python
scan_symbol(
    symbol: str,
    timeframe_bars: dict[str, DataFrame],
    config: ScannerConfig,
) -> ScanResult
```

这样可以在不改变因子和评分逻辑的情况下替换行情源、重建缓存、执行离线测试或注入固定历史样本。
