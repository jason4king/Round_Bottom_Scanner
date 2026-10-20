from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import time as dtime

import pandas as pd

from app.buy_signal import already_pushed, build_wecom_message, evaluate_signal, load_pushed_state, mark_pushed, save_pushed_state
from app.config import Settings
from app.market_data import TIMEFRAMES, _NYSE_CALENDAR, ParquetBarRepository
from app.notifier import send_wecom_text
from app.scan_service import ScanService

SCAN_POLL_SECONDS = 5
PUSH_GAP_SECONDS = 1.5
TICK_SECONDS = 60

def _run_scan_and_dispatch(
    scan_service: ScanService,
    repository: ParquetBarRepository,
    settings: Settings,
    sync_timeframes: tuple[str, ...],
    push_timeframes: tuple[str, ...],
) -> bool:
    run_id, status, _ = scan_service.create_run("official", sync_timeframes)
    if status != "running":
        return False
    while True:
        time.sleep(SCAN_POLL_SECONDS)
        info = scan_service.run_status(run_id)
        if info and info["status"] in ("completed", "completed_with_errors", "failed"):
            break
    if info["status"] not in ("completed", "completed_with_errors"):
        return False
    _dispatch_signals(scan_service, repository, settings, run_id, push_timeframes)
    return True


def _dispatch_signals(
    scan_service: ScanService,
    repository: ParquetBarRepository,
    settings: Settings,
    run_id,
    timeframes: tuple[str, ...],
) -> None:
    webhook_url = settings.webhook_qiyeweixin
    if not webhook_url:
        return
    results = scan_service.run_results(run_id)
    state = load_pushed_state(settings)
    changed = False
    for symbol, result in results.items():
        for timeframe in timeframes:
            try:
                signal = evaluate_signal(repository, symbol, timeframe)
            except Exception:
                traceback.print_exc()
                continue
            if signal is None or already_pushed(state, timeframe, symbol, signal):
                continue
            message = build_wecom_message(symbol, signal, result["total_score"], result["triggered_factors"])
            try:
                send_wecom_text(webhook_url, message)
                mark_pushed(state, timeframe, symbol, signal)
                changed = True
            except Exception:
                traceback.print_exc()
            time.sleep(PUSH_GAP_SECONDS)
    if changed:
        save_pushed_state(settings, state)


class DailyPushScheduler:
    """Runs a full official scan shortly after the 16:15 ET daily close and
    pushes any newly confirmed daily-level buy signal to WeCom."""

    DISPATCH_TIME = dtime(16, 15)

    def __init__(self, settings: Settings, scan_service: ScanService, repository: ParquetBarRepository):
        self.settings = settings
        self.scan_service = scan_service
        self.repository = repository
        self._state_path = settings.database_path.parent / "daily_push_state.json"
        self._skip_startup_date: str | None = None

    def start(self) -> None:
        now_ny = pd.Timestamp.now(tz="America/New_York")
        if now_ny.time() >= self.DISPATCH_TIME:
            # A service restart after the dispatch time must not immediately
            # launch a full-universe catch-up scan on a constrained VPS.
            self._skip_startup_date = now_ny.strftime("%Y-%m-%d")
        threading.Thread(target=self._loop, daemon=True).start()

    def _read_state(self) -> dict[str, str]:
        if not self._state_path.is_file():
            return {}
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _last_dispatch_date(self) -> str | None:
        return self._read_state().get("last_dispatch_date")

    def _last_attempt_date(self) -> str | None:
        return self._read_state().get("last_attempt_date")

    def _save_state(self, **updates: str) -> None:
        state = self._read_state()
        state.update(updates)
        temporary = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(self._state_path)

    def _save_dispatch_date(self, date_str: str) -> None:
        self._save_state(last_dispatch_date=date_str)

    def _save_attempt_date(self, date_str: str) -> None:
        self._save_state(last_attempt_date=date_str)

    def _loop(self) -> None:
        while True:
            try:
                self._tick()
            except Exception:
                traceback.print_exc()
            time.sleep(TICK_SECONDS)

    def _tick(self) -> None:
        now_ny = pd.Timestamp.now(tz="America/New_York")
        today = now_ny.strftime("%Y-%m-%d")
        if not _NYSE_CALENDAR.is_session(today):
            return
        if now_ny.time() < self.DISPATCH_TIME:
            return
        if self._skip_startup_date == today:
            return
        if self._last_dispatch_date() == today:
            return
        if self._last_attempt_date() == today:
            return
        if self.scan_service.active:
            return
        if not self.scan_service.provider.configured:
            return
        # Persist before starting work so a restart or failed partial run does
        # not launch another full scan later the same day.
        self._save_attempt_date(today)
        succeeded = _run_scan_and_dispatch(
            self.scan_service, self.repository, self.settings, TIMEFRAMES, ("daily",)
        )
        if succeeded:
            self._save_dispatch_date(today)
