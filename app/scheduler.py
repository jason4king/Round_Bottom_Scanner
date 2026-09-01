from __future__ import annotations

import json
import threading
import time
import traceback
from datetime import time as dtime
from pathlib import Path

import pandas as pd

from app.buy_signal import build_wecom_message, evaluate_daily_signal
from app.config import Settings
from app.market_data import _NYSE_CALENDAR, ParquetBarRepository
from app.notifier import send_wecom_text
from app.scan_service import ScanService

DISPATCH_TIME = dtime(16, 15)
POLL_INTERVAL_SECONDS = 60
SCAN_POLL_SECONDS = 5
PUSH_GAP_SECONDS = 1.5


class DailyPushScheduler:
    """Runs an official scan shortly after the 16:15 ET daily close and pushes
    any newly confirmed daily-level buy signal to the WeCom webhook."""

    def __init__(self, settings: Settings, scan_service: ScanService, repository: ParquetBarRepository):
        self.settings = settings
        self.scan_service = scan_service
        self.repository = repository
        self._state_path = settings.database_path.parent / "daily_push_state.json"

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
            time.sleep(POLL_INTERVAL_SECONDS)

    def _tick(self) -> None:
        now_ny = pd.Timestamp.now(tz="America/New_York")
        today = now_ny.strftime("%Y-%m-%d")
        if not _NYSE_CALENDAR.is_session(today):
            return
        if now_ny.time() < DISPATCH_TIME:
            return
        if self._last_dispatch_date() == today:
            return
        if self.scan_service.active:
            return
        self._run_once(today)

    def _run_once(self, today: str) -> None:
        run_id, status, message = self.scan_service.create_run("official")
        if status != "running":
            return
        while True:
            time.sleep(SCAN_POLL_SECONDS)
            info = self.scan_service.run_status(run_id)
            if info and info["status"] in ("completed", "completed_with_errors"):
                break
        self._save_dispatch_date(today)
        self._dispatch_signals(run_id)

    def _dispatch_signals(self, run_id) -> None:
        webhook_url = self.settings.webhook_qiyeweixin
        if not webhook_url:
            return
        results = self.scan_service.run_results(run_id)
        for symbol, result in results.items():
            try:
                signal = evaluate_daily_signal(self.repository, symbol)
            except Exception:
                traceback.print_exc()
                continue
            if signal is None:
                continue
            message = build_wecom_message(symbol, signal, result["total_score"], result["triggered_factors"])
            try:
                send_wecom_text(webhook_url, message)
            except Exception:
                traceback.print_exc()
            time.sleep(PUSH_GAP_SECONDS)
