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
RETRY_SECONDS = 15 * 60

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
    if info["status"] != "completed":
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
        self._last_attempt_at: float | None = None

    def start(self) -> None:
        threading.Thread(target=self._loop, daemon=True).start()

    def _last_dispatch_date(self) -> str | None:
        if not self._state_path.is_file():
            return None
        try:
            return json.loads(self._state_path.read_text(encoding="utf-8")).get("last_dispatch_date")
        except (OSError, ValueError):
            return None

    def _save_dispatch_date(self, date_str: str) -> None:
        self._state_path.write_text(json.dumps({"last_dispatch_date": date_str}), encoding="utf-8")

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
        if self._last_dispatch_date() == today:
            return
        if self.scan_service.active:
            return
        now_monotonic = time.monotonic()
        if self._last_attempt_at is not None and now_monotonic - self._last_attempt_at < RETRY_SECONDS:
            return
        self._last_attempt_at = now_monotonic
        succeeded = _run_scan_and_dispatch(
            self.scan_service, self.repository, self.settings, TIMEFRAMES, ("daily",)
        )
        if succeeded:
            self._save_dispatch_date(today)
