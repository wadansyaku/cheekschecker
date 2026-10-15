# Cheekschecker Implementation Plan

2026-10-15 に現行コードとテストを再点検しました。以下の最新評価・実装結果を優先し、その後に2026-04-24の計画履歴を残しています。

## 2026-10-15 本番反映と受け入れ結果

### 残課題の追加調査と実装

最新のmonitor専用API100件にはscheduled 97件とmanual 3件があり、scheduledの作成間隔は中央値298.79分、最大585.82分、96区間すべてが30分超でした（2026-09-25 20:43〜10-15 14:49 JST）。全件success、runの記録上の所要時間は中央値41秒、最大395秒です。最新runと最大間隔のrunのjob開始も作成から数秒でした。長い空白は監視コードの実行時間ではなくrun作成間隔であり、定期イベントの生成・配信段階が主な調査対象です。GitHub内部の原因までは断定しません。

- 本文取得時刻とは別に成功確認時刻を保存し、`report_monitor_health.py` で履歴上の起動間隔・現在の確認鮮度・本文の鮮度を別々に解析します。欠測・未来時刻はunknownです。
- 取得失敗記録や送信受付済みマーカーが後続の失敗でrunnerごと失われないよう、実行済みmonitorのcheckpointを公開データ検査後にcommitします。失敗判定自体は維持します。観測履歴は通知前に保存します。
- 実際のfailure jobを意図的に発火させる診断モードを用意し、通知を日本語化します。診断は通常stateを変更しません。
- [独立起動Worker](scheduler/README.md)を実装しました。新規Cloudflare設置と対象repo限定のGitHub認証は未承認・未実施であり、GitHub cronの長期間隔が解消したとは扱いません。
- スマートフォン実機・OS通知・既読・受信者の理解、GitHubへの最終push失敗やSlack応答喪失時の完全な重複防止は引き続き別の受け入れ条件です。

