# 実用版 State-Aware RAG 仕様書

## HelixDB + Bosun + 大きな言語モデルによる作業用メモ型検索拡張生成

## 1. 目的

このシステムは、研究論文の State-Aware RAG を完全に再現するものではない。

目的は、実用的な検索拡張生成システムとして、次の動きを実装することである。

1. ユーザーの質問を受け取る。
2. 大きな言語モデルで検索クエリを作る。
3. HelixDBで、ベクトル検索、全文検索、グラフ探索を行う。
4. Bosunで検索候補を採点する。
5. 高スコアの候補だけを根拠として残す。
6. 大きな言語モデルで根拠から短い事実メモを作る。
7. Bosunで既存メモとの重複や矛盾を判定する。
8. HelixDB上の作業用メモを更新する。
9. 足りない情報があれば、次の検索クエリを作って繰り返す。
10. 最後に、作業用メモだけを使って回答する。

この仕様では、論文上の trainable extractor は使わない。
その代わりに、Bosunと大きな言語モデルで代替する。

---

## 2. 全体アーキテクチャ

### 2.1 構成要素

このシステムは、次の部品で構成する。

| 部品              | 役割                              |
| --------------- | ------------------------------- |
| 大きな言語モデル        | 検索計画、検索クエリ作成、事実メモ作成、最終回答生成      |
| HelixDB         | 文書、チャンク、エンティティ、作業用メモ、根拠関係の保存と検索 |
| Bosun           | 検索候補の関連性、メモの価値、重複、矛盾を採点         |
| Orchestrator    | ループ制御、停止条件、スコア管理、ログ保存           |
| Embedding Model | 文書チャンクとメモのベクトル化                 |
| Working Memory  | 質問ごとの作業用メモ。HelixDB上で管理する        |

---

## 3. 基本フロー

```text
ユーザー質問
↓
検索計画を作る
↓
HelixDBで検索する
  - ベクトル検索
  - 全文検索
  - グラフ探索
↓
Bosunで候補を採点する
↓
高スコア候補だけを evidence として残す
↓
大きな言語モデルで atomic note を作る
↓
Bosunで既存メモとの重複・矛盾を判定する
↓
WorkingMemory を更新する
↓
停止条件を判定する
↓
必要なら次の検索へ
↓
WorkingMemory だけを使って最終回答を生成する
```

---

## 4. HelixDB上のデータモデル

### 4.1 ノード

最低限、次のノードを作る。

```text
(:Document)
(:Chunk)
(:Entity)
(:Question)
(:WorkingMemory)
(:MemoryNote)
(:Evidence)
(:SearchRound)
```

### 4.2 エッジ

最低限、次のエッジを作る。

```text
(:Document)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:MENTIONS]->(:Entity)
(:Question)-[:HAS_MEMORY]->(:WorkingMemory)
(:WorkingMemory)-[:HAS_NOTE]->(:MemoryNote)
(:MemoryNote)-[:SUPPORTED_BY]->(:Evidence)
(:Evidence)-[:FROM_CHUNK]->(:Chunk)
(:MemoryNote)-[:RELATED_TO]->(:Entity)
(:MemoryNote)-[:DUPLICATE_OF]->(:MemoryNote)
(:MemoryNote)-[:CONFLICTS_WITH]->(:MemoryNote)
(:SearchRound)-[:RETURNED]->(:Evidence)
(:SearchRound)-[:UPDATED]->(:WorkingMemory)
```

### 4.3 Document ノード

```json
{
  "id": "doc_001",
  "title": "文書タイトル",
  "source_uri": "元文書の場所",
  "created_at": "2026-06-16T00:00:00+09:00",
  "updated_at": "2026-06-16T00:00:00+09:00",
  "metadata": {}
}
```

### 4.4 Chunk ノード

```json
{
  "id": "chunk_001",
  "document_id": "doc_001",
  "body": "検索対象となる本文",
  "embedding": [0.01, 0.02, 0.03],
  "token_count": 320,
  "section_title": "章タイトル",
  "source_uri": "元文書の場所",
  "metadata": {}
}
```

### 4.5 WorkingMemory ノード

WorkingMemory は、ユーザー質問ごとに1つ作る。

