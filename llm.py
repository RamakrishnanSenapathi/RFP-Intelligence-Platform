"""Gemini (Google AI Studio) wrapper: rate limiting, retries, JSON-mode with Pydantic validation."""
from __future__ import annotations

import json
import logging
import threading
import time
from functools import lru_cache
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from config import Settings, get_settings
from observability import record_llm
from utils import extract_json_text

log = logging.getLogger("rfp.llm")
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class RateLimiter:
    """Thread-safe minimum-interval limiter (shared by parallel agents)."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(int(rpm), 1)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


class GeminiLLM:
    def __init__(self, settings: Settings):
        if not settings.google_api_key:
            raise LLMError("GOOGLE_API_KEY is not set. Copy .env.example to .env and add your Google AI Studio key.")
        from google import genai
        from google.genai import types

        self._types = types
        self.settings = settings
        self.model = settings.llm_model
        self.client = genai.Client(
            api_key=settings.google_api_key,
            http_options=types.HttpOptions(timeout=settings.llm_timeout_s * 1000),
        )
        self.limiter = RateLimiter(settings.llm_rpm)

    def generate_text(self, prompt: str, system: Optional[str] = None, temperature: float = 0.0,
                      json_mode: bool = False) -> str:
        types = self._types
        kw: dict = {"temperature": temperature}
        if system:
            kw["system_instruction"] = system
        if json_mode:
            kw["response_mime_type"] = "application/json"
        if self.settings.thinking_budget is not None:
            kw["thinking_config"] = types.ThinkingConfig(thinking_budget=self.settings.thinking_budget)
        config = types.GenerateContentConfig(**kw)

        last: Optional[Exception] = None
        retries = self.settings.llm_max_retries
        for attempt in range(retries + 1):
            self.limiter.wait()
            t0 = time.perf_counter()
            try:
                resp = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                text = resp.text or ""
                usage = getattr(resp, "usage_metadata", None)
                record_llm(getattr(usage, "prompt_token_count", 0) or 0,
                           getattr(usage, "candidates_token_count", 0) or 0,
                           self.model, time.perf_counter() - t0)
                if not text.strip():
                    raise LLMError("empty response")
                return text
            except Exception as e:  # noqa: BLE001
                last = e
                msg = str(e)
                quota = any(s in msg for s in ("429", "RESOURCE_EXHAUSTED")) or "quota" in msg.lower()
                delay = 20 * (attempt + 1) if quota else 2 ** (attempt + 1)
                log.warning("Gemini call failed (attempt %d/%d): %s", attempt + 1, retries + 1, msg[:200])
                if attempt < retries:
                    time.sleep(delay)
        raise LLMError(f"Gemini call failed after {retries + 1} attempts: {last!r}")

    def generate_json(self, prompt: str, schema: Type[T], system: Optional[str] = None,
                      temperature: float = 0.0) -> T:
        hint = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        full = f"{prompt}\n\nReturn ONLY valid JSON (no markdown fences, no commentary) matching this JSON schema:\n{hint}"
        last_err = ""
        for i in range(3):
            p = full if i == 0 else f"{full}\n\nYour previous reply was invalid ({last_err}). Return corrected, valid JSON only."
            text = self.generate_text(p, system=system, temperature=temperature, json_mode=True)
            try:
                return schema.model_validate(json.loads(extract_json_text(text)))
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                last_err = str(e)[:250]
                log.warning("Bad JSON from LLM (try %d): %s", i + 1, last_err)
        raise LLMError(f"LLM returned invalid JSON after 3 tries: {last_err}")


@lru_cache(maxsize=1)
def get_llm() -> GeminiLLM:
    return GeminiLLM(get_settings())
