#!/usr/bin/env python3
"""最終MP4の音声を faster-whisper で書き起こし、台本の表示時刻と照合する。"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from publish_guard_common import add, finish, load_json, result_base, write_json


def normalize(text: str) -> str:
    """比較対象をひらがな/カタカナ/漢字/数字だけに限定する。"""
    text = unicodedata.normalize("NFKC", text)
    return "".join(c for c in text if ("ぁ" <= c <= "ゟ") or ("ァ" <= c <= "ヿ") or ("一" <= c <= "鿿") or c.isdigit())


MIN_TRANSCRIPT_CHARS = 4
# 同一表現の再掲（例: 冒頭の紹介と終盤のまとめ）を取り違えないため、
# 1秒あたりこの値だけ類似度を下げる。60秒離れた行は最大 0.40 減点する。
TIME_PENALTY_PER_SEC = 0.01
MAX_TIME_PENALTY = 0.40


def expected_line(lines: list[dict], at_sec: float) -> int | None:
    for i, line in enumerate(lines):
        start = float(line.get("start_sec", 0)); end = start + float(line.get("duration_sec", 0))
        if start <= at_sec <= end:
            return i
    return min(range(len(lines)), key=lambda i: abs(at_sec - float(lines[i].get("start_sec", 0)))) if lines else None


def overlapping_lines(lines: list[dict], start_sec: float, end_sec: float) -> list[int]:
    """認識区間がまたぐ台本行を、いずれも正しい候補として返す。"""
    return [
        i for i, line in enumerate(lines)
        if float(line.get("start_sec", 0)) <= end_sec
        and start_sec <= float(line.get("start_sec", 0)) + float(line.get("duration_sec", 0))
    ]


def scored_best_line(lines: list[dict], transcript: str, anchor_sec: float) -> tuple[int, list[float], list[float]]:
    """文字類似度に時刻距離の減点を加え、再掲行の誤マッチを防ぐ。"""
    normalized = normalize(transcript)
    similarities = [
        difflib.SequenceMatcher(None, normalized, normalize(str(line.get("text", "")))).ratio()
        for line in lines
    ]
    scores = [
        similarity - min(
            MAX_TIME_PENALTY,
            abs(anchor_sec - (float(line.get("start_sec", 0)) + float(line.get("duration_sec", 0)) / 2))
            * TIME_PENALTY_PER_SEC,
        )
        for similarity, line in zip(similarities, lines, strict=True)
    ]
    return max(range(len(lines)), key=lambda i: scores[i]), similarities, scores


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", required=True, type=Path)
    ap.add_argument("--model", default="small")
    args = ap.parse_args(); work = args.work_dir.resolve(); result = result_base("speech_check", work); started = time.monotonic()
    mp4 = work / "output.mp4"
    try: lines = load_json(work / "script.json").get("lines", [])
    except Exception as exc: lines = []; add(result, "FAIL", "G2_SCRIPT", "script.json を読めません", error=str(exc))
    if not mp4.exists(): add(result, "FAIL", "G2_MP4", "output.mp4 がありません")
    segments_out: list[dict] = []
    if lines and mp4.exists():
        try:
            with tempfile.TemporaryDirectory(prefix="publish-guard-speech-") as temp:
                wav = Path(temp) / "audio.wav"
                extract = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4), "-vn", "-ac", "1", "-ar", "16000", str(wav)], capture_output=True, text=True)
                if extract.returncode: raise RuntimeError(extract.stderr.strip() or "ffmpeg audio extraction failed")
                from faster_whisper import WhisperModel
                model = WhisperModel(args.model, device="cpu", compute_type="int8")
                # VAD による行の断片化は、相槌・数字だけを独立セグメント化して
                # 一致率を不当に下げる。音声全体をそのまま書き起こしてから短文を除外する。
                segments, info = model.transcribe(str(wav), language="ja", vad_filter=False)
                for segment in segments:
                    transcript = segment.text.strip()
                    normalized = normalize(transcript)
                    item = {"start_sec": round(segment.start, 3), "end_sec": round(segment.end, 3), "transcript": transcript}
                    if len(normalized) < MIN_TRANSCRIPT_CHARS:
                        item.update({"ignored": True, "ignore_reason": f"normalized transcript < {MIN_TRANSCRIPT_CHARS} chars"})
                        segments_out.append(item)
                        continue
                    # 字幕表示は発話開始直後を基準にし、認識区間が行境界をまたぐ時は
                    # 両行のどちらへ最良一致しても合格とする。
                    display_at = segment.start + 0.2
                    expected = expected_line(lines, display_at)
                    overlaps = overlapping_lines(lines, segment.start, segment.end)
                    accepted = sorted(set(overlaps + ([expected] if expected is not None else [])))
                    best, similarities, scores = scored_best_line(lines, transcript, display_at)
                    item.update({
                        "display_at_sec": round(display_at, 3),
                        "expected_line_id": lines[expected].get("id") if expected is not None else None,
                        "overlapping_line_ids": [lines[i].get("id") for i in overlaps],
                        "best_line_id": lines[best].get("id"),
                        "best_similarity": round(similarities[best], 4),
                        "best_score": round(scores[best], 4),
                        "matches_expected": best in accepted,
                    })
                    segments_out.append(item)
                result["checks"]["language"] = getattr(info, "language", "ja")
        except Exception as exc:
            add(result, "FAIL", "G2_TRANSCRIBE", "音声の抽出またはfaster-whisper認識に失敗しました", error=str(exc))
    eligible = [item for item in segments_out if not item.get("ignored")]
    if not eligible and not result["failures"]: add(result, "FAIL", "G2_NO_SPEECH", "比較可能な認識セグメントが0件です")
    if eligible:
        matched = sum(1 for item in eligible if item["matches_expected"]); rate = matched / len(eligible)
        result["checks"].update({"segments": len(segments_out), "eligible_segments": len(eligible), "ignored_segments": len(segments_out) - len(eligible), "matched_segments": matched, "match_rate": round(rate, 4), "min_best_similarity": min(item["best_similarity"] for item in eligible)})
        if rate < .90: add(result, "FAIL", "G2_MATCH_RATE", "音声セグメントと時刻上の台本行の一致率が90%未満です", actual=rate)
        elif rate < .95: add(result, "WARN", "G2_MATCH_RATE", "音声セグメントと時刻上の台本行の一致率が95%未満です", actual=rate)
    result["segments"] = segments_out; result["elapsed_sec"] = round(time.monotonic() - started, 3)
    finish(result); write_json(work / "speech_check.json", result)
    print(f"[speech-check] verdict={result['verdict']} elapsed={result['elapsed_sec']}s segments={len(segments_out)}")
    sys.exit(1 if result["verdict"] == "FAIL" else 0)


if __name__ == "__main__": main()
