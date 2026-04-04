#!/usr/bin/env python3
"""
Prune unstarred files from the reMarkable News folder.

Dry-run by default — pass --confirm to actually delete.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config.settings import DEFAULT_RMAPI_PATH, REMARKABLE_FOLDER

TRACKING_FILES = [
    Path(__file__).parent / "dispatch_tracking.json",
    Path(__file__).parent / "dispatch_email_tracking.json",
]


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️  Could not load {path}: {e}")
        return None


def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        print(f"❌ Could not save {path}: {e}")
        return False


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def mark_expired_in_tracking(rm_name):
    """Mark matching entries in both tracking JSONs as remarkable_expired: true."""
    rm_stem = Path(rm_name).stem  # strip .pdf if present (rmapi strips extension on upload)
    rm_norm = normalize(rm_stem)

    for tracking_path in TRACKING_FILES:
        data = load_json(tracking_path)
        if data is None:
            continue

        changed = False
        for entry in data.values():
            # Primary: match by PDF filename stem
            pdf_path = entry.get("pdf_path", "")
            pdf_stem = Path(pdf_path).stem if pdf_path else ""
            if pdf_stem and pdf_stem == rm_stem:
                entry["remarkable_expired"] = True
                changed = True
                continue

            # Fallback: fuzzy match on subject (for email-pipeline entries)
            subject = entry.get("subject", "")
            if subject and normalize(subject) == rm_norm:
                entry["remarkable_expired"] = True
                changed = True

        if changed:
            save_json(tracking_path, data)


def main():
    parser = argparse.ArgumentParser(
        description="Prune unstarred files from the reMarkable News folder."
    )
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete files (default is dry run)")
    parser.add_argument("--days", type=int, default=14,
                        help="Only delete files older than this many days (default: 14)")
    parser.add_argument("--rmapi-path", default=DEFAULT_RMAPI_PATH,
                        help=f"Path to rmapi binary (default: {DEFAULT_RMAPI_PATH})")
    args = parser.parse_args()

    rmapi = str(Path(args.rmapi_path).expanduser())
    folder = REMARKABLE_FOLDER  # "News"

    # Check rmapi is available
    check = subprocess.run([rmapi, "ls"], capture_output=True, text=True, timeout=30)
    if check.returncode != 0:
        print(f"❌ rmapi not available: {check.stderr}")
        sys.exit(1)

    # List News folder
    print(f"📋 Listing /{folder}...")
    ls = subprocess.run([rmapi, "-json", "ls", f"/{folder}"],
                        capture_output=True, text=True, timeout=30)
    if ls.returncode != 0:
        print(f"❌ Failed to list /{folder}: {ls.stderr}")
        sys.exit(1)

    try:
        entries = json.loads(ls.stdout)
    except json.JSONDecodeError as e:
        print(f"❌ Failed to parse rmapi output: {e}")
        sys.exit(1)

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    documents = [e for e in entries if e.get("type") == "DocumentType"]
    starred = [e for e in documents if e.get("starred")]

    # Unstarred AND older than --days
    unstarred = []
    too_recent = 0
    for e in documents:
        if e.get("starred"):
            continue
        modified = e.get("modifiedClient", "")
        try:
            ts = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = None
        if ts and ts > cutoff:
            too_recent += 1
        else:
            unstarred.append(e)

    print(f"\n📊 /{folder} summary:")
    print(f"   Total documents       : {len(documents)}")
    print(f"   Starred (kept)        : {len(starred)}")
    print(f"   Unstarred < {args.days}d (kept) : {too_recent}")
    print(f"   Unstarred ≥ {args.days}d (prune): {len(unstarred)}")

    if not unstarred:
        print("\n✅ Nothing to prune.")
        return

    if not args.confirm:
        print(f"\n🔍 DRY RUN — would delete {len(unstarred)} files (unstarred, older than {args.days} days):")
        for e in unstarred:
            print(f"   🗑  {e['name']}  ({e.get('modifiedClient','?')[:10]})")
        print(f"\nRun with --confirm to actually delete.")
        return

    # --confirm: delete and update tracking
    print(f"\n🗑  Deleting {len(unstarred)} unstarred files...")
    deleted = 0
    failed = 0
    for e in unstarred:
        name = e["name"]
        result = subprocess.run([rmapi, "rm", f"/{folder}/{name}"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"   ✅ Deleted: {name}")
            mark_expired_in_tracking(name)
            deleted += 1
        else:
            print(f"   ❌ Failed:  {name} — {result.stderr.strip()}")
            failed += 1

    print(f"\n📊 Done: {deleted} deleted, {failed} failed, {len(starred)} kept (starred).")


if __name__ == "__main__":
    main()
