# unattended-publish-guards

Fail-closed publish gates for an unattended video pipeline: script-vs-manifest number checks, speech-vs-script verification, hash-bound publish manifests, duplicate-upload guards, an emergency unpublish command, a batch lock and an atomic artifact swap.

無人で動画を公開するパイプラインに組み込む、事故防止のガード部品集です。台本の数値と成果物マニフェストの照合、完成動画の音声と台本の照合、hash 束縛した公開 manifest、重複投稿の防止、緊急の非公開化、バッチの排他ロックと成果物の原子的な入れ替えを、単体で使える部品として実装しています。公開用に整理したサンプル実装で、テストはすべて合成データで動きます。

## 概要

自動公開では、「検査をすり抜けたら、そのまま世に出る」ことが最大のリスクです。ここでは、公開の直前に 3 つの独立したゲートを通し、1 つでも不合格または判定不能なら投稿しない設計にしています。判定できない場合は通さない側（fail-closed）に倒します。

## 主な機能・設計のポイント

- **台本とマニフェストの照合**（`publish_guard_claims.py`）: 画面に出す値は `content_manifest.json` を唯一の正本とし、台本（`script.json`）が読み上げる数値・項目名・総件数・大小比較がそれと一致するかを検証します。台本の行に型付きの主張（`claims`）を付けると、マニフェストの指定した項目だけと照合します。自由な文章の意味は推測せず、決められない場合は不合格です。
- **基本チェック**（`publish_guard_basic_check.py`）: 日付、項目数と台本の行数、台本の尺の下限、時間制限のある項目が公開時刻にまだ有効か、読み上げ数値の照合を決定的に確認します。
- **音声照合**（`publish_guard_speech_check.py`）: 完成した MP4 の音声を faster-whisper で書き起こし、台本の表示時刻にある行と照合します。一致率が基準未満なら不合格、一定未満なら警告にします。同じ表現の再掲を取り違えないよう、時刻が離れるほど類似度を減点します。
- **公開ゲート**（`publish_guard_gate.py`）: 公開の境界を検査し、成果物のハッシュに束縛した `publish_manifest.json` を発行します。動画が入力より古くないか、解像度と尺が設定した値と一致するか、台本がマニフェストと一致するか、投稿先チャンネルが許可値と一致するか、などを見ます。許可するチャンネル ID が未設定なら不合格です。
- **重複投稿の防止**（`publish_guard_duplicate.py`）: 同じ作業ディレクトリへの二重投稿を、ファイルシステム上の記録から止めます。
- **緊急の非公開化**（`publish_guard_unpublish.py`）: 動画を削除せず private に変更し、インシデントの記録を残します。
- **バッチの排他と原子性**（`publish_guard_batch_lock.sh`、`publish_guard_artifact_atomic.sh`）: mkdir ベースのロック（flock 不要、古いロックは失効）と、作業ディレクトリの退避・復元で、失敗時に前の成果物へ戻します。

## ディレクトリ構成

```
publish_guard_claims.py            台本とマニフェストの照合（検証器）
publish_guard_basic_check.py       基本チェック
publish_guard_speech_check.py      音声照合
publish_guard_gate.py              公開ゲートと publish_manifest.json の発行
publish_guard_common.py            ゲート共通のユーティリティと設定の読み取り
publish_guard_duplicate.py / publish_guard_unpublish.py
publish_guard_batch_lock.sh / publish_guard_artifact_atomic.sh
tests/                             回帰テストと合成データ（synthetic_artifact.py）
```

## 動かし方

必要なもの: Python 3.10 以上、`requirements.txt` の依存、`ffmpeg` / `ffprobe`（無ければ動画を使うテストはスキップ）、シェルの部品とそのテストには bash（GNU 系・BSD 系どちらの `date` / `stat` でも動くようにしてあります）。

```bash
pip install -r requirements.txt
python -m pytest tests
python publish_guard_basic_check.py --work-dir <dir> --date YYYY-MM-DD
python publish_guard_speech_check.py --work-dir <dir>
python publish_guard_gate.py --work-dir <dir> --channel-yaml channel.yaml
python publish_guard_unpublish.py --latest   # 緊急の非公開化（--video-id でも指定可）
```

作業ディレクトリには `content_manifest.json`、`script.json`、`meta.json`、`log.yaml`（`final_verdict`）、`output.mp4` を置きます。形式は `publish_guard_claims.py` の冒頭と `tests/synthetic_artifact.py` を参照してください（合成データ）。

環境変数:

| 変数 | 用途 |
|---|---|
| `EXPECTED_CHANNEL_ID` | 投稿を許可するチャンネル ID（未設定なら公開ゲートが不合格） |
| `PUBLISH_GUARD_VIDEO_SIZE` | 完成動画に求める解像度 `WIDTHxHEIGHT`。**既定値なし**（未設定なら公開ゲートが不合格） |
| `PUBLISH_GUARD_VIDEO_SECONDS` | 完成動画の尺の下限と上限 `MIN-MAX`（秒）。**既定値なし**（未設定なら公開ゲートと基本チェックが不合格） |
| `PUBLISH_GUARD_MIN_ITEMS` / `PUBLISH_GUARD_MIN_LINES` | 基本チェックの項目数・台本行数の下限（既定は 1） |
| `PUBLISH_GUARD_LOCK_ROOT` / `PUBLISH_GUARD_LOCK_MAX_AGE_SECONDS` | バッチロックの置き場と、持ち主が不明なロックを失効させる秒数 |
| `PUBLISH_GUARD_ARTIFACT_RETENTION_DAYS` | 退避した旧成果物を残す日数（既定は 14） |
| `PUBLISH_GUARD_WORK_ROOT` / `YOUTUBE_TOKEN_FILE` / `PUBLISH_GUARD_INCIDENT_LEDGER` | 緊急の非公開化が使う、作業ディレクトリの置き場・OAuth トークンファイル・記録ファイル |

YouTube への操作には OAuth のトークンファイル（`token.json`）を使います。鍵や秘密情報はリポジトリに含まれません。同梱の `.gitignore` は `secrets/`、`.env`、`token.json` などを除外しますが、秘密情報はリポジトリの外に置くことを勧めます。

## 制限事項（閉包）

- **同梱していない依存**: `google-api-python-client` と `google-auth`（`publish_guard_unpublish.py` のみが使用。`requirements.txt` にはコメントで記載）。
- **合成データ**: `tests/` の fixtures と `synthetic_artifact.py` は架空のデータです。
- **同梱していないもの**: 動画の生成パイプライン本体、アップロード用のスクリプト、チャンネル設定、OAuth のトークンは含みません。ゲートは、それらが作った成果物を検査する部品です。
- **Windows での実行**: シェルの部品（ロック、原子的な入れ替え）とそのテストは bash を使います。Windows の bash（WSL など）では、ファイルシステムの違いでテストが失敗することがあります。
- ゲートは「公開してよい」ことの許可ではありません。合格は「検査した範囲で危険が見つからない」という意味で、判断できないものは通さない（本数が減る）設計です。

## ライセンス

All rights reserved. 閲覧目的で公開しています。詳細は LICENSE を参照。
