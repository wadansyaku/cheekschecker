# プルリクエスト作成ガイド

6つのPRを以下の順序で作成してください。

---

## PR #1: 型チェック（mypy）の追加

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/add-type-checking-A1-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Add mypy type checking to CI/CD pipeline
```

**本文**:
```markdown
## 概要
CI/CDパイプラインに型チェック（mypy）を統合し、型安全性を向上させます。

## 変更内容

### 新規ファイル
- `mypy.ini` - mypy設定ファイル（Python 3.11対応、実用的な設定）
- `requirements-dev.txt` - 開発用依存関係（mypy + 型スタブ）
- `.github/workflows/test.yml` - 型チェック & テスト実行ワークフロー

### 主な機能
- **型チェックジョブ**: mypyによる静的型チェック
- **テストジョブ**: pytest実行 + カバレッジ収集
- **キャッシュ最適化**: pipとPlaywrightのキャッシュで高速化
- **タイムアウト設定**: 型チェック5分、テスト10分

### mypy設定の特徴
- Python 3.11対応
- 実用的なバランスの取れた設定
  - `ignore_missing_imports=True` - サードパーティライブラリの型スタブ不足に対応
  - `strict_optional=True` - Optional型の厳密チェック
  - `warn_return_any=True` - Any型の返り値に警告

## 期待される効果
- ✅ 型エラーの早期発見
- ✅ リファクタリングの安全性向上
- ✅ IDEサポートの向上
- ✅ コードレビューの効率化

## テスト
- CI環境で自動実行されます
- 既存テストとの互換性を維持

## マージ推奨順序
**1/6 - 最優先** - 他のPRの品質保証に役立つため最初にマージ推奨

関連: Phase 1 改善タスク（Task A1）
```

---

## PR #2: ワークフロー失敗アラート

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/add-workflow-alerts-A2-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Add workflow failure alerts to Slack
```

**本文**:
```markdown
## 概要
GitHub Actionsワークフローの失敗時に、Slackへ自動通知を送信する機能を追加します。

## 変更内容

### 修正ファイル
- `.github/workflows/monitor.yml` - 失敗通知ジョブ追加
- `.github/workflows/summary_weekly.yml` - 失敗通知ジョブ追加
- `.github/workflows/summary_monthly.yml` - 失敗通知ジョブ追加

### 追加機能
各ワークフローに `notify-failure` ジョブを追加：
- **トリガー条件**: `if: failure()` - メインジョブ失敗時のみ実行
- **依存関係**: `needs:` でメインジョブに依存
- **通知内容**:
  - ❌ ワークフロー失敗ヘッダー
  - ワークフロー名（Monitor Calendar / Weekly Summary / Monthly Summary）
  - ブランチ名
  - トリガータイプ（schedule / workflow_dispatch）
  - ログへの直接リンクボタン

### 通知フォーマット
- Slack Block Kit形式
- 視認性の高いヘッダーとボタン
- 環境変数チェック（`SLACK_WEBHOOK_URL` 未設定時はスキップ）

## 期待される効果
- ✅ ワークフロー失敗の即時検知
- ✅ 障害対応の迅速化
- ✅ 運用の可視性向上
- ✅ 手動確認の削減

## テスト
- YAML構文検証済み
- 各ワークフローで動作確認可能

## マージ推奨順序
**2/6** - ワークフローファイルのみの変更のため、コンフリクトリスク低

関連: Phase 1 改善タスク（Task A2）
```

---

## PR #3: 構造化ログ（structlog）

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/add-structured-logging-A3-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Add structured logging with structlog
```

**本文**:
```markdown
## 概要
structlogを導入し、構造化ログによるオブザーバビリティを向上させます。

## 変更内容

### 新規ファイル
- `src/__init__.py` - パッケージ初期化
- `src/logging_config.py` - 一元化されたログ設定
  - `configure_logging(debug: bool)` - ログ設定関数
  - `get_logger(name: str)` - ロガー取得関数

### 修正ファイル
- `requirements.txt` - structlog==24.1.0 追加
- `watch_cheeks.py` - structlog初期化 + 構造化ログ追加
- `summarize.py` - structlog初期化

### 構造化ログの実装箇所
**parse_day_entries():**
- `parsing_started` - HTML サイズ
- `parsing_completed` - エントリ数、基準達成数

### ログ出力形式
- **TTY環境（開発）**: 人間が読みやすいコンソール形式
- **非TTY環境（CI/CD）**: JSON形式（機械可読）

