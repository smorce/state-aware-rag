# 実用版 State-Aware RAG

仕様書に沿って、質問ごとの WorkingMemory を更新しながら検索する実用版 RAG 実装です。

## 実装範囲

- Document / Chunk / Entity / WorkingMemory / MemoryNote / Evidence の SQLite 永続化
- ベクトル検索、全文検索、Entity と Evidence 近傍によるグラフ探索
- Bosun XS による relevance / memory value / duplicate / conflict 採点
- Transformers + PEFT で in-process に Bosun XS を読む native scorer (最終位置 logits 直読)
- Evidence から Atomic Note を作成し、検索結果の原文を最終回答へ直接渡さないループ
- 最大ラウンド数、新規メモなし、低 gain による停止
- SearchStrategy インターフェース、SocraticSearchStrategy、MctsSearchStrategy の差し替え口
- llama-server 用 OpenAI 互換クライアント設定の同梱
- `vendor/helix-db` に clone した HelixDB と、Python から `/v1/query` を呼ぶ HTTP adapter

## 使い方

```powershell
$env:PYTHONUTF8='1'
$env:UV_LINK_MODE='copy'
uv run state-aware-rag --db .\rag.sqlite3 ingest .\docs\sample.md
uv run state-aware-rag --db .\rag.sqlite3 ask "この文書の要点は？" --llm server
```

埋め込みは既定で `cl-nagoya/ruri-v3-310m` (`RuriEmbedder`) を使います。文書には `検索文書: `、クエリには `検索クエリ: ` のプレフィックスを付けてベクトル化します。GPU があれば `cuda`、なければ `cpu` に自動切替します。軽量な開発用フォールバックが必要な場合は `EMBEDDING_BACKEND=hashed` または `RagConfig(embedding_backend="hashed")` を指定してください。

日本語本文は budoux で文節境界に分割した上でチャンク化します (`BudouxChunker`)。英語など日本語を含まない本文は従来通り文字数ベースで分割されます。チャンカーの強制切り替えは `RagConfig(chunker_backend="char")` で行えます。

Bosun XS を使う場合 (既定):

```powershell
$env:PYTHONUTF8='1'
$env:UV_LINK_MODE='copy'
uv add ".[bosun-native]" --link-mode=copy
uv run state-aware-rag --db .\rag.sqlite3 ask "この文書の要点は？" --llm server --bosun xs
```

`--bosun xs` は Bosun XS 公式の `serving.json` 契約に合わせて prompt を組み、最終位置の `yes_id` / `no_id` logits 差を sigmoid してスコア化します。JSON 生成やルールベース代替ではありません。

`Hanno-Labs/bosun-xs` の tokenizer と adapter、`Qwen/Qwen3-Reranker-0.6B` base model を Transformers + PEFT で in-process に読み、`logits_to_keep=1` で最終 token logits を直接使います。GPU があれば `cuda` + `bfloat16`、なければ `cpu` + `float32` に自動切替します。

Bosun XS の公式既定値は `yes_id=9693`, `no_id=2152`, `max_len=3072` です。別の `serving.json` を使う場合は `BOSUN_SERVING_JSON` にファイルパスを指定してください。adapter / base model / device は `BOSUN_REPO` / `BOSUN_BASE_MODEL` / `BOSUN_DEVICE` で差し替えられます。

外部 llama-server に Bosun XS GGUF を載せて completion logprobs を読む経路は本プロジェクトでは原則使わないため、`--bosun-backend` オプションごと削除済みです。開発検証で Bosun XS 自体をスキップしたい場合は `--bosun rule` で素のルール採点に切り替えられます。

HelixDB を使う場合は、公式 CLI を入れてローカルインスタンスを起動します。CLI は Docker または Podman を必要とします。

Windows (Docker Desktop):

```powershell
.\.tools\helix.exe init
.\.tools\helix.exe start dev --disk
```

WSL2 Linux / Linux (Docker Engine):

```bash
.tools/helix init
cd helix_project && ../.tools/helix start --port 6969 dev
```

Linux 用バイナリは GitHub Releases から取得できます。

```bash
curl -L -o .tools/helix \
  https://github.com/HelixDB/helix-db/releases/download/v3.0.5/helix-x86_64-unknown-linux-gnu
chmod +x .tools/helix
```

Python 側では `HelixHttpClient` が `http://localhost:6969/v1/query` に dynamic query JSON を送ります。クエリ JSON の作成には、clone 済みの TypeScript SDK をビルドした `vendor/helix-db/sdks/typescript/dist/index.js` を sidecar として使えます。

HelixDB backend で実行する場合:

```powershell
uv run state-aware-rag --backend helix --db .\helix_mirror.sqlite3 ingest .\docs\sample.md
uv run state-aware-rag --backend helix --db .\helix_mirror.sqlite3 ask "この文書の要点は？" --llm server --bosun xs
```

Linux:

```bash
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag --backend helix \
  --db helix_mirror.sqlite3 ingest docs/sample.md
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag --backend helix \
  --db helix_mirror.sqlite3 ask "この文書の要点は？" --llm server --bosun xs
```

`--backend helix` は HelixDB に `Document` / `Chunk` / `Entity` / `Question` / `WorkingMemory` / `Evidence` / `MemoryNote` / `SearchRound` ノードを書き込み、`HAS_CHUNK` / `MENTIONS` / `HAS_MEMORY` / `FROM_CHUNK` / `HAS_NOTE` / `SUPPORTED_BY` / `RELATED_TO` / `RETURNED` / `UPDATED` などの主要エッジを張ります。検索時は HelixDB の `vectorSearchNodesWith`、`textSearchNodesWith`、`Entity <- MENTIONS - Chunk` と `WorkingMemory -> HAS_NOTE -> SUPPORTED_BY -> FROM_CHUNK` の graph traversal を使います。Python 側の SQLite は ID と型復元の mirror として残します。

## テスト

```powershell
$env:PYTHONUTF8='1'
$env:UV_LINK_MODE='copy'
uv run pytest
```

Helix TypeScript SDK のビルドが必要な場合:

```powershell
Push-Location .\vendor\helix-db\sdks\typescript
npm install
npm run build
Pop-Location
```

## 設計メモ

外部 HelixDB が無い環境でも core flow を検証できるよう、SQLite backend も残しています。CLI の既定では、検索計画、Atomic Note 作成、最終回答生成に `LlamaServerEnvConfig` と `call_llama_server` を使い、Bosun 採点は `--bosun xs` が既定です。サーバーを使わない開発検証だけ `--llm local --bosun rule` を指定できます。

最終回答は `MemoryNote` と、それに紐づく `Evidence` の出典だけから生成します。検索候補本文を直接回答生成へ渡さないため、仕様の「作業用メモだけを使って回答する」制約を守ります。

Rust 製 HelixDB との結合方針は [ADR-001](docs/adr-001-helixdb-python-integration.md) に記録しています。直接 FFI ではなく、DB サーバを別プロセスとして動かし、Python は HTTP adapter と TypeScript SDK sidecar を使います。

Bosun XS の使用方針は [ADR-002](docs/adr-002-bosun-xs-integration.md) に記録しています。

現在の検証結果と HelixDB 実サーバー起動時の前提は [検証記録](docs/verification.md) に残しています。
