#!/usr/bin/env python3
"""Final publish gate: check the artifacts and issue a hash-bound publish manifest (synthetic sample).

Fails closed: any failure, or anything that cannot be decided, blocks publishing.
Checks: log verdict, artifacts exist, the video is not older than its inputs, resolution
and duration, the manifest-vs-script claims, items still valid at publish time, and the
destination channel id against an allow value. Writes ``publish_manifest.json`` that
binds the verdict to the SHA-256 of every artifact.

  EXPECTED_CHANNEL_ID   the only destination channel id allowed (unset -> G1_CHANNEL_ID fails)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from publish_guard_claims import validate_claims
from publish_guard_common import (JST, add, finish, load_json, now_jst, probe_mp4, result_base, sha256,
                                  video_seconds, video_size, write_json)

EXPECTED_CHANNEL_ID = os.environ.get("EXPECTED_CHANNEL_ID", "")
DEPENDENCY_NAMES = ("script.json", "content_manifest.json", "meta.json")
HASHED_ARTIFACTS = (("content_manifest", "content_manifest.json"), ("script", "script.json"),
                    ("meta", "meta.json"), ("output_mp4", "output.mp4"))
VALID_MARGIN_SECONDS = 3600


def canonical_sha256(value: Any) -> str:
    """Hash a JSON value independent of key order."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_public_at(value: str | None) -> datetime:
    if value is None:
        return now_jst()
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=JST) if parsed.tzinfo is None else parsed.astimezone(JST)


def check_validity(result: dict[str, Any], manifest: dict[str, Any], public_at: datetime) -> None:
    """Re-check right before publishing that every time-limited item is still valid with a margin."""
    too_soon = []
    for item in manifest.get("items", []):
        until = item.get("valid_until")
        if until is None:
            continue
        try:
            expires = datetime.fromisoformat(str(until))
            expires = expires.replace(tzinfo=JST) if expires.tzinfo is None else expires.astimezone(JST)
        except ValueError:
            add(result, "FAIL", "G1_VALID_UNTIL_DATA", "valid_until is not ISO 8601", item_id=item.get("id"))
            continue
        left = (expires - public_at).total_seconds()
        if left < VALID_MARGIN_SECONDS:
            too_soon.append({"item_id": item.get("id"), "valid_until": expires.isoformat(), "seconds_left": round(left, 3)})
    result["checks"]["public_at_jst"] = public_at.isoformat()
    if too_soon:
        add(result, "FAIL", "G1_VALID_UNTIL", "an item expires within the required margin", items=too_soon,
            margin_seconds=VALID_MARGIN_SECONDS)