### 主な機能
- タイムスタンプ（ISO 8601、UTC）
- ログレベル自動付与
- 例外情報の自動フォーマット
- 既存の `DEBUG_LOG` 環境変数との後方互換性

## 期待される効果
- ✅ トラブルシューティングの効率化
- ✅ ログ分析の容易化
- ✅ パフォーマンス測定の基盤
- ✅ CI/CDでの機械可読ログ

## テスト結果
- 14/16 テストパス
- 2件の失敗は既存の問題（summarize module）

## 今後の拡張
以下の関数にも構造化ログを追加予定：
- `fetch_calendar_html()` - fetch duration tracking
- `process_notifications()` - notification counts
- `notify_slack()` - Slack API tracking

## マージ推奨順序
**3/6** - 軽微な変更のため、早期マージ推奨

関連: Phase 1 改善タスク（Task A3）
```

---

## PR #4: parse_day_entries() リファクタリング

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/refactor-parse-entries-B1-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Refactor parse_day_entries() into smaller functions
```

**本文**:
```markdown
## 概要
`parse_day_entries()` 関数（96行）を6つの小さな関数に分割し、保守性とテスト容易性を向上させます。

## 変更内容

### リファクタリング前後の比較
**元の関数:**
- 96行の単一関数
- 4層のネストループ
- 複数の責務が混在

**リファクタリング後:**
- メイン関数: 約52行（docstring含む）
- 5つのヘルパー関数: 約133行

### 追加されたヘルパー関数

1. **`_extract_calendar_table(html: str) -> Optional[Tag]`**
   - HTMLからカレンダーテーブルを抽出
   - BeautifulSoupを使用

2. **`_extract_day_number(parts: List[str]) -> Optional[int]`**
   - セルテキストから日番号を抽出
   - 正規表現で1-31の日を検出

3. **`_extract_content_lines(parts: List[str]) -> List[str]`**
   - 日付と曜日ラベルを除外
   - イベント説明のみを返す

4. **`_count_participants(content_lines: List[str], exclude_keywords: Sequence[str]) -> Dict[str, int]`**
   - 男性/女性/単女の参加者をカウント
   - 除外キーワードを考慮

5. **`_build_daily_entry(cell_date: date, day_of_month: int, counts: Dict[str, int], settings: Settings) -> DailyEntry`**
   - パースデータからDailyEntry構築
   - 基準評価ロジックを含む

### 修正ファイル
- `watch_cheeks.py`
  - `from bs4.element import Tag` インポート追加
  - 5つのヘルパー関数追加
  - `parse_day_entries()` を簡潔に書き直し

## 期待される効果
- ✅ **単一責任の原則**: 各関数が1つの明確な責務
- ✅ **テスト容易性**: 小さな関数は個別テストが容易
- ✅ **可読性向上**: 短い関数で処理の流れが明確
- ✅ **循環的複雑度削減**: 96行 → 52行（約46%削減）

## テスト結果
```
8 passed, 1 warning in 0.65s
```
- 全ての既存テストがパス
- ヘルパー関数は統合テストでカバー

## マージ推奨順序
**4/6** - watch_cheeks.py の前半部分のため、B2より前にマージ推奨

関連: Phase 1 改善タスク（Task B1）
```

---

## PR #5: process_notifications() リファクタリング

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/refactor-notifications-B2-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Refactor process_notifications() into smaller functions
```

**本文**:
```markdown
## 概要
`process_notifications()` 関数（97行）を4つの小さな関数に分割し、保守性とテスト容易性を向上させます。

## 変更内容

### リファクタリング前後の比較
**元の関数:**
- 97行の単一関数（行925-1022）
- 通知処理のすべてのロジックが混在

**リファクタリング後:**
- メイン関数: 約73行（docstring含む）
- 3つのヘルパー関数: 約109行

### 追加されたヘルパー関数

1. **`_build_notification_sections(...) -> List[Tuple[str, List[str]]]`**
   - 通知セクションをカテゴリ別に整理
   - 基準達成通知、新規成立日、人数更新の3種類

2. **`_process_single_entry(...) -> Tuple[Optional[str], str, int, Dict[str, Any]]`**
   - 単一エントリの状態遷移処理
   - ステージ評価とステート更新

3. **`_categorize_notifications(...) -> None`**
   - エントリを適切な通知リストに振り分け
   - newly_met、changed_counts、stage_notifications

4. **`process_notifications()` (リファクタリング後)**
   - メインのオーケストレーション
   - クリーンで理解しやすい構造

### 修正ファイル
- `watch_cheeks.py` - 3つのヘルパー関数追加 + メイン関数書き直し
- `tests/test_notify_helpers.py` - 7つの新規ユニットテスト

