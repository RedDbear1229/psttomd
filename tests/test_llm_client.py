"""
tests/test_llm_client.py — llm_client 단위 테스트

httpx 를 unittest.mock 으로 패치하여 네트워크 없이 검증한다.

검증 항목:
1. OpenAIClient   — chat/completions JSON 응답 파싱
2. AnthropicClient — tool-use 블록 추출
3. OllamaClient   — /api/chat JSON 응답 파싱
4. 공통 재시도    — 5xx / 429 / timeout 에서 max_retries 후 RuntimeError
5. get_client()   — provider 이름에 따른 팩토리 분기
6. _resolve_token — env 우선, config fallback
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.llm_client import (
    AnthropicClient,
    LLMRequest,
    OllamaClient,
    OpenAIClient,
    get_client,
)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _cfg(
    provider: str = "openai",
    endpoint: str = "https://api.openai.com/v1",
    model: str = "gpt-4o-mini",
    token: str = "test-token",
    max_retries: int = 2,
    timeout: int = 10,
) -> dict:
    return {
        "llm": {
            "provider": provider,
            "endpoint": endpoint,
            "model": model,
            "token": token,
            "max_retries": max_retries,
            "timeout": timeout,
        }
    }


def _mock_httpx_response(status_code: int, body: dict | str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.json.return_value = body
        resp.text = json.dumps(body)
    else:
        resp.json.side_effect = ValueError("not json")
        resp.text = body
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# OpenAIClient
# ---------------------------------------------------------------------------

class TestOpenAIClient:
    def _make(self, **kwargs) -> OpenAIClient:
        cfg = _cfg(provider="openai", **kwargs)
        client = OpenAIClient.__new__(OpenAIClient)
        client._endpoint = cfg["llm"]["endpoint"].rstrip("/")
        client._model = cfg["llm"]["model"]
        client._max_retries = cfg["llm"]["max_retries"]
        client._token = cfg["llm"]["token"]
        return client

    def test_successful_response(self) -> None:
        client = self._make()
        api_resp = {
            "choices": [{"message": {"content": '{"summary": "요약", "tags": ["a"]}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            "model": "gpt-4o-mini",
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        resp = client.complete(LLMRequest(system="sys", user="user"))

        assert json.loads(resp.text)["summary"] == "요약"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.model == "gpt-4o-mini"

    def test_endpoint_and_headers(self) -> None:
        client = self._make(endpoint="https://custom.endpoint/v1")
        api_resp = {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {},
            "model": "x",
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        client.complete(LLMRequest(system="s", user="u"))

        call_kwargs = mock_http.post.call_args
        assert "https://custom.endpoint/v1/chat/completions" in call_kwargs[0]
        headers = call_kwargs[1]["headers"]
        assert headers["Authorization"] == "Bearer test-token"

    def test_retries_on_5xx(self) -> None:
        client = self._make(max_retries=3)
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(500, "server error")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):  # skip actual sleep
            with pytest.raises(RuntimeError, match="3회 실패"):
                client.complete(LLMRequest(system="s", user="u"))

        assert mock_http.post.call_count == 3

    def test_retries_on_429(self) -> None:
        client = self._make(max_retries=2)
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(429, "rate limit")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            with pytest.raises(RuntimeError):
                client.complete(LLMRequest(system="s", user="u"))

        assert mock_http.post.call_count == 2

    def test_success_after_retry(self) -> None:
        client = self._make(max_retries=3)
        api_resp = {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {},
            "model": "gpt-4o-mini",
        }
        fail_resp = _mock_httpx_response(500, "error")
        ok_resp = _mock_httpx_response(200, api_resp)
        mock_http = MagicMock()
        mock_http.post.side_effect = [fail_resp, ok_resp]
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            resp = client.complete(LLMRequest(system="s", user="u"))

        assert resp.text == "{}"
        assert mock_http.post.call_count == 2


# ---------------------------------------------------------------------------
# AnthropicClient
# ---------------------------------------------------------------------------

class TestAnthropicClient:
    def _make(self) -> AnthropicClient:
        cfg = _cfg(
            provider="anthropic",
            endpoint="https://api.anthropic.com",
            model="claude-haiku-4-5-20251001",
        )
        client = AnthropicClient.__new__(AnthropicClient)
        client._endpoint = cfg["llm"]["endpoint"].rstrip("/")
        client._model = cfg["llm"]["model"]
        client._max_retries = cfg["llm"]["max_retries"]
        client._token = cfg["llm"]["token"]
        return client

    def test_tool_use_extraction(self) -> None:
        client = self._make()
        tool_input = {"summary": "계약 관련", "tags": ["계약"], "related": []}
        api_resp = {
            "content": [
                {"type": "tool_use", "name": "enrich_mail", "input": tool_input}
            ],
            "usage": {"input_tokens": 200, "output_tokens": 80},
            "model": "claude-haiku-4-5-20251001",
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        resp = client.complete(LLMRequest(system="sys", user="user"))

        parsed = json.loads(resp.text)
        assert parsed["summary"] == "계약 관련"
        assert parsed["tags"] == ["계약"]
        assert resp.input_tokens == 200
        assert resp.output_tokens == 80

    def test_tool_choice_in_payload(self) -> None:
        client = self._make()
        tool_input = {"summary": "x", "tags": [], "related": []}
        api_resp = {
            "content": [{"type": "tool_use", "name": "enrich_mail", "input": tool_input}],
            "usage": {},
            "model": "claude-haiku-4-5-20251001",
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        client.complete(LLMRequest(system="s", user="u"))

        payload = mock_http.post.call_args[1]["json"]
        assert payload["tool_choice"] == {"type": "tool", "name": "enrich_mail"}
        assert any(t["name"] == "enrich_mail" for t in payload["tools"])

    def test_retries_on_5xx(self) -> None:
        client = self._make()
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(500, "error")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            with pytest.raises(RuntimeError):
                client.complete(LLMRequest(system="s", user="u"))


# ---------------------------------------------------------------------------
# OllamaClient
# ---------------------------------------------------------------------------

class TestOllamaClient:
    def _make(self) -> OllamaClient:
        cfg = _cfg(
            provider="ollama",
            endpoint="http://localhost:11434",
            model="llama3.1:8b",
            token="",
        )
        client = OllamaClient.__new__(OllamaClient)
        client._endpoint = cfg["llm"]["endpoint"].rstrip("/")
        client._model = cfg["llm"]["model"]
        client._max_retries = cfg["llm"]["max_retries"]
        return client

    def test_successful_response(self) -> None:
        client = self._make()
        api_resp = {
            "message": {"content": '{"summary": "Ollama 요약", "tags": ["로컬"]}'},
            "model": "llama3.1:8b",
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        resp = client.complete(LLMRequest(system="sys", user="user"))

        assert json.loads(resp.text)["summary"] == "Ollama 요약"
        assert resp.model == "llama3.1:8b"

    def test_format_json_in_payload(self) -> None:
        client = self._make()
        api_resp = {"message": {"content": "{}"}, "model": "llama3.1:8b"}
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(200, api_resp)
        client._http = mock_http

        client.complete(LLMRequest(system="s", user="u"))

        payload = mock_http.post.call_args[1]["json"]
        assert payload["format"] == "json"
        assert payload["stream"] is False

    def test_retries_on_503(self) -> None:
        client = self._make()
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_httpx_response(503, "unavailable")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            with pytest.raises(RuntimeError):
                client.complete(LLMRequest(system="s", user="u"))


# ---------------------------------------------------------------------------
# get_client() 팩토리
# ---------------------------------------------------------------------------

class TestGetClient:
    def test_returns_openai_client(self) -> None:
        with patch("scripts.lib.llm_client.build_httpx_client", return_value=MagicMock()):
            client = get_client(_cfg(provider="openai"))
        assert isinstance(client, OpenAIClient)

    def test_returns_anthropic_client(self) -> None:
        with patch("scripts.lib.llm_client.build_httpx_client", return_value=MagicMock()):
            client = get_client(_cfg(provider="anthropic"))
        assert isinstance(client, AnthropicClient)

    def test_returns_ollama_client(self) -> None:
        with patch("scripts.lib.llm_client.build_httpx_client", return_value=MagicMock()):
            client = get_client(_cfg(provider="ollama"))
        assert isinstance(client, OllamaClient)

    def test_invalid_provider_raises(self) -> None:
        with pytest.raises(ValueError, match="지원하지 않는"):
            get_client(_cfg(provider="cohere"))


# ---------------------------------------------------------------------------
# _resolve_token
# ---------------------------------------------------------------------------

class TestResolveToken:
    def test_env_takes_priority(self) -> None:
        from scripts.lib.llm_client import _resolve_token
        with patch.dict(os.environ, {"LLM_TOKEN": "env-secret"}):
            assert _resolve_token({"token": "config-secret"}) == "env-secret"

    def test_config_fallback(self) -> None:
        from scripts.lib.llm_client import _resolve_token
        env_backup = os.environ.pop("LLM_TOKEN", None)
        try:
            assert _resolve_token({"token": "config-secret"}) == "config-secret"
        finally:
            if env_backup is not None:
                os.environ["LLM_TOKEN"] = env_backup

    def test_empty_when_no_token(self) -> None:
        from scripts.lib.llm_client import _resolve_token
        env_backup = os.environ.pop("LLM_TOKEN", None)
        try:
            assert _resolve_token({}) == ""
        finally:
            if env_backup is not None:
                os.environ["LLM_TOKEN"] = env_backup
