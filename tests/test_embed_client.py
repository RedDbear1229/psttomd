"""
tests/test_embed_client.py — embed_client 단위 테스트

httpx 를 mock 으로 패치하여 네트워크 없이 검증한다.

검증 항목:
1. embed() 가 OpenAI 호환 페이로드를 생성하고 응답을 파싱
2. 빈 리스트 호출 시 빈 EmbeddingResponse 반환
3. data 배열의 index 기준 재정렬
4. 토큰 환경변수 우선 (EMBEDDING_TOKEN > config.token)
5. 5xx / 429 재시도 후 RuntimeError
6. response item 수 불일치 시 ValueError
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.embed_client import EmbeddingClient, _resolve_token


def _cfg(
    endpoint: str = "https://api.openai.com/v1",
    model: str = "text-embedding-3-small",
    token: str = "test-token",
    max_retries: int = 2,
    timeout: int = 10,
) -> dict:
    return {
        "embedding": {
            "endpoint": endpoint,
            "model": model,
            "token": token,
            "max_retries": max_retries,
            "timeout": timeout,
        }
    }


def _mock_response(status_code: int, body: dict | str) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if isinstance(body, dict):
        resp.json.return_value = body
    else:
        resp.text = body
    resp.raise_for_status = MagicMock()
    return resp


def _make_client(**kwargs) -> EmbeddingClient:
    cfg = _cfg(**kwargs)
    client = EmbeddingClient.__new__(EmbeddingClient)
    e = cfg["embedding"]
    client._endpoint = e["endpoint"].rstrip("/")
    client._model = e["model"]
    client._max_retries = e["max_retries"]
    client._token = e["token"]
    return client


class TestEmbed:
    def test_empty_input_returns_empty(self) -> None:
        client = _make_client()
        client._http = MagicMock()
        out = client.embed([])
        assert out.vectors == []
        assert out.dim == 0
        client._http.post.assert_not_called()

    def test_successful_call_parses_vectors(self) -> None:
        client = _make_client()
        api_resp = {
            "data": [
                {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                {"index": 1, "embedding": [0.4, 0.5, 0.6]},
            ],
            "model": "text-embedding-3-small",
            "usage": {"prompt_tokens": 12},
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, api_resp)
        client._http = mock_http

        out = client.embed(["a", "b"])

        assert out.vectors == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        assert out.dim == 3
        assert out.input_tokens == 12
        assert out.model == "text-embedding-3-small"

    def test_payload_and_headers(self) -> None:
        client = _make_client(endpoint="http://localhost:11434/v1")
        api_resp = {
            "data": [{"index": 0, "embedding": [0.0]}],
            "model": "nomic-embed-text",
            "usage": {},
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, api_resp)
        client._http = mock_http

        client.embed(["hello"])

        call = mock_http.post.call_args
        assert "http://localhost:11434/v1/embeddings" in call[0]
        payload = call[1]["json"]
        assert payload["model"] == "text-embedding-3-small"
        assert payload["input"] == ["hello"]
        assert payload["encoding_format"] == "float"
        headers = call[1]["headers"]
        assert headers["Authorization"] == "Bearer test-token"

    def test_no_auth_header_when_token_empty(self) -> None:
        client = _make_client(token="")
        api_resp = {
            "data": [{"index": 0, "embedding": [0.0]}],
            "model": "x",
            "usage": {},
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, api_resp)
        client._http = mock_http

        client.embed(["x"])
        headers = mock_http.post.call_args[1]["headers"]
        assert "Authorization" not in headers

    def test_index_based_reordering(self) -> None:
        """서버가 순서를 섞어 반환해도 입력 순서대로 정렬되어야 함."""
        client = _make_client()
        api_resp = {
            "data": [
                {"index": 2, "embedding": [3.0]},
                {"index": 0, "embedding": [1.0]},
                {"index": 1, "embedding": [2.0]},
            ],
            "model": "x",
            "usage": {},
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, api_resp)
        client._http = mock_http

        out = client.embed(["a", "b", "c"])
        assert out.vectors == [[1.0], [2.0], [3.0]]

    def test_retries_on_5xx(self) -> None:
        client = _make_client(max_retries=3)
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(500, "boom")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            with pytest.raises(RuntimeError, match="3회 실패"):
                client.embed(["x"])
        assert mock_http.post.call_count == 3

    def test_retries_on_429(self) -> None:
        client = _make_client(max_retries=2)
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(429, "limit")
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            with pytest.raises(RuntimeError):
                client.embed(["x"])
        assert mock_http.post.call_count == 2

    def test_success_after_retry(self) -> None:
        client = _make_client(max_retries=3)
        api_resp = {
            "data": [{"index": 0, "embedding": [0.0]}],
            "model": "x",
            "usage": {},
        }
        fail = _mock_response(503, "down")
        ok = _mock_response(200, api_resp)
        mock_http = MagicMock()
        mock_http.post.side_effect = [fail, ok]
        client._http = mock_http

        with patch("scripts.lib.http_retry.backoff"):
            out = client.embed(["x"])
        assert out.dim == 1
        assert mock_http.post.call_count == 2

    def test_item_count_mismatch_raises(self) -> None:
        client = _make_client()
        api_resp = {
            "data": [{"index": 0, "embedding": [0.0]}],
            "model": "x",
            "usage": {},
        }
        mock_http = MagicMock()
        mock_http.post.return_value = _mock_response(200, api_resp)
        client._http = mock_http

        with pytest.raises(ValueError, match="item 수 불일치"):
            client.embed(["a", "b"])  # 2개 보냈는데 1개 응답


class TestResolveToken:
    def test_env_takes_priority(self) -> None:
        with patch.dict(os.environ, {"EMBEDDING_TOKEN": "env-key"}):
            assert _resolve_token({"token": "config-key"}) == "env-key"

    def test_config_fallback(self) -> None:
        env_backup = os.environ.pop("EMBEDDING_TOKEN", None)
        try:
            assert _resolve_token({"token": "config-key"}) == "config-key"
        finally:
            if env_backup is not None:
                os.environ["EMBEDDING_TOKEN"] = env_backup

    def test_empty_when_no_token(self) -> None:
        env_backup = os.environ.pop("EMBEDDING_TOKEN", None)
        try:
            assert _resolve_token({}) == ""
        finally:
            if env_backup is not None:
                os.environ["EMBEDDING_TOKEN"] = env_backup
