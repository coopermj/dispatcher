#!/usr/bin/env python3
"""Failure alerting — no run failure should be silent.

Pipelines collect failure records with record_failure() as they go; at the
end of a run alert_on_failures() does three best-effort things:
  1. writes last_run_failures.md next to the tracking files (details +
     concrete workaround commands),
  2. posts a macOS Notification Center banner,
  3. emails ALERT_EMAIL through the pipeline's own Gmail — using the saved
     token ONLY (never opens a browser; unattended runs must not block).

Every alert path swallows its own errors: alerting must never break a run.
"""

import base64
import pickle
import subprocess
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from config.settings import ALERT_EMAIL, ALERTS_ENABLED, TOKEN_FILE, BASE_DIR

REPORT_FILE = Path(BASE_DIR) / "last_run_failures.md"

# Category → the command/action that fixes or retries it.
WORKAROUNDS = {
    "conversion": "Retries automatically next run; to force now: .venv/bin/python main.py",
    "upload": ".venv/bin/python main.py --retry-uploads",
    "prune": ".venv/bin/python prune_news.py --confirm  (also retries automatically next run)",
    "rmapi": "Check `~/rmapi/rmapi ls`; if auth expired, re-register the device "
             "with a code from https://my.remarkable.com/device/desktop/connect",
    "auth": "Run `.venv/bin/python main.py` interactively once to re-consent to Google",
}
DEFAULT_WORKAROUND = "Check the run log; re-run .venv/bin/python main.py after fixing."


def record_failure(failures, category, item, detail):
    """Append one failure record. `failures` is a plain list owned by the run."""
    failures.append({"category": category, "item": str(item), "detail": str(detail)})


def build_failure_report(failures, run_label=""):
    """Human-readable report: every failure with its category's workaround."""
    lines = [f"# Dispatch run: {len(failures)} failure(s)"]
    if run_label:
        lines.append(f"Run: {run_label}")
    lines.append(f"Time: {datetime.now().isoformat(timespec='seconds')}")
    by_cat = {}
    for f in failures:
        by_cat.setdefault(f["category"], []).append(f)
    for cat, items in by_cat.items():
        lines.append(f"\n## {cat} ({len(items)})")
        for f in items:
            lines.append(f"- {f['item']} — {f['detail']}")
        lines.append(f"**Workaround:** {WORKAROUNDS.get(cat, DEFAULT_WORKAROUND)}")
    return "\n".join(lines)


def send_macos_notification(title, message):
    """Notification Center banner. Best-effort."""
    try:
        script = f'display notification "{message[:200]}" with title "{title[:80]}" sound name "Basso"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)
        return True
    except Exception as e:
        print(f"⚠️ macOS notification failed: {e}")
        return False


def _gmail_service_noninteractive():
    """Gmail service from the saved token only — refresh if expired, but never
    open a browser. Returns None (with a hint) when send isn't possible."""
    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        if not Path(TOKEN_FILE).exists():
            print("⚠️ Alert email skipped: no Google token yet (run main.py once in email mode)")
            return None
        with open(TOKEN_FILE, "rb") as fh:
            creds = pickle.load(fh)
        if "https://www.googleapis.com/auth/gmail.send" not in (creds.scopes or []):
            print("⚠️ Alert email skipped: token lacks gmail.send — re-consent needed "
                  "(run: .venv/bin/python -c \"from modules.auth import AuthManager; AuthManager().authenticate_google()\")")
            return None
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, "wb") as fh:
                    pickle.dump(creds, fh)
            else:
                print("⚠️ Alert email skipped: Google token invalid and not refreshable")
                return None
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        print(f"⚠️ Alert email skipped: {e}")
        return None


def send_email_alert(subject, body, to_addr=None):
    """Send via the pipeline's Gmail. Best-effort; returns True on success."""
    service = _gmail_service_noninteractive()
    if service is None:
        return False
    try:
        msg = MIMEText(body)
        msg["to"] = to_addr or ALERT_EMAIL
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"📧 Failure alert emailed to {to_addr or ALERT_EMAIL}")
        return True
    except Exception as e:
        print(f"⚠️ Alert email failed: {e}")
        return False


def alert_on_failures(failures, run_label=""):
    """Fan out all alert channels for a run's failures. No failures → no-op."""
    if not failures or not ALERTS_ENABLED:
        return
    report = build_failure_report(failures, run_label=run_label)
    print(f"\n🚨 {len(failures)} failure(s) this run — alerting")
    try:
        REPORT_FILE.write_text(report)
        print(f"📝 Failure report: {REPORT_FILE}")
    except Exception as e:
        print(f"⚠️ Could not write failure report: {e}")
    send_macos_notification(
        f"Dispatch: {len(failures)} failure(s)",
        f"{run_label or 'run'} — see {REPORT_FILE.name} / email for workarounds",
    )
    send_email_alert(f"Dispatch pipeline: {len(failures)} failure(s)", report)
