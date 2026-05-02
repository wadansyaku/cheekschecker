# cheekschecker

Cheekschecker は公開カレンダーを巡回し、女性参加が濃い営業日を Slack に通知する運用基盤です。Playwright でカレンダーを取得し、BeautifulSoup で表を解析し、Slack Incoming Webhook へ Block Kit 形式で情報を投稿します。公開リポジトリ運用を前提に、永続化するのは公開安全な状態と帯域化済みデータのみです。

現行の運用前提は [CURRENT_ARCHITECTURE.md](/Users/Yodai/CheeksChecker/CURRENT_ARCHITECTURE.md) を基準にしてください。大規模実装の現行計画は [IMPLEMENTATION_PLAN.md](/Users/Yodai/CheeksChecker/IMPLEMENTATION_PLAN.md) を参照します。`SYSTEM_IMPROVEMENT_ANALYSIS.md` と `PARALLEL_TASK_PLAN.md` は履歴資料です。

## 監視と通常通知（monitor）
- JST の営業日ロールオーバーを実装し、曜日ごとの締め時刻までに追加されたセルも「論理営業日」へ正しく再割り当てします。
- 通知対象は「論理営業日で今日以降」のセルのみです。過去セルは記録されますが Slack 通知は抑止されます（営業日ロールオーバー前提）。
- scheduled `monitor` の正式モードは `NOTIFY_MODE=newly` です。公開 workflow では `changed` 通知の継続保証は行いません。
- `monitor` ワークフローでは Playwright を用いた取得、`monitor_state.json` の更新、`history_masked.json` の更新、Block Kit での投稿を行います。robots.txt が `Disallow` の場合は WARN ログを出して解析をスキップし、Slack には投稿しません。
- scheduled workflow では upstream の一時的な接続失敗を warning skip として扱います。外部サイト timeout だけでは job failure にせず、step summary / Slack warning で観測します。Slack warning は `WARNING_THROTTLE_MINUTES` で抑制し、公開状態には時刻・回数・粗いカテゴリだけを保存します。
- 取得前に HEAD リクエストで ETag / Last-Modified を確認し、未更新であればフェッチをスキップします。ただし `last_fetched_at` が古い、または未記録の場合は強制的に再取得します。結果は GitHub Step Summary にも反映され、Slack と整合します。
- monitor の Slack 通知は実通知用の詳細を保ちますが、`GITHUB_STEP_SUMMARY` には raw counts ではなく public-safe band 表現を追記します。過去履歴を GitHub 上で確認しやすくしつつ、公開面へ exact 値を残しません。
- 公開リポジトリに残す monitor 状態は `monitor_state.json` に限定し、`days[date]` には `met`、`stage`、`last_notified_at` だけを保存します。`last_fetched_at` は HEAD skip の鮮度判定用、`warning_throttle` は warning 抑制用の公開安全な operational metadata です。raw counts、exact ratio、例外メッセージは保存しません。

## 週次／月次サマリー（summary）
- `.github/workflows/summary_weekly.yml`（毎週月曜）と `.github/workflows/summary_monthly.yml`（毎月 1 日）が `watch_cheeks.py summary` で最新データを取得し、`summarize.py` で集計します。生データ収集時は `--no-notify` フラグで Slack 送信を抑止し、集計済みの通知は `summarize.py` 側だけから行います。
- `summarize.py` は `history_masked.json` と当該 run の raw dataset を使って、public-safe approximation の summary を生成します。長期の exact reconstruction は行いません。
- Slack には Block Kit（header → context → fields（今日/近日）→ Top3 → actions）を基本として投稿します。表現は band / trend / rank ベースで、raw average を装う exact wording は避けます。エラー時のみプレーンテキストへフォールバックします。
- サマリーで生成した情報は GitHub Step Summary にも同じブロック構成で記録され、レポートの監査・再確認が容易です。
- `summary_masked.json` は `weekly` / `monthly` キーを維持しつつ、`mode: "public-safe"`、`status`、`coverage` metadata を含みます。取得元が利用できない場合は stale な成功結果を残さず `status: "source-unavailable"` に更新します。
- リポジトリにコミットされるのは `monitor_state.json`、`summary_masked.json`、`history_masked.json` で、いずれも公開安全な情報だけを保持します（個人名・生値・raw counts は保存しません）。
- `--raw-output` を指定したり `--no-notify` を付けて実行すると Slack 投稿は行わず、生データ収集だけを行えます。summary raw dataset は exact 値を含むため、GitHub Actions では manual dispatch の短期診断 artifact としてのみアップロードします。
- summary ワークフローは monitor と同じ writer transaction で動き、push failure は失敗として扱います。