追加実装は [8dfed2a](https://github.com/wadansyaku/cheekschecker/commit/8dfed2aa9f2973c658dce29c20b2c666f2beb23c) でmainへ反映しました。

- [追加CI](https://github.com/wadansyaku/cheekschecker/actions/runs/38694739569): Python 225 testsとWorker 19 testsが成功、source coverage 84.14%、型・workflow・public artifact検査が成功しました。新規helperのlint/整形は成功し、リポジトリ全体の既存lint/整形指摘は残っています。
- [障害通知の実動作](https://github.com/wadansyaku/cheekschecker/actions/runs/38694941029): monitorを診断として意図的に失敗させたためrun全体のfailureは期待どおりです。failure jobのcheckoutと正式Webhook送信はsuccessで、15:51 JSTに実Slackで「診断」と明示された日本語の障害通知を受信・表示確認しました。通常監視・artifact・state commitはskippedでした。
- [独立起動の受け口](https://github.com/wadansyaku/cheekschecker/actions/runs/38695255465): Workerの判断ロジックをローカルから既存GitHub CLIへ接続して、実際のAPIデータでmainを起動しました。既存CLI認証をCloudflareへ渡してはいません。独立起動用monitor、取得・解析・公開state commit/pushはsuccess、manual artifactとsynthetic通知はskippedです。新たな通知対象の変化はありませんでした。
- [同じイベントの再配信](https://github.com/wadansyaku/cheekschecker/actions/runs/38695754859): 同じexternal_tickで再実行しsuccess。再取得ログ・通知はなく、commitは変更なし、monitor_stateのGit blobも再実行前後で一致しました。
- Cloudflare向けのdry-runビルドは成功し、既存アカウントへの認証も読み取りで確認しました。専用GitHub認証は未作成で、Workerの新設・定期イベントの発火・24時間以上の間隔検証は未実施です。

ユーザーの明示承認に基づき、最新の公開state履歴を保持してmainへpushしました。実装コミットは [43c09fe](https://github.com/wadansyaku/cheekschecker/commit/43c09fed49c653f59003824cdc6da48d8c3f8307)、手動artifact生成の追加修正は [62ba2c4](https://github.com/wadansyaku/cheekschecker/commit/62ba2c4e7e93b5e37cd0ea1be9db3367864aa0cc) です。

- [本番CI](https://github.com/wadansyaku/cheekschecker/actions/runs/38690201110): 213 tests成功、source coverage 84.16%、mypy・workflow・public artifact検査成功。既存のlint/整形指摘は今回一括修正していません。
- [正式Webhookの5状態診断](https://github.com/wadansyaku/cheekschecker/actions/runs/38690204136): mainの新コードを実行しsuccess。実Slackで15:04 JSTの正式通知アプリから5件を読み戻し、通常通知・週次のデータ不足・取得失敗・観測なし・監視復旧を確認しました。Slackデスクトップの通常幅で見出し、本文、欠測表示、略語説明、主要ボタンの緑色表示を確認しました。実予約取得・artifact upload・state commitはすべてskippedで、メンションもありません。
- [実カレンダーによる本番monitor](https://github.com/wadansyaku/cheekschecker/actions/runs/38690344158): 取得・解析、sanitized/公開state artifact生成、state commit・pushが成功。15:05 JSTの観測では新たな通知対象の変化がなく、通常通知は送信しませんでした。公開state更新は [e217808](https://github.com/wadansyaku/cheekschecker/commit/e217808) です。一時的なpush障害は発生せず、retry動作は回帰テストで検証しています。

残る優先課題はscheduled起動の遅延・欠落の原因調査です。GitHubの最終push失敗時の配送重複、failure jobの実障害による発火、スマートフォン実機・OS通知・受信者の理解や既読は今回の成功範囲に含めません。Slackのprivate URLや本文、実予約の人数・個人情報は公開文書へ保存していません。

## 2026-10-15 評価と改善計画

対象は取得・HTML解析・営業日判定・通知state・Slack/Step Summaryの表示・公開保存境界・writer/failure workflow・ローカル検証です。独立したWebアプリはなく、ユーザーの主な体験は「Slackで変化を理解し、カレンダーを開き、必要なら公開集計や実行ログを確認する」動線です。既存の公開安全な保存形式と今日・明日の通知範囲を保った改善を優先しました。初回実装時には外部サイト、実Slack、GitHub Actionsの実runを実行せず、追加依頼後の確認を後段に記録しています。

| 優先度 | 現状の問題・影響 | 改善と完了条件 | ローカル実装 |
| --- | --- | --- | --- |
| P1 | 送信前に通知済みstateが保存され、Slack失敗後に再通知できない | 受付後にstate保存。失敗時は入力stateも通知済み日時も進めず再実行できる | 実装・回帰検証済み |
| P1 | failure jobの新規runnerに専用scriptがなく、障害通知が失敗する | 同job内でcheckoutし、順序欠落をcheckerで拒否する | 実装・回帰検証済み |
| P1 | 比率39.5%などが帯域隙間から最上位へ誤分類される | monitor/summary共通の下限閾値分類と境界値テスト | 実装・回帰検証済み |
| P1 | 公開保存の任意ラベル、日付欄、曜日欄からfree textが残り得る | 既知ラベル・形式を許可。保存境界とartifact検査で拒否する | 実装・回帰検証済み |
| P2 | validatorのOR判定、営業日跨ぎ、失敗後のskipが観測を遅らせる | ETag優先、同営業日限定、失敗時再取得。解析成功までvalidatorを確定しない | 実装・回帰検証済み |
| P2 | 空ページを取得成功、欠けた人数を観測0として扱う | 空解析を取得不能、欠測/不整合人数を未取得にする。取得した0は維持 | 実装・回帰検証済み |
| P2 | 不正な明示日付がカレンダー解析全体を止める | 不正属性を無視して既存の日付推定を継続する | 実装・回帰検証済み |
| P2 | 初回通知が新規成立にも重複し、略語と鮮度不足で判断しにくい | 1日1掲載、今日/明日、確認時刻、営業日、人数の意味を明示 | 実装・payload検証済み |
| P2 | 欠測と条件未達が混同され、傾向が記号だけで伝わりにくい | 未取得/観測なし/比較データ不足を区別し、データ充足と行動ボタンを日本語化 | 実装・payload検証済み |

### UI/UXの判断と検証境界

通常通知は「タイトル → 確認時刻と営業日 → 変化と人数 → カレンダー」の順に整理しました。summaryは対象期間と概算の説明、最新観測、期間帯域、Top3、傾向とデータ充足、公開結果への導線を保ちます。欠測日を0として埋めないこと、色や矢印だけで状態を伝えないこと、重複通知で注意を消費しないことを重視しています。

`scripts/preview_notifications.py` は実装と同じpayloadから4状態を架空データだけでHTMLへ描画します。標準幅/375px、シナリオ切替、フォーカス表示を用意しました。アプリ内ブラウザーがfile URLを拒否したため、実画面・実操作の確認は未実施です。Slack本体のモバイル表示、OS通知、省略時表示、読み上げ、受信者の理解は今回の検証結果に含めません。

### 次段階の優先順位

1. **監視起動の信頼性**: main反映・CI・正式Webhook5状態・本番monitorのwriter実run確認は完了。scheduled起動の遅延・欠落を優先調査し、GitHub基盤障害を含むfailure通知の検証と、必要なら起動基盤の選択を別途進める。
2. **配送と保存の最終境界**: monitorの実行後失敗に対するcheckpointと、一時push障害の再試行は実装済み。Slack受付後のローカル保存失敗・GitHubへの最終push失敗・受付応答喪失では重複が残り得る。summaryの失敗時保存も別途検討する。現時点でexactly-onceを保証しない。
3. **MASK_LEVEL=2のsummary補完**: 既定level2ラベルは保存できるが、summary補完はlevel1帯域だけ逆変換するため欠測になる。scheduledのlevel1は維持し、別途level2の統計表現と比較可能性を決めて回帰検証する。
4. **設定・保守の整理**: 任意カスタムラベルは公開保存不可になった。必要なら設定読込時の明示的な検証を追加する。既存のlint/整形指摘と依存更新は、本変更と分けて互換性を確認しながら整備する。

### この回の検証

既存131件は変更前に成功。変更後は回帰テスト、workflow checker、public artifact checker、mypy、source-only coverageの品質ゲートを実行しました。最新件数・coverageは最終報告を参照してください。既存の公開JSONは変更していません。lint/整形検査も実行し、既存指摘を別途確認しています。

### 2026-10-15 実Slackでの追加確認

ユーザーの追加指示で、個人Slack管理システムの `#cheekschecker` へ架空データの診断を送信しました。個人Slack内の診断スレッドで、通常通知・週次のデータ不足・取得失敗・観測なしの4件を読み戻し、Slackデスクトップアプリの通常幅と幅320のスレッドで本文、見出し、日付、欠測表示、ボタンの表示と折り返しを確認しました。メンションは無効で、実予約の取得や通常監視stateの更新はありません。

4件の送信はSlackコネクター経由です。生成元のブロック構成と表示文を維持し、コネクターの入力仕様に合わせてcontextのtext型、見出しのMarkdown記法、ボタンのaction_idを診断payloadだけで調整しました。正式Webhookから今回の未公開コードを実行した結果ではありません。Slack公式ビルダーでも通常通知のブロック検証・プレビューはできましたが、ブラウザー接続が途中で切れたため送信には使用していません。

別途、公開済みmainの `monitor.yml` を `send_monitor_diagnostic=true` で実行し、正式通知アプリからの診断受信を実Slackで確認しました。[診断run](https://github.com/wadansyaku/cheekschecker/actions/runs/38687410267)はsuccessで完了し、通常monitor・artifact upload・公開stateのcommit stepはすべてskippedでした。公開済みコードのWebhook疎通検証であり、今回の変更のpush・本番反映、スマートフォン実機、OS通知、受信者の既読、ボタン遷移までは検証していません。

## 過去ログに基づく追加改善とリリース手順

2026-10-15の追加依頼でpush・本番反映・正式Webhook診断が承認されました。以下を今回の反映対象に追加します。

- Slack直近100件（2026-07-21〜10-15 JST、診断2件を含む）を読み取り、通常通知38件、週次12件、月次3件、monitor取得失敗警告45件を分類しました。通常通知のうち初回/新規の重複掲載が28件あり、週次1件で平均比率の帯域が最大比率より高い表示を確認しました。前段の重複・比率分類修正が実履歴の問題へ対応します。本文・個人情報・実予約の人数は公開資料へ転記しません。
- GitHub直近100 run（2026-09-26 00:55〜10-15 14:33 JST）はすべてsuccessでしたが、scheduled monitor95件のうち88件は通知対象の変化なし、7件だけがSlack受付ログでした。successを取得・通知・受信・既読の共通指標として扱いません。
- その95件のscheduled monitorの起動間隔は中央値302.0分、最大585.8分で、94区間のうち80区間が3時間超でした。10分cronを確認頻度の保証として説明せず、通知の確認時刻を基準にします。GitHub側の遅延・欠落の根因は未確定です。
- [8/21の障害run](https://github.com/wadansyaku/cheekschecker/actions/runs/32390977196)で、取得timeout後にstateのpushがGitHubの一時的なサーバーエラーで拒否され、失敗通知もcheckout不足で送れない二次障害を確認しました。checkout修正に加え、同一コミットのpushを一時障害に限って最大3回再試行します。競合・権限エラーは隠さず失敗とします。
- 取得失敗警告の後に復旧を知らせる通知がなく、利用者には障害が続いているのか分かりにくいため、送信済み警告の後の取得・解析成功時だけ復旧通知を一度送ります。失敗時には「最新状況は不明」「次回実行で再試行」「カレンダーで直接確認」を案内します。復旧だけの送信失敗はpendingのまま明示して通常通知のstateをGit保存へ進め、次回再試行します。
- summaryには主要なカレンダーボタンと略語の説明を追加し、集計の確認から実際の予約状況へ移れるようにします。
- `monitor-diagnostic --scenario all` で通常通知・週次のデータ不足・取得失敗・観測なし・監視復旧の5状態を、実装と同じbuilderから正式Webhookへ送れます。実予約取得・state更新・メンションは行いません。
- 本番受け入れ確認時に、手動monitorがHEAD一致で省略すると要求されたartifactが存在せずuploadで失敗する経路を確認しました。artifactを要求した実行だけは本体を再取得し、通常のscheduled skipは維持します。HEAD一致/不一致の両方でartifact生成を回帰検証します。

反映は、最新のリモート履歴を取り込み、公開データ検査・workflow検査・型検査・回帰テストを通してから行います。push後のCI、main上の5状態診断、実Slackの受信・表示をそれぞれ確認し、実行リンクと反映コミットは最終報告に記録します。ジョブ開始前の基盤障害、受付後の保存失敗に伴う重複、スマートフォン実機・OS通知・既読は別の検証境界です。

## 2026-04-24 計画履歴

以下は当時の現行contractに基づく記録です。

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
