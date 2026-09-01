from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    service: Literal["ok"] = "ok"
    longport_configured: bool
    database_ready: bool
    watchlist_count: int
    watchlist_errors: list[str]
    longport_auth_mode: str


class WatchlistResponse(BaseModel):
    path: str
    symbols: list[str]
    errors: list[str]


class WatchlistUpdateRequest(BaseModel):
    symbols: list[str]


class AuthSettingsUpdateRequest(BaseModel):
    auth_mode: Literal["oauth", "apikey"]
    oauth_client_id: str | None = None


class ProxySettingsUpdateRequest(BaseModel):
    enabled: bool
    host: str = "127.0.0.1"
    port: int = Field(default=7890, ge=1, le=65535)


class ScanCreateRequest(BaseModel):
    run_type: Literal["official", "preview"] = "official"


class ScanCreateResponse(BaseModel):
    run_id: str
    status: str
    message: str


class ScanResultItem(BaseModel):
    symbol: str
    total_score: float
    triggered_factors: list[str]
    data_status: str
    f7_pattern: dict | None = None
    breakout_patterns: list[dict] = Field(default_factory=list)
