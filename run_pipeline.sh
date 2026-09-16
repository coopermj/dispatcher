#!/usr/bin/env bash
# Cron wrapper for the pipeline: single-instance lock + collision/crash alerts.
#
# - flock guarantees only one run at a time; a scheduled run that finds a
#   previous one still going emails an alert and exits cleanly.
# - main.py alerts on its own internal failures; this wrapper additionally
#   catches hard crashes (nonzero exit before alerting could run) and emails
#   the log tail with recovery steps.
cd "$(dirname "$0")" || exit 1
PY=.venv/bin/python
LOCK=/tmp/dispatchweb.run.lock

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
# (a manual ./main.py holds no lock).
if pgrep -f "python3? \./main\.py" >/dev/null 2>&1; then
    collision_alert
    exit 0
fi

./main.py > cron_run.log 2>&1
rc=$?

# rc=2 means main.py failed but already sent its own failure report — don't
# double-alert. Anything else nonzero is a hard crash it never got to report.
if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    RC="$rc" "$PY" - <<'PYEOF'
import os
from modules.alerts import send_email_alert
try:
    with open("cron_run.log", errors="replace") as fh:
        tail = fh.read()[-3000:]
except OSError:
    tail = "(cron_run.log unreadable)"
send_email_alert(
    f"Dispatch pipeline: run CRASHED (exit {os.environ['RC']})",
    "The scheduled run exited nonzero before completing.\n\n"
    "Workarounds:\n"
    "  ssh micah@dispatcher.tail48ca7.ts.net\n"
    "  cd ~/dispatchweb && ./main.py   # re-run and watch\n"
    "  cat cron_run.log                # full log\n\n"
    f"--- log tail ---\n{tail}",
)
PYEOF
fi
exit $rc
