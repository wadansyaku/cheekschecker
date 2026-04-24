# Cheekschecker Implementation Plan

この文書は 2026-04-24 時点の現行 contract に基づく実装計画です。履歴資料の `SYSTEM_IMPROVEMENT_ANALYSIS.md` / `PARALLEL_TASK_PLAN.md` よりも `README.md` / `CURRENT_ARCHITECTURE.md` とこの文書を優先します。

## 現状判断

Cheekschecker の中核価値は「公開安全な状態だけを残しながら、外部サイトの変化を Slack と GitHub Step Summary で観測する」ことです。したがって大規模実装では、機能追加より先に以下を守ります。

- raw counts / exact ratio / 個人名 / free text を公開永続化しない。
- scheduled workflow は外部サイト障害で過剰に失敗しないが、診断不能にもならない。
- monitor / summary / workflow の public-safe 契約を CI で固定する。
- `watch_cheeks.py` の責務分割は、挙動を固定するテストを増やしてから進める。

## 批判的なリスク

1. summary raw dataset は exact 値を含むため、scheduled artifact として残すと「永続化していないが公開面に近い」抜け道になります。
2. `history_masked.json` / `summary_masked.json` の保存境界が緩いと、将来のリファクタで raw-like な値が混入しても止まりません。
3. `load_settings()` の debug 出力は Slack webhook を含み得るため、DEBUG_LOG 有効時の事故面がありました。
4. robots.txt 判定は独自実装のままだと `Allow` / specific user-agent group の扱いを誤りやすいです。
5. ETag / Last-Modified が壊れている upstream では、HEAD skip だけに依存すると長時間 stale になる可能性があります。
6. summary 通知経路と dead helper が残っており、大きな機能追加時に public-safe 方針とズレる圧力になります。

## Phase 1: Public-Safe Boundary Hardening

Status: implemented in the current batch.

- `src/public_state.py` に public-safe sanitizer を追加し、masked history / summary store の保存境界で許可形だけ残す。
- summary raw dataset artifact を `workflow_dispatch` 限定にする。
- workflow checker に writer concurrency / push failure / raw artifact 契約を追加する。
- `monitor_state.json` に `last_fetched_at` を追加し、HEAD skip の最大鮮度を導入する。
- `load_settings()` の debug log から Slack webhook と masking config details を秘匿する。
- robots.txt 判定を longest-match の Allow/Disallow と user-agent group に対応させる。

Acceptance gate:

```bash
.venv/bin/python scripts/ci/check_workflows.py
.venv/bin/python -m mypy watch_cheeks.py summarize.py
.venv/bin/python -m pytest tests -q
```

## Phase 2: Summary Path Consolidation

Status: implemented in the current batch.

- `watch_cheeks.py` に残る legacy summary helper を削除または deprecated に寄せる。
- `_state_entry_to_daily_entry()` / `_supplement_entries_from_state()` の raw counts 前提コードを削除し、補完は `history_masked.json` / `src.public_summary` 側へ一本化する。
- monitor と summary の Slack send / fallback send / missing webhook behavior を `src/notifications.py` に共通化する。
- `append_step_summary()` を共通 helper へ移し、Step Summary の表現差分をテストで固定する。

Acceptance gate:

- `watch_cheeks.py summary --no-notify --raw-output ...` は raw collection のみを担当する。
- Slack summary payload は `src.public_summary` だけで組み立てる。
- monitor / summary の Slack fallback policy が同じ helper のテストで固定される。monitor は既存どおり fallback 再送なし、summary CLI は fallback 再送ありです。

## Phase 3: Parser And Domain Isolation

Status: implemented in the current batch.

- 月跨ぎ推定、明示日付属性、曜日ラベル、参加者 dedupe の境界テストを増やす。
- parser / criteria / notification state machine を小さなモジュールへ分離する。
- public-safe masking config の validation を強め、危険な custom config を warning または failure にする。

Implemented notes:

- `src/domain.py` に `DailyEntry`、JST、曜日定義を分離しました。
- `src/calendar_parser.py` に HTML parser、日付推定、参加者集計、基準評価を移しました。`watch_cheeks.py` の `parse_day_entries()` は互換 wrapper として残します。
- `src/notification_state.py` に stage transition rules を分離しました。`watch_cheeks.evaluate_stage_transition` import 互換は維持します。
- 数字入り参加者名は identity として扱い、`×2` / `3人` のような count hint とは分けます。
- masking config は malformed entry の fallback に加え、空 label、overlap/unsorted band、ratio 範囲外 threshold を warning で検知します。

## Phase 4: Operations And Release Discipline

Status: implemented in the current batch.

- manual dispatch 時の診断 artifact 一覧を README に固定する。
- scheduled monitor の Slack warning spam を抑える `warning_throttle` state を `monitor_state.json` に追加する。
- workflow checker で manual diagnostic artifact の名前・source・`workflow_dispatch` 条件・`retention-days: 3` を固定する。
- workflow checker で `ALLOW_FETCH_FAILURE` の schedule/manual 分岐、writer commit 対象、`TZ=Asia/Tokyo`、`ROBOTS_ENFORCE=1`、`WARNING_THROTTLE_MINUTES=180`、failure notifier の read-only 権限を固定する。
- `src/` module は CI / `scripts/check_local.sh` の mypy 対象に含めています。

Implemented notes:

- `WARNING_THROTTLE_MINUTES` の既定値は 180 分です。`0` で Slack warning 抑制を無効化できます。
- `warning_throttle` は `last_seen_at`、`last_warned_at`、`consecutive_runs`、`suppressed_runs`、`last_category` のみ保存します。raw exception、HTTP body、stack trace は保存しません。
- GitHub Actions の実 run 確認は、現在の未コミット差分を push した後に実施します。ローカルでは `scripts/ci/check_workflows.py` が scheduled raw artifact 禁止と manual artifact 契約を検査します。
