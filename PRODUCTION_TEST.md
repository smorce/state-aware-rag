# 本番テスト手順

State-Aware RAG の本番構成（HelixDB + ruri GPU + Bosun XS + llama-server）での検証手順とベンチマーク記録です。

## 本番構成

| 項目 | 本番既定 |
| --- | --- |
| backend | `helix` |
| 埋め込み | `ruri` (`cl-nagoya/ruri-v3-310m`) |
| GPU | `RURI_DEVICE=cuda`（必須。CPU フォールバックなし） |
| Bosun | `xs` (`NativeBosunXSScorer`) |
| LLM | `server` (`JsonLlamaPlannerAndWriter` → llama-server) |
| タイムアウト | `LLAMA_SERVER_TIMEOUT_SECONDS=180`（既定値。これ未満は非推奨） |
| 実行ログ | `logs/rag_events.jsonl` + `logs/rag_session_<id>.xlsx` |

## 前提

```bash
# Bosun XS native 依存
uv sync --extra bosun-native

# Helix TypeScript SDK（初回のみ）
cd vendor/helix-db/sdks/typescript && npm install && npm run build && cd -

# llama-server（例: ポート 1067）
curl -s http://127.0.0.1:1067/health

# HelixDB（ruri 768 次元用にクリーンなインスタンスを起動）
.tools/helix init --path helix_project --no-skills local
cd helix_project && ../.tools/helix start --port 6969 dev && cd ..
```

**注意**: 過去に `HashedEmbedder`（128 次元）で ingest した HelixDB へ ruri（768 次元）を書き込むと次のエラーになります。

```text
Invalid vector dimension: expected 128, got 768
```

ベクトル次元を変える場合は `helix prune --all --yes` でローカル状態を消してから再起動してください。

## 本番スモークテスト（sample.md）

```bash
export PYTHONUTF8=1
export UV_LINK_MODE=copy
export RURI_DEVICE=cuda
export LLAMA_SERVER_TIMEOUT_SECONDS=180

uv run state-aware-rag \
  --backend helix --db helix_mirror.sqlite3 \
  ingest docs/sample.md

uv run state-aware-rag \
  --backend helix --db helix_mirror.sqlite3 \
  ask "State-Aware RAG が最終回答に使うのは何ですか？" \
  --llm server --bosun xs --max-rounds 2
```

成功時は `logs/rag_session_*.xlsx` に各ルートの成否が記録されます。Bosun 棄却は `score.candidate_rejected.relevance_below_threshold` 行で **スコアと閾値が確定** します。

## SciFact 本番テスト（データセット）

ユーザーから指示があった場合は基本的にこのデータセットでテストする。

データ配置: `data/beir/scifact/`（`data/README.md` 参照）

```bash
export PYTHONUTF8=1 RURI_DEVICE=cuda LLAMA_SERVER_TIMEOUT_SECONDS=180

# 評価用スクリプト例（50 文書サブセット + 10 クエリ）
# もっと大量にテストしても良い
uv run python scripts/run_production_scifact_eval.py \
  --helix-url http://localhost:6969 \
  --db /tmp/prod_scifact_helix.sqlite3 \
  --max-docs 50 \
  --max-queries 10
```

## 実行ログの見方

| route | 意味 |
| --- | --- |
| `score.candidate_rejected.relevance_below_threshold` | relevance が閾値未満（棄却確定） |
| `score.candidate_rejected.memory_value_below_threshold` | memory_value が閾値未満 |
| `round.no_accepted_evidence` | ラウンド内で全候補棄却 |
| `answer.final_no_evidence` | 最終的に Evidence ゼロ |
| `answer.final_dememoization_failed` | Evidence はあるが note 化失敗 |

ログ無効化: `RAG_RUN_LOG=0`

## 言語別 Bosun 閾値

Bosun XS は英語科学文と日本語でスコア分布が異なります。質問文の Unicode スクリプト比率から言語を推定し、`RagConfig.scoring_thresholds()` で閾値を切り替えます。

| 言語 | relevance | memory_value |
| --- | ---: | ---: |
| 英語 (`en`) | 0.70 | 0.60 |
| 日本語 (`ja`) | 0.35 | 0.55 |
| 混合 (`mixed`) | 0.50 | 0.55 |
| その他 (`other`) | 0.55 | 0.55 |

環境変数での上書きは `RagConfig` フィールドをコードまたは CLI 拡張で調整してください。

## ベンチマーク（開発用 HashedEmbedder 基準）

以下は **開発フォールバック** (`--backend sqlite`, `HashedEmbedder`) の計測値です。本番 ruri + Helix との比較用ベースラインとして記録します。

実施日: 2026-06-17  
データ: BEIR SciFact `data/beir/scifact/`  
環境: WSL2 Linux, CPU, SQLite backend

### 検索 Recall（全コーパス 5,183 文書 ingest、test 先頭 30 クエリ）

| 方式 | Recall@1 | Recall@5 | Recall@10 |
| --- | ---: | ---: | ---: |
| vector (hashed) | 0.000 | 0.033 | 0.100 |
| text (BM25-like) | 0.400 | 0.533 | 0.600 |
| hybrid | 0.333 | 0.533 | 0.600 |

### 検索 Recall（正解文書のみ 283 文書、test 300 クエリ）

| 方式 | Recall@1 | Recall@5 | Recall@10 |
| --- | ---: | ---: | ---: |
| vector (hashed) | 0.253 | 0.470 | 0.530 |
| text | 0.643 | 0.777 | 0.807 |
| hybrid | 0.527 | 0.707 | 0.810 |

### 本番構成との比較（参考: ruri + Helix、50 文書サブセット、10 クエリ）

| 方式 | Recall@1 | Recall@5 | Recall@10 |
| --- | ---: | ---: | ---: |
| vector (ruri) | 0.90 | 0.90 | 0.90 |
| text | 0.80 | 0.90 | 0.90 |
| hybrid | 0.90 | 0.90 | 0.90 |

### RAG end-to-end（HashedEmbedder + local LLM + rule Bosun）

- 全コーパス ingest 後の代表クエリ: `status=completed` だが正解文書未ヒットで無関係 chunk から note 生成
- 正解文書 1 件のみ ingest: `status=stopped_by_no_new_notes`（Bosun rule + 既定閾値で棄却）

### 単体テスト

```text
54 passed (pytest, 2026-06-17)
```

## トラブルシュート

| 症状 | 対処 |
| --- | --- |
| `Invalid vector dimension: expected 128, got 768` | HelixDB を prune して再起動 |
| `llama-server request timeout` | `LLAMA_SERVER_TIMEOUT_SECONDS=180` 以上 |
| `RURI_DEVICE is cuda but CUDA is not available` | NVIDIA ドライバ更新、または検証のみ `RURI_DEVICE=cpu` |
| 日本語で evidence=0 | ログの `question_language=ja` と閾値行を確認。0.35/0.55 が適用されているか |
