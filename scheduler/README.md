# 独立した監視起動

Cloudflareの定期起動から、既存GitHub Actionsの通常monitorを呼ぶ小さなWorkerです。カレンダー取得・Slack通知・公開state保存はGitHub側に残します。**まだCloudflareには設置していません。**

## 動作

- UTCの毎時2,12,22,32,42,52分に確認します。既存GitHub cronも予備として残します。
- queued/in_progress/pending/waiting/requestedのrunがあれば追加起動しません。20分を超えて停滞したrunはエラーとして記録します。
- 公開stateの成功確認時刻が8分以内なら省略します。取得失敗がpendingなら再試行を優先します。
- `external_trigger=true` と元の定期イベント時刻でmainのmonitorを起動します。manual artifactやsynthetic通知は作りません。
- 同じ定期イベントの重複配信は、GitHub側のwriter直列化と公開時刻マーカーで省略します。曖昧なPOST失敗を直ちに再送せず、次の定期実行で稼働中runを再確認します。
- HTTP入口は作りません。ログは粗い判定・エラー種別だけです。Slack Webhookや予約データはWorkerへ渡しません。

`last_checked_at` は成功したHEAD確認または本文取得・解析の時刻、`last_fetched_at` は本文取得・解析の時刻です。診断通知はどちらも更新しません。時刻不明を0分として扱いません。

## 検証と設置

Node.js 22以上で、追加ライブラリなしに回帰テストを実行できます。

```bash
node --test scheduler/worker.test.mjs
WRANGLER_SEND_METRICS=false npx --yes wrangler@4.151.0 deploy --dry-run --config scheduler/wrangler.jsonc
```

設置にはCloudflareの対象アカウントと、新しい専用GitHub認証の承認が必要です。fine-grained tokenの対象は `wadansyaku/cheekschecker` のみ、権限は **Actions: Read and write / Contents: Read-only** とします。既存の広いCLI認証を転用しません。tokenはチャットや設定ファイルに貼らず、`GITHUB_ACTIONS_TOKEN` というWorker Secretとして登録します。有効期限と交換担当を決めます。

承認・認証確認後、`wrangler secret put GITHUB_ACTIONS_TOKEN --config scheduler/wrangler.jsonc` で登録し、同じ固定バージョンのWranglerでdeployします。config・Secretが揃うまでは定期実行を有効化しません。

設置後は24時間以上、Workerの定期イベント、dispatch受付、GitHubの開始・完了、`last_checked_at` の更新間隔を別々に比較します。Cronの伝播には時間がかかるため、dry-runや手動dispatchの成功を定期起動の保証とはしません。GitHubの実行基盤そのものが止まった場合はWorkerだけでは復旧できません。

取り消す場合はWorkerのcronを空にして再deployします。既存のGitHub cronと手動monitorは継続できます。

根拠: [GitHub scheduleの遅延・欠落](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)、[dispatchの権限](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)、[Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)。
