"""
설정 키 레지스트리 — pst2md-config 의 단일 진실 원천.

`KNOWN_KEYS` 에 등록된 키만 `pst2md-config set/get/unset` 로 수정 가능하다.
각 항목은 타입·기본값·설명·(선택) 열거값·민감 플래그를 기술한다.

구조는 `scripts/lib/config.py::DEFAULT_CONFIG` 와 1:1 대응한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Optional


KeyType = Literal["str", "int", "bool", "list", "choice"]


@dataclass(frozen=True)
class KeySpec:
    """설정 키 한 개의 메타데이터.

    Attributes:
        path:        dotted full key (예: "llm.scope.tag_max_count").
        section:     TOML 섹션 이름 (예: "llm.scope").
        key:         섹션 내 키 이름 (예: "tag_max_count").
        type:        값 타입 (str / int / bool / list / choice).
        choices:     type == "choice" 일 때 허용 값 튜플.
        default:     DEFAULT_CONFIG 와 일치하는 기본값.
        description: --help 및 show 출력용 한 줄 설명.
        sensitive:   True 면 show/get 시 마스킹하고 set 시 경고를 출력.
    """
    path: str
    section: str
    key: str
    type: KeyType
    choices: Optional[tuple[str, ...]] = None
    default: Any = None
    description: str = ""
    sensitive: bool = False


def _ks(
    path: str,
    *,
    type: KeyType,
    default: Any = None,
    choices: Optional[tuple[str, ...]] = None,
    description: str = "",
    sensitive: bool = False,
) -> KeySpec:
    """KeySpec 헬퍼 — path 를 '.' 로 쪼개 section/key 를 분리한다."""
    section, _, key = path.rpartition(".")
    return KeySpec(
        path=path,
        section=section,
        key=key,
        type=type,
        choices=choices,
        default=default,
        description=description,
        sensitive=sensitive,
    )


KNOWN_KEYS: dict[str, KeySpec] = {
    # ── archive ─────────────────────────────────────────────────────────────
    "archive.root": _ks(
        "archive.root", type="str", default="~/mail-archive",
        description="아카이브 루트 디렉터리 (~ 확장 지원).",
    ),
    "archive.roots": _ks(
        "archive.roots", type="list", default=[],
        description="추가 아카이브 루트 목록. 쉼표로 구분 (예: ~/work,~/personal).",
    ),
    # ── pst_backend (top-level) ─────────────────────────────────────────────
    "pst_backend": _ks(
        "pst_backend", type="choice", default="auto",
        choices=("auto", "pypff", "readpst", "win32com"),
        description="PST 파서 백엔드.",
    ),
    # ── tools ───────────────────────────────────────────────────────────────
    "tools.fzf": _ks(
        "tools.fzf", type="str", default="fzf",
        description="fzf 실행 파일 경로 또는 이름.",
    ),
    "tools.glow": _ks(
        "tools.glow", type="str", default="glow",
        description="glow 실행 파일 경로 또는 이름.",
    ),
    "tools.bat": _ks(
        "tools.bat", type="str", default="bat",
        description="bat 실행 파일 경로 또는 이름 (fallback 뷰어).",
    ),
    "tools.sqlite3": _ks(
        "tools.sqlite3", type="str", default="sqlite3",
        description="sqlite3 CLI 실행 파일 경로 또는 이름.",
    ),
    "tools.rg": _ks(
        "tools.rg", type="str", default="rg",
        description="ripgrep 실행 파일 경로 또는 이름.",
    ),
    # ── win32com ────────────────────────────────────────────────────────────
    "win32com.outlook_profile": _ks(
        "win32com.outlook_profile", type="str", default="",
        description="Outlook 프로파일 이름 (빈 문자열 = 기본 프로파일).",
    ),
    # ── mailview ────────────────────────────────────────────────────────────
    "mailview.glow_style": _ks(
        "mailview.glow_style", type="str", default="",
        description="fzf preview 에서 glow 가 쓸 테마. dark|light|dracula|tokyo-night|notty 또는 JSON 경로.",
    ),
    "mailview.auto_index": _ks(
        "mailview.auto_index", type="bool", default=True,
        description="mailview 시작 시 새 MD 파일이 있으면 인덱스를 자동 갱신.",
    ),
    "mailview.preview_viewer": _ks(
        "mailview.preview_viewer", type="choice", default="glow",
        choices=("glow", "mdcat"),
        description="fzf preview 와 Enter 전체 열람에 사용할 뷰어.",
    ),
    # ── llm ─────────────────────────────────────────────────────────────────
    "llm.provider": _ks(
        "llm.provider", type="choice", default="openai",
        choices=("openai", "anthropic", "ollama"),
        description="LLM 프로바이더.",
    ),
    "llm.endpoint": _ks(
        "llm.endpoint", type="str", default="https://api.openai.com/v1",
        description="LLM API 엔드포인트 URL.",
    ),
    "llm.token": _ks(
        "llm.token", type="str", default="", sensitive=True,
        description="LLM API 토큰 (비워두면 env LLM_TOKEN 을 사용 — 권장).",
    ),
    "llm.model": _ks(
        "llm.model", type="str", default="gpt-4o-mini",
        description="사용할 모델 이름 (예: gpt-4o-mini, claude-haiku-4-5-20251001, llama3.1:8b).",
    ),
    "llm.timeout": _ks(
        "llm.timeout", type="int", default=60,
        description="요청 타임아웃(초).",
    ),
    "llm.max_retries": _ks(
        "llm.max_retries", type="int", default=3,
        description="실패 시 최대 재시도 횟수 (5xx/429/타임아웃).",
    ),
    "llm.concurrency": _ks(
        "llm.concurrency", type="int", default=4,
        description="동시 LLM 호출 수 (CPU 코어 × 2 이하 권장).",
    ),
    # ── llm.scope ───────────────────────────────────────────────────────────
    "llm.scope.summary_max_chars": _ks(
        "llm.scope.summary_max_chars", type="int", default=300,
        description="요약 최대 글자 수.",
    ),
    "llm.scope.tag_max_count": _ks(
        "llm.scope.tag_max_count", type="int", default=5,
        description="생성할 의미 태그 최대 개수.",
    ),
    "llm.scope.related_max_count": _ks(
        "llm.scope.related_max_count", type="int", default=5,
        description="관련 문서 링크 최대 개수.",
    ),
    "llm.scope.skip_body_shorter_than": _ks(
        "llm.scope.skip_body_shorter_than", type="int", default=100,
        description="본문 길이가 이 값(바이트) 미만이면 enrichment 를 건너뜀.",
    ),
    "llm.scope.skip_folders": _ks(
        "llm.scope.skip_folders", type="list",
        default=["Junk", "Spam", "Deleted Items"],
        description="처리에서 제외할 폴더 이름 목록 (부분 일치, 쉼표 구분).",
    ),
}


# ---------------------------------------------------------------------------
# 타입 변환 / 포맷
# ---------------------------------------------------------------------------

_BOOL_TRUE = {"true", "yes", "y", "1", "on"}
_BOOL_FALSE = {"false", "no", "n", "0", "off"}


def convert_value(spec: KeySpec, raw: str) -> Any:
    """CLI 입력 문자열을 KeySpec 타입에 맞게 변환한다.

    Args:
        spec: 대상 KeySpec.
        raw:  CLI 에서 받은 문자열 값.

    Returns:
        str / int / bool / list 중 하나.

    Raises:
        ValueError: 타입 변환 실패 (예: int 변환 불가, bool 미인식, choice 불일치).
    """
    if spec.type == "str":
        return raw
    if spec.type == "choice":
        if spec.choices and raw not in spec.choices:
            allowed = " | ".join(spec.choices)
            raise ValueError(f"{spec.path} 값은 다음 중 하나여야 합니다: {allowed}")
        return raw
    if spec.type == "int":
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"{spec.path} 는 정수여야 합니다 (받은 값: {raw!r})") from exc
    if spec.type == "bool":
        low = raw.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
        raise ValueError(
            f"{spec.path} 는 true/false/yes/no/1/0 중 하나여야 합니다 (받은 값: {raw!r})"
        )
    if spec.type == "list":
        if not raw.strip():
            return []
        return [p.strip() for p in raw.split(",") if p.strip()]
    raise ValueError(f"알 수 없는 타입: {spec.type}")


def format_toml_value(value: Any) -> str:
    """Python 값을 TOML 리터럴 문자열로 변환한다.

    Args:
        value: bool / int / str / list[str] 중 하나.

    Returns:
        TOML 에 그대로 삽입 가능한 리터럴 (예: ``"foo"``, ``42``, ``true``,
        ``["a", "b"]``).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        inner = ", ".join(f'"{str(v)}"' for v in value)
        return f"[{inner}]"
    # str (역슬래시 → 슬래시 정규화)
    return f'"{str(value).replace(chr(92), "/")}"'


def mask_sensitive(value: Any) -> str:
    """민감 값 마스킹. 끝 4글자만 보이도록 처리한다."""
    if value is None:
        return ""
    s = str(value)
    if not s:
        return ""
    if len(s) <= 4:
        return "***"
    return f"***{s[-4:]}"