```json
{
  "id": "wm_001",
  "question_id": "q_001",
  "original_question": "ユーザーの元質問",
  "status": "running",
  "round_count": 0,
  "created_at": "2026-06-16T00:00:00+09:00",
  "updated_at": "2026-06-16T00:00:00+09:00"
}
```

`status` は次のいずれかにする。

```text
running
completed
stopped_by_max_rounds
stopped_by_no_new_notes
stopped_by_low_gain
failed
```

### 4.6 MemoryNote ノード

MemoryNote は、検索結果をそのまま入れない。
大きな言語モデルで短い事実に変換してから保存する。

```json
{
  "id": "note_001",
  "working_memory_id": "wm_001",
  "claim": "短く独立した事実",
  "normalized_claim": "表記ゆれを減らした事実",
  "note_type": "fact",
  "support_score": 0.91,
  "relevance_score": 0.88,
  "novelty_score": 0.76,
  "conflict_score": 0.12,
  "confidence": 0.84,
  "source_count": 2,
  "embedding": [0.01, 0.02, 0.03],
  "created_round": 1,
  "last_updated_round": 1,
  "status": "active"
}
```

`note_type` は次のいずれかにする。

```text
fact
definition
constraint
open_question
intermediate_answer
assumption
```

`status` は次のいずれかにする。

```text
active
duplicate
conflicted
deprecated
rejected
```

### 4.7 Evidence ノード

Evidence は、検索候補のうち採用された根拠である。

```json
{
  "id": "ev_001",
  "chunk_id": "chunk_001",
  "round": 1,
  "query": "検索クエリ",
  "body_excerpt": "根拠として使う本文の抜粋",
  "retrieval_method": "vector",
  "raw_rank": 3,
  "relevance_score": 0.91,
  "memory_value_score": 0.82,
  "accepted": true
}
```

`retrieval_method` は次のいずれかにする。

```text
vector
text
graph
hybrid
```

---

## 5. 検索方式

### 5.1 ベクトル検索

目的は、質問や小質問と意味が近いチャンクを取ることである。

入力:

```json
{
  "query_text": "検索クエリ",
  "query_embedding": [0.01, 0.02, 0.03],
  "top_k": 20
}
```

出力:

```json
{
  "method": "vector",
  "candidates": [
    {
      "chunk_id": "chunk_001",
      "body": "候補本文",
      "raw_score": 0.82
    }
  ]
}
```

### 5.2 全文検索

目的は、固有名詞、技術用語、関数名、製品名を正確に拾うことである。

入力:

```json
{
  "query_text": "BM25向け検索語",
  "top_k": 20
}
```

出力:

```json
{
  "method": "text",
  "candidates": [
    {
      "chunk_id": "chunk_010",
      "body": "候補本文",
      "raw_score": 7.3
    }
  ]
}
```

### 5.3 グラフ探索

目的は、チャンク、文書、エンティティ、既存メモの関係をたどることである。

例:

```text
- 既に作業用メモにある Entity から関連 Chunk を探す
- 採用済み Evidence と同じ Document の前後 Chunk を探す
- MemoryNote と RELATED_TO でつながる Entity から追加情報を探す
- CONFLICTS_WITH の可能性がある Note を探す
```

出力:

```json
{
  "method": "graph",
  "candidates": [
    {
      "chunk_id": "chunk_020",
      "body": "候補本文",
      "graph_reason": "既存メモ note_001 の Entity 経由で発見"
    }
  ]
}
```

### 5.4 候補の統合

3種類の検索結果を統合する。

同じ `chunk_id` が複数の検索方式から出た場合は、1件にまとめる。

```json
{
  "chunk_id": "chunk_001",
  "body": "候補本文",
  "retrieval_methods": ["vector", "text"],
  "vector_rank": 4,
  "text_rank": 2,
  "graph_reason": null
}
```

---

## 6. Bosunの使いどころ

Bosunは、自由回答には使わない。
必ず、ルールに対する yes/no の強さをスコア化するために使う。

### 6.1 検索候補の関連性判定

目的は、検索候補が元質問に直接役立つかを判定することである。

Bosunへの入力:

```text
Rule:
この文書断片は、ユーザーの質問に答えるための具体的な根拠を含む場合だけ yes。
一般論、背景説明だけ、重複情報だけ、推測だけの場合は no。

Question:
{question}

Working Memory:
{working_memory_summary}

Candidate:
{candidate_chunk}
```

出力:

