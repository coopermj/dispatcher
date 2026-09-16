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
