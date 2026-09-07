from app.main import should_suppress_scheduler_notification


def test_exact_silent_marker_suppresses_notification():
    assert should_suppress_scheduler_notification("SCHEDULER_SILENT", [])
    assert should_suppress_scheduler_notification("  SCHEDULER_SILENT\n", [])


def test_regular_result_is_not_suppressed():
    assert not should_suppress_scheduler_notification("应用正常", [])


def test_artifacts_are_never_suppressed():
    assert not should_suppress_scheduler_notification(
        "SCHEDULER_SILENT", [{"name": "report.csv"}]
    )
