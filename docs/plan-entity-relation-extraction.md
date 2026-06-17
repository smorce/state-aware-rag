# 実装計画: LLM によるエンティティ・関係抽出

このドキュメントは、`docs/HelixDBの役割.md` で言及されている
「LLM でエンティティと関係を抽出する」フェーズを、本リポジトリにどう実装するかの計画書である。

---

## 1. 目的とスコープ

### 1.1 目的

- ドキュメントから **型付きエンティティ** と **エンティティ間の関係** を LLM で抽出し、
  HelixDB / SQLite ミラーに保存できる状態にする。
- 抽出結果はベクトル検索・全文検索・**グラフ探索の質**を底上げするための入力とする。
- 既存の WorkingMemory / MemoryNote / Evidence ループを壊さず、ingest 時の差し込みで完結させる。

### 1.2 スコープ内

- 文書チャンクからの抽出（ingest フェーズ）。
- エンティティ型ラベル付け（`Person` / `Company` / `Product` / `Contract` / `Date` / `Concept` / `Other`）。
- 二項関係の抽出（`from_entity`, `relation_type`, `to_entity`, `source_chunk_id`）。
- 名寄せ（同一エンティティのエイリアス統合）。
- HelixDB スキーマ拡張（ラベル分離・新エッジ）。

### 1.3 スコープ外（別 ADR で扱う）

- 文書間ジョイン推論（クロスドキュメント co-reference）。
- 時系列で変化する関係の履歴管理（valid_from / valid_to）。
- 大規模グラフのバッチ更新最適化。
- 埋め込みベースの抽出（今回は LLM + 後処理ルールで行う）。

---

## 2. 現状の課題（再掲）

`src/state_aware_rag/text.py::extract_entities` は正規表現で

```python
re.finditer(r"\b[A-Z][A-Za-z0-9_+-]{2,}\b|[\u3400-\u9fff]{2,}", text)
```

を回しているだけで、

1. **エンティティ型** が無い（全部フラットな `entities(name TEXT PRIMARY KEY)`）。
2. **関係** が抽出されていない。`chunk_entities` の隣接表しか無い。
3. **名寄せ** が無い。`ABC株式会社` / `ABC Corp.` / `ABC` が別レコードになる。
4. **LLM 抽出ではない**。誤検出・取りこぼしが多い。

`docs/HelixDBの役割.md` の例

```text
Person -[WORKS_AT]-> Company
Chunk  -[MENTIONS]-> Person
```

に対応できない。

---

## 3. データモデル変更案

### 3.1 SQLite ミラー（`store.py`）

#### 3.1.1 既存の置き換え

```sql
-- 旧
CREATE TABLE entities (name TEXT PRIMARY KEY);
CREATE TABLE chunk_entities (chunk_id TEXT, entity_name TEXT, PRIMARY KEY(chunk_id, entity_name));
CREATE TABLE note_entities (note_id TEXT, entity_name TEXT, PRIMARY KEY(note_id, entity_name));
```

を、以下の構造に置き換える（旧テーブルは削除）。

```sql
CREATE TABLE entities (
  id TEXT PRIMARY KEY,                 -- ent_xxxxxxxxxxxxxxxx
  entity_type TEXT NOT NULL,           -- Person / Company / Product / Contract / Date / Concept / Other
  canonical_name TEXT NOT NULL,        -- 名寄せ後の正規名
  normalized_name TEXT NOT NULL,       -- 名寄せ判定用 (lower + 全角半角統一 + 空白除去)
  embedding TEXT NOT NULL,             -- canonical_name の embedding (JSON)
  attributes TEXT NOT NULL,            -- 任意の追加属性 (JSON)
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_entities_type_norm ON entities(entity_type, normalized_name);

CREATE TABLE entity_aliases (
  entity_id TEXT NOT NULL,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  source_chunk_id TEXT,                -- 初出チャンク (NULL 可)
  PRIMARY KEY(entity_id, normalized_alias),
  FOREIGN KEY(entity_id) REFERENCES entities(id)
);
CREATE INDEX idx_aliases_norm ON entity_aliases(normalized_alias);

CREATE TABLE chunk_entities (
  chunk_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  surface TEXT NOT NULL,               -- チャンク中での表記
  PRIMARY KEY(chunk_id, entity_id, surface),
  FOREIGN KEY(chunk_id) REFERENCES chunks(id),
  FOREIGN KEY(entity_id) REFERENCES entities(id)
);

CREATE TABLE note_entities (
  note_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  PRIMARY KEY(note_id, entity_id),
  FOREIGN KEY(entity_id) REFERENCES entities(id)
);

CREATE TABLE relations (
  id TEXT PRIMARY KEY,                 -- rel_xxxxxxxxxxxxxxxx
  from_entity_id TEXT NOT NULL,
  to_entity_id TEXT NOT NULL,
  relation_type TEXT NOT NULL,         -- WORKS_AT / OWNED_BY / SIGNED_ON など (大文字スネーク)
  source_chunk_id TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence_text TEXT NOT NULL,         -- 抽出根拠の生テキスト断片
  attributes TEXT NOT NULL,            -- JSON (期間・役職等の追加メタ)
  created_at TEXT NOT NULL,
  FOREIGN KEY(from_entity_id) REFERENCES entities(id),
  FOREIGN KEY(to_entity_id) REFERENCES entities(id),
  FOREIGN KEY(source_chunk_id) REFERENCES chunks(id)
);
CREATE INDEX idx_relations_from ON relations(from_entity_id);
CREATE INDEX idx_relations_to ON relations(to_entity_id);
CREATE INDEX idx_relations_type ON relations(relation_type);
```