## 期待される効果
- ✅ **単一責任の原則**: 各関数が1つの明確な責務
- ✅ **テスト容易性**: ヘルパー関数を個別にテスト可能
- ✅ **関心の分離**: 通知ロジックが局所化
- ✅ **変更容易性**: 通知の変更が簡単に

## テスト結果
```
9 passed in 0.74s
- 2 existing tests (test_notify.py)
- 7 new unit tests (test_notify_helpers.py)
```

### 新規テスト内容
- `test_build_notification_sections_empty()` - 空の通知セクション
- `test_build_notification_sections_with_newly_met()` - 新規成立通知
- `test_categorize_notifications_adds_to_newly_met()` - newly_metリストへの追加
- `test_categorize_notifications_adds_to_changed_when_counts_differ()` - 人数変更検知
- その他3件

## マージ推奨順序
**5/6** - watch_cheeks.py の後半部分のため、B1の後にマージ推奨

関連: Phase 1 改善タスク（Task B2）
```

---

## PR #6: build_slack_payload() リファクタリング

**作成リンク**: https://github.com/wadansyaku/cheekschecker/compare/main...claude/refactor-slack-payload-B3-011CUXPbUkSEniWTYX9H3Xam

**タイトル**:
```
Refactor build_slack_payload() into smaller functions
```

**本文**:
```markdown
## 概要
`build_slack_payload()` 関数（162行）を6つの小さな関数に分割し、保守性とテスト容易性を向上させます。

## 変更内容

### リファクタリング前後の比較
**元の関数:**
- 162行の単一関数（行389-551）
- 9個以上のネストしたヘルパー関数
- 深い変数定義

**リファクタリング後:**
- メイン関数: 約80行（docstring含む）
- 6つのヘルパー関数: 約153行

### 追加されたヘルパー関数

1. **`_format_date_range(start: date, end: date) -> str`**
   - 日付範囲のフォーマット
   - 例: "01/15(月)〜01/21(日)"

2. **`_format_stats_field(...) -> List[str]`**
   - 統計フィールドのフォーマット
   - 平均、中央値、最大値

3. **`_format_latest_field(latest: DailyRecord) -> List[str]`**
   - 最新エントリフィールドのフォーマット
   - 日付、参加者数、比率

4. **`_format_top_days_section(top_days: Sequence[DailyRecord]) -> List[str]`**
   - Top3 好条件日のフォーマット
   - 箇条書きリスト

5. **`_format_weekday_profile_line(weekday_profile: Dict[...]) -> str`**
   - 曜日別プロファイルのフォーマット
   - 曜日ごとの統計

6. **`_build_context_elements(context: SummaryContext) -> List[Dict[str, Any]]`**
   - Slackコンテキスト要素の構築
   - 期間、営業日数、トレンド

7. **`build_slack_payload()` (リファクタリング後)**
   - メインのオーケストレーション
   - クリーンで構造化された実装

### 修正ファイル
- `summarize.py` - 6つのヘルパー関数追加 + メイン関数書き直し
- `tests/test_summary_helpers.py` - 5つの新規ユニットテスト

## 期待される効果
- ✅ **単一責任の原則**: 各関数が1つのフォーマット処理
- ✅ **テスト容易性**: 個別のフォーマット関数をテスト可能
- ✅ **ネスト削減**: 深いネストを平坦化
- ✅ **変更容易性**: フォーマット変更が局所化

## テスト結果
新規テスト5件追加：
- `test_format_date_range()` - 日付範囲フォーマット
- `test_format_latest_field()` - 最新エントリフォーマット
- `test_format_top_days_section_empty()` - 空のTop3
- `test_format_top_days_section_with_records()` - Top3データあり
- `test_format_stats_field()` - 統計フィールド

## マージ推奨順序
**6/6 - 最後** - summarize.py のみの変更のため、他のPRと独立

関連: Phase 1 改善タスク（Task B3）
```

---

## マージ推奨順序まとめ

1. **PR #1** (型チェック) - 最優先
2. **PR #2** (アラート) - 独立
3. **PR #3** (ログ) - 独立
4. **PR #4** (parse関数) - watch_cheeks.py 前半
5. **PR #5** (notify関数) - watch_cheeks.py 後半
6. **PR #6** (slack関数) - summarize.py

---

## 作成手順

1. 各リンクをクリック
2. タイトルと本文をコピー&ペースト
3. "Create pull request" をクリック
4. 順番にマージを進める

全6つのPRで Phase 1 の改善が完了します！