```json
{
  "relevance_score": 0.0
}
```

採用条件:

```text
relevance_score >= 0.70
```

### 6.2 メモに入れる価値の判定

目的は、候補が作業用メモを前進させるかを判定することである。

Bosunへの入力:

```text
Rule:
この候補は、現在の作業用メモにない新しい具体的事実を追加する場合だけ yes。
既存メモの言い換えにすぎない場合、一般論だけの場合、質問に不要な場合は no。

Question:
{question}

Current Working Memory:
{working_memory}

Candidate:
{candidate_chunk}
```

出力:

```json
{
  "memory_value_score": 0.0
}
```

採用条件:

```text
memory_value_score >= 0.60
```

### 6.3 重複除去

目的は、新しい MemoryNote が既存の MemoryNote と同じ意味かどうかを判定することである。

Bosunへの入力:

```text
Rule:
2つのメモが実質的に同じ事実を述べている場合だけ yes。
表現が違っても、主張の内容が同じなら yes。
対象、条件、時点、結論のいずれかが明確に異なる場合は no。

Note A:
{existing_note}

Note B:
{new_note}
```

出力:

```json
{
  "duplicate_score": 0.0
}
```

判定:

```text
duplicate_score >= 0.80 の場合、新規メモとして追加しない。
既存メモの source_count と evidence だけを更新する。
```

### 6.4 矛盾検出

目的は、新しい MemoryNote が既存メモと両立しないかを判定することである。

Bosunへの入力:

```text
Rule:
2つのメモが同じ対象について、同時に正しいとは言えない主張をしている場合だけ yes。
時点、条件、範囲が違うだけで両立できる場合は no。
片方がより詳しいだけの場合も no。

Note A:
{existing_note}

Note B:
{new_note}
```

出力:

```json
{
  "conflict_score": 0.0
}
```

判定:

```text
conflict_score >= 0.70 の場合、CONFLICTS_WITH エッジを作る。
新規メモの status は active のままにする。
ただし最終回答時には、矛盾ありとして扱う。
```

---

## 7. Atomic Note 作成

### 7.1 目的

検索候補をそのまま作業用メモに入れない。
大きな言語モデルで、短く独立した事実に変換する。

### 7.2 入力

```json
{
  "question": "ユーザーの元質問",
  "working_memory": "現在の作業用メモ",
  "accepted_evidence": [
    {
      "evidence_id": "ev_001",
      "body_excerpt": "根拠本文",
      "source_uri": "出典"
    }
  ]
}
```

### 7.3 大きな言語モデルへの指示

```text
あなたは検索拡張生成システムの作業用メモ更新器です。

ユーザーの質問、現在の作業用メモ、採用済み根拠を読み、作業用メモに追加すべき atomic note を作ってください。

条件:
- 1つの note には1つの事実だけを書く。
- 根拠に書かれていないことを推測しない。
- 一般論ではなく、質問に役立つ具体的な事実を書く。
- 既存メモにある情報は繰り返さない。
- 不確かな内容は assumption として分ける。
- まだ足りない情報は open_question として分ける。
- 出力は JSON のみ。

出力形式:
{
  "notes": [
    {
      "claim": "短い事実",
      "note_type": "fact | definition | constraint | intermediate_answer | assumption",
      "supported_by_evidence_ids": ["ev_001"],
      "confidence": 0.0
    }
  ],
  "open_questions": [
    {
      "question": "まだ足りない小質問",
      "reason": "なぜ必要か"
    }
  ]
}
```

---

## 8. Socratic Planning

### 8.1 目的

Socratic Planning は、大きな言語モデルで代用する。
目的は、「次に何を調べるべきか」を小質問に分けることである。

### 8.2 入力

```json
{
  "original_question": "ユーザーの元質問",
  "working_memory": "現在の作業用メモ",
  "open_questions": ["まだ足りない情報"],
  "round": 1
}
```

### 8.3 大きな言語モデルへの指示