#### 3.1.2 マイグレーション戦略

- バージョン管理列を `documents.metadata` ではなく専用テーブル `schema_version (version INTEGER)` で持つ。
- 既存 DB は破棄前提（開発段階のため）。本番運用が始まる前に決定する。
- 互換のため、`text.extract_entities` の Python 関数は残すが、`store.ingest_document` 内では呼ばない。

### 3.2 HelixDB スキーマ拡張（`helix_store.py`）

- `Entity` 単一ラベルを廃止し、`(:Person)` `(:Company)` `(:Product)` `(:Contract)` `(:Date)` `(:Concept)` `(:Other)` に分割。
- 既存の `(:Chunk)-[:MENTIONS]->(:Entity)` は型別ラベルに対しても張る（例: `(:Chunk)-[:MENTIONS]->(:Person)`）。
- 新エッジ: `(:From)-[:<RELATION_TYPE>]->(:To)`、ただし HelixDB は動的ラベルが使えるので `addE("WORKS_AT", ...)` で問題なし。
- `Relation` 自体をノードにして両端に張る選択肢（reified relation）も検討する。**まずはエッジ直結で実装し、属性が増えてきたら reify する**。
- 既存の `vectorSearchNodesWith("Chunk", "embedding", ...)` 等の queries は変更不要。
- 既存テスト `test_helix_backend.py` の `MENTIONS` / `RELATED_TO` 文字列アサートは型別ラベルに対しても張るため壊れない。

### 3.3 `models.py` 追加データクラス

```python
class EntityType(StrEnum):
    PERSON = "Person"
    COMPANY = "Company"
    PRODUCT = "Product"
    CONTRACT = "Contract"
    DATE = "Date"
    CONCEPT = "Concept"
    OTHER = "Other"

@dataclass(frozen=True)
class Entity:
    id: str
    entity_type: EntityType
    canonical_name: str
    normalized_name: str
    aliases: tuple[str, ...]
    embedding: list[float]
    attributes: dict[str, Any]
    created_at: str
    updated_at: str

@dataclass(frozen=True)
class Relation:
    id: str
    from_entity_id: str
    to_entity_id: str
    relation_type: str
    source_chunk_id: str
    confidence: float
    evidence_text: str
    attributes: dict[str, Any]
    created_at: str

@dataclass(frozen=True)
class ExtractionResult:
    chunk_id: str
    entities: tuple[Entity, ...]
    relations: tuple[Relation, ...]
```

---

## 4. 抽出パイプライン

### 4.1 抽出器インターフェース

```python
# src/state_aware_rag/extraction.py
class EntityExtractor(Protocol):
    def extract(self, chunk: Chunk) -> ExtractionResult: ...
```

実装は 2 つ用意する。

| 実装 | 用途 |
|---|---|
| `LlmEntityExtractor` | 本番。`llama-server` 経由で LLM 呼び出し。 |
| `RuleEntityExtractor` | 既存正規表現の置き換え。LLM 無し環境のフォールバック & ユニットテスト用。 |

### 4.2 LLM プロンプト設計

`JsonLlamaPlannerAndWriter` と同じ JSON-only 出力契約に揃える。

