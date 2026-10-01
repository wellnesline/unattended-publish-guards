"""Duplicate-upload guard, speech matching helpers and batch lock (synthetic data only)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from publish_guard_speech_check import normalize, scored_best_line

SCRIPTS = Path(__file__).resolve().parents[1]


def _dup(work: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPTS / "publish_guard_duplicate.py"), "--work-dir", str(work), *extra],
                          text=True, capture_output=True)


def test_duplicate_guard_blocks_second_post_but_allows_force(tmp_path: Path) -> None:
    assert _dup(tmp_path).returncode == 0
    (tmp_path / "publish_manifest.json").write_text(json.dumps({"youtube_video_id": "abc123"}), encoding="utf-8")
    assert _dup(tmp_path).returncode == 10
    assert _dup(tmp_path, "--force").returncode == 0


def test_normalize_keeps_only_japanese_characters_and_digits() -> None:
    assert normalize("ＡＢＣ、項目１の 42!") == "項目1の42"


def test_time_penalty_prefers_the_line_near_the_audio_position() -> None:
    lines = [
        {"id": "L01", "text": "項目1のスコアは42です", "start_sec": 0, "duration_sec": 5},
        {"id": "L02", "text": "項目1のスコアは42です", "start_sec": 50, "duration_sec": 5},
    ]
    best, _, _ = scored_best_line(lines, "項目1のスコアは42です", 52)
    assert best == 1


@pytest.mark.skipif(sys.platform == "win32", reason="bash on Windows cannot take native Windows paths")
def test_batch_lock_second_acquire_is_skipped(tmp_path: Path) -> None:
    script = r'''
source "$1"
export PUBLISH_GUARD_LOCK_ROOT="$2"
publish_guard_acquire_lock job_a || exit 90
publish_guard_acquire_lock job_a; second=$?
publish_guard_release_lock
echo "second=$second"
'''
    run = subprocess.run(["bash", "-c", script, "lock-test", str(SCRIPTS / "publish_guard_batch_lock.sh"), str(tmp_path / "locks")],
                         text=True, capture_output=True)
    assert "second=1" in run.stdout, run.stdout + run.stderr
