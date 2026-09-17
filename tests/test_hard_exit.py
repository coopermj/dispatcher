"""main.py's `__main__` block must not be able to hang after main() returns
(e.g. a lingering Playwright transport keeping the loop or interpreter alive).
For a cron job the acceptable answer is: flush the redirected log, then
os._exit with the intended status."""
import io
import os


def test_hard_exit_flushes_stdout_then_calls_os_exit(monkeypatch):
    import main

    calls = []
    monkeypatch.setattr(os, "_exit", lambda code: calls.append(("exit", code)))
    buf = io.StringIO()
    flushed = []
    buf.flush = lambda: flushed.append(True)  # type: ignore[method-assign]
    monkeypatch.setattr("sys.stdout", buf)
    monkeypatch.setattr("sys.stderr", io.StringIO())

    main.hard_exit(2)

    assert flushed, "stdout must be flushed before os._exit (cron redirects it to a block-buffered file)"
    assert calls == [("exit", 2)]


def test_main_block_uses_hard_exit_for_the_normal_return_path():
    from pathlib import Path
    body = Path(main_path()).read_text()
    assert "hard_exit(asyncio.run(main()))" in body


def main_path():
    import main
    return main.__file__
