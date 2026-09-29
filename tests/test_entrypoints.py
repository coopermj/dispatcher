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


def test_run_pipeline_runs_main_unbuffered():
    """Python 3.14 raised the default file buffer to 128 KiB, so with stdout
    redirected to cron_run.log nothing appears for many minutes and, if
    timeout(1) kills the run, the buffered tail (with the crash) is lost and
    the crash email carries an empty log. Both main.py invocations must run
    unbuffered."""
    body = (ROOT / "run_pipeline.sh").read_text()
    invocations = [line for line in body.splitlines() if '"$PY" main.py' in line]
    assert invocations, "no main.py invocation found"
    for line in invocations:
        assert "PYTHONUNBUFFERED=1" in line, line


# ---------------------------------------------------------------------------
# Dead-man's switch: the alert channel died with the Gmail token on 2026-09-21
# and nothing could say so for six days. run_pipeline.sh pings a healthchecks
# URL (HEALTHCHECK_URL in .env) so an outside service notices silence.
# ---------------------------------------------------------------------------
import os
import subprocess
import textwrap


def _run_hc_ping(tmp_path, args, url="https://hc.example/abc", curl_exit=0):
    """Source hc_ping() out of run_pipeline.sh and call it with a stub curl
    that records its argv and any --data-binary body. Returns (rc, argv_lines, body)."""
    body_file = tmp_path / "curl_body"
    log = tmp_path / "curl_args"
    stub = tmp_path / "bin"
    stub.mkdir(parents=True)
    (stub / "curl").write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        printf '%s\\n' "$*" >> "{log}"
        for a in "$@"; do case "$a" in @*) cat "${{a#@}}" > "{body_file}";; esac; done
        exit {curl_exit}
        """))
    (stub / "curl").chmod(0o755)
    script = f"""
        set -u
        HC_URL="{url}"
        eval "$(sed -n '/^hc_ping()/,/^}}/p' "{ROOT / 'run_pipeline.sh'}")"
        hc_ping {args}
    """
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"})
    argv = log.read_text().splitlines() if log.exists() else []
    body = body_file.read_text() if body_file.exists() else None
    return r.returncode, argv, body, r.stderr


def test_hc_ping_start_success_and_fail_hit_the_right_urls(tmp_path):
    for kind, suffix in (("start", "/start"), ("success", ""), ("fail", "/fail")):
        rc, argv, _, err = _run_hc_ping(tmp_path / kind, kind)
        assert rc == 0, err
        assert len(argv) == 1 and argv[0].endswith(f"https://hc.example/abc{suffix}"), (kind, argv)


def test_hc_ping_fail_posts_the_given_body_file(tmp_path):
    report = tmp_path / "report.md"
    report.write_text("# Dispatch run: 1 failure(s)\n")
    rc, argv, body, err = _run_hc_ping(tmp_path, f"fail '{report}'")
    assert rc == 0, err
    assert body == "# Dispatch run: 1 failure(s)\n"


def test_hc_ping_is_a_noop_without_a_url(tmp_path):
    rc, argv, _, _ = _run_hc_ping(tmp_path, "success", url="")
    assert rc == 0 and argv == []


def test_hc_ping_never_fails_the_run_when_curl_fails(tmp_path):
    rc, argv, _, _ = _run_hc_ping(tmp_path, "success", curl_exit=22)
    assert rc == 0 and len(argv) == 1


def test_hc_ping_bounds_curl_so_a_slow_ping_cannot_stall_the_run(tmp_path):
    _, argv, _, _ = _run_hc_ping(tmp_path, "success")
    assert "-m 10" in argv[0] or "--max-time 10" in argv[0], argv


def test_run_pipeline_pings_start_then_success_or_fail():
    body = (ROOT / "run_pipeline.sh").read_text()
    main_at = body.index('"$PY" main.py')
    assert body.index("hc_ping start") < main_at, "start ping must precede the run"
    assert body.index("hc_ping success") > main_at
    assert body.index("hc_ping fail") > main_at
    assert "HEALTHCHECK_URL" in body


def test_settings_expose_healthcheck_url_defaulting_to_empty(monkeypatch):
    monkeypatch.delenv("HEALTHCHECK_URL", raising=False)
    import importlib
    import config.settings as settings
    importlib.reload(settings)
    assert settings.HEALTHCHECK_URL == ""


def test_wrapper_captures_only_the_url_despite_settings_banner_output(tmp_path):
    """config.settings prints status lines on import. The HC_URL capture in
    run_pipeline.sh must yield exactly the URL, or every ping goes to a
    multi-line garbage URL and the switch silently never fires."""
    body = (ROOT / "run_pipeline.sh").read_text()
    capture = next(l for l in body.splitlines() if l.startswith("HC_URL="))
    script = f'cd "{ROOT}"; PY=.venv/bin/python; {capture}; printf "%s" "$HC_URL"'
    venv_py = ROOT / ".venv" / "bin" / "python"
    if not venv_py.exists():  # worktree without a venv: point PY at the interpreter running pytest
        import sys
        script = f'cd "{ROOT}"; PY="{sys.executable}"; {capture}; printf "%s" "$HC_URL"'
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       env={**os.environ, "HEALTHCHECK_URL": "https://hc.example/abc"})
    assert r.stdout == "https://hc.example/abc", repr(r.stdout)



async def test_refresh_auth_does_not_attempt_an_atlantic_login(monkeypatch):
    """The Atlantic blocks automated browsers now; an interactive attempt would
    sit on the block page for its full 5-minute wait on every token refresh."""
    from unittest.mock import AsyncMock, MagicMock
    import refresh_auth
    bm = MagicMock()
    bm.start_browser_session = AsyncMock(return_value=True)
    bm.close_browser_session = AsyncMock()
    monkeypatch.setattr(refresh_auth, "BrowserManager", lambda: bm)
    am = MagicMock()
    am.authenticate_with_dispatch = AsyncMock(return_value=True)
    am.authenticate_with_atlantic = AsyncMock(return_value=True)
    assert await refresh_auth.refresh_dispatch(am) is True
    am.authenticate_with_atlantic.assert_not_called()


def test_refresh_tokens_mac_no_longer_ships_an_atlantic_jar():
    body = (ROOT / "refresh_tokens_mac.sh").read_text()
    assert "atlantic_cookies.json" not in body
