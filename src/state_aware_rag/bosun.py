from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from state_aware_rag.models import MemoryNote, RetrievalCandidate
from state_aware_rag.text import normalize_claim, overlap_score, tokenize


class RuleBosunScorer:
    def relevance_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        q_overlap = overlap_score(question, candidate.body)
        wm_overlap = overlap_score(working_memory_summary, candidate.body) if working_memory_summary else 0.0
        method_bonus = 0.15 if len(candidate.retrieval_methods) > 1 else 0.05
        if any(token in tokenize(candidate.body) for token in tokenize(question)):
            q_overlap += 0.25
        return min(1.0, 0.35 + q_overlap * 1.4 + wm_overlap * 0.4 + method_bonus)

    def memory_value_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        relevance = self.relevance_score(question, working_memory_summary, candidate)
        duplicate_penalty = overlap_score(working_memory_summary, candidate.body) if working_memory_summary else 0.0
        concreteness = min(0.25, len(tokenize(candidate.body)) / 120.0)
        return max(0.0, min(1.0, relevance + concreteness - duplicate_penalty * 0.45))

    def duplicate_score(self, existing_note: MemoryNote, new_claim: str) -> float:
        existing = existing_note.normalized_claim
        incoming = normalize_claim(new_claim)
        if not existing or not incoming:
            return 0.0
        if existing == incoming:
            return 1.0
        return overlap_score(existing.split(), incoming.split())

    def conflict_score(self, existing_note: MemoryNote, new_claim: str) -> float:
        left = set(tokenize(existing_note.claim))
        right = set(tokenize(new_claim))
        if not left or not right:
            return 0.0
        same_subject = len(left & right) / max(1, min(len(left), len(right)))
        negative_pairs = [
            ("is", "is not"),
            ("can", "cannot"),
            ("must", "must not"),
            ("true", "false"),
            ("enabled", "disabled"),
            ("ある", "ない"),
            ("する", "しない"),
        ]
        left_text = existing_note.claim.lower()
        right_text = new_claim.lower()
        has_negation_flip = any((a in left_text and b in right_text) or (b in left_text and a in right_text) for a, b in negative_pairs)
        if same_subject >= 0.45 and has_negation_flip:
            return 0.75
        return 0.0


@dataclass(frozen=True)
class BosunXSServingConfig:
    prefix: str = (
        "<|im_start|>system\n"
        'Judge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
        "<|im_end|>\n<|im_start|>user\n"
    )
    suffix: str = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    yes_id: int = 9693
    no_id: int = 2152
    max_len: int = 3072

    @classmethod
    def from_env(cls) -> "BosunXSServingConfig":
        path = os.getenv("BOSUN_SERVING_JSON")
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            prefix=str(data.get("prefix", cls.prefix)),
            suffix=str(data.get("suffix", cls.suffix)),
            yes_id=int(data.get("yes_id", cls.yes_id)),
            no_id=int(data.get("no_id", cls.no_id)),
            max_len=int(data.get("max_len", cls.max_len)),
        )


@dataclass(frozen=True)
class NativeBosunXSConfig:
    repo: str = "Hanno-Labs/bosun-xs"
    base_model: str = "Qwen/Qwen3-Reranker-0.6B"
    device: str | None = None
    query: str = "These two findings share the specified relationship."
    serving: BosunXSServingConfig | None = None


