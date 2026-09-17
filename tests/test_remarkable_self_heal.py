"""ReMarkableManager must try one rmapi self-update per run when rmapi itself
looks broken, then retry the failed operation once — and must NOT do so for
auth failures, which a newer client cannot fix."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from modules import remarkable as rm
from modules.remarkable import ReMarkableManager
from modules.rmapi_updater import UpdateResult


def _ok(stdout=""):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def _fail(stderr="500 internal error"):
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


class Script:
    """subprocess.run stand-in: `put` fails until `heal()` is called, then works."""

    def __init__(self, put_stderr="500 internal error"):
        self.healed = False
        self.puts = 0
        self.put_stderr = put_stderr

    def __call__(self, cmd, **kw):
        sub = cmd[1] if cmd[1] != "-json" else cmd[2]
        if sub == "put":
            self.puts += 1
            return _ok() if self.healed else _fail(self.put_stderr)
        if sub == "mkdir":
            return _fail("already exists")
        return _ok("[]")  # ls, version


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    rmapi = tmp_path / "rmapi"
    rmapi.write_text("bin")
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    return rmapi, pdf


def _updated():
    return UpdateResult("updated", old_version="v0.0.35", new_version="v0.0.36", detail="")


def _latest():
    return UpdateResult("already-latest", old_version="v0.0.35", new_version="v0.0.35", detail="")


def test_upload_failure_triggers_self_update_and_retries_once(manager):
    rmapi, pdf = manager
    script = Script()

    def heal(path, **kw):
        script.healed = True
        return _updated()

    with patch.object(rm.subprocess, "run", script), patch.object(rm, "self_update", side_effect=heal) as su:
        failures = []
        mgr = ReMarkableManager(str(rmapi), failures=failures)
        assert mgr.upload_pdf(str(pdf), "News") is True
    assert su.call_count == 1
    assert script.puts == 4, "3 normal attempts + 1 retry after the update"
    assert any(f["category"] == "rmapi" and "v0.0.36" in f["detail"] for f in failures), failures


def test_no_retry_when_no_newer_release(manager):
    rmapi, pdf = manager
    script = Script()
    with patch.object(rm.subprocess, "run", script), patch.object(rm, "self_update", return_value=_latest()):
        failures = []
        mgr = ReMarkableManager(str(rmapi), failures=failures)
        assert mgr.upload_pdf(str(pdf), "News") is False
    assert script.puts == 3
    assert any(f["category"] == "rmapi" and "not a stale client" in f["detail"].lower() for f in failures)


def test_auth_errors_do_not_trigger_update(manager):
    rmapi, pdf = manager
    script = Script(put_stderr="401 Unauthorized: invalid user token")
    with patch.object(rm.subprocess, "run", script), patch.object(rm, "self_update") as su:
        mgr = ReMarkableManager(str(rmapi))
        assert mgr.upload_pdf(str(pdf), "News") is False
    su.assert_not_called()


def test_self_update_runs_at_most_once_per_run(manager):
    rmapi, pdf = manager
    script = Script()
    with patch.object(rm.subprocess, "run", script), patch.object(rm, "self_update", return_value=_latest()) as su:
        mgr = ReMarkableManager(str(rmapi))
        mgr.upload_pdf(str(pdf), "News")
        mgr.upload_pdf(str(pdf), "News")
    assert su.call_count == 1


def test_broken_rmapi_at_startup_is_healed(manager):
    rmapi, pdf = manager
    state = {"healed": False}

    def run(cmd, **kw):
        if cmd[1] == "ls":
            return _ok("[]") if state["healed"] else _fail("json: cannot unmarshal")
        return _ok()

    def heal(path, **kw):
        state["healed"] = True
        return _updated()

    with patch.object(rm.subprocess, "run", run), patch.object(rm, "self_update", side_effect=heal) as su:
        mgr = ReMarkableManager(str(rmapi))
    assert mgr.is_available() is True
    assert su.call_count == 1
