from __future__ import annotations

import os
from typing import Literal, Optional, Protocol

from state_aware_rag.text import hashed_embedding


RuriMode = Literal["semantic", "topic", "query", "document"]

_RURI_PREFIX_MAP: dict[RuriMode, str] = {
    "semantic": "",
    "topic": "トピック: ",
    "query": "検索クエリ: ",
    "document": "検索文書: ",
}


class Embedder(Protocol):
    """埋め込み実装の共通インターフェース。

    document / query / claim で使い分けるのは、Ruri v3 の 1+3 プレフィックス方式
    （`検索文書: ` `検索クエリ: ` など）にそのまま乗せるためである。
    """

    @property
    def dimensions(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def embed_claim(self, text: str) -> list[float]: ...


class HashedEmbedder:
    """SHA256 hashing trick の軽量埋め込み。LLM/ML を持たない環境のフォールバック。"""

    def __init__(self, dimensions: int = 128) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [hashed_embedding(text, self._dimensions) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return hashed_embedding(text, self._dimensions)

    def embed_claim(self, text: str) -> list[float]:
        return hashed_embedding(text, self._dimensions)


class RuriEmbedder:
    """cl-nagoya/ruri-v3-310m を sentence-transformers でラップした実装。

    モデルカードに従い、document/query/topic/semantic で文頭プレフィックスを変える。
    GPU は既定で `cuda`（`RURI_DEVICE=cuda`）。PyTorch は CUDA 12.4 ビルド（cu124）を使用し、
    ドライバ CUDA 12.0 系と互換です。CPU のみの場合は `RURI_DEVICE=cpu`。
    """

    def __init__(
        self,
        *,
        model_name: str = "cl-nagoya/ruri-v3-310m",
        device: Optional[str] = None,
        batch_size: int = 64,
        normalize: bool = True,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.normalize = normalize
        self._model: object | None = None
        self._dimensions: int | None = None

    def _resolve_device(self) -> str:
        requested = (self.device or os.getenv("RURI_DEVICE", "cuda")).strip().lower()
        if requested == "cpu":
            return "cpu"
        if requested not in {"cuda", "gpu"}:
            raise ValueError(f"Unsupported RURI_DEVICE: {requested!r}. Use cuda or cpu.")
        try:
            import torch  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "RuriEmbedder requires `torch`. Run `uv sync` to install dependencies."
            ) from exc
        if not torch.cuda.is_available():
            raise RuntimeError(
                "RURI_DEVICE is cuda but CUDA is not available. "
                "Install a compatible NVIDIA driver or set RURI_DEVICE=cpu."
            )
        try:
            torch.zeros(1, device="cuda")
        except Exception as exc:
            raise RuntimeError(
                f"RURI_DEVICE is cuda but GPU initialization failed: {exc}"
            ) from exc
        return "cuda"

    def _ensure_model(self) -> object:
        if self._model is None:
            try:
                import torch  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "RuriEmbedder requires `torch`. Run `uv sync` to install dependencies."
                ) from exc
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "RuriEmbedder requires `sentence-transformers`. "
                    "Run `uv sync` to install dependencies."
                ) from exc
            device = self._resolve_device()
            model = SentenceTransformer(self.model_name, device=device)
            get_dim = getattr(model, "get_embedding_dimension", model.get_sentence_embedding_dimension)
            dim = int(get_dim() or 0)
            if dim <= 0:
                raise RuntimeError(
                    f"Failed to read embedding dimension from {self.model_name}"
                )
            self._model = model
            self._dimensions = dim
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._ensure_model()
        assert self._dimensions is not None
        return self._dimensions

    def _encode(self, texts: list[str], mode: RuriMode) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        prefix = _RURI_PREFIX_MAP[mode]
        inputs = [prefix + text for text in texts]
        arr = model.encode(  # type: ignore[attr-defined]
            inputs,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self.normalize,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in arr]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts, "document")

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text], "query")[0]

    def embed_claim(self, text: str) -> list[float]:
        return self._encode([text], "document")[0]


def build_embedder(backend: str, *, dimensions: int = 128, device: str | None = None) -> Embedder:
    name = (backend or "").strip().lower()
    if name in {"hashed"}:
        return HashedEmbedder(dimensions=dimensions)
    if name in {"", "auto", "default", "ruri"}:
        resolved_device = device or os.getenv("RURI_DEVICE")
        return RuriEmbedder(device=resolved_device)
    raise ValueError(f"Unknown embedding backend: {backend}")


def default_embedder(dimensions: int = 128) -> Embedder:
    backend = os.getenv("EMBEDDING_BACKEND", "ruri")
    return build_embedder(backend, dimensions=dimensions)
