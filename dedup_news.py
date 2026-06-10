#!/usr/bin/env python3
"""
Remove duplicate documents from the reMarkable News folder.

Articles uploaded before the inventory-dedup feature existed can appear multiple
times (e.g. once from the email pipeline as `dispatch_001_…` and again from the
website pipeline as `dispatch_website_NNN_…`). This tool groups documents by the
same normalized title used at upload time and deletes the redundant copies,
keeping exactly one per article.

Rules:
  - Never delete a starred document (starred = you want to keep it).
  - If any copy in a group is starred, keep all starred copies and delete the
    unstarred duplicates.
  - If no copy is starred, keep the most recently modified and delete the rest.
  - Documents with no duplicate are left untouched. Folders are ignored.

Dry-run by default — pass --confirm to actually delete.
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from config.settings import DEFAULT_RMAPI_PATH, REMARKABLE_FOLDER
# Reuse the exact normalization the runtime dedup uses, so this cleanup agrees
# with what upload_if_new() would consider "the same" document.
from modules.remarkable import _normalize_document_name


def select_duplicates_to_delete(entries):
    """Given an rmapi `-json ls` listing, return the document entries to delete.

    Keeps one copy per normalized title (preferring starred, then most recent).
    """
    groups = defaultdict(list)
    for e in entries:
        if e.get("type") != "DocumentType":
            continue
        groups[_normalize_document_name(e.get("name", ""))].append(e)

    to_delete = []
    for group in groups.values():
        if len(group) < 2:
            continue
        starred = [e for e in group if e.get("starred")]
        if starred:
            # Keep every starred copy; drop the unstarred duplicates.
            to_delete.extend(e for e in group if not e.get("starred"))
        else:
            # Keep the most recently modified; drop the older copies.
            ordered = sorted(group, key=lambda e: e.get("modifiedClient", ""), reverse=True)
            to_delete.extend(ordered[1:])
    return to_delete


def main():
    parser = argparse.ArgumentParser(
        description="Remove duplicate documents from the reMarkable News folder."
    )
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete duplicates (default is dry run)")
    parser.add_argument("--rmapi-path", default=DEFAULT_RMAPI_PATH,
                        help=f"Path to rmapi binary (default: {DEFAULT_RMAPI_PATH})")
    args = parser.parse_args()

    rmapi = str(Path(args.rmapi_path).expanduser())
    folder = REMARKABLE_FOLDER  # "News"

    check = subprocess.run([rmapi, "ls"], capture_output=True, text=True, timeout=30)
    if check.returncode != 0:
        print(f"❌ rmapi not available: {check.stderr}")
        sys.exit(1)

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

    documents = [e for e in entries if e.get("type") == "DocumentType"]
    to_delete = select_duplicates_to_delete(entries)
    unique = len(documents) - len(to_delete)

    print(f"\n📊 /{folder} summary:")
    print(f"   Total documents     : {len(documents)}")
    print(f"   Unique articles     : {unique}")
    print(f"   Duplicate copies    : {len(to_delete)}")

    if not to_delete:
        print("\n✅ No duplicates to remove.")
        return

    if not args.confirm:
        print(f"\n🔍 DRY RUN — would delete {len(to_delete)} duplicate documents:")
        for e in to_delete:
            print(f"   🗑  {e['name']}  ({e.get('modifiedClient', '?')[:10]})")
        print("\nRun with --confirm to actually delete.")
        return

    print(f"\n🗑  Deleting {len(to_delete)} duplicate documents...")
    deleted = 0
    failed = 0
    for e in to_delete:
        name = e["name"]
        result = subprocess.run([rmapi, "rm", f"/{folder}/{name}"],
                                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print(f"   ✅ Deleted: {name}")
            deleted += 1
        else:
            print(f"   ❌ Failed:  {name} — {result.stderr.strip()}")
            failed += 1

    print(f"\n📊 Done: {deleted} deleted, {failed} failed, {unique} unique articles kept.")


if __name__ == "__main__":
    main()