class NativeBosunXSScorer(RuleBosunScorer):
    """Bosun XS を Transformers + PEFT で in-process に動かす scorer。

    別 llama-server に Bosun XS GGUF を載せて completion logprobs を読む経路は
    現プロジェクトでは原則使わないため削除済み。yes/no logits は最終位置から
    `logits_to_keep=1` で直接取り、`sigmoid(logit_yes - logit_no)` を返す。
    """

    def __init__(self, config: NativeBosunXSConfig | None = None) -> None:
        self.native_config = config or NativeBosunXSConfig()
        self.serving = self.native_config.serving or BosunXSServingConfig.from_env()
        self._runtime: tuple[Any, Any, Any] | None = None

    def score_rule(self, instruct: str, query: str, document: str) -> float:
        prompt = self._prompt(instruct, query, document)
        return asyncio.run(self._score_prompt(prompt))

    async def _score_prompt(self, prompt: str) -> float:
        def _run() -> float:
            tok, model, torch = self._load_runtime()
            encoded = tok(
                prompt,
                return_tensors="pt",
                truncation=True,
                max_length=self.serving.max_len,
                padding=False,
            )
            device = next(model.parameters()).device
            encoded = {key: value.to(device) for key, value in encoded.items()}
            with torch.no_grad():
                logits = model(**encoded, logits_to_keep=1).logits[:, -1, :]
            return _score_from_logits(logits[0], self.serving.yes_id, self.serving.no_id, torch)

        return await asyncio.to_thread(_run)

    def relevance_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        document = build_pair_document(f"Question:\n{question}\n\nWorking Memory:\n{working_memory_summary}", candidate.body)
        return self.score_rule(
            "This candidate contains concrete evidence useful for answering the question.",
            self.native_config.query,
            document,
        )

    def memory_value_score(self, question: str, working_memory_summary: str, candidate: RetrievalCandidate) -> float:
        memory = working_memory_summary.strip() or "(empty working memory)"
        document = build_pair_document(f"Question:\n{question}\n\nCurrent working memory:\n{memory}", candidate.body)
        return self.score_rule(
            "The second finding adds a new concrete fact that advances the working memory for the question.",
            self.native_config.query,
            document,
        )

    def duplicate_score(self, existing_note: MemoryNote, new_claim: str) -> float:
        return self.score_rule(
            "The two findings state substantially the same fact.",
            self.native_config.query,
            build_pair_document(existing_note.claim, new_claim),
        )

    def conflict_score(self, existing_note: MemoryNote, new_claim: str) -> float:
        return self.score_rule(
            "The two findings make claims about the same subject that cannot both be true at the same time.",
            self.native_config.query,
            build_pair_document(existing_note.claim, new_claim),
        )

    def _prompt(self, instruct: str, query: str, document: str) -> str:
        body = f"<Instruct>: {instruct}\n<Query>: {query}\n<Document>: {document}"
        return f"{self.serving.prefix}{body}{self.serving.suffix}"

    def _load_runtime(self) -> tuple[Any, Any, Any]:
        if self._runtime is not None:
            return self._runtime
        try:
            import torch
            from peft import PeftModel
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Native Bosun XS backend requires torch, transformers, and peft. "
                'Install them via `uv add ".[bosun-native]" --link-mode=copy`.'
            ) from exc

        repo = os.getenv("BOSUN_REPO", self.native_config.repo)
        base_repo = os.getenv("BOSUN_BASE_MODEL", self.native_config.base_model)
        device_name = self.native_config.device or os.getenv("BOSUN_DEVICE")
        if not device_name:
            device_name = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device_name == "cuda" else torch.float32
        tok = AutoTokenizer.from_pretrained(repo, subfolder="tokenizer", padding_side="left")
        base = AutoModelForCausalLM.from_pretrained(
            base_repo,
            torch_dtype=dtype,
            attn_implementation="sdpa",
            trust_remote_code=True,
        )
        model = PeftModel.from_pretrained(base, repo).merge_and_unload().eval().to(device_name)
        self._runtime = (tok, model, torch)
        return self._runtime


def build_pair_document(finding_a: str, finding_b: str) -> str:
    return f"FINDING A:\n{finding_a}\n\nFINDING B:\n{finding_b}"


def _score_from_logits(logits: Any, yes_id: int, no_id: int, torch: Any) -> float:
    score = torch.sigmoid(logits[yes_id] - logits[no_id])
    return float(score.detach().cpu().item())
