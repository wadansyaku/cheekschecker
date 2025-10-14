# cheekschecker

月間カレンダー（http://cheeks.nagoya/yoyaku.shtml）から参加者を取得し、女性比率がしきい値を満たした日に Slack 通知を送る監視スクリプトです。Playwright でページを取得し、BeautifulSoup で HTML を解析します。営業日ロールオーバーに対応し、深夜帯の前日セル追記にも即時追従します。

## 営業日ロールオーバーと通知ポリシー
- JST 現在時刻と曜日別のロールオーバー時刻（既定値は金曜/土曜=6時、火曜〜木曜=5時など）から「論理上の営業日」を導出します。
- 論理営業日より未来のセルは通知対象外、`IGNORE_OLDER_THAN` で指定した日数より古いセルも無視します（既定1日で、論理営業日より前はスキップ）。
- state.json は `YYYY-MM-DD` キーで営業日ごとの状態を保持します。旧形式（`"1"` などの日付キー）は自動で昇格します。
- 初回通知後に `単女 >= 基準+2` または `女性比 >= 0.50` で同一営業日につき1度だけ追加通知します。追加後は `COOLDOWN_MINUTES`（既定3時間）内は沈黙し、経過後に再度「初回→追加」の流れを繰り返します。

## 判定ロジック概要
- 参加者行ごとに `♂` / `♀` の記号を数え、さらに `×2`/`X3`/`＊4`/`*5` や `2人`/`３名`/`４組` などの数詞を読み取って女性人数を補正します。全角数字・任意空白・括弧内の表記にも対応します。
- `♀` が含まれ `♂` が含まれない行は、数詞で示された人数と `♀` 記号数の最大値を女性人数として採用します。
- 「単女」は `female==1` かつ `male==0` かつ 数詞が 1 以下の行と定義します。
- 日毎に `male`, `female`, `single_female` を集計します。除外キーワードに `スタッフ` が含まれていても除外しません（スタッフは常にカウント対象）。
- 金曜・土曜は「単女≧5 かつ 女性比≧max(0.40, FEMALE_RATIO_MIN)」、日曜〜木曜は「単女≧3 かつ 女性比≧max(0.40, FEMALE_RATIO_MIN)」が成立条件です。`FEMALE_MIN` を指定している場合はその値も下限として併用します。
- `MIN_TOTAL` を設定すると合計人数がしきい値未満のセルは不成立になります。
- `INCLUDE_DOW` を指定した場合は対象曜日のセルのみ判定します（Sun..Sat）。

## Slack 設定
Slack には [Incoming Webhooks](https://api.slack.com/messaging/webhooks) の URL を利用します。GitHub Actions では `SLACK_WEBHOOK_URL` を Secrets に登録してください。通知は Block Kit を利用し、必要に応じてテキストフォールバックを送信します。`PING_CHANNEL=1` の場合、テキストと Block 双方の先頭に `<!channel>` を付与します。

## 環境変数
| 変数名 | 既定値 | 説明 |
| --- | --- | --- |
| `TARGET_URL` | `http://cheeks.nagoya/yoyaku.shtml` | 監視対象の URL |
| `SLACK_WEBHOOK_URL` | なし | Slack Incoming Webhook URL |
| `FEMALE_MIN` | `3` | 成立に必要な女性人数（曜日基準の単女しきい値とも併用） |
| `FEMALE_RATIO_MIN` | `0.3` | 成立に必要な女性比率の下限（実際は `max(0.40, FEMALE_RATIO_MIN)`） |
| `MIN_TOTAL` | 未設定 | 合計人数の下限（設定すると有効） |
| `EXCLUDE_KEYWORDS` | 未設定 | カンマ区切りの除外キーワード。`スタッフ` は常にカウント対象です。 |
| `INCLUDE_DOW` | 未設定 | 判定対象の曜日（Sun..Sat、カンマ区切り） |
| `NOTIFY_MODE` | `newly` | `newly`（新規成立のみ通知）/`changed`（人数変化も通知） |
| `DEBUG_LOG` | 未設定 | `1` で DEBUG ログを出力 |
| `DEBUG_SUMMARY` | 未設定 | `1` で常にデバッグサマリーを Slack に送信 |
| `PING_CHANNEL` | `1` | `1` で `<!channel>` メンションを付与 |
| `COOLDOWN_MINUTES` | `180` | 追加通知後のクールダウン時間（分） |
| `BONUS_SINGLE_DELTA` | `2` | 追加通知に必要な単女増分（基準+この値） |
| `BONUS_RATIO_THRESHOLD` | `0.50` | 追加通知を許可する女性比率 |
| `IGNORE_OLDER_THAN` | `1` | 論理営業日からこの日数以上前のセルは通知対象外 |
| `ROLLOVER_HOURS_JSON` | `{"Sun":2,"Mon":0,"Tue":5,"Wed":5,"Thu":5,"Fri":6,"Sat":6}` | 曜日別ロールオーバー時刻（JSON 文字列、単位:時） |

## 通知ルール
- 条件を初めて満たした営業日は `[初回]` 通知を送ります。
- 単女が基準より `BONUS_SINGLE_DELTA` 多い、または女性比率が `BONUS_RATIO_THRESHOLD` を超えた場合に `[追加]` 通知を一度だけ送信します。
- 追加通知後は `COOLDOWN_MINUTES` のあいだ沈黙し、経過後にステージを初期化して再度「初回→追加」の流れを許容します。
- DEBUG ログにはロールオーバー後の営業日、ステージ遷移、最新非ゼロ日のサマリー、テーブル取得元フレーム URL・HTML の SHA-256 などを出力します。

## 観測性と手動実行
- HTML 取得時に `table[border='2']` をフレーム横断で探索し、取得元フレーム URL と outerHTML の SHA-256 をログします。見つからなければページ全体の HTML を利用します。
- 解析直後に `days_coverage` や先頭10件・末尾5件のサマリー、当月で合計人数>0の最新日を DEBUG で表示します。
- GitHub Actions の `workflow_dispatch` 手動実行では、取得 HTML を `fetched_table.html` としてワークスペースに保存し、後続ステップで Artifact として確認できます。

## ローカル実行手順
1. Python 3.11 以上を用意します。
2. 仮想環境を作成し、依存をインストールします。
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python -m playwright install --with-deps chromium
   ```
3. 必要な環境変数を設定してスクリプトを実行します。
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
   python watch_cheeks.py
   ```

## テスト
pytest でユニットテストを実行できます。
```bash
pytest -q
```

## GitHub Actions ワークフロー
`.github/workflows/monitor.yml` で監視ジョブを実行します。スケジュールは UTC で動作し、手動実行時のみ HTML を Artifact 化します。Slack に通知が届かない場合は以下を確認してください。
- `SLACK_WEBHOOK_URL` が正しく設定されているか
- `NOTIFY_MODE` や `IGNORE_OLDER_THAN`/`ROLLOVER_HOURS_JSON` の設定で通知が抑制されていないか
- GitHub Actions のログでエラーや DEBUG 情報を確認し、table SHA や days_coverage に異常がないか

## よくある質問
- **通知が来ません**: 環境変数と Slack Webhook の設定を確認し、`DEBUG_LOG=1` で解析結果と営業日ロールオーバーのログを取得してください。
- **YAML エラーが出る**: `monitor.yml` 内でタブではなくスペースを使用してください。
- **HTML の中身を確認したい**: 手動実行 (`workflow_dispatch`) で保存される `fetched_table.html` を Artifact からダウンロードし、table SHA-256 と突き合わせてください。