```text
元の質問:
{question}

現在の作業用メモ:
{working_memory}

まだ足りない情報:
{open_questions}

作業:
まだ足りない情報を1〜3個の小質問に分けてください。
各小質問について、検索クエリも作ってください。
すでに作業用メモにある情報は、再検索しないでください。

条件:
- 小質問は、元の質問に答えるために必要なものだけにする。
- すでに作業用メモにある内容を調べ直さない。
- 検索クエリは、ベクトル検索向け、全文検索向け、グラフ探索向けに分ける。
- 出力は JSON のみ。

出力形式:
{
  "sub_questions": [
    {
      "sub_question": "小質問",
      "why_needed": "この情報が必要な理由",
      "vector_query": "意味検索向けクエリ",
      "text_query": "全文検索向けクエリ",
      "graph_seed_entities": ["Entity名"],
      "priority": 1
    }
  ]
}
```

### 8.4 小質問数

1ラウンドあたり最大3個までにする。

```text
max_sub_questions_per_round = 3
```

---

## 9. Monte Carlo Tree Search の扱い

### 9.1 方針

初期実装では、Monte Carlo Tree Search を本体ループには入れない。
ただし、後から差し替えられるように、検索戦略としてインターフェースを分離する。

### 9.2 SearchStrategy インターフェース

```typescript
interface SearchStrategy {
  proposeNextActions(input: SearchState): SearchAction[];
  scoreAction(action: SearchAction, state: SearchState): number;
  selectActions(actions: SearchAction[], budget: SearchBudget): SearchAction[];
}
```

### 9.3 SearchState

```typescript
type SearchState = {
  question: string;
  workingMemoryId: string;
  round: number;
  notes: MemoryNote[];
  openQuestions: OpenQuestion[];
  previousQueries: string[];
  previousEvidenceIds: string[];
};
```

### 9.4 SearchAction

```typescript
type SearchAction = {
  actionId: string;
  subQuestion: string;
  vectorQuery: string;
  textQuery: string;
  graphSeedEntities: string[];
  expectedGain: number;
  costEstimate: number;
};
```

### 9.5 初期実装

初期実装では、`SocraticSearchStrategy` を使う。

```typescript
class SocraticSearchStrategy implements SearchStrategy {
  proposeNextActions(input: SearchState): SearchAction[] {
    // 大きな言語モデルで小質問と検索クエリを作る
  }

  scoreAction(action: SearchAction, state: SearchState): number {
    // priority, 未探索性, open_question との対応で機械的に点数化する
  }

  selectActions(actions: SearchAction[], budget: SearchBudget, state: SearchState): SearchAction[] {
    // 点数が高い順に最大3件選ぶ
  }
}
```

### 9.6 将来実装

将来、`MctsSearchStrategy` を追加する。現行 Python 実装では差し替え口と軽いスタブだけを置き、本格的な木探索は今回の実装範囲に含めない。

```typescript
class MctsSearchStrategy implements SearchStrategy {
  proposeNextActions(input: SearchState): SearchAction[] {
    // 木の展開候補を作る
  }

  scoreAction(action: SearchAction, state: SearchState): number {
    // 期待情報利得、根拠数、重複率、矛盾率、コストを使って評価する
  }

  selectActions(actions: SearchAction[], budget: SearchBudget, state: SearchState): SearchAction[] {
    // 探索と活用のバランスで選ぶ
  }
}
```

### 9.7 MCTS用の評価関数案

MCTSを後から入れる場合、報酬は次のようにする。

```text
reward =
  + 0.35 * new_note_count_score
  + 0.25 * relevance_score_avg
  + 0.20 * open_question_reduction_score
  + 0.10 * evidence_diversity_score
  - 0.20 * duplicate_rate
  - 0.20 * conflict_rate
  - 0.10 * cost_score
```

この報酬は初期案であり、ログを見ながら調整する。

---

## 10. ループ設計

### 10.1 メインループ

