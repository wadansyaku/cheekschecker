# Current Architecture

この文書は 2026-04-24 時点の Cheekschecker の現行運用前提をまとめたものです。

## Public-safe contract
- 公開リポジトリに永続化するのは `monitor_state.json`、`history_masked.json`、`summary_masked.json` のみです。
- `monitor_state.json` は public-safe operational state です。`generated_at`、`etag`、`last_modified`、`last_fetched_at`、`warning_throttle`、`days` だけを保持します。
- `monitor_state.json.days[YYYY-MM-DD]` に保存するのは `met`、`stage`、`last_notified_at` のみです。人数、比率、性別ごとの raw counts は保存しません。
- `monitor_state.json.warning_throttle` に保存するのは warning 種別ごとの `last_seen_at`、`last_warned_at`、`consecutive_runs`、`suppressed_runs`、`last_category` のみです。例外メッセージ、URL detail、HTTP body、stack trace は保存しません。
- `history_masked.json` と `summary_masked.json` は公開安全な帯域化データだけを保持します。
- `history_masked.json` / `summary_masked.json` の保存境界では public-safe schema sanitizer を通し、raw-like な数値値、未知の日付キー、余計な top-level debug key は保存しません。

## Scheduled monitor
- GitHub Actions の scheduled `monitor` は `NOTIFY_MODE=newly` を正式仕様とします。
- `changed` 通知は公開 workflow の継続状態だけでは正確に保証できないため、public-safe scheduled mode の保証対象から外します。
- `monitor` は `monitor_state.json` と `history_masked.json` を更新し、同一 writer transaction で commit / push します。
- upstream の一時的な接続失敗は scheduled run では warning skip として扱い、job failure にはしません。manual dispatch は fail-fast のまま維持します。
- ETag / Last-Modified が同一でも `last_fetched_at` が古い、または未記録の場合は再取得します。`HEAD_SKIP_MAX_AGE_MINUTES=0` で HEAD skip を無効化できます。
- scheduled monitor の fetch failure Slack warning は `WARNING_THROTTLE_MINUTES` で抑制します。初回と throttle 経過後だけ Slack に出し、抑制中も GitHub Step Summary と `monitor_state.json.warning_throttle` で観測可能にします。

## Public-safe summary
- `watch_cheeks.py summary` は raw dataset を採取しつつ、public-safe な履歴更新を行う収集フェーズです。
- `summarize.py` は `current run` の raw dataset と `history_masked.json` を入力として、public-safe approximation の summary を生成します。
- Slack summary payload は `src/public_summary.py` で組み立てます。`watch_cheeks.py` 側の旧 exact summary payload 経路は持ちません。
- 長期の exact reconstruction は行いません。過去日の評価は masked bands を元にした近似です。
- `summary_masked.json` は `weekly` / `monthly` キーを維持しつつ、`mode: "public-safe"` と `coverage` metadata を持ちます。

## Domain and parser modules
- `src/domain.py` は `DailyEntry`、JST、曜日定義の共有型を持ちます。
- `src/calendar_parser.py` は calendar HTML の抽出、日付推定、参加者集計、基準評価を担当します。`watch_cheeks.py` は互換 wrapper と orchestration に寄せます。
- `src/notification_state.py` は monitor 通知の stage transition rules を担当します。
- masking config は `src/masking.py` で読み込み、危険な custom band / threshold を warning で観測します。

## Workflow discipline
- `monitor.yml`、`summary_weekly.yml`、`summary_monthly.yml` の writer workflow は同一 `concurrency` group を使います。
- writer job の手順は `checkout -> git pull --rebase --autostash -> 実行 -> git add -> commit if changed -> git push` に統一します。
- `git push || true` は使いません。push failure は job failure として扱い、failure notifier を確実に発火させます。
- `contents: write` は writer job のみに付与し、failure 通知 job には付与しません。
- manual dispatch の短期診断 artifact は `sanitized-table`、`masked-history`、`monitor-state`、`weekly-summary-raw`、`monthly-summary-raw` のみです。いずれも `workflow_dispatch` 限定、`retention-days: 3` です。
- summary raw dataset artifact は exact 値を含むため、scheduled run ではアップロードしません。manual dispatch の短期診断 artifact としてのみ保持します。
- workflow 契約は `scripts/ci/check_workflows.py` で検査し、retry timeout、writer concurrency、push failure 無視禁止、manual artifact の条件・保持期間、`ALLOW_FETCH_FAILURE` の schedule/manual 分岐、writer commit 対象、`TZ=Asia/Tokyo`、`ROBOTS_ENFORCE=1`、monitor の `WARNING_THROTTLE_MINUTES=180` を固定します。
- Slack 送信と GitHub Step Summary 追記は `src/notifications.py` の共通 helper を使います。monitor は fallback 再送なし、summary CLI は fallback text 再送ありに分けます。
