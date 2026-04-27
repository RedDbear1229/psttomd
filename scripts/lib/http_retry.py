"""
http_retry — 공통 HTTP 재시도 / 백오프 / 토큰 해석 유틸리티

llm_client 와 embed_client 가 공유하는 POST 재시도 로직을 한 곳에서 관리한다.
지수 백오프, 5xx/429 재시도, httpx 선택적 의존성 처리를 포함한다.

사용 예:
    from scripts.lib.http_retry import (
        post_with_retry, build_httpx_client, resolve_token,
    )

    http = build_httpx_client(timeout=60)
    token = resolve_token("LLM_TOKEN", cfg["llm"])
    resp = post_with_retry(http, url, headers, payload, max_retries=3, name="OpenAI")
"""
from __future__ import annotations

import os
import time
from typing import Any

# ---------------------------------------------------------------------------
# 선택적 의존성: httpx (실제 HTTP 호출 시에만 필요)
# ---------------------------------------------------------------------------

try:
    import httpx as _httpx
    NETWORK_ERRORS: tuple[type[Exception], ...] = (
        _httpx.HTTPError,
        _httpx.RequestError,
        OSError,
        ValueError,
    )
except ImportError:
    _httpx = None  # type: ignore[assignment]
    NETWORK_ERRORS = (OSError, ValueError)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

#: 재시도 가능한 HTTP 상태 코드 집합 (rate limit + 5xx).
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: 백오프 상한 (초). 2^attempt 가 이 값을 넘으면 캡.
MAX_BACKOFF_SECONDS = 16

_ERROR_TEXT_LIMIT = 200


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def backoff(attempt: int) -> None:
    """지수 백오프 sleep: attempt 0 → 1s, 1 → 2s, 2 → 4s, ... (cap MAX_BACKOFF_SECONDS).

    테스트에서는 ``patch("scripts.lib.http_retry.backoff")`` 로 무력화한다.

    Args:
        attempt: 0-based 시도 횟수.
    """
    time.sleep(min(2 ** attempt, MAX_BACKOFF_SECONDS))


def build_httpx_client(timeout: int) -> Any:
    """httpx.Client 를 생성한다.

    Args:
        timeout: 요청 타임아웃 (초).

    Returns:
        httpx.Client 인스턴스.

    Raises:
        ImportError: httpx 미설치.
    """
    if _httpx is None:
        raise ImportError(
            "httpx 가 설치되어 있지 않습니다. "
            "pip install httpx  또는  pip install -e '.[mailenrich]'"
        )
    return _httpx.Client(timeout=timeout)


def resolve_token(env_var: str, cfg_section: dict[str, Any]) -> str:
    """API 토큰을 env 우선·config 폴백 순으로 반환한다.

    Args:
        env_var:     읽을 환경 변수 이름 (예: "LLM_TOKEN", "EMBEDDING_TOKEN").
        cfg_section: ``cfg["llm"]`` / ``cfg["embedding"]`` 같은 섹션 dict.

    Returns:
        env 값(있으면) 또는 ``cfg_section["token"]``. 둘 다 없으면 빈 문자열.
    """
    env = os.environ.get(env_var, "").strip()
    return env if env else cfg_section.get("token", "")


def post_with_retry(
    http: Any,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    max_retries: int,
    *,
    name: str = "HTTPClient",
) -> Any:
    """POST 요청을 max_retries 회까지 재시도하며 응답을 반환한다.

    재시도 트리거:
      * status_code in RETRY_STATUSES (429 / 5xx)
      * NETWORK_ERRORS (httpx 네트워크 오류 / OSError / ValueError)

    각 재시도 사이에 :func:`backoff` 으로 지수 sleep.

    Args:
        http:        httpx.Client 인스턴스.
        url:         요청 URL.
        headers:     HTTP 헤더 dict.
        payload:     JSON body dict.
        max_retries: 최대 시도 횟수 (3 권장).
        name:        에러 메시지에 표시할 클라이언트 이름.

    Returns:
        성공한 httpx.Response (status_code 2xx).

    Raises:
        RuntimeError: max_retries 초과 시 마지막 예외와 함께.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = http.post(url, headers=headers, json=payload)
            if resp.status_code in RETRY_STATUSES:
                last_exc = ValueError(
                    f"HTTP {resp.status_code}: {resp.text[:_ERROR_TEXT_LIMIT]}"
                )
                backoff(attempt)
                continue
            resp.raise_for_status()
            return resp
        except NETWORK_ERRORS as exc:
            last_exc = exc
            backoff(attempt)

    raise RuntimeError(
        f"{name} 호출 {max_retries}회 실패: {last_exc}"
    ) from last_exc
