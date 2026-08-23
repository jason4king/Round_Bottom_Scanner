from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    scanner_host: str = "127.0.0.1"
    scanner_port: int = 8000
    watchlist_path: Path = PROJECT_ROOT / "watchlist.txt"
    database_path: Path = PROJECT_ROOT / "data" / "scanner.duckdb"
    parquet_root: Path = PROJECT_ROOT / "data" / "market"
    bars_per_timeframe: int = 1000
    cache_retention_bars: int | None = None
    tail_refresh_bars: int = 3
    auto_backfill_new_symbols: bool = True
    deduplicate_unchanged_results: bool = True
    algorithm_version: str = "legacy-extracted-v1"
    config_version: str = "v2-weekly-close"

    longport_app_key: str | None = None
    longport_app_secret: str | None = None
    longport_access_token: str | None = None
    longport_auth_mode: str = "apikey"
    longport_oauth_client_id: str | None = None
    longport_region: str = "hk"
    network_proxy_enabled: bool = False
    network_proxy_host: str = "127.0.0.1"
    network_proxy_port: int = 7890

    @property
    def longport_configured(self) -> bool:
        if self.longport_auth_mode.lower() == "oauth":
            return bool(self.longport_oauth_client_id)
        return all(
            (self.longport_app_key, self.longport_app_secret, self.longport_access_token)
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.parquet_root.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
