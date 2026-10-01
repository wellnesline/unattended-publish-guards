#!/usr/bin/env python3
"""動画を緊急 private 化する。削除は行わない。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

# 許可する YouTube channel_id は環境変数で渡す。
EXPECTED_CHANNEL_ID = os.environ.get("EXPECTED_CHANNEL_ID", "")
# 作業ディレクトリの置き場、OAuth トークン、記録ファイルは環境変数で渡す (synthetic sample)。
WORK_ROOT = Path(os.environ.get("PUBLISH_GUARD_WORK_ROOT", "work"))
TOKEN_FILE = Path(os.environ.get("YOUTUBE_TOKEN_FILE", "secrets/token.json"))
LEDGER = Path(os.environ.get("PUBLISH_GUARD_INCIDENT_LEDGER", "publish_incidents.jsonl"))


def latest_video_id() -> tuple[str, Path]:
    manifests = sorted(WORK_ROOT.glob("*/*/publish_manifest.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in manifests:
        try:
            video_id = json.loads(path.read_text(encoding="utf-8")).get("youtube_video_id")
            if video_id: return str(video_id), path
        except Exception: pass
    raise RuntimeError("video_id を持つ publish_manifest.json がありません。--video-id を指定してください")


def main() -> None:
    ap = argparse.ArgumentParser(description="YouTube動画をprivateに変更する緊急コマンド")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--video-id"); group.add_argument("--latest", action="store_true")
    ap.add_argument("--reason", default="QA incident emergency private")
    args = ap.parse_args()
    try:
        video_id, manifest = (latest_video_id() if args.latest else (args.video_id, None))
        from google.oauth2.credentials import Credentials  # type: ignore
        from googleapiclient.discovery import build  # type: ignore
        token = TOKEN_FILE
        if not token.exists(): raise RuntimeError(f"OAuth token がありません: {token}")
        youtube = build("youtube", "v3", credentials=Credentials.from_authorized_user_file(str(token)))
        response = youtube.videos().list(part="snippet,status", id=video_id).execute(); items = response.get("items", [])
        if not items: raise RuntimeError(f"動画が見つからないか所有権がありません: {video_id}")
        item = items[0]; actual_channel = item.get("snippet", {}).get("channelId")
        if not EXPECTED_CHANNEL_ID or actual_channel != EXPECTED_CHANNEL_ID: raise RuntimeError(f"許可外チャンネルを拒否: {actual_channel}")
        before = item.get("status", {}).get("privacyStatus")
        youtube.videos().update(part="status", body={"id": video_id, "status": {"privacyStatus": "private"}}).execute()
        event = {"at_utc": datetime.now(timezone.utc).isoformat(), "action": "set_private", "video_id": video_id, "title": item.get("snippet", {}).get("title"), "before_privacy": before, "after_privacy": "private", "reason": args.reason, "operator": os.environ.get("USER", "unknown"), "manifest": str(manifest) if manifest else None}
        ledger = LEDGER; ledger.parent.mkdir(parents=True, exist_ok=True)
        with ledger.open("a", encoding="utf-8") as handle: handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        print(f"[INCIDENT] video_id={video_id} privacy={before}->private ledger={ledger}")
    except Exception as exc:
        print(f"[unpublish] ERROR: {exc}", file=sys.stderr); sys.exit(1)


if __name__ == "__main__": main()
