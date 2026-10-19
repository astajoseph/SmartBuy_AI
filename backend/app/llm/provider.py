"""LLM provider abstraction. The application depends only on `LLMProvider.generate()`;
swap providers with LLM_PROVIDER (gemini | none). 'none' disables the LLM and the assistant
falls back to a deterministic template explanation."""
from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    async def generate(self, system: str, user: str, *, max_tokens: int = 2048) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str, timeout: float):
        from google import genai  # imported lazily so the package is optional for other providers

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.model = model
        self.timeout = timeout

    async def generate(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        types = self._genai.types
        # No model-side "thinking": the backend has already done the reasoning; the model only explains it.
        # (Thinking tokens also count against max_output_tokens and were truncating answers.)
        cfg = types.GenerateContentConfig(system_instruction=system, temperature=0.2, max_output_tokens=max_tokens,
                                          thinking_config=types.ThinkingConfig(thinking_budget=0))
        try:
            resp = await asyncio.wait_for(
                self.client.aio.models.generate_content(model=self.model, contents=user, config=cfg), self.timeout)
        except asyncio.TimeoutError as e:
            raise LLMError("LLM request timed out") from e
        except Exception as e:  # noqa: BLE001 - SDK raises several error types; never leak keys
            raise LLMError(f"LLM request failed: {e.__class__.__name__}") from e
        text = (resp.text or "").strip()
        if not text:
            raise LLMError("LLM returned an empty response")
        finish = str(getattr((resp.candidates or [None])[0], "finish_reason", "") or "")
        if "MAX_TOKENS" in finish:
            raise LLMError("LLM answer was cut off (output limit reached)")
        return text


class NullProvider:
    name = "none"

    async def generate(self, system: str, user: str, *, max_tokens: int = 2048) -> str:
        raise LLMError("No LLM provider configured")


def get_provider(settings: Settings | None = None) -> LLMProvider:
    s = settings or get_settings()
    if s.llm_provider == "gemini" and s.gemini_api_key:
        return GeminiProvider(s.gemini_api_key.get_secret_value(), s.gemini_model, s.llm_timeout_seconds)
    if s.llm_provider == "gemini":
        log.warning("LLM_PROVIDER=gemini but GEMINI_API_KEY is not set; using template explanations")
    return NullProvider()
