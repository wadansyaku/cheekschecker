# cheekschecker

月間カレンダー（http://cheeks.nagoya/yoyaku.shtml）から参加者を取得し、女性比率がしきい値を満たした日に Slack 通知を送る監視スクリプトです。Playwright でページを取得し、BeautifulSoup で HTML を解析します。

## 判定ロジック概要
- 参加者文字列中の `♂` / `♀` の出現回数で男女数をカウントします。
- 女性人数が `FEMALE_MIN` 以上、かつ女性比率（female / total）が `FEMALE_RATIO_MIN` 以上のとき成立とみなします。
- `MIN_TOTAL` を設定すると合計人数がしきい値未満の行は不成立になります。
- `EXCLUDE_KEYWORDS` に含まれる語を含む行は参加者計算から除外します。
- `INCLUDE_DOW` を指定した場合は対象曜日のセルのみ判定します（Sun..Sat）。
- 曜日はテーブル内の列位置（0=Sun〜6=Sat）で判断します。

## Slack 設定
Slack には [Incoming Webhooks](https://api.slack.com/messaging/webhooks) の URL を利用します。GitHub Actions では `SLACK_WEBHOOK_URL` をリポジトリ/環境の Secrets に登録してください。

通知は Block Kit のセクション＋箇条書き＋URLボタンで送信します。Block Kit 送信に失敗した場合は従来のテキスト形式に自動フォールバックします。

## 環境変数
| 変数名 | 既定値 | 説明 |
| --- | --- | --- |
| `TARGET_URL` | `http://cheeks.nagoya/yoyaku.shtml` | 監視対象のURL |
| `SLACK_WEBHOOK_URL` | なし | Slack Incoming Webhook URL |
| `FEMALE_MIN` | `3` | 成立に必要な女性人数 |
| `FEMALE_RATIO_MIN` | `0.3` | 成立に必要な女性比率 |
| `MIN_TOTAL` | 未設定 | 合計人数の下限（設定すると有効） |
| `EXCLUDE_KEYWORDS` | 未設定 | カンマ区切りの除外キーワード（例：`スタッフ,T-TIME,POLE`） |
| `INCLUDE_DOW` | 未設定 | 判定対象の曜日（カンマ区切り、Sun..Sat） |
| `NOTIFY_MODE` | `newly` | `newly`（新規成立のみ通知）/`changed`（人数変化と新規成立を通知） |
| `DEBUG_LOG` | 未設定 | `1` で DEBUG ログを出力 |
| `DEBUG_SUMMARY` | 未設定 | `1` でサマリーを常に Slack に送信 |

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
`.github/workflows/monitor.yml` で監視ジョブを実行します。YAML はタブ禁止、スケジュールは UTC で動作します。Slack に通知が届かない場合は以下を確認してください。
- `SLACK_WEBHOOK_URL` が正しく設定されているか
- `NOTIFY_MODE` や `INCLUDE_DOW` の設定で通知が抑制されていないか
- GitHub Actions のログでエラーが出ていないか

## よくある質問
- **通知が来ません**: 環境変数の設定と GitHub Actions の Secrets を確認してください。`DEBUG_LOG=1` や `DEBUG_SUMMARY=1` を付与すると解析状況を Slack で確認できます。
- **YAML エラーが出る**: `monitor.yml` 内でタブを使わずスペースを使用してください。
- **実行が重複するのが心配**: ワークフローの `concurrency` で同時実行を防げます（本リポジトリでは同名ジョブの同時実行を抑止しています）。

