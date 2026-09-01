from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import pandas as pd
import exchange_calendars as xcals
import webbrowser
import os
from threading import Lock


TIMEFRAMES = ("weekly", "daily", "4hour")
_NYSE_CALENDAR = xcals.get_calendar("XNYS")
BAR_COLUMNS = (
    "symbol",
    "timeframe",
    "timestamp_utc",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "is_closed",
    "adjustment_type",
    "trade_session",
    "data_source",
    "updated_at",
)


class MarketDataProvider(Protocol):
    def fetch_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame: ...


class ProviderNotConfiguredError(RuntimeError):
    pass


class LongPortProvider:
    def __init__(self, configured: bool, app_key: str | None = None, app_secret: str | None = None, access_token: str | None = None, auth_mode: str = "apikey", oauth_client_id: str | None = None, region: str = "hk", proxy_enabled: bool = False, proxy_host: str = "127.0.0.1", proxy_port: int = 7890):
        self.configured = configured
        self._credentials = (app_key, app_secret, access_token)
        self.auth_mode = auth_mode.lower()
        self.oauth_client_id = oauth_client_id
        self.region = region
        self.proxy_enabled = proxy_enabled
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self._context = None
        self._auth_lock = Lock()

    @staticmethod
    def _open_oauth_url(url: str) -> None:
        if not webbrowser.open(url):
            print(f"请在浏览器中打开 LongPort OAuth 授权地址：{url}")

    def ensure_authenticated(self) -> None:
        """Initialize auth even if a scan does not need a market-data request."""
        if not self.configured:
            raise ProviderNotConfiguredError("LongPort 凭据尚未配置")
        with self._auth_lock:
            if self._context is not None:
                return
            from longport.openapi import Config, OAuthBuilder, QuoteContext
            self._apply_proxy()
            if self.auth_mode == "oauth":
                if not self.oauth_client_id:
                    raise ProviderNotConfiguredError("LongPort OAuth client_id 尚未配置")
                os.environ["LONGPORT_REGION"] = self.region
                oauth = OAuthBuilder(self.oauth_client_id).build(self._open_oauth_url)
                config = Config.from_oauth(oauth)
            else:
                config = Config.from_apikey(*self._credentials)
            self._context = QuoteContext(config)

    def _apply_proxy(self) -> None:
        keys=("HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","http_proxy","https_proxy","all_proxy")
        if self.proxy_enabled:
            proxy=f"http://{self.proxy_host}:{self.proxy_port}"
            for key in keys:
                os.environ[key]=proxy
            os.environ["NO_PROXY"]="127.0.0.1,localhost"
            os.environ["no_proxy"]="127.0.0.1,localhost"
        else:
            for key in keys:
                os.environ.pop(key,None)

    def configure_proxy(self, enabled: bool, host: str, port: int) -> None:
        with self._auth_lock:
            self.proxy_enabled=enabled
            self.proxy_host=host
            self.proxy_port=port
            self._apply_proxy()
            self._context=None

    def configure_auth(self, auth_mode: str, oauth_client_id: str | None) -> None:
        with self._auth_lock:
            self.auth_mode = auth_mode.lower()
            self.oauth_client_id = oauth_client_id
            self.configured = bool(oauth_client_id) if self.auth_mode == "oauth" else all(self._credentials)
            self._context = None

    def fetch_bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        if not self.configured:
            raise ProviderNotConfiguredError("LongPort 凭据尚未配置")
        from longport.openapi import AdjustType, Period, TradeSessions
        self.ensure_authenticated()
        periods = {"weekly": Period.Week, "daily": Period.Day, "4hour": Period.Min_240}
        trade_sessions = TradeSessions.All if timeframe == "4hour" else TradeSessions.Intraday
        trade_session_name = "all" if timeframe == "4hour" else "intraday"
        candles = self._context.candlesticks(symbol, periods[timeframe], count, AdjustType.ForwardAdjust, trade_sessions)
        now = pd.Timestamp.now(tz="UTC")
        rows=[]
        for candle in candles:
            timestamp=pd.Timestamp(candle.timestamp)
            if timestamp.tzinfo is None: timestamp=timestamp.tz_localize("UTC")
            else: timestamp=timestamp.tz_convert("UTC")
            is_closed = _is_closed(timestamp, timeframe, now)
            rows.append({"symbol":symbol,"timeframe":timeframe,"timestamp_utc":timestamp,"open":float(candle.open),"high":float(candle.high),"low":float(candle.low),"close":float(candle.close),"volume":float(candle.volume),"is_closed":is_closed,"adjustment_type":"forward","trade_session":trade_session_name,"data_source":"LongPort","updated_at":now})
        return pd.DataFrame(rows,columns=BAR_COLUMNS)

    def fetch_security_names(self, symbols: list[str]) -> dict[str, dict[str, str | None]]:
        """Return localized security names from LongPort static information."""
        if not symbols:
            return {}
        self.ensure_authenticated()
        return {
            item.symbol: {
                "name_cn": item.name_cn or None,
                "name_hk": item.name_hk or None,
                "name_en": item.name_en or None,
            }
            for item in self._context.static_info(symbols)
        }


