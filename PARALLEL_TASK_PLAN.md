# 並列実行タスク振り分け計画

## 概要

Phase 1の改善タスクを、依存関係を考慮して並列実行可能なグループに分割します。各タスクは独立したブランチで作業し、個別にPRを作成します。

---

## タスクグループA: インフラ・設定系（並列実行可能）

これらのタスクは互いに独立しており、同時に作業できます。

### Task A1: 型チェック（mypy）のCI/CD統合

**ブランチ名**: `claude/add-type-checking-A1`

**作業内容**:
1. `mypy.ini` 設定ファイルの作成
2. `requirements-dev.txt` の作成（mypy, types-* パッケージ）
3. `.github/workflows/test.yml` の作成（型チェックジョブ）
4. 既存の型エラーの修正（もしあれば）

**成果物**:
- `mypy.ini`
- `requirements-dev.txt`
- `.github/workflows/test.yml`

**推定時間**: 2-3時間

**依存関係**: なし

---

### Task A2: ワークフロー失敗アラート

**ブランチ名**: `claude/add-workflow-alerts-A2`

**作業内容**:
1. `monitor.yml` に失敗通知ジョブを追加
2. `summary_weekly.yml` に失敗通知ジョブを追加
3. `summary_monthly.yml` に失敗通知ジョブを追加
4. アラートメッセージのフォーマット統一

**成果物**:
- 修正された3つのワークフローファイル

**推定時間**: 1-2時間

**依存関係**: なし

---

### Task A3: 構造化ログ（structlog）のセットアップ

**ブランチ名**: `claude/add-structured-logging-A3`

**作業内容**:
1. `requirements.txt` に structlog を追加
2. `src/logging_config.py` の作成（ログ設定の一元化）
3. 既存のロガー初期化コードを置き換え
4. 主要な5-10箇所で構造化ログのサンプル実装

**成果物**:
- 更新された `requirements.txt`
- `src/logging_config.py`
- `watch_cheeks.py`, `summarize.py` の一部修正

**推定時間**: 2-3時間

**依存関係**: なし

---

## タスクグループB: 関数リファクタリング系（並列実行可能）

これらのタスクは異なる関数を対象とするため、コンフリクトなく並列作業できます。

### Task B1: parse_day_entries() のリファクタリング

**ブランチ名**: `claude/refactor-parse-entries-B1`

**作業内容**:
1. 以下の関数に分割:
   - `_extract_calendar_table()`
   - `_extract_table_rows()`
   - `_parse_single_row()`
   - `_parse_cell()`
   - `_extract_day_number()`
   - `_count_participants()`
2. 各関数に型ヒントとdocstringを追加
3. 既存テストが全てパスすることを確認
4. 新しい関数のユニットテストを追加

**成果物**:
- リファクタリングされた `watch_cheeks.py`（parse_day_entries 関連部分）
- 追加のユニットテスト（`tests/test_parse_refactored.py`）

**推定時間**: 4-5時間

**依存関係**: なし

---

### Task B2: process_notifications() のリファクタリング

**ブランチ名**: `claude/refactor-notifications-B2`

**作業内容**:
1. 以下の関数に分割:
   - `_build_notification_sections()`
   - `_create_slack_blocks()`
   - `_create_step_summary()`
   - `_handle_stage_notifications()`
2. 各関数に型ヒントとdocstringを追加
3. 既存テストが全てパスすることを確認
4. 新しい関数のユニットテストを追加

**成果物**:
- リファクタリングされた `watch_cheeks.py`（process_notifications 関連部分）
- 追加のユニットテスト（`tests/test_notify_refactored.py`）

**推定時間**: 4-5時間

**依存関係**: なし

---

### Task B3: build_slack_payload() のリファクタリング

**ブランチ名**: `claude/refactor-slack-payload-B3`

**作業内容**:
1. 以下の関数に分割:
   - `_format_date_range()`
   - `_format_latest_field()`
   - `_format_recent_stats()`
   - `_format_top_days()`
   - `_format_weekday_profile()`
   - `_build_context_elements()`
