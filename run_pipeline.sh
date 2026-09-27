#!/usr/bin/env bash
# Cron wrapper for the pipeline: single-instance lock + collision/crash alerts.
#
# - flock guarantees only one run at a time; a scheduled run that finds a
#   previous one still going emails an alert and exits cleanly.
# - main.py alerts on its own internal failures; this wrapper additionally
#   catches hard crashes (nonzero exit before alerting could run) and emails
#   the log tail with recovery steps.
# - main.py runs under timeout(1) so a hung Chromium can never wedge the box:
#   one run stuck in browser.close() for 27 days once made every later cron
#   run exit on the collision guard. 4h fits between the 6:30/7:05 morning
#   slots and the 17:00 slot, and between 17:00 and the next morning.
# - Dead-man's switch: if HEALTHCHECK_URL is set in .env, every run pings
#   <url>/start, then <url> on success or <url>/fail with the failure report.
#   The email alerts above all ride the Gmail token; when it expired on
#   2026-09-21 they went silent for six days. An outside service that expects
#   a ping per cron slot notices silence no matter what broke.
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
LOCK=/tmp/dispatchweb.run.lock
RUN_TIMEOUT="${RUN_TIMEOUT:-4h}"
# config.settings prints a status banner on import; the URL is the last line.
HC_URL="$("$PY" -c 'from config.settings import HEALTHCHECK_URL; print(HEALTHCHECK_URL)' 2>/dev/null | tail -n 1)"

# hc_ping start|success|fail [body-file]  — best effort, never fails the run.
hc_ping() {
    [ -n "$HC_URL" ] || return 0
    local url="$HC_URL"
    case "$1" in
        start) url="$HC_URL/start" ;;
        fail)  url="$HC_URL/fail" ;;
    esac
    if [ -n "${2:-}" ] && [ -f "$2" ]; then
        curl -fsS -m 10 --retry 3 -o /dev/null --data-binary "@$2" "$url" \
            || echo "healthcheck ping ($1) failed" >&2
    else
        curl -fsS -m 10 --retry 3 -o /dev/null "$url" \
            || echo "healthcheck ping ($1) failed" >&2
    fi
    return 0
}

collision_alert() {
    "$PY" - <<'PYEOF'
from modules.alerts import send_email_alert
send_email_alert(
    "Dispatch pipeline: run skipped (collision)",
    "A scheduled run was skipped because another run is still in progress.\n"
    "This is normal occasionally (link-heavy days run long). If it repeats "
    "every day:\n"
    "  ssh micah@dispatcher.tail48ca7.ts.net\n"
    "  ps aux | grep main.py        # see what's running and for how long\n"
    "  tail -50 ~/dispatchweb/cron_run.log\n",
)
PYEOF
}

exec 9>"$LOCK"
if ! flock -n 9; then
    collision_alert
    exit 0
fi
# Belt and suspenders: also catch runs started OUTSIDE this wrapper
# (a manual ./main.py or .venv/bin/python main.py holds no lock).
if pgrep -f "python3? (\./)?main\.py" >/dev/null 2>&1; then
    collision_alert
    exit 0
fi

# GNU coreutils timeout exists on the Linux box; macOS lacks it, so fall back
# to an unbounded run there (the Mac is not the scheduler).
# PYTHONUNBUFFERED: Python 3.14 buffers 128 KiB of stdout when redirected to
# a file, so the log would show nothing for minutes and lose its tail (the
# crash) if timeout(1) kills the run.
hc_ping start
if command -v timeout >/dev/null 2>&1; then
    # SIGTERM at the deadline; SIGKILL 60s later if the process ignores it.
    PYTHONUNBUFFERED=1 timeout --kill-after=60 "$RUN_TIMEOUT" "$PY" main.py > cron_run.log 2>&1
else
    PYTHONUNBUFFERED=1 "$PY" main.py > cron_run.log 2>&1
fi
rc=$?

# rc=2 means main.py failed but already sent its own failure report — don't
# double-alert. Anything else nonzero is a hard crash it never got to report.
# timeout(1) exits 124 when the deadline fired (137 if it had to SIGKILL);
# both land here and are reported as a timeout rather than a generic crash.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    RC="$rc" RUN_TIMEOUT="$RUN_TIMEOUT" "$PY" - <<'PYEOF'
import os
from modules.alerts import send_email_alert
try:
    with open("cron_run.log", errors="replace") as fh:
        tail = fh.read()[-3000:]
except OSError:
    tail = "(cron_run.log unreadable)"
rc = os.environ["RC"]
if rc in ("124", "137"):
    subject = f"Dispatch pipeline: run TIMED OUT after {os.environ['RUN_TIMEOUT']} (exit {rc})"
    lead = (f"The scheduled run exceeded {os.environ['RUN_TIMEOUT']} and was killed by "
            "timeout(1). Check for a hung Chromium/Playwright driver and for "
            "orphaned chromium processes (ps aux | grep -i chrom).\n\n")
else:
    subject = f"Dispatch pipeline: run CRASHED (exit {rc})"
    lead = "The scheduled run exited nonzero before completing.\n\n"
send_email_alert(
    subject,
    lead +
    "Workarounds:\n"
    "  ssh micah@dispatcher.tail48ca7.ts.net\n"
    "  cd ~/dispatchweb && ./main.py   # re-run and watch\n"
    "  cat cron_run.log                # full log\n\n"
    f"--- log tail ---\n{tail}",
)
PYEOF
fi

if [ "$rc" -eq 0 ]; then
    hc_ping success
else
    # Body: this run's failure report (main.py writes it only when rc=2) plus
    # the log tail. healthchecks keeps the first 100 KB of a ping body.
    HC_BODY="$(mktemp)"
    {
        echo "exit status: $rc"
        [ "$rc" -eq 2 ] && [ -f last_run_failures.md ] && cat last_run_failures.md
        echo; echo "--- log tail ---"
        tail -c 20000 cron_run.log 2>/dev/null
    } > "$HC_BODY"
    hc_ping fail "$HC_BODY"
    rm -f "$HC_BODY"
fi
exit $rc
