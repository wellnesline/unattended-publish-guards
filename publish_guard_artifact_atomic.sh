#!/bin/bash
# バッチ共通: canonical work_dir を backup/restore で保護する。
# pipeline は canonical へ直接書くため完全な staging ではないが、旧版を同じ親
# directory に rename してから実行し、失敗時に戻す。rename の原子性を保つため、
# /tmp 等の別 filesystem は退避先に使わない。

PUBLISH_GUARD_ARTIFACT_ACTIVE=0
PUBLISH_GUARD_ARTIFACT_WORK=""
PUBLISH_GUARD_ARTIFACT_PREV=""
PUBLISH_GUARD_ARTIFACT_FAILED=""

publish_guard_artifact_cleanup_old() {
  # 14日より古い診断用退避だけを best-effort で消す。本体の成功・失敗には影響させない。
  local work="$1" parent base days
  parent="$(dirname "$work")"
  base="$(basename "$work")"
  days="${PUBLISH_GUARD_ARTIFACT_RETENTION_DAYS:-14}"
  if ! [[ "$days" =~ ^[0-9]+$ ]]; then
    echo "[publish_guard-artifact] cleanup skipped: invalid retention days=$days" >&2
    return 0
  fi
  find "$parent" -mindepth 1 -maxdepth 1 -type d \( -name "$base.prev-*" -o -name "$base.failed-*" \) \
    -mtime "+$days" -exec rm -rf {} + 2>/dev/null \
    || echo "[publish_guard-artifact] cleanup failed (ignored) work=$work" >&2
}

publish_guard_artifact_latest_prev() {
  # run_id は時刻順なので glob の最後を最も新しい orphan とみなす。
  local work="$1" candidate latest=""
  for candidate in "$work".prev-*; do
    [[ -e "$candidate" ]] || continue
    latest="$candidate"
  done
  [[ -n "$latest" ]] && printf '%s\n' "$latest"
}

publish_guard_artifact_recover_orphan() {
  # SIGKILL/ホスト停止後に .prev-* が残った場合、旧完全版を先に canonical へ戻す。
  # canonical に途中版が残っていれば failed として隔離してから戻す。
  local work="$1" orphan recovered_id failed
  orphan="$(publish_guard_artifact_latest_prev "$work")"
  [[ -n "$orphan" ]] || return 0
  recovered_id="recovered-$(date +%Y%m%d_%H%M%S)-$$"
  failed="$work.failed-$recovered_id"
  if [[ -e "$work" || -L "$work" ]]; then
    mv "$work" "$failed" || {
      echo "[publish_guard-artifact] ERROR: cannot isolate interrupted canonical work=$work" >&2
      return 1
    }
  fi
  mv "$orphan" "$work" || {
    echo "[publish_guard-artifact] ERROR: cannot restore orphan prev=$orphan" >&2
    return 1
  }
  echo "[publish_guard-artifact] recovered interrupted run prev=$orphan canonical=$work"
}

publish_guard_artifact_begin() {
  # Call before the first command that can write WORK (generator included).
  local work="$1" run_id="$2" parent
  if [[ "$PUBLISH_GUARD_ARTIFACT_ACTIVE" == 1 ]]; then
    echo "[publish_guard-artifact] ERROR: transaction already active work=$PUBLISH_GUARD_ARTIFACT_WORK" >&2
    return 1
  fi
  parent="$(dirname "$work")"
  mkdir -p "$parent" || return 1
  publish_guard_artifact_recover_orphan "$work" || return 1
  PUBLISH_GUARD_ARTIFACT_WORK="$work"
  PUBLISH_GUARD_ARTIFACT_PREV="$work.prev-$run_id"
  PUBLISH_GUARD_ARTIFACT_FAILED="$work.failed-$run_id"
  if [[ -e "$PUBLISH_GUARD_ARTIFACT_PREV" || -L "$PUBLISH_GUARD_ARTIFACT_PREV" || -e "$PUBLISH_GUARD_ARTIFACT_FAILED" || -L "$PUBLISH_GUARD_ARTIFACT_FAILED" ]]; then
    echo "[publish_guard-artifact] ERROR: run_id collision run_id=$run_id" >&2
    return 1
  fi
  # WORK と PREV は同じ親の sibling。ここでの mv は同一 filesystem の atomic rename。
  if [[ -e "$work" || -L "$work" ]]; then
    mv "$work" "$PUBLISH_GUARD_ARTIFACT_PREV" || return 1
    echo "[publish_guard-artifact] backed up canonical prev=$PUBLISH_GUARD_ARTIFACT_PREV"
  fi
  PUBLISH_GUARD_ARTIFACT_ACTIVE=1
  publish_guard_artifact_cleanup_old "$work"
}

publish_guard_artifact_commit() {
  # 3 gate (特に publish_guard=PASS) が全て通った後だけ呼ぶ。
  [[ "$PUBLISH_GUARD_ARTIFACT_ACTIVE" == 1 ]] || return 0
  if [[ ! -d "$PUBLISH_GUARD_ARTIFACT_WORK" ]]; then
    echo "[publish_guard-artifact] ERROR: canonical missing at commit work=$PUBLISH_GUARD_ARTIFACT_WORK" >&2
    return 1
  fi
  if [[ -e "$PUBLISH_GUARD_ARTIFACT_PREV" || -L "$PUBLISH_GUARD_ARTIFACT_PREV" ]]; then
    rm -rf "$PUBLISH_GUARD_ARTIFACT_PREV" || return 1
  fi
  PUBLISH_GUARD_ARTIFACT_ACTIVE=0
  echo "[publish_guard-artifact] committed canonical work=$PUBLISH_GUARD_ARTIFACT_WORK"
  publish_guard_artifact_cleanup_old "$PUBLISH_GUARD_ARTIFACT_WORK"
}

publish_guard_artifact_abort() {
  # 新版/半端版を canonical から外してから、旧完全版だけを戻す。
  [[ "$PUBLISH_GUARD_ARTIFACT_ACTIVE" == 1 ]] || return 0
  if [[ -e "$PUBLISH_GUARD_ARTIFACT_WORK" || -L "$PUBLISH_GUARD_ARTIFACT_WORK" ]]; then
    mv "$PUBLISH_GUARD_ARTIFACT_WORK" "$PUBLISH_GUARD_ARTIFACT_FAILED" || {
      echo "[publish_guard-artifact] ERROR: cannot isolate failed canonical work=$PUBLISH_GUARD_ARTIFACT_WORK" >&2
      return 1
    }
    echo "[publish_guard-artifact] isolated failed work=$PUBLISH_GUARD_ARTIFACT_FAILED"
  fi
  if [[ -e "$PUBLISH_GUARD_ARTIFACT_PREV" || -L "$PUBLISH_GUARD_ARTIFACT_PREV" ]]; then
    mv "$PUBLISH_GUARD_ARTIFACT_PREV" "$PUBLISH_GUARD_ARTIFACT_WORK" || {
      echo "[publish_guard-artifact] ERROR: cannot restore previous canonical prev=$PUBLISH_GUARD_ARTIFACT_PREV" >&2
      return 1
    }
    echo "[publish_guard-artifact] restored previous canonical work=$PUBLISH_GUARD_ARTIFACT_WORK"
  fi
  PUBLISH_GUARD_ARTIFACT_ACTIVE=0
  publish_guard_artifact_cleanup_old "$PUBLISH_GUARD_ARTIFACT_WORK"
}
