# prune_news.py — Design Spec

**Date:** 2026-04-02  
**Status:** Implemented

## Problem

The reMarkable `News` folder accumulates files indefinitely. Articles read and discarded pile up alongside ones the user wants to keep (starred). There was no way to clean up old unread articles without manually deleting them on the device.

Additionally, the website scanner was blocking the Morning Dispatch newsletter from being re-processed on subsequent days because title-based deduplication treated "The Morning Dispatch" as already seen after the first issue was processed.

---

## Solution

### 1. `prune_news.py` — Standalone pruner script

Deletes unstarred files from `/News` on reMarkable that are older than a configurable age threshold (default: 14 days). Starred files are always kept regardless of age. Dry-run by default; requires `--confirm` to actually delete.

**Usage:**
```
python prune_news.py                  # dry run (safe)
python prune_news.py --confirm        # delete unstarred files older than 14 days
python prune_news.py --confirm --days 30  # use a 30-day window instead
python prune_news.py --rmapi-path ~/custom/rmapi  # override rmapi path
```

**Flow:**
1. Verify `rmapi` is available
2. `rmapi -json ls /News` — single call, `starred` field included inline
3. Filter to `DocumentType` entries where `starred == false` AND `modifiedClient < now - days`
4. Dry-run: print what would be deleted with dates
5. `--confirm`: call `rmapi rm /News/<name>` for each; on success, mark tracking entries `remarkable_expired: true`

**Tracking update:**  
After each successful deletion, both `dispatch_tracking.json` and `dispatch_email_tracking.json` are scanned. Matching is done by PDF filename stem first, subject fuzzy-match as fallback. Matched entries get `"remarkable_expired": true` added. No other fields are touched.

**Error handling:**
- `rmapi` unavailable at startup → exit, no changes
- `ls` failure → exit, no changes
- Individual `rm` failures → logged, script continues with remaining files
- Tracking JSON save failure → logged (deletion already happened; user is informed)

---

### 2. Bug fix — Morning Dispatch title dedup (`website_scanner.py`)

**Root cause:** `WebsiteScanner.filter_articles` was applying title-based deduplication as a backward-compat fallback for old tracking entries without URLs. Since every Morning Dispatch issue shares the title "The Morning Dispatch," once one issue was processed, all future issues were silently skipped.

**Fix:** Removed the title-based dedup block entirely. URL-based dedup is sufficient — all website-scanned articles have unique `/p/slug` URLs. The `processed_subjects` set and its loading code were also removed.

---

## Files Changed

- `prune_news.py` — new script (repo root)
- `modules/website_scanner.py` — removed title-based dedup in `filter_articles` and `__init__`
