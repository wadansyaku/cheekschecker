# cheekschecker

Cheekschecker は公開カレンダーを巡回し、女性参加が濃い営業日を Slack に通知する運用基盤です。Playwright でカレンダーを取得し、BeautifulSoup で表を解析し、Slack Incoming Webhook へ Block Kit 形式で情報を投稿します。公開リポジトリ運用を想定し、保存物はレンジ化されたマスク済みデータのみです。

## 監視と通常通知（monitor）
- JST の営業日ロールオーバーを実装し、曜日ごとの締め時刻までに追加されたセルも「論理営業日」へ正しく再割り当てします。
- 通知対象は「論理営業日で今日以降」のセルのみです。過去セルは記録されますが Slack 通知は抑止されます（営業日ロールオーバー前提）。
- 通常通知は人数・女性比率をそのまま Slack に送信します。リポジトリ内に残すのは `history_masked.json` のマスク済み履歴だけです。
- `monitor` ワークフローでは Playwright を用いた取得、マスク履歴の更新、Block Kit での投稿を行います。robots.txt が `Disallow` の場合は WARN ログを出して解析をスキップし、Slack には投稿しません。
- 取得前に HEAD リクエストで ETag / Last-Modified を確認し、未更新であればフェッチをスキップします。結果は GitHub Step Summary にも反映され、Slack と整合します。
- Slack へ送った内容（または「該当なし」）は常に `GITHUB_STEP_SUMMARY` に同じ構成で追記されます。過去履歴を GitHub 上で確認しやすくしています。

## 週次／月次サマリー（summary）
- `.github/workflows/summary_weekly.yml`（毎週月曜）と `.github/workflows/summary_monthly.yml`（毎月 1 日）が `watch_cheeks.py summary` で最新データを取得し、`summarize.py` で集計します。
- `summarize.py` は `history_masked.json` と当該期間の生データ（Artifact 化）を用いて、平均／中央値／最大値、Hot day Top3、直前期間との差分、曜日別プロファイルを算出します。
- Slack には Block Kit（header → context → fields（今日/近日）→ divider → Top3 → actions）を基本として投稿し、空データ時も同じ構成で「No data for this period / 集計対象なし」を表示します。エラー時のみプレーンテキストへフォールバックします。
- サマリーで生成した情報は GitHub Step Summary にも同じブロック構成で記録され、レポートの監査・再確認が容易です。
- リポジトリにコミットされるのは `summary_masked.json` と `history_masked.json` のみで、双方とも人数や比率を帯域表示に丸めた情報です（個人名・生値は保存しません）。
- `--raw-output` を指定したり `--no-notify` を付けて実行すると Slack 投稿は行わず、生データ収集だけを行えます。
- summary ワークフローでは `git pull --rebase --autostash` → 変更確認 → コミット → `git push || true` の安全 push を行い、monitor と競合しても落ちない設計にしています。

## GitHub Actions の構成
- `.github/workflows/monitor.yml`：10 分おき／手動で実行。手動実行時はサニタイズ済み HTML を Artifact 化します。
- `.github/workflows/summary_weekly.yml`：週次サマリーを作成し、Slack へ投稿、`history_masked.json` と `summary_masked.json` を更新します。手動実行時は「Cheekschecker: Webhook OK」で疎通確認後に本投稿を行います。
- `.github/workflows/summary_monthly.yml`：月次サマリーの集計・投稿・マスク済みファイルの更新を行います。週次と同様に Safe Push（pull --rebase → diff --cached --quiet → push || true）を実装しています。
- すべてのワークフローで `TZ=Asia/Tokyo`、`ROBOTS_ENFORCE=1` を設定し、robots.txt を尊重します。

## GitHub Actions の安定化
- monitor / weekly / monthly すべてに `concurrency` と `timeout-minutes`（10 / 15 / 20 分）を設定し、重複実行やハングアップを抑止しています。
- `actions/cache@v4` で pip / Playwright のキャッシュを共有し、安定かつ高速なデプロイを実現しています。
- 生成物（サニタイズ済み HTML や期間生データ JSON）は Artifact として最長 3 日間だけ保持し、生データの露出を最小化しています。
- すべてのジョブで `actions/checkout@v4`（`fetch-depth: 0`）と Safe Push 手順を採用し、公開ブランチの履歴をクリーンに保ちます。

## 環境変数・Secrets（抜粋）
| 変数 | 用途 | 備考 |
| --- | --- | --- |
| `TARGET_URL` | 監視対象ページ | robots.txt で許可されているパスのみ解析します |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook | Secrets で管理。空の場合は WARN ログを出して処理を継続します |
| `NOTIFY_MODE` / `NOTIFY_FROM_TODAY` | 通常通知の挙動 | `NOTIFY_FROM_TODAY=1` で今日以降のみ通知 |
| `COOLDOWN_MINUTES` / `BONUS_SINGLE_DELTA` / `BONUS_RATIO_THRESHOLD` | 通常通知の追加条件 | 値は Secrets/環境変数で管理し、README では具体値を公開しません |
| `ROLLOVER_HOURS_JSON` | 曜日別ロールオーバー設定 | 営業日ロールオーバーの締め時刻（JST） |
| `MASK_LEVEL` | マスキング強度 | `history_masked.json` / `summary_masked.json` の帯域粒度 |
| `ROBOTS_ENFORCE` | robots.txt 準拠 | `1` で Disallow を尊重し、取得をスキップ（Slack 通知は無し） |
| `UA_CONTACT` | User-Agent 連絡先 | 監視主体の連絡先メールなど |

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
3. サマリーのみ実行する場合は `python watch_cheeks.py summary --days 7 --raw-output weekly.json` → `python summarize.py --period weekly --raw-data weekly.json` のように呼び出します。`--raw-output` や `--no-notify` を付けると Slack には投稿されず、ローカルに生データだけが保存されます。

## プライバシーと法務
- `ROBOTS_ENFORCE=1` のときは `/robots.txt` を取得し、対象パスが `Disallow` の場合は WARN ログを出して解析・Slack 投稿を行いません。
- 保存物は `history_masked.json` と `summary_masked.json` のみで、人数・比率を帯域化した値だけを保持します。個人名や自由記述は保存せず、生データは短期保持の Artifact のみに残します。
- 本ツールは非公式・私的用途の支援を目的とし、対象サイトの利用規約や関連法令を代替するものではありません。疑義がある場合は速やかに連絡先（`UA_CONTACT`）へ報告してください。

## トラブルシュート
- **Slack に投稿されない**：`SLACK_WEBHOOK_URL` が未設定か、Block Kit 投稿で失敗した可能性があります。ログの WARN/ERROR を確認し、必要なら手動で `python summarize.py --ping-only` を実行してください。
- **空コミット／push 失敗**：summary ワークフローでは `git pull --rebase --autostash` → `git diff --cached --quiet` → `git push || true` を採用しています。権限不足の場合は `contents: write` を付与してください。
- **Playwright の依存不足**：`python -m playwright install --with-deps chromium` を再実行してください。CI では毎回実行しています。
- **空データ期間**：`summarize.py` が「No data for this period / 集計対象なし」を Slack へ投稿し、ジョブは成功扱いになります。
- **robots.txt で拒否された**：WARN ログが出て処理がスキップされます。対象 URL を見直すか、運用責任者に確認してください。

## テスト
`pytest` でユニットテストを実行できます。通知ロジックやサマリー集計のマスキングをカバーしています。

```bash
pytest -q
```
