# 検証記録

## 実施済み

```powershell
$env:PYTHONUTF8='1'
$env:UV_LINK_MODE='copy'
uv run pytest
```

結果:

```text
43 passed
```

LLM サーバー接続:

```powershell
$env:LLAMA_SERVER_MAX_TOKENS='64'
$env:LLAMA_SERVER_TIMEOUT_SECONDS='30'
uv run python -c "..."
```

結果:

```json
{"ok": true}
```

State-Aware RAG フロー:

```powershell
uv run state-aware-rag --backend sqlite --db tmp_flow.sqlite3 ingest tmp_flow_sample.md
uv run state-aware-rag --backend sqlite --db tmp_flow.sqlite3 ask "What should the final answer use?" --llm server --bosun rule --max-rounds 1
```

結果:

```text
最終回答で使用すべきものは、「作業用メモ（working memory notes）」のみです。
```

Bosun XS scorer (旧 server backend, 当初検証):

```powershell
$env:LLAMA_SERVER_TIMEOUT_SECONDS='30'
uv run python -c "..."
```

結果:

```text
RuntimeError: Bosun XS official yes/no token ids were not present in returned logprobs
```

このとき走らせていた LLM サーバーは Qwen3.6-27B-MTP のような汎用 LLM だったため、返却 token id が Bosun XS 公式の `yes_id=9693` / `no_id=2152` と一致せず、契約違反として正しく失敗した。

その後 ADR-002 の方針に基づき、別 llama-server に Bosun XS GGUF を載せて completion logprobs を読む経路 (`BosunLlamaServerScorer`、`--bosun-backend server`) は削除し、Bosun XS は `NativeBosunXSScorer` (Transformers + PEFT, in-process) に一本化した。`--bosun xs` は常に native 経路で動く。

## HelixDB

CLI の既定 backend は `helix`。HelixDB を起動せずに既定 backend を使った場合、CLI は次の形式の英語エラーで非 0 終了する。

```text
HelixDB backend is not available: ...
Start HelixDB before using the default backend. Windows: .\.tools\helix.exe start dev --port 6969 --persist. Linux/macOS: cd helix_project && ../.tools/helix start --port 6969 dev. Use --backend sqlite for local development without HelixDB.
```

Helix CLI は公式 release URL から導入済み。

```powershell
.\.tools\helix.exe --version
```

結果:

```text
Helix CLI 3.0.5
```

Helix project 初期化:

```powershell
.\.tools\helix.exe init --path .\helix_project --no-skills local
```

結果:

```text
Initialized 'helix_project' successfully
```

HelixDB 実サーバー起動 (Windows ホスト, 当初):

```powershell
.\.tools\helix.exe start dev --port 6969 --persist
```

結果:

```text
Docker is not available. Install/start docker and try again: program not found
```

Windows ホストには Docker Desktop が入っていなかったため、`--backend helix` の実サーバー end-to-end はそこでは未実施だった。

### WSL2 Linux ホストでの end-to-end 検証 (Docker 利用可)

同じワークスペースを WSL2 Linux 6.18 から開いた環境では Docker Engine 29.1.3 (Server 起動済み, 11+ コンテナ) が利用できたため、Linux 用 Helix CLI を導入して end-to-end を実施した。

```bash
docker info        # Docker Engine 29.1.3 (Server 起動済み)
file .tools/helix  # ELF 64-bit LSB pie executable, x86-64 (Linux 用 v3.0.5)
.tools/helix --version
```

結果:

```text
Helix CLI 3.0.5
```

`helix start dev` (memory mode) で HelixDB コンテナ `ghcr.io/helixdb/enterprise-dev:latest` が起動。

```bash
cd helix_project && ../.tools/helix start --port 6969 dev
docker ps --filter name=helix
```

結果:

```text
Started 'dev' successfully
URL: http://localhost:6969
Container: helix-helix_project-dev   (image: ghcr.io/helixdb/enterprise-dev:latest, 0.0.0.0:6969->8080/tcp)
```

end-to-end ingest と ask:

```bash
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag --backend helix \
  --db tmp_helix_mirror.sqlite3 ingest docs/sample.md
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag --backend helix \
  --db tmp_helix_mirror.sqlite3 ask "State-Aware RAG が最終回答に使うのは何ですか？" \
  --llm local --bosun rule --max-rounds 1
```

結果:

```text
ingested document_id=doc_63ce97c0324e4f24 chunks=1
作業用メモから確認できた範囲では、次の通りです。
1. # State-Aware RAG サンプル ... 出典: docs/sample.md
working_memory_id=wm_cf6910441dff48ec
status=stopped_by_max_rounds
```

`HelixBackedRagStore.helix_vector_search` / `helix_text_search` / `helix_graph_search` を直接呼んだ確認:

| 検索系 | 結果 |
| :--- | :--- |
| `vectorSearchNodesWith("Chunk", "embedding", ...)` | `chunk_172ec310bc294bb3` を距離付きで返す |
| `textSearchNodesWith("Chunk", "body", ...)` | `"State"` / `"RAG"` / `"サンプル"` などスペース区切り英数語は hit。`"作業用メモ"` のような連続日本語文字列は HelixDB 既定の text index が tokenize しないため空 (HelixDB 側の仕様) |
| `Entity <- MENTIONS - Chunk` graph traversal | 該当 Chunk を返す |

