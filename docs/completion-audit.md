# 完了監査

## 実装済み

- ユーザー質問を受け取り、`WorkingMemory` を作成する。
- LLM で `vector_query` / `text_query` / `graph_seed_entities` を含む検索計画を作る。
- HelixDB backend で vector 検索、全文検索、graph traversal を呼ぶ。
- 検索候補を Bosun XS scorer で relevance / memory value 採点する。
- 閾値を超えた候補だけ `Evidence` として残す。
- LLM で採用済み `Evidence` から短い `MemoryNote` を作る。
- Bosun XS scorer で既存メモとの duplicate / conflict を判定する。
- `WorkingMemory`、`MemoryNote`、`Evidence`、`SearchRound` と主要 graph edge を HelixDB に書く。
- Helix `Evidence` に `working_memory_id` を同期し、note 未紐付けの採用済み Evidence も graph search の anchor にする。
- HelixDB 上の `WorkingMemory` status / round_count / updated_at をループ中・終了時に同期する。
- `SearchRound -RETURNED-> Evidence` を round log の accepted evidence ID で正確に紐付ける。
- 日本語全文検索のため query と Helix `Chunk.body` を同じ正規化規則でスペース区切りにする。
- 採用済み Evidence と同じ Document の前後 Chunk、`CONFLICTS_WITH` 起点の Evidence / Entity 関連 Chunk を Helix graph traversal で候補へ含める。
- SQLite mirror は Helix backend の graph 候補発見には使わず、表示用原文と型復元に限定する。
- Evidence はあるが active `MemoryNote` がない場合は §15.2 固定メッセージを返す。
- オーケストレータ内部例外時は `WorkingMemoryStatus.FAILED` を保存して再送出する。
- open question と gain に基づいて最大 3 ラウンドまで検索を繰り返す。
- 最終回答は `MemoryNote` だけを根拠に作る。
- WSL2 + Docker Engine 環境で HelixDB 実サーバー起動と `--backend helix` end-to-end を確認済み (`docs/verification.md`)。

## Bosun XS の扱い

`NativeBosunXSScorer` (Transformers + PEFT, in-process) のみを使う。Bosun XS 公式の `serving.json` に従って prompt を組み、`logits_to_keep=1` で取り出した最終位置の logits から `sigmoid(logits[yes_id] - logits[no_id])` を計算する。`yes_id=9693 / no_id=2152` の token id が読めない場合は失敗させ、別モデルの yes/no 出力を Bosun XS として扱わない。

別 llama-server に Bosun XS GGUF を載せて completion logprobs を読む経路 (`BosunLlamaServerScorer`、`--bosun-backend server`) は ADR-002 の方針に基づき削除済み。

## 残タスク

コード上の残タスクはなし。

環境依存の注意点:

- Bosun XS native backend は `torch` / `transformers` / `peft` を要求する。`uv add ".[bosun-native]" --link-mode=copy` で導入する。
- HelixDB 実サーバー end-to-end は Docker または Podman がある環境で実施する。本リポジトリでは WSL2 + Docker Engine で確認済み。
