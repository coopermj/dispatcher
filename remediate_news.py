#!/usr/bin/env python3
"""
Remediate wrong-content / over-bloated website PDFs on the reMarkable.

Earlier website-pipeline PDFs suffered from a parallel content-bleed bug (wrong
article content) and over-aggressive link-following (huge multi-article PDFs).
Both are fixed in the pipeline now; this tool regenerates the affected documents
with the fixed code and replaces them on the device.

Scope: website-sourced /News docs that are STARRED or modified within --days.
Old unstarred docs are skipped — prune_news.py removes those anyway.

Dry-run by default — pass --confirm to regenerate + replace on the device.
Use --limit N to process only the first N (useful for a validation batch).
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_ts(s):
    try:
        return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def select_targets(entries, days, now):
    """Website-sourced docs that are starred or modified within `days`."""
    cutoff = now - timedelta(days=days)
    targets = []
    for e in entries:
        if e.get("type") != "DocumentType":
            continue
        if not Path(e.get("name", "")).stem.startswith("dispatch_website_"):
            continue
        if e.get("starred"):
            targets.append(e)
            continue
        ts = _parse_ts(e.get("modifiedClient"))
        if ts and ts > cutoff:
            targets.append(e)
    return targets


def url_for(device_name, tracking):
    """Find the article URL for a device doc by matching tracking pdf_path stems."""
    stem = Path(device_name).stem
    for v in tracking.values():
        pdf_stem = Path(v.get("pdf_path", "")).stem
        if pdf_stem and pdf_stem == stem:
            return v.get("read_online_url", "")
    return ""


def _load_tracking():
    from config.settings import TRACKING_FILE
    try:
        return json.load(open(TRACKING_FILE))
    except Exception:
        return {}


async def _regenerate_and_replace(targets, rmapi, folder):
    """Live: regenerate each target by URL (preserving its filename) and replace it."""
    import subprocess
    from main import DispatchConverter
    from modules import LinkProcessor
    from config.settings import FOLLOW_ARTICLE_LINKS, MIN_PDF_SIZE_BYTES

    conv = DispatchConverter()
    if not await conv.initialize():
        print("❌ Initialization failed (browser/auth)")
        return

    done = failed = 0
    for i, (name, url) in enumerate(targets, 1):
        stem = Path(name).stem
        print(f"\n[{i}/{len(targets)}] {stem}")
        out = conv.output_dir / f"{stem}.pdf"
        # Convert directly to the exact output path so the device name is preserved.
        page = await conv.browser_manager.create_new_page()
        ok = False
        try:
            if FOLLOW_ARTICLE_LINKS:
                lp = LinkProcessor(conv.browser_manager)
                ok = await lp.process_article_with_links(url, str(out), page=page)
            else:
                ok = await conv.browser_manager.convert_url_to_pdf_with_page(url, str(out), page)
        except Exception as e:
            print(f"   ❌ regenerate error: {e}")
        finally:
            if page:
                await conv.browser_manager.close_page(page)

        if not ok or not out.exists() or out.stat().st_size < MIN_PDF_SIZE_BYTES:
            print("   ❌ regeneration failed — leaving device copy untouched")
            failed += 1
            continue
        # Replace on device: delete old copy, upload the regenerated one (same name).
        subprocess.run([rmapi, "rm", f"/{folder}/{stem}"], capture_output=True, text=True, timeout=30)
        up = subprocess.run([rmapi, "put", str(out), f"/{folder}"], capture_output=True, text=True, timeout=120)
        if up.returncode == 0:
            print(f"   ✅ replaced on device ({out.stat().st_size // 1024 // 1024} MB)")
            done += 1
        else:
            print(f"   ❌ upload failed: {up.stderr.strip()}")
            failed += 1
    await conv.cleanup()
    print(f"\n📊 Remediation done: {done} replaced, {failed} failed")


def main():
    parser = argparse.ArgumentParser(description="Regenerate + replace wrong/bloated website PDFs on the reMarkable.")
    parser.add_argument("--confirm", action="store_true", help="Actually regenerate + replace (default dry run)")
    parser.add_argument("--days", type=int, default=14, help="Remediate docs modified within this many days (default 14)")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N targets (0 = all)")
    args = parser.parse_args()

    from config.settings import DEFAULT_RMAPI_PATH, REMARKABLE_FOLDER
    rmapi = str(Path(DEFAULT_RMAPI_PATH).expanduser())
    folder = REMARKABLE_FOLDER
    import subprocess
    ls = subprocess.run([rmapi, "-json", "ls", f"/{folder}"], capture_output=True, text=True, timeout=30)
    if ls.returncode != 0:
        print(f"❌ Failed to list /{folder}: {ls.stderr}")
        return
    entries = json.loads(ls.stdout)
    tracking = _load_tracking()

    now = datetime.now(timezone.utc)
    targets_docs = select_targets(entries, days=args.days, now=now)
    pairs = []
    missing = []
    for e in targets_docs:
        u = url_for(e["name"], tracking)
        (pairs if u else missing).append(e["name"])
    pairs = [(name, url_for(name, tracking)) for name in pairs]
    if args.limit:
        pairs = pairs[:args.limit]

    print(f"📊 Remediation scope (website docs, starred or <= {args.days}d): {len(targets_docs)}")
    print(f"   with a known URL (remediable): {len(pairs)}")
    print(f"   no URL in tracking (skipped) : {len(missing)}")

    if not args.confirm:
        print("\n🔍 DRY RUN — would regenerate + replace:")
        for name, url in pairs:
            print(f"   ↻ {name}\n      {url}")
        if missing:
            print("\n   (no URL, skipped):")
            for name in missing[:20]:
                print(f"   ? {name}")
        print(f"\nRun with --confirm to regenerate + replace ({len(pairs)} docs). --limit N for a batch.")
        return

    asyncio.run(_regenerate_and_replace(pairs, rmapi, folder))


if __name__ == "__main__":
    main()
