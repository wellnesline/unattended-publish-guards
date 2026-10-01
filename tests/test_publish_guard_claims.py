"""Claim validator and basic check regression tests (synthetic data only)."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from publish_guard_claims import get_path, validate_claims
from synthetic_artifact import DATE, build_work, content_manifest, script_doc

SCRIPTS = Path(__file__).resolve().parents[1]
PUBLIC_AT = f"{DATE}T09:00:00+09:00"


def codes(manifest: dict, script: dict) -> set[str]:
    return {failure["code"] for failure in validate_claims(manifest, script)}


def test_unmodified_script_passes() -> None:
    assert validate_claims(content_manifest(), script_doc()) == []


def test_get_path_handles_lists_and_missing() -> None:
    assert get_path(content_manifest(), "items.1.values.score") == 17
    assert get_path(content_manifest(), "items.9.values.score") is None


def test_number_not_in_manifest_is_detected() -> None:
    script = script_doc()
    script["lines"][4]["text"] = "項目2のスコアは99です。"
    assert "NP_NUMERIC" in codes(content_manifest(), script)


def test_value_claim_that_differs_from_manifest_is_detected() -> None:
    script = script_doc()
    script["lines"][1]["claims"][0]["value"] = 43
    script["lines"][1]["text"] = "項目1のスコアは43です。"
    assert "NP_NUMERIC" in codes(content_manifest(), script)


def test_unknown_item_reference_is_detected() -> None:
    script = script_doc()
    script["lines"][4]["text"] = "項目9のスコアは17です。"
    assert "NP_TARGET" in codes(content_manifest(), script)


def test_total_count_mismatch_is_detected() -> None:
    script = script_doc()
    script["lines"][0]["text"] = "今日は全4件を順に見ていきます。"
    assert "NP_COUNT" in codes(content_manifest(), script)


def test_reversed_comparison_is_detected() -> None:
    script = script_doc()
    script["lines"][3]["claims"][0]["op"] = "lt"
    assert "NP_LOGIC" in codes(content_manifest(), script)


def test_unknown_claim_kind_is_rejected() -> None:
    script = script_doc()
    script["lines"][3]["claims"][0]["kind"] = "mystery"
    assert "NP_LOGIC" in codes(content_manifest(), script)


def test_manifest_is_not_modified_by_validation() -> None:
    manifest = content_manifest()
    before = copy.deepcopy(manifest)
    validate_claims(manifest, script_doc())
    assert manifest == before


def _basic_check(work: Path, public_at: str = PUBLIC_AT) -> tuple[subprocess.CompletedProcess[str], dict]:
    run = subprocess.run([sys.executable, str(SCRIPTS / "publish_guard_basic_check.py"), "--work-dir", str(work),
                          "--date", DATE, "--public-at", public_at], text=True, capture_output=True)
    return run, json.loads((work / "basic_check.json").read_text(encoding="utf-8"))


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    return build_work(tmp_path / DATE / "job_a", mp4=False)


def test_basic_check_passes_on_synthetic_work_dir(work: Path) -> None:
    run, result = _basic_check(work)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result["verdict"] == "PASS"


def test_basic_check_detects_tampered_number(work: Path) -> None:
    path = work / "script.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["lines"][4]["text"] = "項目2のスコアは99です。"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    run, result = _basic_check(work)
    assert run.returncode != 0
    assert "NP_NUMERIC" in {f["code"] for f in result["failures"]}


def test_basic_check_rejects_expired_item(work: Path) -> None:
    run, result = _basic_check(work, public_at=f"{DATE}T19:00:00+09:00")
    assert run.returncode != 0
    assert "G3_EXPIRED" in {f["code"] for f in result["failures"]}


def test_basic_check_rejects_wrong_date(work: Path) -> None:
    run = subprocess.run([sys.executable, str(SCRIPTS / "publish_guard_basic_check.py"), "--work-dir", str(work),
                          "--date", "2030-01-16", "--public-at", PUBLIC_AT], text=True, capture_output=True)
    result = json.loads((work / "basic_check.json").read_text(encoding="utf-8"))
    assert run.returncode != 0
    assert "G3_DATE" in {f["code"] for f in result["failures"]}


def test_basic_check_fails_closed_without_script(tmp_path: Path) -> None:
    work = tmp_path / DATE / "job_a"
    work.mkdir(parents=True)
    run = subprocess.run([sys.executable, str(SCRIPTS / "publish_guard_basic_check.py"), "--work-dir", str(work),
                          "--date", DATE], text=True, capture_output=True)
    assert run.returncode != 0
    assert "G3_INPUT" in {f["code"] for f in json.loads((work / "basic_check.json").read_text(encoding="utf-8"))["failures"]}