## GitHub Actions の構成
- `.github/workflows/monitor.yml`：10 分おき／手動で実行。`monitor_state.json` と `history_masked.json` を更新し、writer job で commit / push します。手動実行時はサニタイズ済み HTML も Artifact 化します。
- `monitor.yml` の手動実行で `send_monitor_diagnostic=true` を指定すると、実予約データではない synthetic payload を使って monitor の Slack 通知分岐を強制送信します。この診断は `monitor_state.json` と `history_masked.json` を更新せず、Webhook 未設定や送信失敗は job failure として扱います。
- `.github/workflows/summary_weekly.yml`：週次サマリーを作成し、Slack へ投稿、`history_masked.json` と `summary_masked.json` を更新します。手動実行時は「Cheekschecker: Webhook OK」で疎通確認後に本投稿を行います。
- `.github/workflows/summary_monthly.yml`：月次サマリーを作成し、Slack へ投稿、`history_masked.json` と `summary_masked.json` を更新します。週次と同じ writer transaction で commit / push します。
- すべてのワークフローで `TZ=Asia/Tokyo`、`ROBOTS_ENFORCE=1` を設定し、robots.txt を尊重します。
- JavaScript action は `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` で Node.js 24 実行へ opt-in し、GitHub Actions runner の Node.js 20 removal に先行対応します。

### Manual dispatch 診断 artifact
| workflow | artifact | source | retention | 公開安全性 |
| --- | --- | --- | --- | --- |
| `monitor.yml` | `sanitized-table` | `fetched_table_sanitized.html` | 3 days | サニタイズ済み HTML。元 HTML 派生のため短期診断限定 |
| `monitor.yml` | `masked-history` | `history_masked.json` | 3 days | public-safe snapshot |
| `monitor.yml` | `monitor-state` | `monitor_state.json` | 3 days | public-safe operational state |
| `summary_weekly.yml` | `weekly-summary-raw` | `weekly_summary_raw.json` | 3 days | exact 値を含むため manual dispatch 限定 |
| `summary_monthly.yml` | `monthly-summary-raw` | `monthly_summary_raw.json` | 3 days | exact 値を含むため manual dispatch 限定 |

## GitHub Actions の安定化
- monitor / weekly / monthly の writer workflow は同じ `concurrency` group を使い、公開状態の同時更新を防ぎます。
- monitor / weekly / monthly すべてに `timeout-minutes`（10 / 15 / 20 分）を設定し、ハングアップを抑止しています。
- `actions/cache@v5` で pip / Playwright のキャッシュを共有し、安定かつ高速なデプロイを実現しています。
- 生成物（サニタイズ済み HTML や期間生データ JSON）は Artifact として最長 3 日間だけ保持し、生データの露出を最小化しています。
- writer job だけに `contents: write` を付与し、failure 通知 job には write 権限を持たせません。

## 環境変数・Secrets（抜粋）
| 変数 | 用途 | 備考 |
| --- | --- | --- |
| `TARGET_URL` | 監視対象ページ | robots.txt で許可されているパスのみ解析します |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook | Secrets で管理。空の場合は WARN ログを出して処理を継続します |
| `NOTIFY_MODE` / `NOTIFY_FROM_TODAY` | 通常通知の挙動 | `NOTIFY_FROM_TODAY=1` で今日以降のみ通知 |
| `COOLDOWN_MINUTES` / `BONUS_SINGLE_DELTA` / `BONUS_RATIO_THRESHOLD` | 通常通知の追加条件 | 値は Secrets/環境変数で管理し、README では具体値を公開しません |
| `ROLLOVER_HOURS_JSON` | 曜日別ロールオーバー設定 | 営業日ロールオーバーの締め時刻（JST） |
| `MASK_LEVEL` | マスキング強度 | `history_masked.json` / `summary_masked.json` の帯域粒度 |
| `MASK_CONFIG_PATH` | マスキング設定 JSON | band 定義を差し替える場合のみ使用します |
| `ROBOTS_ENFORCE` | robots.txt 準拠 | `1` で Disallow を尊重します。monitor は取得と Slack 通知をスキップし、summary は source-unavailable marker を残します |
| `ALLOW_FETCH_FAILURE` | 外部取得失敗の graceful degrade | scheduled workflow では `1`。upstream timeout を warning skip に落とし、manual dispatch では `0` のまま fail させます |
| `UA_CONTACT` | User-Agent 連絡先 | 監視主体の連絡先メールなど |
| `HEAD_SKIP_MAX_AGE_MINUTES` | HEAD 未更新時の最大 skip 鮮度 | 既定は 180。`0` で HEAD skip を無効化し、毎回取得します |
| `WARNING_THROTTLE_MINUTES` | scheduled monitor の Slack warning 抑制 | 既定は 180。`0` で抑制を無効化します |

