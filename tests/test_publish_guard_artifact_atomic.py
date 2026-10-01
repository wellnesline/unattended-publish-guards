"""バッチ共通の canonical backup/restore shell helper の回帰テスト。"""
from __future__ import annotations

import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]  # repo root (flat layout)
HELPER = ROOT / "publish_guard_artifact_atomic.sh"


def _executable(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\nset -u\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _run_transaction(work: Path, run_id: str, build: Path) -> subprocess.CompletedProcess[str]:
    # build は意図的に canonical を直接更新する。helper が失敗時にそこから隔離する。
    command = r'''
source "$1"
work="$2"
run_id="$3"
build="$4"
publish_guard_artifact_begin "$work" "$run_id" || exit $?
if "$build" "$work"; then
  publish_guard_artifact_commit
else
  build_rc=$?
  publish_guard_artifact_abort
  exit "$build_rc"
fi
'''
    return subprocess.run(
        ["bash", "-c", command, "atomic-test", str(HELPER), str(work), run_id, str(build)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_success_commits_new_canonical_and_removes_prev(tmp_path: Path) -> None:
    work = tmp_path / "job_a"
    work.mkdir()
    (work / "version").write_text("old", encoding="utf-8")
    build = _executable(
        tmp_path / "build_success.sh",
        'mkdir -p "$1"\nprintf new > "$1/version"\nrm -f "$1/old-only"\n',
    )

    run = _run_transaction(work, "success", build)

    assert run.returncode == 0, run.stdout + run.stderr
    assert (work / "version").read_text(encoding="utf-8") == "new"
    assert not list(tmp_path.glob("job_a.prev-*"))
    assert not list(tmp_path.glob("job_a.failed-*"))


def test_failure_restores_previous_canonical_and_isolates_partial(tmp_path: Path) -> None:
    work = tmp_path / "job_a"
    work.mkdir()
    (work / "version").write_text("old", encoding="utf-8")
    build = _executable(
        tmp_path / "build_failure.sh",
        'mkdir -p "$1"\nprintf partial > "$1/version"\nprintf half > "$1/half-made"\nexit 23\n',
    )

    run = _run_transaction(work, "failure", build)

    assert run.returncode == 23, run.stdout + run.stderr
    assert (work / "version").read_text(encoding="utf-8") == "old"
    failed = list(tmp_path.glob("job_a.failed-failure"))
    assert len(failed) == 1
    assert (failed[0] / "version").read_text(encoding="utf-8") == "partial"
    assert (failed[0] / "half-made").read_text(encoding="utf-8") == "half"
    assert not list(tmp_path.glob("job_a.prev-*"))


def test_failure_without_previous_leaves_canonical_absent(tmp_path: Path) -> None:
    work = tmp_path / "job_b"
    build = _executable(
        tmp_path / "build_failure.sh",
        'mkdir -p "$1"\nprintf partial > "$1/version"\nexit 9\n',
    )

    run = _run_transaction(work, "no-previous", build)

    assert run.returncode == 9, run.stdout + run.stderr
    assert not work.exists()
    failed = list(tmp_path.glob("job_b.failed-no-previous"))
    assert len(failed) == 1
    assert (failed[0] / "version").read_text(encoding="utf-8") == "partial"


def test_next_begin_recovers_orphan_prev_left_by_unkillable_interruption(tmp_path: Path) -> None:
    work = tmp_path / "job_a"
    work.mkdir()
    (work / "version").write_text("old", encoding="utf-8")
    # この subprocess には trap を置かず、begin 後に突然終了した状態を再現する。
    interrupted = subprocess.run(
        [
            "bash", "-c",
            'source "$1"; publish_guard_artifact_begin "$2" killed; mkdir -p "$2"; printf partial > "$2/version"',
            "atomic-test", str(HELPER), str(work),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert interrupted.returncode == 0, interrupted.stdout + interrupted.stderr

    recovered = subprocess.run(
        [
            "bash", "-c",
            'source "$1"; publish_guard_artifact_begin "$2" next; publish_guard_artifact_abort',
            "atomic-test", str(HELPER), str(work),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert (work / "version").read_text(encoding="utf-8") == "old"
    recovered_failed = list(tmp_path.glob("job_a.failed-recovered-*"))
    assert len(recovered_failed) == 1
    assert (recovered_failed[0] / "version").read_text(encoding="utf-8") == "partial"
