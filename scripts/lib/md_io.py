"""
md_io — Markdown MD 파일 분리·쓰기 유틸리티

pst2md 가 생성한 MD 파일을 4 개의 --- 구분자 기준으로 분리하고,
LLM enrichment 결과를 원자적으로 기록한다.

구조:
    ---
    <frontmatter>                    ← YAML 필드
    ---

    # 제목
    **보낸사람:** ...
    ...

    ---

    <pristine body>                  ← PST 추출 본문, 절대 수정 금지

    ---

    <!-- LLM-ENRICH:BEGIN -->        ← 이 영역만 mailenrich 가 관리
    ## 요약 (LLM)
    ...
    <!-- LLM-ENRICH:END -->

    ## 첨부 파일 (있을 경우)
    관련: [[...]]

사용 예:
    from scripts.lib.md_io import split, write, body_hash

    parts = split(Path("mail.md"))
    h     = body_hash(parts)
    write(path, fm_updates, llm_sections, parts)
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_ENCODING = "utf-8"

#: frontmatter 종료 구분자 — "...last_key_value\n---\n\n"
_FM_END = "\n---\n\n"

#: 본문 앞뒤 구분자 — "\n\n---\n\n"
_BODY_SEP = "\n\n---\n\n"

#: LLM 블록 마커 (BEGIN .. END)
_LLM_BLOCK_RE = re.compile(
    r"<!-- LLM-ENRICH:BEGIN -->.*?<!-- LLM-ENRICH:END -->\n?",
    re.DOTALL,
)

#: mailenrich 가 쓰고 읽는 frontmatter 키 집합 (다른 키는 보존)
_LLM_KEYS = frozenset({
    "summary",
    "llm_tags",
    "related",
    "llm_enriched_at",
    "llm_model",
    "llm_hash",
})

#: JSON 배열로 파싱할 frontmatter 키
_JSON_ARRAY_KEYS = frozenset({"to", "cc", "references", "tags", "llm_tags"})

#: JSON (임의 타입)으로 파싱할 frontmatter 키
_JSON_ANY_KEYS = frozenset({"related"})


# ---------------------------------------------------------------------------
# 데이터 클래스
# ---------------------------------------------------------------------------

@dataclass
class MdParts:
    """split() 이 반환하는 MD 파일의 분해 결과."""

    frontmatter: dict[str, Any]
    """loose 파싱된 frontmatter dict (llm_hash 체크 등에 사용)."""

    frontmatter_raw: str
    """원본 frontmatter 텍스트 (블록 리스트 등 보존용)."""

    head: str
    """# 제목 + **보낸사람:** 블록 (불변)."""

    body: str
    """PST 추출 본문 (pristine, 절대 수정 금지)."""

    llm_block: str | None
    """이전 LLM 블록 내용 (없으면 None)."""

    tail: str
    """## 첨부 파일 + 관련: footer (LLM 블록 제거 후)."""


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def split(path: Path) -> MdParts:
    """MD 파일을 구조별로 분해한다.

    Args:
        path: 파싱할 Markdown 파일 경로.

    Returns:
        MdParts 인스턴스.

    Raises:
        ValueError: frontmatter 또는 본문 구분자를 찾지 못한 경우.
        OSError: 파일 읽기 실패.
    """
    text = path.read_text(encoding=_ENCODING)

    if not text.startswith("---\n"):
        raise ValueError(f"YAML frontmatter 없음: {path}")

    # 1. Frontmatter: "---\n" (4자) 뒤 ~ 첫 "\n---\n\n" 앞
    p_fm = text.find(_FM_END, 3)
    if p_fm == -1:
        raise ValueError(f"Frontmatter 종료 구분자 없음: {path}")
    fm_raw = text[4:p_fm]                          # 내용만 (앞뒤 --- 제외)
    after_fm = text[p_fm + len(_FM_END):]           # "# 제목\n\n..."

    # 2. Head: after_fm 에서 첫 "\n\n---\n\n" 앞
    p_head = after_fm.find(_BODY_SEP)
    if p_head == -1:
        raise ValueError(f"본문 시작 구분자 없음: {path}")
    head = after_fm[:p_head]
    after_head = after_fm[p_head + len(_BODY_SEP):]  # "{body}\n\n---\n\n{tail}"

    # 3. Body: after_head 에서 마지막 "\n\n---\n\n" 앞
    #    rfind 사용 → body 안의 --- 수평선에 강건
    p_body = after_head.rfind(_BODY_SEP)
    if p_body == -1:
        raise ValueError(f"본문 종료 구분자 없음: {path}")
    body = after_head[:p_body]
    tail_raw = after_head[p_body + len(_BODY_SEP):]

    # 4. Tail 에서 LLM 블록 분리
    llm_match = _LLM_BLOCK_RE.search(tail_raw)
    if llm_match:
        llm_block = llm_match.group(0)
        raw_tail = tail_raw[: llm_match.start()] + tail_raw[llm_match.end():]
        tail = raw_tail.lstrip("\n")   # LLM 블록 뒤 빈 줄 제거
    else:
        llm_block = None
        tail = tail_raw

    return MdParts(
        frontmatter=_parse_frontmatter(fm_raw),
        frontmatter_raw=fm_raw,
        head=head,
        body=body,
        llm_block=llm_block,
        tail=tail,
    )


