# 使用例:
# async def main():
#     cfg = LlamaServerEnvConfig.from_env()
#     text = await cfg.complete("Hello")
#     print(text)
# asyncio.run(main())

# call_llama_server.py
# OpenAI 互換の llama-server へ chat.completions を送る最小モジュール

from __future__ import annotations

import asyncio
import os
import random
from dataclasses import dataclass
from typing import Any, Optional


def _ensure_openai_v1_base_url(raw: str) -> str:
    value = (raw or "").strip() or "http://127.0.0.1:1067"
    if "://" not in value:
        value = f"http://{value}"
    value = value.rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def _without_v1_suffix(base_url: str) -> str:
    v = _ensure_openai_v1_base_url(base_url)
    return v[:-3] if v.endswith("/v1") else v


def _get_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _get_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _should_retry(exc: Exception) -> bool:
    try:
        from openai import APIConnectionError, APIStatusError, RateLimitError
    except Exception:
        APIConnectionError = APIStatusError = RateLimitError = None  # type: ignore[assignment]

    if APIConnectionError and isinstance(exc, APIConnectionError):
        return True
    if RateLimitError and isinstance(exc, RateLimitError):
        return True
    if APIStatusError and isinstance(exc, APIStatusError):
        return bool(getattr(exc, "status_code", 0) >= 500)
    if isinstance(exc, ConnectionError):
        return True
    return False


async def call_llama_server(
    client: Any,
    model: str,
    prompt: str,
    *,
    enable_thinking: bool,
    temperature: Optional[float],
    top_p: Optional[float],
    top_k: Optional[int],
    min_p: Optional[float],
    max_tokens: int,
    timeout_seconds: float,
    max_retries: int,
    retry_base_delay_seconds: float,
    retry_max_delay_seconds: float,
) -> str:
    """OpenAI 互換クライアントで 1 回の Responses API（同期 API をスレッド実行）。"""

    def _run() -> str:
        if enable_thinking:
            payload = {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0.0,
                "chat_template_kwargs": {"enable_thinking": True},
            }
        else:
            payload = {
                "temperature": 0.7,
                "top_p": 0.8,
                "top_k": 20,
                "min_p": 0.0,
                "chat_template_kwargs": {"enable_thinking": False},
            }

        opts = {
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "min_p": min_p,
        }

        extra_body = {
            k: (v if v is not None else payload.get(k))
            for k, v in opts.items()
        }
        extra_body["chat_template_kwargs"] = payload["chat_template_kwargs"]

        # llama_server は OpenAI 互換の API を提供しているので、OpenAI クライアントを使用して Responses API を呼び出すことができます
        response = client.responses.create(
            model=model,
            input=prompt,
            temperature=extra_body["temperature"],
            top_p=extra_body["top_p"],
            max_output_tokens=max_tokens,
            extra_body={
                "top_k": extra_body["top_k"],
                "min_p": extra_body["min_p"],
                "chat_template_kwargs": extra_body["chat_template_kwargs"],
            },
        )

        return response.output_text or ""

    attempts = max(1, max_retries)
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.wait_for(asyncio.to_thread(_run), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            last_exc = RuntimeError("llama-server request timeout")
            retryable = True
        except Exception as exc:
            last_exc = exc
            retryable = _should_retry(exc)

        if (not retryable) or attempt >= attempts:
            break

        exp_backoff = retry_base_delay_seconds * (2 ** (attempt - 1))
        sleep_seconds = min(exp_backoff, retry_max_delay_seconds) + random.uniform(0, 0.2)
        await asyncio.sleep(sleep_seconds)

    if last_exc is None:
        raise RuntimeError("llama-server request failed")
    raise RuntimeError(f"llama-server request failed after retries: {last_exc}") from last_exc


@dataclass
class LlamaServerEnvConfig:
    """llama-server 用環境変数のみ（OLLAMA_* は使わない）。"""

    base_url_no_v1: str
    api_key: str
    model: str
    enable_thinking: bool
    temperature: float
    top_p: Optional[float]
    top_k: Optional[int]
    min_p: Optional[float]
    max_tokens: int
    timeout_seconds: float
    max_retries: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float

    @classmethod
    def from_env(cls) -> LlamaServerEnvConfig:
        resolved = os.getenv("LLAMA_SERVER_BASE_URL", "http://127.0.0.1:1067")
        base_no_v1 = _without_v1_suffix(resolved)
        return cls(
            base_url_no_v1=base_no_v1,
            api_key=os.getenv("LLAMA_SERVER_API_KEY", "sk-local-no-key-required"),
            model=os.getenv("LLM_MODEL", "unsloth/Qwen3.6-27B-MTP-GGUF-UD-Q4_K_XL"),
            enable_thinking=os.getenv("LLAMA_SERVER_ENABLE_THINKING", "false").lower()
            in ("true", "1", "yes"),
            temperature=float(os.getenv("LLAMA_SERVER_TEMPERATURE", "0.3")),
            top_p=_get_optional_float(os.getenv("LLAMA_SERVER_TOP_P")),
            top_k=_get_optional_int(os.getenv("LLAMA_SERVER_TOP_K")),
            min_p=_get_optional_float(os.getenv("LLAMA_SERVER_MIN_P")),
            max_tokens=int(os.getenv("LLAMA_SERVER_MAX_TOKENS", "130000")),
            timeout_seconds=float(os.getenv("LLAMA_SERVER_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("LLAMA_SERVER_MAX_RETRIES", "5")),
            retry_base_delay_seconds=float(os.getenv("LLAMA_SERVER_RETRY_BASE_SECONDS", "0.8")),
            retry_max_delay_seconds=float(os.getenv("LLAMA_SERVER_RETRY_MAX_SECONDS", "8.0")),
        )

    def build_openai_client(self):
        from openai import OpenAI

        return OpenAI(
            base_url=_ensure_openai_v1_base_url(self.base_url_no_v1),
            api_key=self.api_key,
            timeout=self.timeout_seconds,
            max_retries=0,
        )

    async def complete(self, prompt: str) -> str:
        client = self.build_openai_client()
        return await call_llama_server(
            client,
            self.model,
            prompt,
            enable_thinking=self.enable_thinking,
            temperature=self.temperature,
            top_p=self.top_p,
            top_k=self.top_k,
            min_p=self.min_p,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            max_retries=self.max_retries,
            retry_base_delay_seconds=self.retry_base_delay_seconds,
            retry_max_delay_seconds=self.retry_max_delay_seconds,
        )