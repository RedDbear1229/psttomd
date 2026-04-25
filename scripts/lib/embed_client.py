"""
embed_client — OpenAI 호환 `/v1/embeddings` HTTP 어댑터

endpoint + token + model 만으로 OpenAI / Ollama / LM Studio 등
OpenAI 스키마를 구현한 모든 서버를 지원한다. LLM 어댑터와 달리
provider 분기 없음 — 엔드포인트가 전부 결정.

사용 예:
    from scripts.lib.embed_client import EmbeddingClient

    client = EmbeddingClient(cfg)
    vectors = client.embed(["text 1", "text 2"])
    # vectors[i] : list[float]
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

try:
    import httpx as _httpx
    _NETWORK_ERRORS: tuple[type[Exception], ...] = (
        _httpx.HTTPError,
        _httpx.RequestError,
        OSError,
        ValueError,
    )
except ImportError:
    _httpx = None  # type: ignore[assignment]
    _NETWORK_ERRORS = (OSError, ValueError)


_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_BACKOFF_SECONDS = 16
_ERROR_TEXT_LIMIT = 200


@dataclass
class EmbeddingResponse:
    """한 배치의 embedding 결과.

    Attributes:
        vectors: 입력 순서대로의 float 벡터 목록.
        model:   서버가 응답한 실제 모델 이름.
        dim:     벡터 차원 수 (vectors[0] 길이와 동일).
        input_tokens: 전체 배치 토큰 수 (서버가 반환한 경우).
    """
    vectors: list[list[float]]
    model: str
    dim: int
    input_tokens: int = 0


def _backoff(attempt: int) -> None:
    """지수 백오프: attempt 0 → 1s, 1 → 2s, 2 → 4s."""
    time.sleep(min(2 ** attempt, _MAX_BACKOFF_SECONDS))


def _resolve_token(emb_cfg: dict[str, Any]) -> str:
    """EMBEDDING_TOKEN env → config token 순으로 토큰을 반환한다."""
    env = os.environ.get("EMBEDDING_TOKEN", "").strip()
    return env if env else emb_cfg.get("token", "")


def _build_httpx_client(cfg: dict[str, Any]) -> Any:
    """httpx.Client 를 반환한다. httpx 는 선택적 의존성이므로 임포트 시 확인."""
    if _httpx is None:
        raise ImportError(
            "httpx 가 설치되어 있지 않습니다. "
            "pip install httpx  또는  pip install -e '.[mailenrich]'"
        )
    timeout = cfg.get("embedding", {}).get("timeout", 60)
    return _httpx.Client(timeout=timeout)


class EmbeddingClient:
    """OpenAI 호환 /v1/embeddings 클라이언트.

    재시도·백오프·배치·토큰 env 폴백을 제공한다. 서버 응답의 data 배열을
    입력 인덱스 기준으로 정렬해 반환한다.
    """

    def __init__(self, cfg: dict[str, Any]) -> None:
        emb = cfg.get("embedding", {})
        self._endpoint: str = (emb.get("endpoint") or "").rstrip("/")
        self._model: str = emb.get("model") or "text-embedding-3-small"
        self._max_retries: int = int(emb.get("max_retries", 3))
        self._token: str = _resolve_token(emb)
        self._http = _build_httpx_client(cfg)

        if not self._endpoint:
            self._endpoint = "https://api.openai.com/v1"

    @property
    def model(self) -> str:
        return self._model

    def embed(self, texts: list[str]) -> EmbeddingResponse:
        """텍스트 배치를 embedding 벡터 배치로 변환한다.

        Args:
            texts: 변환할 텍스트 목록. 빈 문자열은 호출 전에 걸러낼 것.

        Returns:
            EmbeddingResponse — vectors 는 입력 순서와 동일.

        Raises:
            RuntimeError: 재시도 소진 후에도 실패한 경우.
            ValueError:   응답 형식 오류.
        """
        if not texts:
            return EmbeddingResponse(vectors=[], model=self._model, dim=0)

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        payload: dict[str, Any] = {
            "model": self._model,
            "input": texts,
            "encoding_format": "float",
        }

        resp = self._post_with_retry(
            f"{self._endpoint}/embeddings", headers, payload,
        )
        return self._parse(resp.json(), expected=len(texts))

    def _post_with_retry(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> Any:
        """POST 요청을 max_retries 회 재시도하며 응답을 반환한다."""
        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = self._http.post(url, headers=headers, json=payload)
                if resp.status_code in _RETRY_STATUSES:
                    last_exc = ValueError(
                        f"HTTP {resp.status_code}: {resp.text[:_ERROR_TEXT_LIMIT]}"
                    )
                    _backoff(attempt)
                    continue
                resp.raise_for_status()
                return resp
            except _NETWORK_ERRORS as exc:
                last_exc = exc
                _backoff(attempt)

        raise RuntimeError(
            f"EmbeddingClient 호출 {self._max_retries}회 실패: {last_exc}"
        ) from last_exc

    def _parse(self, data: dict[str, Any], *, expected: int) -> EmbeddingResponse:
        """OpenAI `/v1/embeddings` 응답 JSON 을 파싱한다.

        응답 스키마:
            {
              "data": [{"index": 0, "embedding": [..]}, ...],
              "model": "...",
              "usage": {"prompt_tokens": N, ...}
            }
        """
        items = data.get("data")
        if not isinstance(items, list) or len(items) != expected:
            raise ValueError(
                f"embedding 응답 item 수 불일치: expected={expected}, "
                f"got={len(items) if isinstance(items, list) else 'non-list'}"
            )

        # index 기준 정렬 (대부분 이미 정렬돼 있으나 스펙상 순서 보장 없음)
        ordered: list[list[float]] = [[]] * expected
        for item in items:
            idx = int(item.get("index", 0))
            vec = item.get("embedding")
            if not isinstance(vec, list):
                raise ValueError(f"embedding 항목 {idx} 의 vector 누락")
            ordered[idx] = [float(x) for x in vec]

        dim = len(ordered[0]) if ordered else 0
        usage = data.get("usage", {}) or {}
        return EmbeddingResponse(
            vectors=ordered,
            model=data.get("model", self._model),
            dim=dim,
            input_tokens=int(usage.get("prompt_tokens", 0)),
        )
