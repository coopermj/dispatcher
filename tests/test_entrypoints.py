"""The cron box (Linux) runs the same checked-in files as the Mac; nothing may
hardcode a Mac path. A Mac-specific shebang in main.py broke the first deploy
with exit 126 ('cannot execute')."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_main_py_shebang_is_portable():
    first = (ROOT / "main.py").read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3", first


def test_run_pipeline_invokes_main_via_venv_python():
    """The wrapper must not rely on main.py's shebang resolving the venv."""
    body = (ROOT / "run_pipeline.sh").read_text()
    assert '"$PY" main.py' in body, "run_pipeline.sh should run main.py with the venv interpreter"
    assert "./main.py >" not in body


def test_run_pipeline_bounds_main_with_timeout():
    """A hung Chromium kept one run alive for 27 days; every later cron run
    tripped the collision guard. The wrapper must put a ceiling on main.py.
    GNU `timeout` exists on the Linux box but not on macOS, so it is guarded."""
    body = (ROOT / "run_pipeline.sh").read_text()
    assert "command -v timeout" in body, "guard timeout(1) so the script still runs on macOS"
    assert 'timeout ' in body and '"$PY" main.py' in body
    # 4h fits between the 6:30/7:05 morning slots and the 17:00 slot, and
    # between 17:00 and the next morning.
    assert "4h" in body


def test_run_pipeline_treats_timeout_exit_as_crash():
    """timeout(1) exits 124 when it fired (137 if it had to SIGKILL). Neither is
    rc=2 (main.py already alerted), so the crash-alert branch must fire and the
    email should say it was a timeout, not a generic crash."""
    body = (ROOT / "run_pipeline.sh").read_text()
    assert "124" in body, "run_pipeline.sh should name timeout's exit status 124"
    assert "TIMED OUT" in body
