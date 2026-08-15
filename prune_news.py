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

from config.settings import DEFAULT_RMAPI_PATH, REMARKABLE_FOLDER, PRUNE_NEWS_DAYS

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


def select_local_prunable(pdf_paths, tracking_by_path, days):
    """Local PDFs safe to delete: older than `days` (mtime) AND either
    confirmed on the device (remarkable_uploaded), already pruned from it
    (remarkable_expired), or orphaned (no tracking entry — nothing will ever
    retry them). Pending uploads (tracked, not uploaded, not expired) are
    kept regardless of age so --retry-uploads still has the file."""
    import time
    cutoff = time.time() - days * 86400
    prunable = []
    for p in pdf_paths:
        try:
            if p.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        entry = tracking_by_path.get(str(p))
        if entry is None or entry.get("remarkable_uploaded") or entry.get("remarkable_expired"):
            prunable.append(p)
    return prunable


def run_local_prune(days, pdf_dirs=None):
    """Delete old local PDFs whose device fate is settled. Returns count
    deleted; never raises."""
    base = Path(__file__).parent
    if pdf_dirs is None:
        pdf_dirs = [base / "dispatch_pdfs", base / "dispatch_persistent_pdfs"]
    tracking_by_path = {}
    for tf in TRACKING_FILES:
        data = load_json(tf) or {}
        for entry in data.values():
            pp = entry.get("pdf_path")
            if pp:
                tracking_by_path[pp] = entry
    deleted = 0
    for d in pdf_dirs:
        if not Path(d).is_dir():
            continue
        pdfs = sorted(Path(d).glob("*.pdf"))
        for p in select_local_prunable(pdfs, tracking_by_path, days):
            try:
                p.unlink()
                deleted += 1
            except OSError as e:
                print(f"   ⚠️ could not delete {p.name}: {e}")
    if deleted:
        print(f"🧹 Deleted {deleted} old local PDF(s) already settled on the device")
    return deleted


def is_pipeline_doc(entry):
    """True for docs this pipeline generated (dispatch_* names). Manually
    added files (WSJ papers, saved articles) are never auto-pruned."""
    return entry.get("name", "").startswith("dispatch_")


def select_prunable(documents, days, now):
    """Split device documents into (starred, kept_recent_count, prunable).

    Prunable = unstarred and NOT provably newer than the cutoff — docs with a
    missing/garbled modifiedClient can't be proven recent, so they prune."""
    cutoff = now - timedelta(days=days)
    starred = [e for e in documents if e.get("starred")]
    prunable = []
    kept_recent = 0
    for e in documents:
        if e.get("starred"):
            continue
        modified = e.get("modifiedClient", "")
        try:
            ts = datetime.fromisoformat(modified.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = None
        if ts and ts > cutoff:
            kept_recent += 1
        else:
            prunable.append(e)
    return starred, kept_recent, prunable


def run_prune(rmapi_path=None, days=None, confirm=False, dispatch_only=False):
    """Prune unstarred docs older than `days` from /News.

    dispatch_only=True (the automatic post-run mode) restricts pruning to
    pipeline-generated dispatch_* docs, protecting manually added files.
    Returns (deleted, failed) — (0, 0) when nothing qualified; None when the
    device couldn't be queried (rmapi missing/unreachable). Never raises."""
    if days is None:
        days = PRUNE_NEWS_DAYS
    rmapi = str(Path(rmapi_path or DEFAULT_RMAPI_PATH).expanduser())
    folder = REMARKABLE_FOLDER  # "News"

    try:
        check = subprocess.run([rmapi, "ls"], capture_output=True, text=True, timeout=30)
        if check.returncode != 0:
            print(f"❌ rmapi not available: {check.stderr}")
            return None

        print(f"📋 Listing /{folder}...")
        ls = subprocess.run([rmapi, "-json", "ls", f"/{folder}"],
                            capture_output=True, text=True, timeout=30)
        if ls.returncode != 0:
            print(f"❌ Failed to list /{folder}: {ls.stderr}")
            return None
        entries = json.loads(ls.stdout)
    except Exception as e:
        print(f"❌ Prune aborted, could not query device: {e}")
        return None

    documents = [e for e in entries if e.get("type") == "DocumentType"]
    if dispatch_only:
        skipped_manual = sum(1 for e in documents if not is_pipeline_doc(e))
        documents = [e for e in documents if is_pipeline_doc(e)]
        if skipped_manual:
            print(f"   (ignoring {skipped_manual} manually added, non-dispatch docs)")
    starred, kept_recent, prunable = select_prunable(
        documents, days, datetime.now(timezone.utc))

    print(f"\n📊 /{folder} summary:")
    print(f"   Total documents       : {len(documents)}")
    print(f"   Starred (kept)        : {len(starred)}")
    print(f"   Unstarred < {days}d (kept) : {kept_recent}")
    print(f"   Unstarred ≥ {days}d (prune): {len(prunable)}")

    if not prunable:
        print("\n✅ Nothing to prune.")
        return (0, 0)

    if not confirm:
        print(f"\n🔍 DRY RUN — would delete {len(prunable)} files (unstarred, older than {days} days):")
        for e in prunable:
            print(f"   🗑  {e['name']}  ({e.get('modifiedClient','?')[:10]})")
        print(f"\nRun with --confirm to actually delete.")
        return (0, 0)

    print(f"\n🗑  Deleting {len(prunable)} unstarred files...")
    deleted = 0
    failed = 0
    for e in prunable:
        name = e["name"]
        try:
            result = subprocess.run([rmapi, "rm", f"/{folder}/{name}"],
                                    capture_output=True, text=True, timeout=30)
        except Exception as err:
            print(f"   ❌ Failed:  {name} — {err}")
            failed += 1
            continue
        if result.returncode == 0:
            print(f"   ✅ Deleted: {name}")
            mark_expired_in_tracking(name)
            deleted += 1
        else:
            print(f"   ❌ Failed:  {name} — {result.stderr.strip()}")
            failed += 1

    print(f"\n📊 Done: {deleted} deleted, {failed} failed, {len(starred)} kept (starred).")
    return (deleted, failed)


def main():
    parser = argparse.ArgumentParser(
        description="Prune unstarred files from the reMarkable News folder."
    )
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete files (default is dry run)")
    parser.add_argument("--days", type=int, default=PRUNE_NEWS_DAYS,
                        help=f"Only delete files older than this many days (default: {PRUNE_NEWS_DAYS})")
    parser.add_argument("--rmapi-path", default=DEFAULT_RMAPI_PATH,
                        help=f"Path to rmapi binary (default: {DEFAULT_RMAPI_PATH})")
    args = parser.parse_args()

    if run_prune(rmapi_path=args.rmapi_path, days=args.days, confirm=args.confirm) is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
