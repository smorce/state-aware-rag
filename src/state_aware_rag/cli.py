from __future__ import annotations

import argparse
import sys
from pathlib import Path

from state_aware_rag.bosun import NativeBosunXSScorer, RuleBosunScorer
from state_aware_rag.config import RagConfig
from state_aware_rag.helix import helix_startup_hint
from state_aware_rag.helix_store import HelixBackedRagStore
from state_aware_rag.llm import JsonLlamaPlannerAndWriter, LocalHeuristicLLM
from state_aware_rag.orchestrator import StateAwareRag
from state_aware_rag.store import SQLiteRagStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="state-aware-rag")
    parser.add_argument("--db", default="rag.sqlite3", help="SQLite database path")
    parser.add_argument("--backend", choices=["sqlite", "helix"], default="helix")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="Ingest a text or markdown file")
    ingest.add_argument("path", help="Input file path")
    ingest.add_argument("--title", help="Document title")
    ingest.add_argument("--source-uri", help="Source URI")
    ingest.add_argument("--chunk-size", type=int, default=700)
    ingest.add_argument("--no-extract", action="store_true", help="Skip entity/relation extraction during ingest")
    ingest.add_argument("--extractor", choices=["rule", "llm"], default="rule", help="Entity extractor backend")

    extract = sub.add_parser("extract", help="Run entity/relation extraction on stored chunks")
    extract.add_argument("--document-id", help="Limit extraction to one document")
    extract.add_argument("--extractor", choices=["rule", "llm"], default="rule", help="Entity extractor backend")

    ask = sub.add_parser("ask", help="Answer a question from working memory")
    ask.add_argument("question")
    ask.add_argument("--max-rounds", type=int, default=3)
    ask.add_argument("--llm", choices=["local", "server"], default="server")
    ask.add_argument("--bosun", choices=["xs", "rule"], default="xs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = RagConfig(max_rounds=getattr(args, "max_rounds", 3))
    try:
        store = _build_store(args, config)
    except RuntimeError as exc:
        if getattr(args, "backend", "helix") == "helix":
            print(f"HelixDB backend is not available: {exc}", file=sys.stderr)
            print(helix_startup_hint(), file=sys.stderr)
            return 2
        raise
    bosun = _build_bosun(args)
    llm = _build_llm(args)
    rag = StateAwareRag(store=store, config=config, bosun=bosun, llm=llm)

    if args.command == "ingest":
        path = Path(args.path)
        body = path.read_text(encoding="utf-8")
        result = rag.ingest_document(
            title=args.title or path.stem,
            body=body,
            source_uri=args.source_uri or str(path),
            chunk_size=args.chunk_size,
            extract_entities=not args.no_extract,
            extractor_backend=args.extractor,
        )
        print(f"ingested document_id={result.document.id} chunks={len(result.chunks)}")
        return 0

    if args.command == "extract":
        chunk_ids = None
        if args.document_id:
            chunk_ids = [
                chunk.id
                for chunk in store.list_chunks()
                if chunk.document_id == args.document_id
            ]
        processed = store.extract_chunks(chunk_ids, extractor_backend=args.extractor)
        print(f"extracted chunks={processed}")
        return 0

    if args.command == "ask":
        result = rag.answer(args.question)
        print(result.answer)
        print(f"\nworking_memory_id={result.working_memory.id}")
        print(f"status={result.working_memory.status.value}")
        return 0

    raise RuntimeError("Unknown command")


def _build_bosun(args: argparse.Namespace) -> RuleBosunScorer:
    if getattr(args, "bosun", "xs") != "xs":
        return RuleBosunScorer()
    return NativeBosunXSScorer()


def _build_store(args: argparse.Namespace, config: RagConfig):
    if getattr(args, "backend", "helix") == "helix":
        return HelixBackedRagStore(args.db, config)
    return SQLiteRagStore(args.db, config)


def _build_llm(args: argparse.Namespace):
    if getattr(args, "llm", "server") == "local":
        return LocalHeuristicLLM()
    return JsonLlamaPlannerAndWriter()


if __name__ == "__main__":
    raise SystemExit(main())