def hash_bundle(work: Path) -> dict[str, Any]:
    files = {label: sha256(work / name) for label, name in HASHED_ARTIFACTS if (work / name).is_file()}
    bundle: dict[str, Any] = {"files": files}
    bundle["bundle_sha256"] = canonical_sha256(bundle)
    return bundle


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--channel-yaml", type=Path, default=Path("channel.yaml"))
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--public-at", help="ISO 8601 publish time; defaults to now (JST)")
    args = ap.parse_args()
    work = args.work_dir.resolve()
    result = result_base("publish_guard", work)
    mp4, script, log = work / "output.mp4", work / "script.json", work / "log.yaml"
    verdict = None
    try:
        public_at = parse_public_at(args.public_at)
    except ValueError as exc:
        add(result, "FAIL", "G1_PUBLIC_AT", "--public-at must be ISO 8601", error=str(exc))
        public_at = now_jst()
    if not log.exists():
        add(result, "FAIL", "G1_LOG_MISSING", "log.yaml is missing")
    else:
        try:
            verdict = (yaml.safe_load(log.read_text(encoding="utf-8")) or {}).get("final_verdict")
            if verdict not in {"PASS", "WARN_ACCEPTED"}:
                add(result, "FAIL", "G1_FINAL_VERDICT", "final_verdict must be PASS or WARN_ACCEPTED", actual=verdict)
        except Exception as exc:
            add(result, "FAIL", "G1_LOG_INVALID", "log.yaml cannot be read", error=str(exc))
    if not mp4.exists() or not script.exists():
        add(result, "FAIL", "G1_ARTIFACT_MISSING", "output.mp4 or script.json is missing",
            mp4_exists=mp4.exists(), script_exists=script.exists())
    else:
        # Compare at one-second resolution: files written in the same build second are not stale.
        newer = [name for name in DEPENDENCY_NAMES if (work / name).is_file()
                 and int((work / name).stat().st_mtime) > int(mp4.stat().st_mtime)]
        if newer:
            add(result, "FAIL", "G1_MP4_STALE", "an input is newer than output.mp4", newer_dependencies=newer)
    mp4_hash = None
    if mp4.exists():
        try:
            mp4_hash = sha256(mp4)
            result["checks"]["mp4_sha256"] = mp4_hash
            media = probe_mp4(mp4)
            result["checks"]["media"] = media
            try:
                want_size, (min_sec, max_sec) = video_size(), video_seconds()
            except RuntimeError as exc:
                add(result, "FAIL", "G1_CONFIG", "video size and duration limits are not configured", error=str(exc))
            else:
                if (media["width"], media["height"]) != want_size:
                    add(result, "FAIL", "G1_RESOLUTION", "video size differs from the configured value",
                        expected=f"{want_size[0]}x{want_size[1]}", actual=f"{media['width']}x{media['height']}")
                if not min_sec <= media["duration_sec"] <= max_sec:
                    add(result, "FAIL", "G1_DURATION", "video duration is outside the configured range",
                        expected=f"{min_sec:g}-{max_sec:g}", actual=media["duration_sec"])
        except Exception as exc:
            add(result, "FAIL", "G1_MEDIA_PROBE", "the video cannot be inspected", error=str(exc))
    try:
        manifest = load_json(work / "content_manifest.json")
        claim_failures = validate_claims(manifest, load_json(script))
        result["checks"]["claim_failures"] = len(claim_failures)
        if claim_failures:
            add(result, "FAIL", "G1_CLAIMS", "the script does not match the content manifest", failures=claim_failures)
        check_validity(result, manifest, public_at)
    except Exception as exc:
        add(result, "FAIL", "G1_CONTENT_MANIFEST", "content_manifest.json or script.json cannot be checked", error=str(exc))
    result["checks"]["artifact_hash_bundle"] = hash_bundle(work)
    try:
        channel = yaml.safe_load(args.channel_yaml.read_text(encoding="utf-8")) or {}
        actual_channel = (channel.get("youtube") or {}).get("channel_id")
        result["checks"]["channel_id"] = actual_channel
        if not EXPECTED_CHANNEL_ID or actual_channel != EXPECTED_CHANNEL_ID:
            add(result, "FAIL", "G1_CHANNEL_ID", "channel_id in the channel config differs from the allowed value", actual=actual_channel)
    except Exception as exc:
        add(result, "FAIL", "G1_CHANNEL_CONFIG", "the channel config cannot be read", error=str(exc))
    git = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False).stdout.strip() or "unknown"
    finish(result)
    publish_manifest = {
        "schema_version": 3, "run_id": args.run_id or f"{work.name}-{now_jst().strftime('%Y%m%dT%H%M%S%z')}",
        "channel_id": EXPECTED_CHANNEL_ID, "git_hash": git, "output_mp4_sha256": mp4_hash,
        "artifact_hash_bundle": result["checks"]["artifact_hash_bundle"],
        "verdict": verdict, "gate_verdict": result["verdict"], "checks": result,
        "created_at_jst": now_jst().isoformat(),
    }
    previous_path = work / "publish_manifest.json"
    if previous_path.exists():
        try:
            previous = load_json(previous_path)
            for key in ("youtube_video_id", "youtube_uploaded_at"):
                if isinstance(previous.get(key), str) and previous[key]:
                    publish_manifest[key] = previous[key]
        except Exception:
            pass
    write_json(previous_path, publish_manifest)
    print(f"[publish-guard] verdict={result['verdict']} manifest={previous_path}")
    sys.exit(1 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__":
    main()