def body_hash(parts: MdParts) -> str:
    """pristine body 의 SHA-256 hex digest 를 반환한다.

    Args:
        parts: split() 반환값.

    Returns:
        64 자 hex 문자열.
    """
    return hashlib.sha256(parts.body.encode(_ENCODING)).hexdigest()


def write(
    path: Path,
    fm_updates: dict[str, Any],
    llm_sections: str,
    original: MdParts,
) -> None:
    """LLM enrichment 결과를 MD 파일에 원자적으로 기록한다.

    본문(body) 바이트가 변경되면 RuntimeError 를 발생시키고 파일을 미저장한다.

    Args:
        path:         대상 MD 파일 경로.
        fm_updates:   frontmatter 에 추가/갱신할 LLM 키-값 dict.
        llm_sections: <!-- LLM-ENRICH:BEGIN/END --> 안에 삽입할 섹션 텍스트.
        original:     split() 에서 얻은 원본 파츠.

    Raises:
        RuntimeError: body 바이트 불변성 검증 실패.
        OSError: 파일 쓰기 실패.
    """
    updated_fm = _update_frontmatter(original.frontmatter_raw, fm_updates)
    llm_block = (
        f"<!-- LLM-ENRICH:BEGIN -->\n"
        f"{llm_sections}"
        f"<!-- LLM-ENRICH:END -->\n"
    )

    new_text = (
        f"---\n{updated_fm}---\n\n"
        f"{original.head}\n\n---\n\n"
        f"{original.body}\n\n---\n\n"
        f"{llm_block}\n"
        f"{original.tail}"
    )

    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(new_text, encoding=_ENCODING)

        # body 바이트 불변성 검증
        new_parts = split(tmp)
        if new_parts.body != original.body:
            raise RuntimeError(
                f"본문 바이트 불변성 위반: {path}\n"
                f"  before: {original.body[:80]!r}\n"
                f"  after : {new_parts.body[:80]!r}"
            )

        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _parse_json_field(val: str, array: bool) -> Any:
    """frontmatter 값 문자열을 JSON 으로 파싱한다.

    Args:
        val:   strip 된 값 문자열.
        array: True 이면 배열 기대 (비어 있으면 [] 반환).

    Returns:
        파싱된 값. 실패 시 [] 또는 None.
    """
    try:
        if array:
            return json.loads(val) if val.startswith("[") else []
        return json.loads(val)
    except (json.JSONDecodeError, ValueError):
        return [] if array else None


def _parse_frontmatter(fm_raw: str) -> dict[str, Any]:
    """frontmatter 원문을 loose 파싱한다.

    build_index.py:311 의 파서 패턴을 따른다.
    들여쓰기 줄(블록 리스트 항목)은 건너뛴다.

    Args:
        fm_raw: --- 마커 사이의 frontmatter 텍스트.

    Returns:
        키-값 dict. JSON 배열 필드는 list 로 변환.
    """
    meta: dict[str, Any] = {}
    for line in fm_raw.splitlines():
        if not line or line[0].isspace() or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip().strip('"').strip("'")

        if key in _JSON_ARRAY_KEYS:
            meta[key] = _parse_json_field(val, array=True)
        elif key in _JSON_ANY_KEYS:
            parsed = _parse_json_field(val, array=False)
            meta[key] = parsed if parsed is not None else []
        else:
            meta[key] = val

    return meta


def _yaml_str(s: str) -> str:
    """YAML 인라인 문자열에서 큰따옴표를 작은따옴표로 치환한다.

    pst2md.py:108 의 _yaml_str() 과 동일한 로직.

    Args:
        s: 원본 문자열.

    Returns:
        큰따옴표가 작은따옴표로 교체된 문자열.
    """
    return s.replace('"', "'")


def _update_frontmatter(fm_raw: str, updates: dict[str, Any]) -> str:
    """frontmatter 원문에서 LLM 키를 제거하고 updates 로 교체한다.

    기존 non-LLM 키와 블록 리스트(attachments: 등)는 원본 그대로 보존한다.

    Args:
        fm_raw:  원본 frontmatter 텍스트 (--- 마커 제외).
        updates: 추가/갱신할 LLM 키-값 dict.

    Returns:
        업데이트된 frontmatter 텍스트 (trailing \\n 포함).
    """
    lines = fm_raw.splitlines(keepends=True)
    result: list[str] = []
    skip_block = False

    for line in lines:
        if not line[0:1].isspace() and ":" in line:
            key = line.split(":", 1)[0].strip()
            if key in _LLM_KEYS:
                skip_block = True
                continue
            else:
                skip_block = False
        elif skip_block and line[0:1].isspace():
            continue  # LLM 블록 리스트 항목 제거
        else:
            skip_block = False
        result.append(line)

    text = "".join(result)
    if not text.endswith("\n"):
        text += "\n"

    for key, val in updates.items():
        if isinstance(val, (list, dict)):
            text += f"{key}: {json.dumps(val, ensure_ascii=False)}\n"
        else:
            text += f'{key}: "{_yaml_str(str(val))}"\n'

    return text  # 항상 \n 으로 끝남 — "---\n{text}---\n\n" 조립에 사용
