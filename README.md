# 圆弧底股票扫描器

本项目正在按 `V1_APPLICATION_DESIGN.md` 实现本地 Web 版本。

## 开发启动

后端：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

前端（PowerShell 执行策略阻止 `npm.ps1` 时使用 `npm.cmd`）：

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

浏览器访问 `http://127.0.0.1:5173`。

复制 `.env.example` 为 `.env` 并填写 LongPort 凭据。凭据文件已被 `.gitignore` 排除。

当前已经实现股票池读取、DuckDB schema、Parquet 增量缓存、LongPort 三周期真实行情读取、F1～F6 与跨周期评分、后台逐股扫描、任务进度查询、正式结果持久化和两栏 GUI。前端会轮询并显示扫描进度，完成后自动刷新评分。

尚待实现的是各详情 Tab 的实际图表和诊断内容、SSE 推送、历史收益回测统计、定时调度及更完整的缓存管理操作。

行情同步采用增量策略：证券/周期没有缓存时请求最近 1000 根；已有 Parquet 缓存时只请求最近 3 根，再按时间戳覆盖缓存尾部并去重。

扫描前会按周期判断缓存新鲜度：完全没有缓存的新股票允许在周末或节假日执行一次历史回填；已有缓存时，周线只在可能出现新的已完成交易周时同步，日线只在可能出现新的已完成交易日时同步，4小时缓存每3小时最多刷新一次。已有缓存的证券在美东周末和完整休市节假日不访问 LongPort，直接使用本地 Parquet 扫描。

Parquet 默认保留全部历史 K 线，不再按 1500 根裁剪。`CACHE_RETENTION_BARS` 默认留空；只有明确需要限制缓存时才设置正整数。

配置版本 `v2-weekly-close` 修正了周末扫描时错误排除当周已收盘周线的问题。升级后应重新执行一次正式扫描；旧批次保留用于审计，但不应与修正后的评分直接混用。

K 线图通过 `/api/v1/symbols/{symbol}/bars` 读取本地 Parquet 缓存并加载到内存，附加 EMA 后返回。浏览图表不会额外调用 LongPort。