```pseudo
function answer(question):
    wm = create_working_memory(question)

    for round in 1..MAX_ROUNDS:
        state = load_search_state(wm)

        actions = search_strategy.proposeNextActions(state)
        actions = search_strategy.selectActions(actions, budget)

        if actions is empty:
            wm.status = "completed"
            break

        candidates = []

        for action in actions:
            candidates += helix_vector_search(action.vectorQuery)
            candidates += helix_text_search(action.textQuery)
            candidates += helix_graph_search(action.graphSeedEntities, wm)

        merged_candidates = merge_and_deduplicate_candidates(candidates)

        scored_candidates = []
        for candidate in merged_candidates:
            relevance_score = bosun_relevance(question, wm, candidate)
            memory_value_score = bosun_memory_value(question, wm, candidate)

            if relevance_score >= 0.70 and memory_value_score >= 0.60:
                evidence = create_evidence(candidate, relevance_score, memory_value_score)
                scored_candidates.append(evidence)

        if scored_candidates is empty:
            record_zero_gain_round(wm)
            if should_stop(wm):
                break
            else:
                continue

        new_notes = llm_create_atomic_notes(question, wm, scored_candidates)

        accepted_note_count = 0

        for note in new_notes:
            duplicate = false

            for existing_note in wm.active_notes:
                duplicate_score = bosun_duplicate(existing_note, note)

                if duplicate_score >= 0.80:
                    link_evidence_to_existing_note(existing_note, note.evidence)
                    increment_source_count(existing_note)
                    duplicate = true
                    break

            if duplicate:
                continue

            for existing_note in wm.active_notes:
                conflict_score = bosun_conflict(existing_note, note)

                if conflict_score >= 0.70:
                    create_conflict_edge(existing_note, note, conflict_score)

            save_memory_note(wm, note)
            accepted_note_count += 1

        update_open_questions(wm)
        update_round_stats(wm, accepted_note_count)

        if should_stop(wm):
            break

    final_answer = llm_generate_final_answer(question, wm.active_notes, wm.conflicts)
    return final_answer
```

---

## 11. 機械的な停止条件

停止条件は、できるだけ大きな言語モデルの主観に頼らない。
以下の条件のいずれかを満たしたら停止する。

### 11.1 最大ラウンド数

```text
MAX_ROUNDS = 3
```

本番で必要なら5まで増やす。
初期実装では3で固定する。

停止条件:

```text
round >= MAX_ROUNDS
```

停止時ステータス:

```text
stopped_by_max_rounds
```

### 11.2 新規メモが増えない

```text
NO_NEW_NOTE_LIMIT = 1
```

停止条件:

```text
直近1ラウンドで accepted_note_count == 0
```

停止時ステータス:

```text
stopped_by_no_new_notes
```

### 11.3 情報利得が低い

ラウンドごとに gain を計算する。

```text
gain =
  accepted_note_count
  + 0.5 * new_evidence_count
  - 0.5 * duplicate_count
  - 0.5 * conflict_count
```

停止条件:

```text
gain <= 0 を2回連続で満たす
```

停止時ステータス:

```text
stopped_by_low_gain
```

### 11.4 未解決の小質問がない

open_questions が空の場合に停止する。

```text
open_questions_count == 0
```

停止時ステータス:

```text
completed
```

ただし、この条件だけは大きな言語モデル由来の open_questions に依存する。
そのため、他の停止条件より優先度を下げる。

### 11.5 候補がすべて低スコア

停止条件:

```text
全候補の relevance_score < 0.70
または
全候補の memory_value_score < 0.60
```

これが2ラウンド連続で起きたら停止する。

停止時ステータス:

```text
stopped_by_low_gain
```

---

## 12. スコアしきい値

初期値は次のようにする。

```text
relevance_score >= 0.70
memory_value_score >= 0.60
duplicate_score >= 0.80
conflict_score >= 0.70
```

意味:

```text
relevance_score:
質問に直接関係するか。

memory_value_score:
作業用メモを前進させるか。

duplicate_score:
既存メモと同じ内容か。

conflict_score:
既存メモと矛盾するか。
```

注意:

最初から厳しすぎるしきい値にしない。
初期実装では、候補をやや多めに残し、ログを見ながら調整する。

---

## 13. 最終回答生成

### 13.1 入力

最終回答には、検索結果の全文を渡さない。
WorkingMemory 内の active な MemoryNote と Evidence 情報だけを渡す。

```json
{
  "question": "ユーザーの元質問",
  "memory_notes": [
    {
      "claim": "短い事実",
      "confidence": 0.84,
      "evidence_ids": ["ev_001"],
      "source_uri": "出典"
    }
  ],
  "conflicts": [
    {
      "note_a": "主張A",
      "note_b": "主張B",
      "conflict_score": 0.78
    }
  ],
  "open_questions": []
}
```

### 13.2 大きな言語モデルへの指示

```text
あなたは、作業用メモだけを使って回答するアシスタントです。

条件:
- 検索結果の原文ではなく、MemoryNote だけを根拠にする。
- MemoryNote にない内容を推測しない。
- 矛盾がある場合は、矛盾があると明記する。
- open_questions が残っている場合は、未確認の点として明記する。
- 根拠が足りない場合は、足りないと答える。
- 出典がある場合は、対応する出典を示す。
```

