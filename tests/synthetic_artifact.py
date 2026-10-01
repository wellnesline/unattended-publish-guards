"""Builds a synthetic work directory for the gate tests (invented values only)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

DATE = "2030-01-15"
VALID_UNTIL = f"{DATE}T18:00:00+09:00"


def content_manifest() -> dict:
    return {
        "date": DATE,
        "items": [
            {"id": "item-1", "label": "項目1", "valid_until": VALID_UNTIL, "values": {"score": 42, "rate": 12.5}},
            {"id": "item-2", "label": "項目2", "valid_until": VALID_UNTIL, "values": {"score": 17, "rate": 8.0}},
            {"id": "item-3", "label": "項目3", "valid_until": VALID_UNTIL, "values": {"score": 29, "rate": 20.5}},
        ],
    }


def script_doc() -> dict:
    lines = [
        {"id": "L01", "text": "今日は全3件を順に見ていきます。"},
        {"id": "L02", "text": "項目1のスコアは42です。",
         "claims": [{"id": "c1", "kind": "value", "value": 42, "manifest_path": "items.0.values.score"}]},
        {"id": "L03", "text": "項目1の割合は12.5%です。",
         "claims": [{"id": "c2", "kind": "value", "value": 12.5, "manifest_path": "items.0.values.rate"}]},
        {"id": "L04", "text": "項目3の割合は項目1より大きく、20.5%です。",
         "claims": [{"id": "c3", "kind": "compare", "op": "gt",
                     "left_path": "items.2.values.rate", "right_path": "items.0.values.rate"}]},
        {"id": "L05", "text": "項目2のスコアは17です。"},
    ]
    start = 0.0
    for line in lines:
        line["start_sec"], line["duration_sec"] = round(start, 3), 12.0
        start += 12.12
    return {"lines": lines, "duration_sec": round(start - 0.12, 3)}


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_mp4(path: Path, seconds: int = 60) -> None:
    """A blank video in the configured size (needs ffmpeg). Created last so it is newer than the JSON files."""
    width, height = os.environ["PUBLISH_GUARD_VIDEO_SIZE"].split("x")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=black:s={width}x{height}:r=1:d={seconds}", "-pix_fmt", "yuv420p", str(path)],
                   check=True)


def build_work(work: Path, *, mp4: bool = True) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    (work / "content_manifest.json").write_text(json.dumps(content_manifest(), ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "script.json").write_text(json.dumps(script_doc(), ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "meta.json").write_text(json.dumps({"title": "sample"}), encoding="utf-8")
    (work / "log.yaml").write_text("final_verdict: PASS\n", encoding="utf-8")
    if mp4:
        make_mp4(work / "output.mp4")
    return work


def write_channel_yaml(path: Path, channel_id: str = "<CHANNEL_ID>") -> Path:
    path.write_text(f"youtube:\n  channel_id: {channel_id}\n", encoding="utf-8")
    return path