## セットアップ
1. Python 3.11 以上と Playwright を準備します。
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install --with-deps chromium
   ```
2. 必要な環境変数を設定して監視を実行します。
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
   python watch_cheeks.py monitor
   ```
3. monitor の Slack 通知分岐だけを診断する場合は `python watch_cheeks.py monitor-diagnostic` を実行します。synthetic payload のため実予約 state は変更せず、Slack 送信失敗はエラーとして扱います。
4. サマリーのみ実行する場合は `python watch_cheeks.py summary --days 7 --raw-output weekly.json --no-notify` → `python summarize.py --period weekly --raw-data weekly.json` のように呼び出します。`--no-notify` を付けることで生データ取得時の Slack 投稿を抑止し、集計完了後の一度だけ通知されます。`summary_masked.json` に丸めた結果が残り、Slack には Block Kit が送信されます。
   - **履歴ファイルの競合対策**：`monitor_state.json`、`history_masked.json`、`summary_masked.json` は共通 writer transaction で更新します。手動実行前に `git pull --rebase --autostash` を走らせて最新化してください。

### ローカル開発の最短手順
毎回手動でセットアップし直さなくてよいように、ローカル再開用スクリプトを用意しています。

```bash
scripts/bootstrap_local.sh
source .venv/bin/activate
scripts/check_local.sh
```

- `scripts/bootstrap_local.sh`: `.venv` 作成、依存インストール、Chromium 導入までをまとめて実行します。
- `scripts/check_local.sh`: workflow 検証、`mypy`、`pytest --cov` をまとめて実行します。
- Playwright ブラウザの再インストールを省きたい場合は `SKIP_PLAYWRIGHT_INSTALL=1 scripts/bootstrap_local.sh` を使えます。

## プライバシーと法務
- `ROBOTS_ENFORCE=1` のときは `/robots.txt` を取得し、対象パスが `Disallow` の場合は WARN ログを出して解析を行いません。summary では raw dataset に source-unavailable marker を書き、後続の public-safe summary に反映します。
- 公開保存物は `monitor_state.json`、`history_masked.json`、`summary_masked.json` のみで、いずれも公開安全な状態だけを保持します。個人名、free text、raw counts、生データは残しません。
- 本ツールは非公式・私的用途の支援を目的とし、対象サイトの利用規約や関連法令を代替するものではありません。疑義がある場合は速やかに連絡先（`UA_CONTACT`）へ報告してください。

## トラブルシュート
- **Slack に投稿されない**：`SLACK_WEBHOOK_URL` が未設定か、Block Kit 投稿で失敗した可能性があります。ログの WARN/ERROR を確認し、summary の疎通は `python summarize.py --ping-only`、monitor 通知分岐の疎通は `python watch_cheeks.py monitor-diagnostic` で確認してください。
- **push 失敗**：writer workflow は push failure を失敗として扱います。権限不足の場合は writer job に `contents: write` が付いているか確認してください。
- **scheduled monitor / summary が timeout した**：`cheeks.nagoya` 側の一時障害なら warning skip になります。Slack warning と step summary を確認し、manual dispatch で再取得したい場合は `ALLOW_FETCH_FAILURE=0` のまま再実行してください。
- **scheduled monitor の warning が何度も出る**：`WARNING_THROTTLE_MINUTES` 内の連続 fetch failure は Slack 投稿を抑制し、`monitor_state.json.warning_throttle.monitor_fetch_failure` に公開安全な回数だけを残します。
- **Playwright の依存不足**：`python -m playwright install --with-deps chromium` を再実行してください。CI では毎回実行しています。
- **空データ期間**：`summarize.py` が「No data for this period / 集計対象なし」を Slack へ投稿し、ジョブは成功扱いになります。
- **robots.txt で拒否された**：monitor は WARN ログを出して通知をスキップします。summary は `source-unavailable` として記録されます。対象 URL を見直すか、運用責任者に確認してください。

## テスト
`pytest` でユニットテストを実行できます。通知ロジックやサマリー集計のマスキングをカバーしています。

```bash
pytest -q
```
