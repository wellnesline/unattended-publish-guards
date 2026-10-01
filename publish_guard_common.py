"""Shared helpers for the publish gates (synthetic sample).

Settings that depend on the deployment (video size, allowed duration range) are read
from environment variables and have no default: when a setting is missing the gate
fails closed instead of guessing.

  PUBLISH_GUARD_VIDEO_SIZE      required output resolution, ``WIDTHxHEIGHT``
  PUBLISH_GUARD_VIDEO_SECONDS   allowed duration range, ``MIN-MAX`` (seconds)
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def video_size() -> tuple[int, int]:
    """Required (width, height). Must be passed by environment variable (no default)."""
    raw = os.environ.get("PUBLISH_GUARD_VIDEO_SIZE", "").strip().lower()
    if not raw:
        raise RuntimeError("PUBLISH_GUARD_VIDEO_SIZE is not set (WIDTHxHEIGHT, no default)")
    try:
        width, height = (int(part) for part in raw.split("x"))
    except ValueError as exc:
        raise RuntimeError(f"PUBLISH_GUARD_VIDEO_SIZE must look like WIDTHxHEIGHT: {raw!r}") from exc
    if width < 1 or height < 1:
        raise RuntimeError(f"PUBLISH_GUARD_VIDEO_SIZE must be positive: {raw!r}")
    return width, height


def video_seconds() -> tuple[float, float]:
    """Allowed (min, max) duration in seconds. Must be passed by environment variable (no default)."""
    raw = os.environ.get("PUBLISH_GUARD_VIDEO_SECONDS", "").strip()
    if not raw:
        raise RuntimeError("PUBLISH_GUARD_VIDEO_SECONDS is not set (MIN-MAX, no default)")
    try:
        low, high = (float(part) for part in raw.split("-"))
    except ValueError as exc:
        raise RuntimeError(f"PUBLISH_GUARD_VIDEO_SECONDS must look like MIN-MAX: {raw!r}") from exc
    if not 0 < low <= high:
        raise RuntimeError(f"PUBLISH_GUARD_VIDEO_SECONDS needs 0 < MIN <= MAX: {raw!r}")
    return low, high


def probe_mp4(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")
    data = json.loads(result.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "width": video.get("width"), "height": video.get("height"),
        "duration_sec": float(data.get("format", {}).get("duration", 0)),
    }


def result_base(name: str, work_dir: Path) -> dict[str, Any]:
    return {
        "gate": name, "work_dir": str(work_dir), "checked_at_jst": now_jst().isoformat(),
        "verdict": "PASS", "failures": [], "warnings": [], "checks": {},
    }


def add(result: dict[str, Any], level: str, code: str, message: str, **details: Any) -> None:
    item: dict[str, Any] = {"code": code, "message": message}
    if details:
        item["details"] = details
    result["failures" if level == "FAIL" else "warnings"].append(item)


def finish(result: dict[str, Any]) -> dict[str, Any]:
    result["verdict"] = "FAIL" if result["failures"] else ("WARN" if result["warnings"] else "PASS")
    return result
