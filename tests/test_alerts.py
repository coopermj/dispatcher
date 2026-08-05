"""Failure alerting: every failure category gets a report entry with a
concrete workaround command; no-failure runs alert nothing."""
from modules.alerts import record_failure, build_failure_report, WORKAROUNDS


def test_record_failure_shape():
    failures = []
    record_failure(failures, "upload", "dispatch_001_TMD.pdf", "rmapi timed out")
    assert failures == [{"category": "upload",
                         "item": "dispatch_001_TMD.pdf",
                         "detail": "rmapi timed out"}]


def test_unknown_category_defaults_and_never_raises():
    failures = []
    record_failure(failures, "weird-new-thing", "x", "y")
    report = build_failure_report(failures)
    assert "weird-new-thing" in report
    assert "x" in report


def test_report_includes_workaround_per_category():
    failures = []
    record_failure(failures, "upload", "dispatch_001_TMD.pdf", "rmapi timed out")
    record_failure(failures, "conversion", "Some Article", "browser crashed")
    record_failure(failures, "prune", "3 docs", "rm failed")
    report = build_failure_report(failures)
    # every failure is listed
    assert "dispatch_001_TMD.pdf" in report and "rmapi timed out" in report
    assert "Some Article" in report
    # each category's workaround command appears
    assert WORKAROUNDS["upload"] in report
    assert WORKAROUNDS["conversion"] in report
    assert WORKAROUNDS["prune"] in report


def test_report_counts_failures_in_header():
    failures = []
    record_failure(failures, "upload", "a.pdf", "d1")
    record_failure(failures, "upload", "b.pdf", "d2")
    report = build_failure_report(failures)
    assert "2 failure" in report
