from __future__ import annotations

import json
import math
from app.json_utils import json_safe
import pandas as pd
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import Database
from app.market_data import LongPortProvider, ParquetBarRepository, _is_closed
from app.scan_service import ScanService
from app.schemas import (
    ScanCreateRequest,
    ScanCreateResponse,
    ScanResultItem,
    StatusResponse,
    WatchlistResponse,
    WatchlistUpdateRequest,
    AuthSettingsUpdateRequest,
    ProxySettingsUpdateRequest,
)
from app.watchlist import load_watchlist, save_watchlist
from app.watchlist import normalize_symbol
from app.scanner_engine import add_indicators
from app.market_structure import calculate_market_structure
from trendline_indicator import add_trendline_channels


settings = get_settings()
database = Database(settings.database_path)
provider = LongPortProvider(settings.longport_configured, settings.longport_app_key, settings.longport_app_secret, settings.longport_access_token, settings.longport_auth_mode, settings.longport_oauth_client_id, settings.longport_region, settings.network_proxy_enabled, settings.network_proxy_host, settings.network_proxy_port)
repository = ParquetBarRepository(settings.parquet_root, settings.cache_retention_bars)
scan_service = ScanService(settings, database, provider, repository)


@asynccontextmanager
async def lifespan(_: FastAPI):
    database.initialize()
    try:
        yield
    finally:
        database.close()


app = FastAPI(title="圆弧底股票扫描器", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type"],
)


