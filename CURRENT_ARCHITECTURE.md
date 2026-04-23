# Current Architecture

この文書は 2026-04-21 時点の Cheekschecker の現行運用前提をまとめたものです。

## Public-safe contract
- 公開リポジトリに永続化するのは `monitor_state.json`、`history_masked.json`、`summary_masked.json` のみです。
- `monitor_state.json` は monitor の継続状態専用で、`generated_at`、`etag`、`last_modified`、`days` だけを保持します。
- `monitor_state.json.days[YYYY-MM-DD]` に保存するのは `met`、`stage`、`last_notified_at` のみです。人数、比率、性別ごとの raw counts は保存しません。
- `history_masked.json` と `summary_masked.json` は公開安全な帯域化データだけを保持します。

## Scheduled monitor
- GitHub Actions の scheduled `monitor` は `NOTIFY_MODE=newly` を正式仕様とします。
- `changed` 通知は公開 workflow の継続状態だけでは正確に保証できないため、public-safe scheduled mode の保証対象から外します。
- `monitor` は `monitor_state.json` と `history_masked.json` を更新し、同一 writer transaction で commit / push します。
- upstream の一時的な接続失敗は scheduled run では warning skip として扱い、job failure にはしません。manual dispatch は fail-fast のまま維持します。

## Public-safe summary
- `watch_cheeks.py summary` は raw dataset を採取しつつ、public-safe な履歴更新を行う収集フェーズです。
- `summarize.py` は `current run` の raw dataset と `history_masked.json` を入力として、public-safe approximation の summary を生成します。
- 長期の exact reconstruction は行いません。過去日の評価は masked bands を元にした近似です。
- `summary_masked.json` は `weekly` / `monthly` キーを維持しつつ、`mode: "public-safe"` と `coverage` metadata を持ちます。

## Workflow discipline
- `monitor.yml`、`summary_weekly.yml`、`summary_monthly.yml` の writer workflow は同一 `concurrency` group を使います。
- writer job の手順は `checkout -> git pull --rebase --autostash -> 実行 -> git add -> commit if changed -> git push` に統一します。
- `git push || true` は使いません。push failure は job failure として扱い、failure notifier を確実に発火させます。
- `contents: write` は writer job のみに付与し、failure 通知 job には付与しません。
