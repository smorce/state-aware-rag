# RAG 実行ログ

`state-aware-rag ask` 実行時に、成功/失敗ルートをここへ記録します。

## 出力ファイル

| ファイル | 内容 |
| --- | --- |
| `rag_events.jsonl` | 全セッションのイベントを追記（LLM / スクリプト向け） |
| `rag_session_<session_id>.xlsx` | セッション単位の Excel（人間レビュー向け） |

## 主な `route` 値

- `answer.session_start` — セッション開始
- `score.candidate_rejected.relevance_below_threshold` — Bosun relevance 未満で棄却（**確定理由**）
- `score.candidate_rejected.memory_value_below_threshold` — Bosun memory_value 未満で棄却
- `score.candidate_accepted` — Evidence 採用
- `round.no_accepted_evidence` — ラウンド内で採用 Evidence ゼロ
- `round.atomic_notes_empty` — LLM が note を返さず
- `answer.final_dememoization_failed` — Evidence はあるが active note なし
- `answer.final_no_evidence` — Evidence なし
- `answer.final_success` — 最終回答生成成功
- `answer.unhandled_exception` — 未処理例外

無効化: `RAG_RUN_LOG=0`