def _is_closed(timestamp: pd.Timestamp, timeframe: str, now: pd.Timestamp) -> bool:
    if timeframe == "4hour": return timestamp + pd.Timedelta(hours=4) <= now
    ny_now=now.tz_convert("America/New_York"); ny_bar=timestamp.tz_convert("America/New_York")
    if timeframe == "daily":
        if ny_bar.date() < ny_now.date(): return True
        if ny_bar.date() > ny_now.date(): return False
        session=pd.Timestamp(ny_bar.date())
        return bool(_NYSE_CALENDAR.is_session(session) and now >= _NYSE_CALENDAR.session_close(session))
    bar_week=(ny_bar.isocalendar().year,ny_bar.isocalendar().week)
    now_week=(ny_now.isocalendar().year,ny_now.isocalendar().week)
    if bar_week < now_week:
        return True
    if bar_week != now_week:return False
    week_start=pd.Timestamp(ny_now.date())-pd.Timedelta(days=ny_now.weekday())
    week_sessions=_NYSE_CALENDAR.sessions_in_range(week_start,week_start+pd.Timedelta(days=6))
    return bool(len(week_sessions) and now >= _NYSE_CALENDAR.session_close(week_sessions[-1]))


def cache_needs_sync(bars: pd.DataFrame, timeframe: str, now: pd.Timestamp | None = None) -> bool:
    """Return whether a cache can contain a newly completed bar.

    Daily/weekly decisions use the official NYSE regular-session close.
    Four-hour data uses a three-hour refresh TTL because it includes all sessions.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    # A cache miss is a one-time historical backfill, not an incremental
    # refresh. Historical bars can be requested while the market is closed.
    if bars.empty:
        return True
    ny_now = now.tz_convert("America/New_York")

    # Never contact LongPort on a US weekend or a full-session NYSE holiday.
    # Early-close days use the close supplied by the NYSE calendar.
    today = pd.Timestamp(ny_now.date())
    if not _NYSE_CALENDAR.is_session(today):
        return False

    latest = pd.Timestamp(bars["timestamp_utc"].max())
    updated = pd.Timestamp(bars["updated_at"].max())
    if timeframe == "4hour":
        return now - updated >= pd.Timedelta(hours=3)

    latest_ny = latest.tz_convert("America/New_York")
    sessions = _NYSE_CALENDAR.sessions_in_range(today - pd.Timedelta(days=14), today)
    if now < _NYSE_CALENDAR.session_close(today):
        sessions = sessions[sessions < today]
    if sessions.empty:
        return False
    completed_date = sessions[-1].date()

    if timeframe == "daily":
        if latest_ny.date() >= completed_date:
            return False
        return True

    # A weekly candle is complete only after the final NYSE session of its week.
    completed_session = pd.Timestamp(completed_date)
    week_end = completed_session + pd.Timedelta(days=6 - completed_session.weekday())
    week_sessions = _NYSE_CALENDAR.sessions_in_range(
        completed_session - pd.Timedelta(days=completed_session.weekday()), week_end
    )
    if week_sessions.empty or completed_session != week_sessions[-1]:
        previous_week_end = completed_session - pd.Timedelta(days=completed_session.weekday() + 1)
        previous_sessions = _NYSE_CALENDAR.sessions_in_range(
            previous_week_end - pd.Timedelta(days=6), previous_week_end
        )
        if previous_sessions.empty:
            return False
        completed_date = previous_sessions[-1].date()

    completed_week = pd.Timestamp(completed_date).isocalendar()
    latest_week = latest_ny.isocalendar()
    if (latest_week.year, latest_week.week) >= (completed_week.year, completed_week.week):
        return False
    return True


@dataclass(frozen=True)
class CacheSummary:
    symbol: str
    timeframe: str
    row_count: int
    earliest_timestamp: str | None
    latest_timestamp: str | None


class ParquetBarRepository:
    def __init__(self, root: Path, retention_bars: int | None = None):
        self.root = root
        self.retention_bars = retention_bars

    def path_for(self, symbol: str, timeframe: str) -> Path:
        if timeframe not in TIMEFRAMES:
            raise ValueError(f"不支持的周期：{timeframe}")
        safe_symbol = quote(symbol, safe="")
        return self.root / f"timeframe={timeframe}" / f"symbol={safe_symbol}" / "bars.parquet"

    def has_cache(self, symbol: str, timeframe: str) -> bool:
        return self.path_for(symbol, timeframe).is_file()

    def read(self, symbol: str, timeframe: str) -> pd.DataFrame:
        path = self.path_for(symbol, timeframe)
        if not path.is_file():
            return pd.DataFrame(columns=BAR_COLUMNS)
        return pd.read_parquet(path)

    def merge(self, symbol: str, timeframe: str, incoming: pd.DataFrame) -> pd.DataFrame:
        normalized = validate_bars(incoming, symbol, timeframe)
        existing = self.read(symbol, timeframe)
        # Avoid concatenating the schema-only empty frame returned on a cache miss.
        # Besides being unnecessary, pandas is deprecating its dtype inference for
        # empty/all-NA concat operands.
        merged = (
            normalized.copy()
            if existing.empty
            else pd.concat([existing, normalized], ignore_index=True)
        )
        merged = (
            merged.sort_values("updated_at")
            .drop_duplicates(
                subset=["symbol", "timeframe", "timestamp_utc", "adjustment_type"],
                keep="last",
            )
            .sort_values("timestamp_utc")
            .reset_index(drop=True)
        )
        if self.retention_bars is not None:
            merged = merged.tail(self.retention_bars).reset_index(drop=True)
        path = self.path_for(symbol, timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        merged.to_parquet(path, index=False)
        return merged


def validate_bars(frame: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"K 线缺少字段：{', '.join(sorted(missing))}")
    result = frame.loc[:, BAR_COLUMNS].copy()
    if result.empty:
        raise ValueError("K 线数据为空")
    if set(result["symbol"]) != {symbol} or set(result["timeframe"]) != {timeframe}:
        raise ValueError("K 线证券代码或周期与请求不一致")
    if set(result["adjustment_type"]) != {"forward"}:
        raise ValueError("K 线必须使用前复权")
    expected_session = "all" if timeframe == "4hour" else "intraday"
    if set(result["trade_session"]) != {expected_session}:
        raise ValueError(f"K 线交易时段必须为 {expected_session}")
    result["timestamp_utc"] = pd.to_datetime(result["timestamp_utc"], utc=True)
    result["updated_at"] = pd.to_datetime(result["updated_at"], utc=True)
    return result.sort_values("timestamp_utc").reset_index(drop=True)
