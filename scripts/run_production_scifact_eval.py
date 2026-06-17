#!/usr/bin/env python3
"""SciFact 本番構成の検索 / RAG 評価スクリプト。"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from state_aware_rag.bosun import NativeBosunXSScorer, RuleBosunScorer
from state_aware_rag.config import RagConfig
from state_aware_rag.helix import HelixConfig
from state_aware_rag.helix_store import HelixBackedRagStore
from state_aware_rag.llm import JsonLlamaPlannerAndWriter, LocalHeuristicLLM
from state_aware_rag.orchestrator import StateAwareRag
from state_aware_rag.retrieval import Retriever
from state_aware_rag.store import SQLiteRagStore


def load_scifact(base: Path) -> tuple[dict[str, set[str]], dict[str, str], dict[str, dict]]:
    qrels: dict[str, set[str]] = defaultdict(set)
    with (base / "qrels/test.tsv").open(encoding="utf-8") as handle:
        next(handle)
        for line in handle:
            qid, doc_id, score = line.strip().split("\t")
            if int(score) > 0:
                qrels[qid].add(doc_id)
    queries: dict[str, str] = {}
    with (base / "queries.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            queries[row["_id"]] = row["text"]
    corpus: dict[str, dict] = {}
    with (base / "corpus.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            corpus[row["_id"]] = row
    return qrels, queries, corpus


def doc_id(uri: str) -> str:
    return uri.removeprefix("scifact:")


def recall_at_k(ranked_lists: list[list[str]], qrels: dict[str, set[str]], qids: list[str], k: int) -> float:
    hits = 0
    for qid, ranked in zip(qids, ranked_lists):
        if any(item in qrels[qid] for item in ranked[:k]):
            hits += 1
    return hits / len(qids) if qids else 0.0


def build_store(args: argparse.Namespace, config: RagConfig):
    if args.backend == "helix":
        return HelixBackedRagStore(
            args.db,
            config,
            helix_config=HelixConfig(base_url=args.helix_url, timeout_seconds=120),
        )
    return SQLiteRagStore(args.db, config)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run production SciFact evaluation")
    parser.add_argument("--data-dir", default="data/beir/scifact")
    parser.add_argument("--backend", choices=["sqlite", "helix"], default="helix")
    parser.add_argument("--helix-url", default="http://localhost:6969")
    parser.add_argument("--db", default="prod_scifact.sqlite3")
    parser.add_argument("--max-docs", type=int, default=50)
    parser.add_argument("--max-queries", type=int, default=10)
    parser.add_argument("--ask-queries", type=int, default=3)
    parser.add_argument("--llm", choices=["local", "server"], default="server")
    parser.add_argument("--bosun", choices=["xs", "rule"], default="xs")
    parser.add_argument("--skip-ingest", action="store_true")
    args = parser.parse_args()

    base = Path(args.data_dir)
    qrels, queries, corpus = load_scifact(base)
    sample_qids = sorted(qrels.keys(), key=int)[: args.max_queries]

    needed = {doc for qid in sample_qids for doc in qrels[qid]}
    extra = [doc_id for doc_id in list(corpus.keys())[:200] if doc_id not in needed]
    ingest_ids = list(needed) + extra[: max(0, args.max_docs - len(needed))]
    ingest_ids = ingest_ids[: args.max_docs]

    config = RagConfig(max_rounds=2)
    store = build_store(args, config)

    if not args.skip_ingest:
        t0 = time.time()
        for index, doc_id_value in enumerate(ingest_ids, start=1):
            row = corpus[doc_id_value]
            body = f"{row['title']}\n\n{row['text']}"
            store.ingest_document(
                title=row["title"],
                body=body,
                source_uri=f"scifact:{doc_id_value}",
                extract_entities=True,
            )
            if index % 10 == 0:
                print(f"ingested {index}/{len(ingest_ids)} elapsed={time.time() - t0:.0f}s")
        print(f"ingest done docs={len(ingest_ids)} chunks={len(store.list_chunks())}")

    retriever = Retriever(store, config)
    ranked = {
        "vector": [],
        "text": [],
        "hybrid": [],
    }
    for qid in sample_qids:
        query = queries[qid]
        if args.backend == "helix":
            vec = store.helix_vector_search(query, 10)  # type: ignore[attr-defined]
            txt = store.helix_text_search(query, 10)  # type: ignore[attr-defined]
        else:
            vec = retriever.vector_search(query, 10)
            txt = retriever.text_search(query, 10)
        merged = retriever.merge_candidates(vec + txt)
        ranked["vector"].append([doc_id(c.source_uri) for c in vec])
        ranked["text"].append([doc_id(c.source_uri) for c in txt])
        ranked["hybrid"].append([doc_id(c.source_uri) for c in merged])

    print("\n=== Retrieval ===")
    for name, lists in ranked.items():
        for k in (1, 5, 10):
            print(f"{name} Recall@{k}: {recall_at_k(lists, qrels, sample_qids, k):.3f}")

    bosun = NativeBosunXSScorer() if args.bosun == "xs" else RuleBosunScorer()
    llm = JsonLlamaPlannerAndWriter() if args.llm == "server" else LocalHeuristicLLM()
    rag = StateAwareRag(store=store, config=config, bosun=bosun, llm=llm)

    print("\n=== RAG ask ===")
    for qid in sample_qids[: args.ask_queries]:
        query = queries[qid]
        rel = sorted(qrels[qid])
        print(f"\nqid={qid} relevant={rel}")
        print(f"Q: {query}")
        t0 = time.time()
        result = rag.answer(query)
        elapsed = time.time() - t0
        src_docs = sorted({doc_id(e.source_uri) for e in result.evidence})
        print(
            f"elapsed={elapsed:.0f}s status={result.working_memory.status.value} "
            f"notes={len(result.memory_notes)} evidence={len(result.evidence)} "
            f"relevant_hit={any(d in rel for d in src_docs)}"
        )
        print(f"answer: {result.answer[:300]}...")
    print("\nSee logs/rag_events.jsonl and logs/rag_session_*.xlsx for route details.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
