from app import scheduler


class FakeScanService:
    def __init__(self, final_status: str):
        self.final_status = final_status

    def create_run(self, run_type, sync_timeframes):
        return "run-id", "running", ""

    def run_status(self, run_id):
        return {"status": self.final_status}


def test_failed_scan_returns_without_dispatch(monkeypatch):
    monkeypatch.setattr(scheduler.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        scheduler,
        "_dispatch_signals",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not dispatch")),
    )

    succeeded = scheduler._run_scan_and_dispatch(
        FakeScanService("failed"), object(), object(), ("daily",), ("daily",)
    )

    assert succeeded is False


def test_completed_scan_dispatches_and_returns_success(monkeypatch):
    dispatched = []
    monkeypatch.setattr(scheduler.time, "sleep", lambda _: None)
    monkeypatch.setattr(scheduler, "_dispatch_signals", lambda *args: dispatched.append(args))

    succeeded = scheduler._run_scan_and_dispatch(
        FakeScanService("completed"), object(), object(), ("daily",), ("daily",)
    )

    assert succeeded is True
    assert len(dispatched) == 1


def test_partially_failed_scan_is_retried(monkeypatch):
    monkeypatch.setattr(scheduler.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        scheduler,
        "_dispatch_signals",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not dispatch incomplete scan")),
    )

    succeeded = scheduler._run_scan_and_dispatch(
        FakeScanService("completed_with_errors"), object(), object(), ("daily",), ("daily",)
    )

    assert succeeded is False
