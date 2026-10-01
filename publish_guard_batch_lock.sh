#!/bin/bash
# バッチ専用の mkdir ベース排他ロック。flock が無い環境でも使える。

PUBLISH_GUARD_LOCK_ROOT="${PUBLISH_GUARD_LOCK_ROOT:-./locks}"
# 再取得と、その後のbuild/QA/uploadを吸収する既定の保持時間(例)。
PUBLISH_GUARD_LOCK_MAX_AGE_SECONDS="${PUBLISH_GUARD_LOCK_MAX_AGE_SECONDS:-18000}"
PUBLISH_GUARD_LOCK_DIR=""

publish_guard_lock_owner_value() {
  local key="$1"
  awk -F= -v key="$key" '$1 == key {print substr($0, length(key) + 2); exit}' "$PUBLISH_GUARD_LOCK_DIR/owner" 2>/dev/null || true
}

publish_guard_lock_age_seconds() {
  local started now
  started="$(publish_guard_lock_owner_value started_epoch)"
  if [[ ! "$started" =~ ^[0-9]+$ ]]; then
    started="$(stat -c %Y "$PUBLISH_GUARD_LOCK_DIR" 2>/dev/null || stat -f %m "$PUBLISH_GUARD_LOCK_DIR" 2>/dev/null || echo 0)"
  fi
  now="$(date +%s)"
  echo $((now - started))
}

publish_guard_lock_owner_is_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null
}

# 成功時は 0、別プロセスが保持中なら 1。呼出元は 1 を正常な skip として扱う。
publish_guard_acquire_lock() {
  local job="$1" age owner_pid reclaim_reason=""
  mkdir -p "$PUBLISH_GUARD_LOCK_ROOT"
  PUBLISH_GUARD_LOCK_DIR="$PUBLISH_GUARD_LOCK_ROOT/${job}.lock"

  if mkdir "$PUBLISH_GUARD_LOCK_DIR" 2>/dev/null; then
    printf 'pid=%s\nstarted_epoch=%s\nrun_id=%s\n' "$$" "$(date +%s)" "${RUN_ID:-unknown}" > "$PUBLISH_GUARD_LOCK_DIR/owner"
    echo "[publish_guard-lock] acquired job=$job dir=$PUBLISH_GUARD_LOCK_DIR pid=$$"
    return 0
  fi

  age="$(publish_guard_lock_age_seconds)"
  owner_pid="$(publish_guard_lock_owner_value pid)"
  # 長寿命coordinatorが生きている限り、年齢だけではlockを剥がさない。
  if [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]] && publish_guard_lock_owner_is_alive "$owner_pid"; then
    echo "[publish_guard-lock] already running; skipped job=$job owner_pid=$owner_pid age=${age:-unknown}s dir=$PUBLISH_GUARD_LOCK_DIR"
    return 1
  elif [[ "$owner_pid" =~ ^[1-9][0-9]*$ ]]; then
    reclaim_reason="owner_pid_dead"
  elif [[ "$age" =~ ^[0-9]+$ ]] && (( age > PUBLISH_GUARD_LOCK_MAX_AGE_SECONDS )); then
    reclaim_reason="max_age_exceeded"
  fi

  if [[ -n "$reclaim_reason" ]]; then
    echo "[publish_guard-lock] stale lock invalidated job=$job reason=$reclaim_reason age=${age}s dir=$PUBLISH_GUARD_LOCK_DIR"
    # 対象はこの job 専用に解決済みの lock directory のみ。
    rm -rf "$PUBLISH_GUARD_LOCK_DIR"
    if mkdir "$PUBLISH_GUARD_LOCK_DIR" 2>/dev/null; then
      printf 'pid=%s\nstarted_epoch=%s\nrun_id=%s\n' "$$" "$(date +%s)" "${RUN_ID:-unknown}" > "$PUBLISH_GUARD_LOCK_DIR/owner"
      echo "[publish_guard-lock] acquired after stale cleanup job=$job dir=$PUBLISH_GUARD_LOCK_DIR pid=$$"
      return 0
    fi
  fi

  echo "[publish_guard-lock] already running; skipped job=$job owner_pid=${owner_pid:-unknown} age=${age:-unknown}s dir=$PUBLISH_GUARD_LOCK_DIR"
  return 1
}

publish_guard_release_lock() {
  if [[ -n "${PUBLISH_GUARD_LOCK_DIR:-}" && -d "$PUBLISH_GUARD_LOCK_DIR" ]] && [[ "$(publish_guard_lock_owner_value pid)" == "$$" ]]; then
    rm -rf "$PUBLISH_GUARD_LOCK_DIR"
    echo "[publish_guard-lock] released dir=$PUBLISH_GUARD_LOCK_DIR pid=$$"
  fi
}