---

## 14. ログ設計

各ラウンドで必ずログを残す。

```json
{
  "working_memory_id": "wm_001",
  "round": 1,
  "actions": [],
  "candidate_count": 45,
  "accepted_evidence_count": 8,
  "created_note_count": 4,
  "accepted_note_count": 3,
  "duplicate_count": 1,
  "conflict_count": 0,
  "gain": 7.0,
  "stop_reason": null
}
```

ログは、しきい値調整と失敗分析に使う。

---

## 15. 失敗時の扱い

### 15.1 検索結果がない場合

```text
検索結果が見つからなかったため、回答に必要な根拠を集められませんでした。
```

### 15.2 根拠はあるがメモ化できない場合

```text
検索結果は見つかりましたが、質問に直接使える事実として整理できませんでした。
```

### 15.3 矛盾がある場合

```text
作業用メモ内に矛盾する情報があります。
このため、断定せずに両方の情報を示します。
```

### 15.4 open_questions が残る場合

```text
一部の情報は確認できていません。
確認できた範囲では、次のように言えます。
```

---

## 16. 実装機能（スコープ）

必須:

```text
- Document / Chunk / WorkingMemory / MemoryNote / Evidence の保存
- ベクトル検索
- 全文検索
- Bosunによる relevance_score
- Bosunによる memory_value_score
- 大きな言語モデルによる atomic note 作成
- 最大3ラウンドのループ
- gain による停止
- duplicate_score
- conflict_score
- Entity ノード
- RELATED_TO / CONFLICTS_WITH エッジ
- open_questions 管理
- Entity 起点の近傍探索
- 採用済み Evidence の周辺 Chunk 探索
- MemoryNote と関連 Entity の探索
- Document 内の前後 Chunk 補完
- SearchStrategy インターフェース
- MctsSearchStrategy
- 最終回答生成
```

---

## 17. 初期設定値

```json
{
  "MAX_ROUNDS": 3,
  "MAX_SUB_QUESTIONS_PER_ROUND": 3,
  "VECTOR_TOP_K": 20,
  "TEXT_TOP_K": 20,
  "GRAPH_TOP_K": 20,
  "MAX_ACCEPTED_EVIDENCE_PER_ROUND": 10,
  "RELEVANCE_THRESHOLD": 0.70,
  "MEMORY_VALUE_THRESHOLD": 0.60,
  "DUPLICATE_THRESHOLD": 0.80,
  "CONFLICT_THRESHOLD": 0.70,
  "NO_NEW_NOTE_LIMIT": 1,
  "LOW_GAIN_LIMIT": 2
}
```

---

## 18. 守るべき原則

1. 検索結果をそのまま最終回答に渡さない。
2. 検索結果は、必ず Evidence と MemoryNote に変換する。
3. WorkingMemory は HelixDB 上で管理する。
4. Bosunは判定だけに使う。
5. 大きな言語モデルは、計画、メモ化、回答生成に使う。
6. 停止条件はできるだけ機械的にする。
7. MCTSは初期実装に入れず、検索戦略として後から差し替えられるようにする。
8. 矛盾は消さずに、CONFLICTS_WITH として残す。
9. 重複は新規メモにせず、既存メモの根拠を増やす。
10. 最終回答は WorkingMemory だけを使う。

---

## 19. この仕様で再現できること

この仕様で再現できるのは、State-Aware RAGの次の実用的な部分である。

```text
- 状態を持ちながら検索する
- 検索結果を毎回整理する
- 作業用メモを更新しながら推論する
- 不要な情報を作業用メモに入れない
- 重複と矛盾を管理する
- 最終回答を作業用メモに基づいて作る
```

再現しないものは次である。

```text
- 論文と同じ trainable extractor
- 論文と同じ報酬設計
- 論文と同じ評価条件
- 論文と同じ精度の保証
```

このため、このシステムは研究再現ではなく、実用版の State-Aware RAG 風システムとして扱う。
State-Aware RAG 論文は気にしなくて良いです。

# リファレンス

車輪の再開発はしない。以下を使って実装する。

- https://github.com/HelixDB/helix-db
- https://huggingface.co/Hanno-Labs/bosun-xs
