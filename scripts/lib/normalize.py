"""
주소/날짜/인코딩 정규화 유틸리티

메일 헤더에서 추출한 원시 문자열을 일관된 형태로 변환한다.
한국어 CP949/EUC-KR 인코딩과 RFC 2047 MIME 헤더 인코딩을 함께 처리한다.

사용 예:
    from lib.normalize import decode_mime_header, normalize_address, make_filename

    subject = decode_mime_header("=?UTF-8?B?7YWM7Iqk?=")
    addr    = normalize_address("홍길동 <hong@example.com>")
    fname   = make_filename(datetime.now(), "회의록", "<abc@mail>")
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from typing import Optional

import chardet
from slugify import slugify


# ---------------------------------------------------------------------------
# 문자열 / 인코딩
# ---------------------------------------------------------------------------

def decode_mime_header(raw: Optional[str]) -> str:
    """RFC 2047 인코딩된 헤더를 유니코드 문자열로 디코딩한다.

    '=?UTF-8?B?...?=' 또는 '=?EUC-KR?Q?...?=' 형식의 MIME 인코딩을 처리한다.
    디코딩에 실패하면 chardet 기반 fallback을 시도한다.

    Args:
        raw: 원시 헤더 문자열 (None 허용).

    Returns:
        디코딩된 유니코드 문자열. 입력이 None이거나 빈 문자열이면 "" 반환.
    """
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return _fallback_decode(raw)


def _fallback_decode(raw: str | bytes) -> str:
    """chardet을 사용해 인코딩을 추론하고 디코딩한다.

    str 입력은 latin-1로 바이트화한 뒤 인코딩을 재탐지한다.
    인코딩 탐지에 실패하면 utf-8로 대체 처리(replace)한다.

    Args:
        raw: 디코딩할 문자열 또는 바이트.

    Returns:
        디코딩된 유니코드 문자열.
    """
    if isinstance(raw, str):
        raw = raw.encode("latin-1", errors="replace")
    detected = chardet.detect(raw)
    enc = detected.get("encoding") or "utf-8"
    return raw.decode(enc, errors="replace")


def safe_decode(data: bytes, hint_charset: Optional[str] = None) -> str:
    """바이트 데이터를 안전하게 유니코드 문자열로 변환한다.

    CP949/EUC-KR 한국어 메일을 우선적으로 처리하고,
    모두 실패하면 chardet 자동 탐지로 최종 변환한다.

    Args:
        data:         변환할 바이트 데이터.
        hint_charset: 헤더 등에서 얻은 힌트 인코딩 (예: "utf-8", "euc-kr").
                      None이면 기본 후보 목록만 사용한다.

    Returns:
        유니코드 문자열. data가 비어 있으면 "" 반환.
    """
    if not data:
        return ""

    # 힌트 인코딩을 후보 목록 맨 앞에 배치
    candidates: list[str] = []
    if hint_charset:
        candidates.append(hint_charset.lower().replace("-", ""))
    candidates += ["utf-8", "cp949", "euc_kr", "iso-8859-1"]

    for enc in candidates:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue

    # 후보 모두 실패 → chardet으로 최종 탐지
    detected = chardet.detect(data)
    enc = detected.get("encoding") or "utf-8"
    return data.decode(enc, errors="replace")


# ---------------------------------------------------------------------------
# 이메일 주소
# ---------------------------------------------------------------------------

def normalize_address(raw: Optional[str]) -> str:
    """'홍길동 <hong@ex.com>' 형식에서 이메일 주소만 소문자로 추출한다.

    "@" 가 없는 문자열은 이메일 주소로 간주하지 않으며 빈 문자열을 반환한다.
    (예: pypff 가 반환하는 "Unknown" 같은 플레이스홀더를 이메일로 처리하지 않음)

    Args:
        raw: 원시 주소 문자열 (MIME 인코딩 포함 가능).

    Returns:
        소문자 이메일 주소 문자열. "@" 를 포함하지 않으면 "" 반환.
    """
    if not raw:
        return ""
    raw = decode_mime_header(raw)
    _, addr = parseaddr(raw)
    addr = addr.strip()
    # "@" 없는 문자열은 유효한 이메일 주소가 아님 (예: "Unknown", "John Doe")
    if "@" not in addr:
        return ""
    return addr.lower()


def parse_address_list(raw: Optional[str]) -> list[str]:
    """쉼표/세미콜론 구분 주소 목록을 파싱한다.

    Outlook PST 의 PR_DISPLAY_TO 는 세미콜론 구분을 사용하므로 쉼표와 함께 처리한다.
    '<>' 내부의 구분자는 분리하지 않는다 (그룹 주소 대응).
    각 항목이 이메일 주소면 소문자 정규화된 주소, 이메일이 없으면 display name
    원문을 그대로 반환한다 (예: "Lokay  Michelle").

    Args:
        raw: "a@x.com, 홍길동 <b@y.com>; Lokay  Michelle" 형태의 원시 문자열.

    Returns:
        이메일 또는 display name 리스트. 공백/중복 빈 항목은 제외된다.
    """
    if not raw:
        return []
    raw = decode_mime_header(raw)
    # '<>' 내부는 보호하면서 쉼표·세미콜론 모두 구분자로 사용
    parts = re.split(r"[,;](?![^<]*>)", raw)
    result: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        addr = normalize_address(part)
        if addr:
            result.append(addr)
            continue
        # 이메일이 없으면 display name 원문을 정리해서 사용
        # parseaddr 로 name 추출 시도
        name, _ = parseaddr(part)
        name = (name or part).strip()
        if name:
            # 연속된 공백은 1칸으로 축약
            result.append(re.sub(r"\s+", " ", name))
    return result


def address_display(raw: Optional[str]) -> str:
    """주소 문자열을 '이름 <email>' 형식으로 정규화한다.

    이름이 없으면 이메일 주소만, 주소가 없으면 이름만 반환한다.
    YAML frontmatter 표시용으로 사용된다.

    Args:
        raw: 원시 주소 문자열 (MIME 인코딩 포함 가능).

    Returns:
        "이름 <email>" 또는 "email" 또는 "이름" 형태의 문자열.
    """
    if not raw:
        return ""
    raw = decode_mime_header(raw)
    name, addr = parseaddr(raw)
    name = name.strip()
    # "@" 없는 addr은 이메일 주소가 아님 — display name 후보로 처리
    if "@" in addr:
        addr = addr.lower().strip()
        if name and addr:
            return f"{name} <{addr}>"
        return addr or name
    # addr이 비어있거나 "@" 없는 경우: name 또는 raw 원문을 표시명으로 사용
    return name or raw
    return addr or name


# ---------------------------------------------------------------------------
# 날짜
# ---------------------------------------------------------------------------

def normalize_date(raw: Optional[str]) -> Optional[datetime]:
    """Date 헤더 문자열을 timezone-aware datetime 객체로 변환한다.

    표준 RFC 2822 형식을 먼저 시도하고, 실패하면 Outlook 날짜 포맷
    등 추가 형식을 순서대로 시도한다.

    Args:
        raw: 날짜 헤더 문자열 (예: "Mon, 01 Jan 2024 12:00:00 +0900").

    Returns:
        timezone-aware datetime 객체. 파싱 실패 시 None.
    """
    if not raw:
        return None
    # RFC 2822 표준 형식 (parsedate_to_datetime은 항상 tz-aware 반환)
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    # Outlook 등에서 사용하는 비표준 날짜 형식 fallback
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"):
        try:
            return datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
    return None


def date_to_iso(dt: Optional[datetime]) -> str:
    """datetime 객체를 ISO 8601 문자열로 변환한다.

    timezone 정보가 없으면 UTC로 가정해 +00:00을 붙인다.

    Args:
        dt: datetime 객체. None이면 빈 문자열 반환.

    Returns:
        "2024-01-15T09:30:00+09:00" 형태의 ISO 8601 문자열.
    """
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# 파일명 / 슬러그
# ---------------------------------------------------------------------------

def make_slug(text: str, max_len: int = 40) -> str:
    """제목 문자열을 URL-safe ASCII 슬러그로 변환한다.

    한글을 포함한 유니코드를 로마자로 음역하거나 제거해 파일명 안전 문자열을 생성한다.
    빈 결과가 되면 "no-subject"를 반환한다.

    Args:
        text:    원본 텍스트 (예: 메일 제목).
        max_len: 최대 슬러그 길이 (기본 40자, 단어 경계 기준으로 잘림).

    Returns:
        소문자 ASCII 슬러그 문자열.
    """
    s = slugify(text, allow_unicode=False, max_length=max_len, word_boundary=True)
    return s or "no-subject"


def make_msgid_short(msgid: str, length: int = 8) -> str:
    """Message-ID를 SHA-1 해시 앞 N자리로 축약한다.

    파일명 충돌 방지용 고유 식별자를 생성하는 데 사용한다.

    Args:
        msgid:  원본 Message-ID 문자열.
        length: 출력 해시 길이 (기본 8자).

    Returns:
        16진수 소문자 문자열 (예: "a3f2c1b0").
    """
    return hashlib.sha1(msgid.encode()).hexdigest()[:length]


def make_filename(dt: Optional[datetime], subject: str, msgid: str) -> str:
    """Markdown 파일명을 생성한다.

    형식: YYYYMMDD-HHMM__<slug>__<msgid8>.md
    날짜가 없으면 "00000000-0000"을 접두어로 사용한다.

    Args:
        dt:      발송 날짜 (None 허용).
        subject: 메일 제목.
        msgid:   Message-ID 문자열.

    Returns:
        파일명 문자열 (예: "20240115-0930__meeting-report__a3f2c1b0.md").
    """
    prefix = dt.strftime("%Y%m%d-%H%M") if dt else "00000000-0000"
    slug = make_slug(subject)
    short = make_msgid_short(msgid)
    return f"{prefix}__{slug}__{short}.md"


# ---------------------------------------------------------------------------
# 스레드 ID
# ---------------------------------------------------------------------------

def make_thread_id(references: list[str], in_reply_to: Optional[str], msgid: str) -> str:
    """스레드 루트 Message-ID를 기반으로 스레드 식별자를 생성한다.

    References 헤더의 첫 번째 항목(스레드 루트)을 우선 사용하고,
    없으면 In-Reply-To, 그것도 없으면 현재 메시지 ID를 사용한다.

    Args:
        references:   References 헤더에서 파싱된 Message-ID 리스트.
        in_reply_to:  In-Reply-To 헤더 값.
        msgid:        현재 메시지의 Message-ID.

    Returns:
        "t_" + SHA-1 앞 8자리 형태의 스레드 ID (예: "t_a3f2c1b0").
    """
    root = references[0] if references else (in_reply_to or msgid)
    return "t_" + hashlib.sha1(root.encode()).hexdigest()[:8]
