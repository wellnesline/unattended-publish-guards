#!/usr/bin/env python3
"""Deterministic lower-bound checks for a script and its content manifest (synthetic sample).

Checks: dates agree with the run date, item and line counts, script duration, items still
valid at publish time, and every spoken number against the manifest
(``publish_guard_claims.validate_claims``). Anything undecidable is a failure.

  PUBLISH_GUARD_MIN_ITEMS   minimum number of items in the manifest (default 1)
  PUBLISH_GUARD_MIN_LINES   minimum number of script lines (default 1)
  PUBLISH_GUARD_VIDEO_SECONDS  the lower bound is also the minimum script duration
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from publish_guard_claims import validate_claims
from publish_guard_common import JST, add, finish, load_json, now_jst, result_base, video_seconds, write_json


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--date", dest="business_date", help="YYYY-MM-DD; defaults to today (JST)")
    ap.add_argument("--public-at", help="ISO 8601 publish time; defaults to now")
    args = ap.parse_args()
    work = args.work_dir.resolve()
    result = result_base("basic_check", work)
    expected_date = args.business_date or now_jst().date().isoformat()
    try:
        script = load_json(work / "script.json")
        manifest = load_json(work / "content_manifest.json")
    except Exception as exc:
        add(result, "FAIL", "G3_INPUT", "required JSON could not be read", error=str(exc))
        finish(result)
        write_json(work / "basic_check.json", result)
        sys.exit(1)
    try:
        public_at = datetime.fromisoformat(args.public_at).astimezone(JST) if args.public_at else now_jst()
    except ValueError:
        add(result, "FAIL", "G3_PUBLIC_AT", "--public-at is not ISO 8601", actual=args.public_at)
        public_at = now_jst()

    items = manifest.get("items", [])
    lines = script.get("lines", [])
    duration = script.get("duration_sec", 0)
    result["checks"].update({"items": len(items), "script_lines": len(lines), "script_duration_sec": duration})

    dates = {str(manifest.get("date"))}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", work.parent.name):
        dates.add(work.parent.name)
    if expected_date not in dates or any(d != expected_date for d in dates if d != "None"):
        add(result, "FAIL", "G3_DATE", "dates in the manifest or work directory differ from the run date",
            expected=expected_date, found=sorted(dates))

    min_items = _int_env("PUBLISH_GUARD_MIN_ITEMS", 1)
    if len(items) < min_items:
        add(result, "FAIL", "G3_ITEM_COUNT", "too few items in the manifest", expected=min_items, actual=len(items))
    ids = [item.get("id") for item in items]
    if None in ids or len(ids) != len(set(ids)):
        add(result, "FAIL", "G3_ITEM_IDS", "item ids must be present and unique", actual=ids)

    for item in items:
        until = item.get("valid_until")
        if until is None:
            continue
        try:
            expires = datetime.fromisoformat(str(until))
            expires = expires.replace(tzinfo=JST) if expires.tzinfo is None else expires.astimezone(JST)
            if expires <= public_at:
                add(result, "FAIL", "G3_EXPIRED", "an item is no longer valid at publish time",
                    item_id=item.get("id"), valid_until=expires.isoformat(), public_at=public_at.isoformat())
        except ValueError:
            add(result, "FAIL", "G3_EXPIRED", "valid_until is not ISO 8601", item_id=item.get("id"), valid_until=until)

    min_lines = _int_env("PUBLISH_GUARD_MIN_LINES", 1)
    if len(lines) < min_lines:
        add(result, "FAIL", "G3_SCRIPT_LINES", "too few script lines", expected=min_lines, actual=len(lines))
    try:
        min_sec = video_seconds()[0]
    except RuntimeError as exc:
        add(result, "FAIL", "G3_CONFIG", "the lower bound for script duration is not configured", error=str(exc))
    else:
        if not isinstance(duration, (int, float)) or duration < min_sec:
            add(result, "FAIL", "G3_SCRIPT_DURATION", "script duration is below the configured minimum",
                expected=min_sec, actual=duration)

    claim_failures = validate_claims(manifest, script)
    result["checks"]["claim_failures"] = claim_failures
    for failure in claim_failures:
        add(result, "FAIL", failure["code"], failure["message"],
            **{k: v for k, v in failure.items() if k not in ("code", "message")})
    finish(result)
    write_json(work / "basic_check.json", result)
    print(f"[basic-check] verdict={result['verdict']} fails={len(result['failures'])} warns={len(result['warnings'])}")
    sys.exit(1 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