検索アダプタ修正: HelixDB の `/v1/query` は projection 結果を `{<var>: {"properties": [...]}}` 形で返すため、`extract_returned_rows` を flat list と `{"properties": [...]}` 両対応に拡張した (テスト用 stub もそのまま通る)。

### ADR との整合

これにより ADR-001 の「現時点の制約」は更新され、Docker が使える環境では HelixDB backend が完全に動作することを確認した。

### llama-server (LLM 本体) 疎通

WSL2 Linux 環境では llama-server が既定ポート `1067` で稼働中だった。`docs/LLMクライアントコード.md` および `src/state_aware_rag/llm.py` の `LlamaServerEnvConfig` の既定 `http://127.0.0.1:1067` がそのまま使える。

```bash
curl -s http://127.0.0.1:1067/health      # → {"status":"ok"}
curl -s http://127.0.0.1:1067/v1/models   # → Qwen3.6-27B-MTP-GGUF-UD-Q4_K_XL (gguf, n_ctx 131072, 27.3B params)
```

`LlamaServerEnvConfig.complete` を直接呼んだ結果:

```python
cfg = LlamaServerEnvConfig.from_env()
# base_url_no_v1 = http://127.0.0.1:1067
# model           = unsloth/Qwen3.6-27B-MTP-GGUF-UD-Q4_K_XL
asyncio.run(cfg.complete("...State-Aware RAG で最終回答に使うのは何ですか？"))
# → "State-Aware RAGでは、ドキュメントのメタデータや状態情報を考慮して..."
```

`--llm server --bosun rule --backend helix` の完全 end-to-end:

```bash
LLAMA_SERVER_MAX_TOKENS=2048 LLAMA_SERVER_TIMEOUT_SECONDS=180 \
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag \
  --backend helix --db tmp_helix_mirror.sqlite3 \
  ask "State-Aware RAG が最終回答に使うのは何ですか？" \
  --llm server --bosun rule --max-rounds 1
```

結果 (約 25 秒):

```text
State-Aware RAG は最終回答において、検索結果の原文を直接使わず、
**WorkingMemory に残った MemoryNote だけを根拠**とします。
出典: docs/sample.md
working_memory_id=wm_ac555b429a1546a0
status=stopped_by_max_rounds
```

retrieval (HelixDB) → plan (llama-server, JSON) → atomic note (llama-server, JSON) → final answer (llama-server, テキスト) のすべてが連動したことを確認した。

注意事項:

- `JsonLlamaPlannerAndWriter.plan` は Qwen3.6-27B-MTP の冗長な sub-question 出力で `LLAMA_SERVER_MAX_TOKENS=512` だと JSON が途中で切れて `RuntimeError("llama-server returned invalid JSON")` になる。プロジェクト既定値は `130000` に上げてある (`LlamaServerEnvConfig.from_env`、`docs/LLMクライアントコード.md`)。`Qwen3.6-27B-MTP` の `n_ctx=131072` に揃えてあり、prompt 分を引いた残り全部を generation に使う想定。短く制限したい場合だけ `LLAMA_SERVER_MAX_TOKENS` を明示的に下げる。
- Bosun XS は `--bosun xs` (= `--bosun-backend native` 相当) のみをサポートする。Qwen3.6-27B-MTP のような汎用 LLM の completion logprobs から `yes_id=9693 / no_id=2152` を読み出す経路は ADR-002 の方針に従い削除済み。Bosun XS 自体をスキップしたい開発検証では `--bosun rule` を使う。

HelixDB backend の contract test では、次の関係を dynamic query に含めることを確認済み。

```text
(:Document)-[:HAS_CHUNK]->(:Chunk)
(:Chunk)-[:MENTIONS]->(:Entity)
(:Question)-[:HAS_MEMORY]->(:WorkingMemory)
(:Evidence)-[:FROM_CHUNK]->(:Chunk)
(:WorkingMemory)-[:HAS_NOTE]->(:MemoryNote)
(:MemoryNote)-[:SUPPORTED_BY]->(:Evidence)
(:MemoryNote)-[:RELATED_TO]->(:Entity)
(:MemoryNote)-[:DUPLICATE_OF]->(:MemoryNote)
(:SearchRound)-[:RETURNED]->(:Evidence)
(:SearchRound)-[:UPDATED]->(:WorkingMemory)
```

さらに contract test で次の Helix graph search クエリ経路を確認済み。

```text
(:Entity)<-[:MENTIONS]-(:Chunk)
(:WorkingMemory)-[:HAS_NOTE]->(:MemoryNote)-[:SUPPORTED_BY]->(:Evidence)-[:FROM_CHUNK]->(:Chunk)
採用済み Evidence と同じ Document の前後 Chunk を SQLite mirror から補完
```

再実行手順 (Windows + Docker Desktop):

```powershell
.\.tools\helix.exe start dev --port 6969 --persist
uv run state-aware-rag --db .\helix_mirror.sqlite3 ingest .\docs\sample.md
uv run state-aware-rag --db .\helix_mirror.sqlite3 ask "この文書の要点は？" --llm server --bosun xs
```

再実行手順 (WSL2 Linux + Docker Engine):

```bash
cd helix_project && ../.tools/helix start --port 6969 dev && cd ..
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag \
  --db helix_mirror.sqlite3 ingest docs/sample.md
PYTHONUTF8=1 UV_LINK_MODE=copy uv run state-aware-rag \
  --db helix_mirror.sqlite3 ask "この文書の要点は？" --llm server --bosun xs
```