```text
あなたは文書からエンティティと関係を抽出する抽出器です。

入力チャンク:
{chunk.body}

抽出方針:
- 型は Person / Company / Product / Contract / Date / Concept / Other のいずれか。
- 一般名詞や指示語（彼/同社/それ など）は抽出しない。
- 同じ実体への異表記は同じ canonical_name にまとめてよい。
- 関係は二項関係のみ。三項以上は分解する。
- relation_type は大文字スネーク (例: WORKS_AT, BELONGS_TO, SIGNED_ON)。
- 根拠が原文に書かれていないものは出力しない。
- 出力は JSON のみ。
```

応答スキーマ:

```json
{
  "entities": [
    {
      "type": "Company",
      "canonical_name": "ABC株式会社",
      "aliases": ["ABC", "ABC Corp."],
      "attributes": {}
    }
  ],
  "relations": [
    {
      "from": "山田太郎",
      "to": "ABC株式会社",
      "relation_type": "WORKS_AT",
      "evidence_text": "山田太郎は2020年からABC株式会社に勤務している。",
      "attributes": {"since": "2020"},
      "confidence": 0.92
    }
  ]
}
```

### 4.3 後処理

LLM の生 JSON を Python 側で必ず検証する。

1. **必須キー**（`entities`, `relations`）の存在チェック。
2. `type` が `EntityType` メンバーに含まれるか。含まれなければ `Other`。
3. `relation_type` が `^[A-Z][A-Z0-9_]*$` を満たすか。違反は破棄。
4. `from` / `to` が `entities` の `canonical_name` または `aliases` のどれかにマッチするか。マッチしなければ破棄（hallucination 対策）。
5. `confidence` のクリップ（0.0 〜 1.0）。
6. 抽出失敗時は空の `ExtractionResult` を返す（例外で ingest を止めない）。

---

## 5. 名寄せ（Entity Resolver）

### 5.1 入力と出力

```python
class EntityResolver:
    def resolve(self, candidate: Entity) -> Entity:
        """candidate を既存エンティティへマージするか、新規作成して返す。"""
```

### 5.2 マッチ判定の段階

1. **完全一致**: `(entity_type, normalized_name)` が既存と一致 → 既存を返す。
2. **エイリアス一致**: `normalized_alias` が `entity_aliases` に存在 → 既存を返す。
3. **正規化類似一致**: 編集距離 / Jaccard が閾値以上 → 候補のうち最古を既存とし、新表記を alias に追加。
4. **埋め込み類似一致**: canonical_name の埋め込みコサイン類似が `0.92` 以上 → 同様にマージ。
5. それ以外 → 新規 `Entity` を作成。

### 5.3 正規化規則

- NFKC 正規化
- 全角→半角（英数記号）
- 大文字小文字統一（ASCII のみ）
- 連続空白の単一化、両端 trim
- 法人接尾辞（株式会社 / 有限会社 / Co., Ltd. / Inc. など）を末尾から除いた版も保持し、別キーで照合

### 5.4 競合解決

- 異なる `entity_type` 同士はマージしない（`Person:山田` と `Company:山田工業`）。
- ただし `Other` → 具体型への昇格は許可（より具体な型を優先）。
- canonical_name は最初に登録されたものを優先。長い表記が来たら alias に追加する。

---

## 6. `ingest_document` への統合

`SQLiteRagStore.ingest_document` のチャンク作成ループに以下を差し込む。

```text
for each chunk:
    chunk = create_chunk(...)
    result = extractor.extract(chunk)
    for e in result.entities:
        resolved = resolver.resolve(e)
        link_chunk_entity(chunk.id, resolved.id, surface=...)
    for r in result.relations:
        save_relation(r)
```

ポイント:

- **トランザクション**: 1 チャンク分の抽出 + 解決 + 保存を 1 トランザクションで囲む。途中失敗時は当該チャンクのみロールバック。
- **遅延化**: 抽出は LLM 呼び出しで重い。`ingest_document` に `extract_entities: bool = True` を生やし、後段で `state-aware-rag extract` サブコマンドだけで実行可能にする経路も用意する。
- **HelixDB 側**: `helix_store.HelixBackedRagStore.ingest_document` 後フックで型別ノード追加 + 関係エッジ追加を行う（`_add_entity_node` を型別に分け、`_link_nodes` で relation_type をエッジラベルに使う）。

---

## 7. 検索への影響

### 7.1 グラフ探索

`retrieval.Retriever.graph_search` を以下のように拡張する。

1. **既存**: seed_entity 名 → `MENTIONS` 逆方向で chunk を取得。
2. **追加 A**: seed_entity から 1〜2 hop の `relations` を辿り、到達したエンティティを `MENTIONS` ←逆方向でチャンクへ展開する。
3. **追加 B**: WorkingMemory に紐づくメモから収集したエンティティの集合を、関係探索の seed に加える。