def _update_env_values(values: dict[str, str]) -> None:
    path = settings.model_config.get("env_file")
    env_path = Path(path) if path else Path(".env")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    remaining = dict(values); updated=[]
    for line in lines:
        key = line.split("=",1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
        else:
            updated.append(line)
    updated.extend(f"{key}={value}" for key,value in remaining.items())
    temporary=env_path.with_suffix(env_path.suffix+".tmp")
    temporary.write_text("\n".join(updated)+"\n",encoding="utf-8")
    temporary.replace(env_path)


@app.get("/api/v1/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    try:
        watchlist = load_watchlist(settings.watchlist_path)
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return StatusResponse(
        longport_configured=settings.longport_configured,
        database_ready=settings.database_path.is_file(),
        watchlist_count=len(watchlist.symbols),
        watchlist_errors=watchlist.errors,
        longport_auth_mode=settings.longport_auth_mode,
    )


@app.get("/api/v1/settings/auth")
def get_auth_settings():
    return {"auth_mode":settings.longport_auth_mode,"oauth_client_id":settings.longport_oauth_client_id,"configured":settings.longport_configured,"token_managed_by_sdk":settings.longport_auth_mode.lower()=="oauth"}


@app.put("/api/v1/settings/auth")
def update_auth_settings(request: AuthSettingsUpdateRequest):
    client_id=(request.oauth_client_id or "").strip() or None
    if request.auth_mode=="oauth" and not client_id:
        raise HTTPException(status_code=422,detail="OAuth 模式必须填写 client_id")
    _update_env_values({"LONGPORT_AUTH_MODE":request.auth_mode,"LONGPORT_OAUTH_CLIENT_ID":client_id or ""})
    settings.longport_auth_mode=request.auth_mode
    settings.longport_oauth_client_id=client_id
    provider.configure_auth(request.auth_mode,client_id)
    return get_auth_settings()


@app.post("/api/v1/settings/auth/oauth/authorize")
def authorize_oauth():
    if settings.longport_auth_mode.lower()!="oauth":
        raise HTTPException(status_code=409,detail="请先将认证方式切换为 OAuth")
    try:
        provider.ensure_authenticated()
    except Exception as exc:
        raise HTTPException(status_code=503,detail=f"OAuth 授权失败：{exc}") from exc
    return {"status":"authorized","auth_mode":"oauth"}


@app.get("/api/v1/settings/proxy")
def get_proxy_settings():
    return {"enabled":settings.network_proxy_enabled,"host":settings.network_proxy_host,"port":settings.network_proxy_port}


@app.put("/api/v1/settings/proxy")
def update_proxy_settings(request: ProxySettingsUpdateRequest):
    host=request.host.strip()
    if request.enabled and not host:
        raise HTTPException(status_code=422,detail="启用代理时必须填写代理主机")
    _update_env_values({"NETWORK_PROXY_ENABLED":str(request.enabled).lower(),"NETWORK_PROXY_HOST":host or "127.0.0.1","NETWORK_PROXY_PORT":str(request.port)})
    settings.network_proxy_enabled=request.enabled
    settings.network_proxy_host=host or "127.0.0.1"
    settings.network_proxy_port=request.port
    provider.configure_proxy(settings.network_proxy_enabled,settings.network_proxy_host,settings.network_proxy_port)
    return get_proxy_settings()


@app.get("/api/v1/watchlist", response_model=WatchlistResponse)
def get_watchlist() -> WatchlistResponse:
    try:
        watchlist = load_watchlist(settings.watchlist_path)
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return WatchlistResponse(
        path=str(settings.watchlist_path), symbols=watchlist.symbols, errors=watchlist.errors
    )


@app.put("/api/v1/watchlist", response_model=WatchlistResponse)
def update_watchlist(request: WatchlistUpdateRequest) -> WatchlistResponse:
    try:
        watchlist = save_watchlist(settings.watchlist_path, request.symbols)
    except (OSError, UnicodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if watchlist.errors:
        raise HTTPException(status_code=422, detail="；".join(watchlist.errors))
    return WatchlistResponse(path=str(settings.watchlist_path), symbols=watchlist.symbols, errors=[])


@app.post("/api/v1/scans", response_model=ScanCreateResponse, status_code=202)
def create_scan(request: ScanCreateRequest) -> ScanCreateResponse:
    try:
        run_id, status, message = scan_service.create_run(request.run_type)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ScanCreateResponse(run_id=str(run_id), status=status, message=message)

@app.get("/api/v1/scans/{run_id}")
def get_scan(run_id: str):
    from uuid import UUID
    try: parsed=UUID(run_id)
    except ValueError: raise HTTPException(status_code=422,detail="无效的任务 ID")
    result=scan_service.run_status(parsed)
    if result is None:raise HTTPException(status_code=404,detail="扫描任务不存在")
    return result


@app.get("/api/v1/scans-latest")
def get_latest_scan():
    with database.connect(read_only=True) as c:
        row=c.execute("SELECT run_id FROM scan_runs ORDER BY started_at DESC LIMIT 1").fetchone()
    if not row:
        raise HTTPException(status_code=404,detail="尚无扫描任务")
    return scan_service.run_status(row[0])


@app.get("/api/v1/results/latest", response_model=list[ScanResultItem])
def get_latest_results() -> list[ScanResultItem]:
    with database.connect(read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT r.symbol, r.total_score, r.triggered_factors_json, r.data_status, r.run_id
            FROM scan_results r
            JOIN scan_runs s ON s.run_id = r.run_id
            WHERE s.run_type = 'official' AND s.status IN ('completed', 'completed_with_errors')
              AND s.completed_at = (
                SELECT max(completed_at) FROM scan_runs
                WHERE run_type = 'official' AND status IN ('completed', 'completed_with_errors')
              )
            ORDER BY r.total_score DESC, r.symbol
            """
        ).fetchall()
        patterns={};classic_patterns={}
        for row in rows:
            pattern=connection.execute("SELECT timeframe,details_json FROM factor_results WHERE run_id=? AND symbol=? AND factor_id='F7' AND triggered ORDER BY CAST(json_extract(details_json,'$.confidence') AS DOUBLE) DESC LIMIT 1",[row[4],row[0]]).fetchone()
            if pattern:patterns[row[0]]={**json.loads(pattern[1]),"timeframe":pattern[0]}
            classic=connection.execute("SELECT timeframe,factor_id,signal_name,details_json FROM factor_results WHERE run_id=? AND symbol=? AND factor_id LIKE 'P%' AND triggered ORDER BY timeframe,factor_id",[row[4],row[0]]).fetchall()
            classic_patterns[row[0]]=[{"timeframe":item[0],"pattern_id":item[1],"signal_name":item[2],**json.loads(item[3])} for item in classic]
    return [
        ScanResultItem(
            symbol=row[0],
            total_score=row[1],
            triggered_factors=json.loads(row[2]),
            data_status=row[3],
            f7_pattern=patterns.get(row[0]),
            classic_patterns=classic_patterns.get(row[0],[]),
        )
        for row in rows
    ]


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/symbols/{symbol}/bars")
def get_symbol_bars(
    symbol: str,
    timeframe: str = Query("daily", pattern="^(weekly|daily|4hour)$"),
    limit: int = Query(300, ge=20, le=1500),
    include_open: bool = False,
):
    """Return chart bars from local Parquet only; never calls LongPort."""
    try:
        normalized_symbol = normalize_symbol(symbol)
        bars = repository.read(normalized_symbol, timeframe)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if bars.empty:
        raise HTTPException(status_code=404, detail="本地尚无该证券和周期的 K 线缓存")
    now = pd.Timestamp.now(tz="UTC")
    bars = bars.copy()
    bars["is_closed"] = bars["timestamp_utc"].map(lambda value: _is_closed(value, timeframe, now))
    if not include_open:
        bars = bars[bars["is_closed"]]
    if bars.empty:
        raise HTTPException(status_code=404, detail="本地缓存中没有可用的已收盘 K 线")
    # Compute long EMAs over the full cached history, then trim the response.
    chart = bars.set_index("timestamp_utc")
    chart = add_indicators(chart.rename(columns={"close": "Close"}))
    chart = add_trendline_channels(chart, lookback=240, peak_distance=5)
    structure_radius = {"weekly": 15, "daily": 20, "4hour": 12}[timeframe]
    market_structure = calculate_market_structure(chart, pivot_radius=structure_radius)
    chart = chart.tail(limit)
    payload = []
    for timestamp, row in chart.iterrows():
        payload.append({
            "timestamp": timestamp.isoformat(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["Close"]),
            "volume": float(row["volume"]),
            "ema12": float(row["EMA12"]), "ema144": float(row["EMA144"]),
            "ema169": float(row["EMA169"]), "ema576": float(row["EMA576"]),
            "ema676": float(row["EMA676"]),
            "rsi": float(row["RSI10"]) if pd.notna(row["RSI10"]) else None,
            "rsi_signal": float(row["RSI_SIGNAL10"]) if pd.notna(row["RSI_SIGNAL10"]) else None,
            "rsi_w_bottom": bool(row["RSI_W_BOTTOM"]),
            "rsi_bullish_divergence": bool(row["RSI_BULL_DIVERGENCE"]),
            "rsi_order_block_confluence": bool(row["RSI_ORDER_BLOCK_CONFLUENCE"]),
            "bullish_order_block_distance_pct": float(row["BULLISH_OB_DISTANCE_PCT"]) if pd.notna(row["BULLISH_OB_DISTANCE_PCT"]) else None,
            "rsi_enhanced_buy": bool(row["RSI_ENHANCED_BUY"]),
            "rsi_breakout_buy": bool(row["RSI_BREAKOUT_BUY"]),
            "rsi_v_bottom_buy": bool(row["RSI_V_BOTTOM_BUY"]),
            "rsi_neckline": float(row["RSI_NECKLINE"]) if pd.notna(row["RSI_NECKLINE"]) else None,
            "rsi_stop_level": float(row["RSI_STOP_LEVEL"]) if pd.notna(row["RSI_STOP_LEVEL"]) else None,
            "rsi_breakout_volume_ratio": float(row["RSI_BREAKOUT_VOLUME_RATIO"]) if pd.notna(row["RSI_BREAKOUT_VOLUME_RATIO"]) else None,
            "trend_support": float(row["trend_support"]) if math.isfinite(float(row["trend_support"])) else None,
            "trend_resistance": float(row["trend_resistance"]) if math.isfinite(float(row["trend_resistance"])) else None,
            "is_closed": bool(row["is_closed"]),
        })
    return {
        "symbol": normalized_symbol,
        "timeframe": timeframe,
        "source": "local_parquet",
        "adjustment_type": "forward",
        "trade_session": "all",
        "count": len(payload),
        "market_structure": market_structure,
        "bars": payload,
    }


def _latest_official_run(connection, symbol: str):
    return connection.execute(
        """SELECT s.run_id, s.completed_at, s.algorithm_version, s.config_version
           FROM scan_runs s JOIN scan_results r ON r.run_id=s.run_id
           WHERE r.symbol=? AND s.run_type='official'
             AND s.status IN ('completed','completed_with_errors')
           ORDER BY s.completed_at DESC LIMIT 1""", [symbol]
    ).fetchone()


@app.get("/api/v1/symbols/{symbol}/diagnostics")
def get_symbol_diagnostics(symbol: str):
    normalized=normalize_symbol(symbol)
    with database.connect(read_only=True) as c:
        run=_latest_official_run(c,normalized)
        if not run:raise HTTPException(status_code=404,detail="尚无该证券的正式扫描结果")
        summary=c.execute("SELECT total_score,base_total,confluence_total,pre_multiplier_score,coverage_multiplier,weekly_score,daily_score,four_hour_score,triggered_factors_json,weekly_bar_timestamp,daily_bar_timestamp,four_hour_bar_timestamp FROM scan_results WHERE run_id=? AND symbol=?",[run[0],normalized]).fetchone()
        factors=c.execute("SELECT timeframe,factor_id,triggered,signal_name,factor_tier,base_score,timeframe_multiplier,score_contribution,bar_timestamp,reason,details_json FROM factor_results WHERE run_id=? AND symbol=? ORDER BY CASE timeframe WHEN 'weekly' THEN 1 WHEN 'daily' THEN 2 ELSE 3 END,factor_id",[run[0],normalized]).fetchall()
    return json_safe({"run_id":str(run[0]),"completed_at":run[1],"algorithm_version":run[2],"config_version":run[3],"symbol":normalized,"scoring":{"total_score":summary[0],"base_total":summary[1],"confluence_total":summary[2],"pre_multiplier_score":summary[3],"coverage_multiplier":summary[4],"timeframe_scores":{"weekly":summary[5],"daily":summary[6],"4hour":summary[7]},"triggered_factors":json.loads(summary[8])},"bar_timestamps":{"weekly":summary[9],"daily":summary[10],"4hour":summary[11]},"factors":[{"timeframe":r[0],"factor_id":r[1],"triggered":r[2],"signal_name":r[3],"tier":r[4],"base_score":r[5],"multiplier":r[6],"contribution":r[7],"bar_timestamp":r[8],"reason":r[9],"details":json.loads(r[10])} for r in factors]})


@app.get("/api/v1/symbols/{symbol}/history")
def get_symbol_history(symbol: str, limit:int=Query(180,ge=1,le=1000)):
    normalized=normalize_symbol(symbol)
    with database.connect(read_only=True) as c:
        rows=c.execute("SELECT s.run_id,s.completed_at,s.market_data_cutoff,s.algorithm_version,r.total_score,r.triggered_factors_json FROM scan_runs s JOIN scan_results r ON r.run_id=s.run_id WHERE r.symbol=? AND s.run_type='official' AND s.status IN ('completed','completed_with_errors') ORDER BY s.completed_at DESC LIMIT ?",[normalized,limit]).fetchall()
    return {"symbol":normalized,"history":[{"run_id":str(r[0]),"completed_at":r[1],"market_data_cutoff":r[2],"algorithm_version":r[3],"total_score":r[4],"triggered_factors":json.loads(r[5])} for r in reversed(rows)]}


@app.get("/api/v1/symbols/{symbol}/cache")
def get_symbol_cache(symbol: str):
    normalized=normalize_symbol(symbol)
    with database.connect(read_only=True) as c:
        rows=c.execute("SELECT timeframe,row_count,earliest_timestamp,latest_timestamp,adjustment_type,trade_session,sync_status,updated_at,last_error FROM market_cache_manifest WHERE symbol=? ORDER BY CASE timeframe WHEN 'weekly' THEN 1 WHEN 'daily' THEN 2 ELSE 3 END",[normalized]).fetchall()
    return {"symbol":normalized,"items":[{"timeframe":r[0],"row_count":r[1],"earliest_timestamp":r[2],"latest_timestamp":r[3],"adjustment_type":r[4],"trade_session":r[5],"sync_status":r[6],"updated_at":r[7],"last_error":r[8]} for r in rows]}
