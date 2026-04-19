"""
llm_client — LLM 어댑터 (OpenAI / Anthropic / Ollama)

mailenrich 가 사용하는 LLM 호출 추상화 레이어.
모든 어댑터는 LLMClient Protocol 을 구현하며, JSON 구조화 응답을 반환한다.

사용 예:
    from scripts.lib.llm_client import get_client, LLMRequest

    client = get_client(cfg)
    resp = client.complete(LLMRequest(system="...", user="..."))
    parsed = json.loads(resp.text)
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# 요청 / 응답 데이터클래스
# ---------------------------------------------------------------------------

@dataclass
class LLMRequest:
    """LLM 완성 요청."""

    system: str
    user: str
    max_tokens: int = 1024
    temperature: float = 0.2
    schema: dict[str, Any] | None = None


@dataclass
class LLMResponse:
    """LLM 완성 응답."""

    text: str
    """JSON 문자열 (structured output)."""

    input_tokens: int
    output_tokens: int
    model: str


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """LLM 클라이언트 인터페이스."""

    def complete(self, req: LLMRequest) -> LLMResponse: ...


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


def _backoff(attempt: int) -> None:
    """지수 백오프: attempt 0 → 1s, 1 → 2s, 2 → 4s."""
    time.sleep(min(2 ** attempt, 16))


def _build_httpx_client(cfg: dict[str, Any]):
    """httpx.Client 를 반환한다. 임포트는 호출 시점으로 지연."""
    try:
        import httpx
    except ImportError as exc:
        raise ImportError(
            "httpx 가 설치되어 있지 않습니다. pip install httpx"
        ) from exc
    timeout = cfg.get("llm", {}).get("timeout", 60)
    return httpx.Client(timeout=timeout)


# ---------------------------------------------------------------------------
# OpenAI 어댑터
# ---------------------------------------------------------------------------

class OpenAIClient:
    """OpenAI 호환 API 어댑터 (POST /chat/completions)."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        llm = cfg.get("llm", {})
        self._endpoint = llm.get("endpoint", "https://api.openai.com/v1").rstrip("/")
        self._model = llm.get("model", "gpt-4o-mini")
        self._max_retries = llm.get("max_retries", 3)
        self._token = _resolve_token(llm)
        self._http = _build_httpx_client(cfg)

    def complete(self, req: LLMRequest) -> LLMResponse:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "response_format": {"type": "json_object"},
        }

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._http.post(
                    f"{self._endpoint}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in _RETRY_STATUSES:
                    last_exc = ValueError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    _backoff(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=choice["message"]["content"],
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    model=data.get("model", self._model),
                )
            except Exception as exc:  # noqa: BLE001 — timeout / network / parse
                last_exc = exc
                _backoff(attempt)

        raise RuntimeError(
            f"OpenAI 호출 {self._max_retries}회 실패: {last_exc}"
        ) from last_exc


# ---------------------------------------------------------------------------
# Anthropic 어댑터
# ---------------------------------------------------------------------------

_ENRICH_TOOL = {
    "name": "enrich_mail",
    "description": "메일 enrichment 결과를 구조화된 JSON 으로 반환한다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "한 문단 요약"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "의미 태그 목록",
            },
            "related": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "thread": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["thread", "reason"],
                },
                "description": "관련 스레드 목록",
            },
        },
        "required": ["summary", "tags", "related"],
    },
}


class AnthropicClient:
    """Anthropic Messages API 어댑터 (tool-use 강제)."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        llm = cfg.get("llm", {})
        self._endpoint = llm.get("endpoint", "https://api.anthropic.com").rstrip("/")
        self._model = llm.get("model", "claude-haiku-4-5-20251001")
        self._max_retries = llm.get("max_retries", 3)
        self._token = _resolve_token(llm)
        self._http = _build_httpx_client(cfg)

    def complete(self, req: LLMRequest) -> LLMResponse:
        headers = {
            "x-api-key": self._token,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": req.max_tokens,
            "system": req.system,
            "messages": [{"role": "user", "content": req.user}],
            "tools": [_ENRICH_TOOL],
            "tool_choice": {"type": "tool", "name": "enrich_mail"},
        }

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._http.post(
                    f"{self._endpoint}/v1/messages",
                    headers=headers,
                    json=payload,
                )
                if resp.status_code in _RETRY_STATUSES:
                    last_exc = ValueError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    _backoff(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()

                # tool_use 블록에서 input 추출 → JSON 직렬화
                tool_input: dict[str, Any] = {}
                for block in data.get("content", []):
                    if block.get("type") == "tool_use":
                        tool_input = block.get("input", {})
                        break

                usage = data.get("usage", {})
                return LLMResponse(
                    text=json.dumps(tool_input, ensure_ascii=False),
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model=data.get("model", self._model),
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _backoff(attempt)

        raise RuntimeError(
            f"Anthropic 호출 {self._max_retries}회 실패: {last_exc}"
        ) from last_exc


# ---------------------------------------------------------------------------
# Ollama 어댑터
# ---------------------------------------------------------------------------

class OllamaClient:
    """Ollama 로컬 API 어댑터 (POST /api/chat, format: json)."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        llm = cfg.get("llm", {})
        self._endpoint = llm.get("endpoint", "http://localhost:11434").rstrip("/")
        self._model = llm.get("model", "llama3.1:8b")
        self._max_retries = llm.get("max_retries", 3)
        self._http = _build_httpx_client(cfg)

    def complete(self, req: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": req.system},
                {"role": "user", "content": req.user},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "num_predict": req.max_tokens,
                "temperature": req.temperature,
            },
        }

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._http.post(
                    f"{self._endpoint}/api/chat",
                    json=payload,
                )
                if resp.status_code in _RETRY_STATUSES:
                    last_exc = ValueError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    _backoff(attempt)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = data.get("message", {}).get("content", "{}")
                usage = data.get("usage", {})
                return LLMResponse(
                    text=content,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    model=data.get("model", self._model),
                )
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                _backoff(attempt)

        raise RuntimeError(
            f"Ollama 호출 {self._max_retries}회 실패: {last_exc}"
        ) from last_exc


# ---------------------------------------------------------------------------
# 팩토리
# ---------------------------------------------------------------------------

def _resolve_token(llm_cfg: dict[str, Any]) -> str:
    """LLM_TOKEN env → config token 순으로 토큰을 반환한다."""
    import os
    env = os.environ.get("LLM_TOKEN", "").strip()
    if env:
        return env
    return llm_cfg.get("token", "")


def get_client(cfg: dict[str, Any]) -> LLMClient:
    """provider 설정에 따라 적합한 LLMClient 를 반환한다.

    Args:
        cfg: load_config() 결과 (cfg["llm"]["provider"] 키 사용).

    Returns:
        LLMClient 구현체 인스턴스.

    Raises:
        ValueError: 지원하지 않는 provider 이름.
    """
    provider = cfg.get("llm", {}).get("provider", "openai").lower()
    if provider == "openai":
        return OpenAIClient(cfg)
    if provider == "anthropic":
        return AnthropicClient(cfg)
    if provider == "ollama":
        return OllamaClient(cfg)
    raise ValueError(
        f"지원하지 않는 LLM provider: {provider!r}. "
        "openai | anthropic | ollama 중 하나를 선택하세요."
    )
