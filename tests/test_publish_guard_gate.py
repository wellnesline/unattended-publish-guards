"""Final publish gate regression tests (synthetic artifact only).

Skipped when ffmpeg / ffprobe are not available (a synthetic video is needed).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from synthetic_artifact import DATE, build_work, have_ffmpeg, write_channel_yaml

pytestmark = pytest.mark.skipif(not have_ffmpeg(), reason="ffmpeg/ffprobe required to create the synthetic mp4")

SCRIPTS = Path(__file__).resolve().parents[1]
GUARD = SCRIPTS / "publish_guard_gate.py"
PUBLIC_AT = f"{DATE}T09:00:00+09:00"


@pytest.fixture(scope="module")
def source(tmp_path_factory) -> Path:
    return build_work(tmp_path_factory.mktemp("src") / DATE / "job_a")


@pytest.fixture()
def work(tmp_path: Path, source: Path) -> Path:
    target = tmp_path / DATE / "job_a"
    shutil.copytree(source, target)
    write_channel_yaml(tmp_path / "channel.yaml")
    return target


def _run(work: Path, public_at: str = PUBLIC_AT, env: dict | None = None):
    run = subprocess.run(
        [sys.executable, str(GUARD), "--work-dir", str(work), "--public-at", public_at,
         "--channel-yaml", str(work.parents[1] / "channel.yaml")],
        cwd=SCRIPTS, text=True, capture_output=True, check=False, env=env or {**os.environ, "EXPECTED_CHANNEL_ID": "<CHANNEL_ID>"},
    )
    manifest = json.loads((work / "publish_manifest.json").read_text(encoding="utf-8"))
    return run, manifest


def _codes(manifest: dict) -> set[str]:
    return {failure["code"] for failure in manifest["checks"]["failures"]}


def _write_json(path: Path, value: dict, *, preserve_mtime: bool = True) -> None:
    stat = path.stat()
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    if preserve_mtime:
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))


def test_full_artifact_passes_and_writes_hash_bundle(work: Path) -> None:
    run, manifest = _run(work)
    assert run.returncode == 0, run.stdout + run.stderr
    assert manifest["gate_verdict"] == "PASS"
    bundle = manifest["artifact_hash_bundle"]
    assert {"content_manifest", "script", "meta", "output_mp4"} <= set(bundle["files"])
    assert len(bundle["bundle_sha256"]) == 64


def test_rejects_missing_expected_channel_id(work: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k != "EXPECTED_CHANNEL_ID"}
    _, manifest = _run(work, env=env)
    assert "G1_CHANNEL_ID" in _codes(manifest)


def test_rejects_other_channel_id(work: Path) -> None:
    write_channel_yaml(work.parents[1] / "channel.yaml", channel_id="<OTHER_CHANNEL_ID>")
    run, manifest = _run(work)
    assert run.returncode == 1
    assert "G1_CHANNEL_ID" in _codes(manifest)


def test_rejects_script_number_tamper(work: Path) -> None:
    path = work / "script.json"
    script = json.loads(path.read_text(encoding="utf-8"))
    script["lines"][4]["text"] = "項目2のスコアは99です。"
    _write_json(path, script)
    run, manifest = _run(work)
    assert run.returncode == 1
    assert "G1_CLAIMS" in _codes(manifest)


def test_rejects_stale_mp4(work: Path) -> None:
    path = work / "content_manifest.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    _write_json(path, doc, preserve_mtime=False)
    os.utime(work / "output.mp4", (time.time() - 60, time.time() - 60))
    run, manifest = _run(work)
    assert run.returncode == 1
    assert "G1_MP4_STALE" in _codes(manifest)


def test_rechecks_validity_immediately_before_publish(work: Path) -> None:
    run, manifest = _run(work, public_at=f"{DATE}T17:30:00+09:00")
    assert run.returncode == 1
    assert "G1_VALID_UNTIL" in _codes(manifest)


def test_rejects_failed_log_verdict(work: Path) -> None:
    (work / "log.yaml").write_text("final_verdict: FAIL\n", encoding="utf-8")
    run, manifest = _run(work)
    assert run.returncode == 1
    assert "G1_FINAL_VERDICT" in _codes(manifest)


def test_rejects_wrong_resolution(work: Path) -> None:
    env = {**os.environ, "EXPECTED_CHANNEL_ID": "<CHANNEL_ID>", "PUBLISH_GUARD_VIDEO_SIZE": "720x1280"}
    run, manifest = _run(work, env=env)
    assert run.returncode == 1
    assert "G1_RESOLUTION" in _codes(manifest)
