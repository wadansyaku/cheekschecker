# cheekschecker

Cheekschecker は公開カレンダーを巡回し、女性参加が濃い営業日を Slack に通知するためのツールです。Playwright でページを取得し、BeautifulSoup で表を解析、Slack Incoming Webhook へ集計値を投稿します。公開リポジトリでの運用を想定し、保存物はすべてレンジ・マスク済みの情報に限定しています。

## 監視と通知の流れ
- JST を基準とした営業日ロールオーバーを実装しています。曜日ごとに「前日扱いの締め時刻」を持ち、深夜に追記されたセルでも論理営業日を正しく割り当てます（例: 火〜木は早朝、週末はやや遅め）。
- 通知対象は「論理営業日 *当日以降*」のセルのみです。過去セルは照合・記録は行いますが Slack には流しません。
- 平日は小規模〜中規模（例: 単独女性 3-4 程度）、金土はより厚め（例: 単独女性 5-6 程度）かつ女性比率が一定以上で `[初回]` 通知を送信します。
- 同一営業日でさらに人数が増える、あるいは女性比が高まったときは `[追加]` 通知を 1 回だけ発火します。その後はクールダウン（例: 数時間）を経てリセットされ、再度「初回→追加」の流れを繰り返せます。
- 通常通知は人数・比率をそのまま Slack に表示します（数値マスクなし）。リポジトリ内にはマスクした履歴のみ保存します。

## 週次・月次サマリー
- 週次（7 日）と月次（30 日）のサマリーを Slack に投稿します。平均・中央値・最大値、直前期間との比較、Hot 日 Top3、曜日別プロファイルなどを含み、運営の意思決定を支援します。
- 集計で得た生データは GitHub Actions の Artifact として短期保存し、リポジトリには `history_masked.json` として帯域化した値のみコミットします（例: 単女 3-4、女性比 50±% など）。

## プライバシーと法務への配慮
- `ROBOTS_ENFORCE=1` で robots.txt を確認し、対象パスが `Disallow` の場合は解析をスキップします（WARN ログのみ）。
- 保存するのは集計値のみで、個人名・ニックネーム・自由記述は保持しません。Slack 通知以外のログ／履歴はレンジ表示で統一しています。
- 手動実行時は個人名を `□` へ置換した `fetched_table_sanitized.html` を Artifact として保存します。公開 repo に生 HTML が残ることはありません。
- README には具体的なしきい値を記載せず、範囲や運用上の考え方のみ示しています。本ツールは私的な意思決定支援であり、法的助言を提供するものではありません。

## 設定（環境変数）
| 変数 | 役割 | 備考 |
| --- | --- | --- |
| `TARGET_URL` | 監視対象 URL | robots.txt で許可されているパスのみ解析します |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook | Secrets で管理してください |
| `NOTIFY_MODE` | 通知モード | `newly`（初回のみ）または `changed`（人数変化も通知） |
| `NOTIFY_FROM_TODAY` | 通知開始位置 | `1` で「今日以降」、`0` で過去も対象 |
| `COOLDOWN_MINUTES` | クールダウン時間 | 例: 180 分（週末イベント向けに調整可能） |
| `BONUS_SINGLE_DELTA` / `BONUS_RATIO_THRESHOLD` | 追加通知条件 | 例: 単独女性が基準+α、比率が 50±% 以上など |
| `ROLLOVER_HOURS_JSON` | 曜日別ロールオーバー | 例: `{ "Fri": 6, "Sat": 6 }` のように時刻を指定 |
| `MASK_LEVEL` | 保存物のマスキング強度 | `0`=開発用（非推奨）、`1`=帯域表示、`2`=抽象語 |
| `ROBOTS_ENFORCE` | robots.txt 強制 | `1` で Disallow を尊重します |
| `UA_CONTACT` | User-Agent 連絡先 | 監視の責任者を示すメールなど |

そのほか `FEMALE_MIN` や `MIN_TOTAL` で成立条件の下限を調整できます。数値を直接 repo に残さず、Secrets や環境変数で運用してください。

## ログと観測性
- 取得した frame URL と table outerHTML の SHA-256 を INFO ログで記録します。
- `days_coverage`（件数／最小日／最大日）、先頭 10 件と末尾 5 件、最新で人数が入った日の要約を DEBUG ログへ出力します。
- HEAD リクエストで ETag/Last-Modified が変わらない場合は取得をスキップします。
- `DEBUG_LOG=1` で詳細ログを出します。公開運用時は Secrets やマスキング設定を併用してください。

## ローカル実行
1. Python 3.11 以上を用意し、仮想環境を作成します。
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
3. サマリーのみ実行する場合は `python watch_cheeks.py summary --days 7` のように呼び出します。`--raw-output` を付けると生集計をローカルファイルに書き出します（Artifact 用）。

## テスト
Pytest でユニットテストを実行できます。ロールオーバー、解析、通知ステージ、サマリーのマスキングなどを網羅しています。
```bash
pytest -q
```

## GitHub Actions
- `.github/workflows/monitor.yml` で 10 分おきに監視を実施します。手動実行時のみサニタイズ済み HTML を Artifact 化します。
- `.github/workflows/summary_weekly.yml` と `.github/workflows/summary_monthly.yml` で週次／月次サマリーを投稿し、`history_masked.json` を更新してコミットします。生データは短期保持の Artifact にのみ残ります。
- lint やテストを追加する場合は `.github/workflows/ci.yml` に統合してください。

## 免責
本ツールは非公式・私的用途の支援を目的としたものであり、施設や参加者の権利・義務を代替するものではありません。利用にあたっては対象サイトの利用規約・robots.txt・個人情報保護法令を遵守し、Slack や保存物に個人が特定される情報を残さない運用を徹底してください。
