from app.main import should_suppress_scheduler_notification


def test_exact_silent_marker_suppresses_notification():
    assert should_suppress_scheduler_notification("SCHEDULER_SILENT", [])
    assert should_suppress_scheduler_notification("  SCHEDULER_SILENT\n", [])


def test_regular_result_is_not_suppressed():
    assert not should_suppress_scheduler_notification("应用正常", [])


def test_marker_inside_a_longer_answer_is_not_suppressed():
    """The match must stay exact so the marker cannot be emitted incidentally."""
    assert not should_suppress_scheduler_notification("SCHEDULER_SILENT 但发现异常", [])
    assert not should_suppress_scheduler_notification("状态：SCHEDULER_SILENT", [])


def test_working_files_do_not_defeat_the_marker():
    """A healthy monitor run leaves logs and snapshots behind.

    Those are not deliverables, so they must not turn a silent run into a
    message full of attachment lines. Task prompts say the marker means the
    scheduler records success without sending anything.
    """
    assert should_suppress_scheduler_notification(
        "SCHEDULER_SILENT",
        [{"name": "logs_raw.txt"}, {"name": "snapshot1.json"}, {"name": "coolify_monitor.pyc"}],
    )
