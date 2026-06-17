# RAG テスト用データ

## BEIR SciFact

配置先:

```text
data/beir/scifact/
```

取得元:

```text
https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip
```

内容:

- `corpus.jsonl`: 5,183 documents
- `queries.jsonl`: 1,109 queries
- `qrels/train.tsv`: 919 relevance rows
- `qrels/test.tsv`: 339 relevance rows

SciFact は科学 claim verification 系の検索データセットで、RAG の検索候補生成、根拠採点、最終回答の根拠制約を検査しやすい。BEIR 形式なので、retrieval の評価では `corpus` / `queries` / `qrels` をそのまま使える。

## 形式

`corpus.jsonl`:

```json
{"_id": "doc id", "title": "title", "text": "document body", "metadata": {}}
```

`queries.jsonl`:

```json
{"_id": "query id", "text": "claim or query", "metadata": {}}
```

`qrels/*.tsv`:

```text
query-id	corpus-id	score
```