2. 各関数に型ヒントとdocstringを追加
3. 既存テストが全てパスすることを確認
4. 新しい関数のユニットテストを追加

**成果物**:
- リファクタリングされた `summarize.py`（build_slack_payload 関連部分）
- 追加のユニットテスト（`tests/test_summary_refactored.py`）

**推定時間**: 5-6時間

**依存関係**: なし

---

## タスク実行戦略

### ステップ1: グループA並列実行（推定3時間）

```
Task A1 (型チェック)     ━━━━━━━━━━━━━┓
                                          ┃
Task A2 (アラート)       ━━━━━━━┓       ┣━━ A完了
                                ┃       ┃
Task A3 (構造化ログ)     ━━━━━━━━━━━┛   ┛
```

3つのタスクを同時に開始し、個別にPR作成。

### ステップ2: グループB並列実行（推定5-6時間）

```
Task B1 (parse)          ━━━━━━━━━━━━━━━━━┓
                                              ┃
Task B2 (notify)         ━━━━━━━━━━━━━━━━━╋━━ B完了
                                              ┃
Task B3 (slack)          ━━━━━━━━━━━━━━━━━━┛
```

3つのリファクタリングタスクを同時に開始し、個別にPR作成。

### ステップ3: 統合とテスト（推定1時間）

全てのPRがマージされた後、統合テストを実行し、相互作用の問題がないことを確認。

---

## ブランチ戦略

```
main
 ├─ claude/add-type-checking-A1
 ├─ claude/add-workflow-alerts-A2
 ├─ claude/add-structured-logging-A3
 ├─ claude/refactor-parse-entries-B1
 ├─ claude/refactor-notifications-B2
 └─ claude/refactor-slack-payload-B3
```

各ブランチは `main` から派生し、独立してマージ可能。

---

## マージ順序の推奨

コンフリクトを最小化するため、以下の順序でのマージを推奨:

1. **A1 (型チェック)** - 他のタスクのコード品質チェックに役立つ
2. **A2 (アラート)** - ワークフローファイルのみの変更
3. **A3 (構造化ログ)** - 軽微な変更
4. **B1 (parse)** - watch_cheeks.py の前半部分
5. **B2 (notify)** - watch_cheeks.py の後半部分
6. **B3 (slack)** - summarize.py

---

## リスク管理

### コンフリクトのリスク

**低**: タスクA1, A2, A3は異なるファイルを編集
**中**: タスクB1とB2は同じファイル（watch_cheeks.py）を編集
**緩和策**: B1は前半（401-497行）、B2は後半（925-1022行）を対象とするため、コンフリクトは最小限

### 品質のリスク

**緩和策**:
- 各タスクで既存テストが全てパスすることを確認
- Task A1の型チェックを早めにマージし、B1-B3で型エラーを早期発見

---

## 並列実行の開始方法

以下のコマンドで6つのタスクを並列起動します:

```bash
# グループA（同時実行）
task_a1: CI/CDに型チェックを追加
task_a2: ワークフロー失敗アラートを追加
task_a3: 構造化ログをセットアップ

# グループB（同時実行）
task_b1: parse_day_entries()をリファクタリング
task_b2: process_notifications()をリファクタリング
task_b3: build_slack_payload()をリファクタリング
```

---

## 期待される成果物

**PRの数**: 6個（各タスク1個）

**総コード変更量**:
- 追加: 約800-1000行（新しい関数、テスト、設定）
- 削除: 約300-400行（リファクタリングによる簡素化）
- 修正: 約200-300行

**テストカバレッジ向上**: 70-80% → 85-90%

**総所要時間**: 約8-10時間（並列実行により、逐次実行の20-25時間から大幅短縮）

---

## 次のステップ

1. この計画を確認し、承認をお願いします
2. 承認後、6つのタスクを並列起動します
3. 各タスク完了後、個別にPRを作成します
4. 全PRマージ後、統合テストを実行します

---

**作成日**: 2025-10-27
