#!/usr/bin/env python3
"""Detect captcha/challenge/blocked pages and build placeholder pages for them."""

import html

# Lowercase substrings that mark bot-challenge / blocked pages
# (Cloudflare, Turnstile, generic captchas, WAF denials).
BLOCK_MARKERS = [
    "verify you are human",
    "just a moment",
    "attention required",
    "access denied",
    "cf-challenge",
    "turnstile",
    "captcha",
    "enable javascript and cookies to continue",
]


def detect_block(title, body_text, status=None):
    """Return a human-readable reason if the page looks blocked, else None."""
    if status is not None and status >= 400:
        return f"HTTP {status}"
    haystack = f"{title or ''} {(body_text or '')[:3000]}".lower()
    for marker in BLOCK_MARKERS:
        if marker in haystack:
            return f"page shows a challenge/blocked marker ('{marker}')"
    return None


def placeholder_html(url, title, reason):
    """One-page notice inserted where a blocked/unreachable page would have gone."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Could not capture</title></head>
<body style="font-family: Georgia, serif; margin: 3em;">
  <h1 style="color: #b00;">⚠️ Could not capture linked page</h1>
  <h2>{html.escape(title or url)}</h2>
  <p><strong>Reason:</strong> {html.escape(reason)}</p>
  <p><strong>Original URL:</strong><br>{html.escape(url)}</p>
  <p style="color: #666;">This page was linked from the article but could not be
     converted to PDF (blocked, timed out, or rejected automation).</p>
</body></html>"""