### 7.2 検索計画（`strategy.py`）

`SearchAction.graph_seed_entities: list[str]` を `list[EntityRef]` に拡張する選択肢があるが、初期実装は文字列 seed のままで動かす。型ラベル指定は次フェーズ。

### 7.3 既存 LLM プロンプト

`JsonLlamaPlannerAndWriter.plan` は `graph_seed_entities` を文字列リストで返している。互換のため変更しない。後で `graph_seed_typed_entities` を別フィールドとして追加してもよい。

---

## 8. 段階的導入（実装フェーズ）

依存関係を最小にしながら、各フェーズで `pytest` が通る状態を保つ。

| フェーズ | 内容 | 主な diff 範囲 |
|---|---|---|
| Phase 1 | `models.py` に `EntityType` / `Entity` / `Relation` / `ExtractionResult` 追加。SQLite 新スキーマ + マイグレーション。`RuleEntityExtractor` で既存挙動を再現。 | `models.py`, `store.py` |
| Phase 2 | `extraction.py` 新設。`LlmEntityExtractor` を `JsonLlamaPlannerAndWriter` 流儀で実装。`ingest_document` から `extractor.extract` を呼ぶ。 | `extraction.py`, `llm.py`, `store.py` |
| Phase 3 | `EntityResolver` 実装。エイリアス・正規化類似・埋め込み類似でマージ。 | `extraction.py`, `store.py` |
| Phase 4 | HelixDB 側の型別ノード分割 + relation エッジ書き込み。HelixDB graph_search の 1〜2 hop 拡張。 | `helix_store.py`, `retrieval.py` |
| Phase 5 | CLI `extract` サブコマンド（後付けで一括抽出を回す）。 | `cli.py` |

フェーズ5まで一気に実装してください。

---

## 9. テスト戦略

### 9.1 ユニットテスト

- `RuleEntityExtractor`: 正規表現抽出が後方互換であること。
- `EntityResolver`: 完全一致 / エイリアス / 編集距離 / 埋め込み類似の各経路を独立に。
- `LlmEntityExtractor`: モック LLM クライアントを差し込んで JSON 検証ロジックをテスト。
- `Relation` の永続化と取得の round-trip。

### 9.2 統合テスト

- 日本語の短い文章（例: 「山田太郎は2020年からABC株式会社で働いている。」）を ingest し、
  `Person` × 1 / `Company` × 1 / `Date` × 1 / `WORKS_AT` × 1 が抽出・保存されることを確認。
- 名寄せ: 同一実体の別表記を含む 2 文書を ingest し、エンティティが 1 件にまとまることを確認。
- HelixDB バックエンド: `FakeHelixClient` のリクエストに `WORKS_AT` 等のエッジラベル付き writeBatch が含まれること。

### 9.3 回帰テスト

- 既存 `test_state_aware_rag.py` / `test_helix_backend.py` が変更後も通ること。
- `extract_entities` 関数自体の挙動は変えないこと（他箇所が呼んでいる前提を壊さないため）。

---

## 10. リスク・未決事項

| 項目 | 内容 | 対応案 |
|---|---|---|
| LLM コスト | 抽出は文書ごとに呼び出すため重い | バッチ化、`ingest --no-extract` で後追い実行 |
| JSON 破綻 | LLM が JSON を返さないことがある | 既存 `_extract_json_object` を流用、失敗時は空結果＋ログ |
| 型衝突 | `Date` を `Concept` と混同しやすい | プロンプトに型定義を併記、`Date` は ISO 8601 / 年月日テンプレに正規化 |
| 関係の方向 | LLM が `from` / `to` を逆にする | プロンプトで例示、後段ルールで一部の対称関係（`RELATED_TO` 等）は無向化 |
| 名寄せの誤マージ | 別人を同一視する事故 | 同姓同名対策として、文脈エンティティ（所属会社など）も特徴量に加える方針を Phase 3 で詰める |
| 抽出失敗時の挙動 | パイプラインを止めるか | ingest は止めない。失敗チャンクは `extraction_status='failed'` を残し再実行可能にする |
| スキーマ移行 | 既存 DB が壊れる | 開発段階のため破棄前提。本番投入前に `schema_version` ベースの up/down マイグレーションを設計 |

---

## 11. 次のアクション

1. Phase 1,2,3,4,5 を実装する。
2. 並行で `docs/HelixDBの役割.md` の「表記揺れ・重複名寄せ」方針を本書 §5 に同期させる。