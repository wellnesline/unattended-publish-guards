#!/usr/bin/env python3
"""同じ成果物ディレクトリの重複投稿を、ファイルシステム上の記録から止める。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml


def video_id_from(work: Path) -> str | None:
    manifest = work / "publish_manifest.json"
    if manifest.exists():
        try:
            value = json.loads(manifest.read_text(encoding="utf-8")).get("youtube_video_id")
            if isinstance(value, str) and value:
                return value
        except (OSError, ValueError, TypeError):
            pass
    log = work / "log.yaml"
    if log.exists():
        try:
            upload = (yaml.safe_load(log.read_text(encoding="utf-8")) or {}).get("upload") or {}
            value = upload.get("youtube_video_id")
            if isinstance(value, str) and value:
                return value
        except (OSError, yaml.YAMLError, TypeError):
            pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    video_id = video_id_from(args.work_dir)
    if video_id and not args.force:
        print(f"[publish-guard-duplicate] already posted; skipped video_id={video_id}")
        raise SystemExit(10)
    if video_id:
        print(f"[publish-guard-duplicate] force override accepted video_id={video_id}")
    else:
        print("[publish-guard-duplicate] no prior post record")


if __name__ == "__main__":
    main()
