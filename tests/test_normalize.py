"""
tests/test_normalize.py — normalize.py 스모크 테스트

주소 / 날짜 / 파일명 / 스레드 ID 정규화 순수 함수들의 핵심 동작을 검증한다.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from lib.normalize import (
    address_display,
    date_to_iso,
    decode_mime_header,
    format_address,
    format_address_list,
    make_filename,
    make_msgid_short,
    make_slug,
    make_thread_id,
    normalize_address,
    normalize_date,
    parse_address_list,
    safe_decode,
)


class TestDecodeMimeHeader:
    def test_empty_and_none(self) -> None:
        assert decode_mime_header(None) == ""
        assert decode_mime_header("") == ""

    def test_plain_ascii(self) -> None:
        assert decode_mime_header("Hello") == "Hello"

    def test_rfc2047_utf8_b(self) -> None:
        # "테스" 를 base64 UTF-8 로 인코딩한 MIME 헤더
        raw = "=?UTF-8?B?7YWM7Iqk?="
        assert decode_mime_header(raw) == "테스"

    def test_rfc2047_mixed(self) -> None:
        raw = "Subject: =?UTF-8?B?7YWM7Iqk?="
        assert "테스" in decode_mime_header(raw)


class TestSafeDecode:
    def test_empty_bytes(self) -> None:
        assert safe_decode(b"") == ""

    def test_utf8_passthrough(self) -> None:
        assert safe_decode("한글".encode("utf-8")) == "한글"

    def test_cp949_fallback(self) -> None:
        assert safe_decode("한글".encode("cp949")) == "한글"

    def test_hint_charset_priority(self) -> None:
        # cp949 로 인코딩한 뒤 hint 로 지정하면 그 인코딩을 먼저 시도
        data = "안녕".encode("cp949")
        assert safe_decode(data, hint_charset="cp949") == "안녕"


class TestNormalizeAddress:
    def test_empty(self) -> None:
        assert normalize_address(None) == ""
        assert normalize_address("") == ""

    def test_simple(self) -> None:
        assert normalize_address("Alice <Alice@Example.COM>") == "alice@example.com"

    def test_no_at_sign_returns_empty(self) -> None:
        # pypff 가 반환하는 "Unknown" 같은 플레이스홀더는 주소가 아님
        assert normalize_address("Unknown") == ""
        assert normalize_address("John Doe") == ""


class TestParseAddressList:
    def test_empty(self) -> None:
        assert parse_address_list(None) == []
        assert parse_address_list("") == []

    def test_comma_separated(self) -> None:
        result = parse_address_list("a@x.com, b@y.com")
        assert result == ["a@x.com", "b@y.com"]

    def test_semicolon_separated_outlook(self) -> None:
        # Outlook PST 의 PR_DISPLAY_TO 는 세미콜론 구분
        result = parse_address_list("a@x.com; b@y.com")
        assert result == ["a@x.com", "b@y.com"]

    def test_display_name_without_email(self) -> None:
        # 이메일이 없으면 display name 을 원문 그대로 사용
        result = parse_address_list("Lokay  Michelle; alice@x.com")
        assert "alice@x.com" in result
        assert any("Lokay" in r for r in result)


class TestAddressDisplay:
    def test_with_name(self) -> None:
        assert address_display("홍길동 <hong@ex.com>") == "홍길동 <hong@ex.com>"

    def test_addr_only(self) -> None:
        assert address_display("hong@ex.com") == "hong@ex.com"

    def test_no_at_uses_display_name(self) -> None:
        assert address_display("Unknown") == "Unknown"


class TestNormalizeDate:
    def test_none_and_empty(self) -> None:
        assert normalize_date(None) is None
        assert normalize_date("") is None

    def test_rfc2822(self) -> None:
        dt = normalize_date("Mon, 01 Jan 2024 12:00:00 +0900")
        assert dt is not None
        assert dt.year == 2024 and dt.month == 1
        assert dt.tzinfo is not None

    def test_invalid_returns_none(self) -> None:
        assert normalize_date("not-a-date") is None


class TestDateToIso:
    def test_none(self) -> None:
        assert date_to_iso(None) == ""

    def test_naive_gets_utc(self) -> None:
        dt = datetime(2024, 1, 1, 12, 0, 0)
        iso = date_to_iso(dt)
        assert iso.endswith("+00:00")

    def test_aware_preserved(self) -> None:
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert date_to_iso(dt) == "2024-01-01T12:00:00+00:00"


class TestFilename:
    def test_make_slug_korean(self) -> None:
        s = make_slug("회의록 2024년 1분기")
        assert s  # non-empty
        assert s == s.lower()

    def test_make_slug_empty(self) -> None:
        assert make_slug("") == "no-subject"

    def test_make_msgid_short(self) -> None:
        s = make_msgid_short("<abc@example.com>")
        assert len(s) == 8
        assert all(c in "0123456789abcdef" for c in s)

    def test_make_filename_with_date(self) -> None:
        dt = datetime(2024, 3, 15, 9, 30, tzinfo=timezone.utc)
        name = make_filename(dt, "테스트", "<abc@x>")
        assert name.startswith("20240315-0930__")
        assert name.endswith(".md")

    def test_make_filename_no_date(self) -> None:
        name = make_filename(None, "x", "<abc@x>")
        assert name.startswith("00000000-0000__")


class TestFormatAddress:
    """frontmatter canonical 주소 포맷."""

    def test_empty(self) -> None:
        assert format_address(None) == ""
        assert format_address("") == ""
        assert format_address("   ") == ""

    def test_email_only_lowercased(self) -> None:
        assert format_address("ALICE@Example.COM") == "alice@example.com"

    def test_name_and_email(self) -> None:
        assert format_address("홍길동 <Hong@Ex.COM>") == "홍길동 <hong@ex.com>"

    def test_name_only(self) -> None:
        # "@" 없는 문자열 — display name 으로 반환
        out = format_address("Lokay Michelle")
        assert "Lokay" in out
        assert "<" not in out


class TestFormatAddressList:
    def test_empty(self) -> None:
        assert format_address_list(None) == []
        assert format_address_list("") == []

    def test_outlook_semicolon(self) -> None:
        result = format_address_list("Alice <A@X.COM>; bob@y.com")
        assert result == ["Alice <a@x.com>", "bob@y.com"]

    def test_mixed_name_and_email(self) -> None:
        result = format_address_list("Lokay Michelle; alice@x.com")
        assert "alice@x.com" in result
        assert any("Lokay" in r for r in result)

    def test_angle_bracket_protected_from_split(self) -> None:
        """<> 내부의 쉼표/세미콜론은 구분자로 쓰이지 않는다."""
        # 현재 split 로직이 <> 를 보호하므로 단일 주소로 파싱된다
        result = format_address_list("Group <a@x.com>")
        assert len(result) == 1


class TestHtmlPostprocess:
    """pst2md._clean_md_body 가 invisible 유니코드를 정규화하는지."""

    def _clean(self, text: str) -> str:
        # pst2md 는 lib 가 아니므로 sys.path 기반으로 호출
        import importlib
        mod = importlib.import_module("pst2md")
        return mod._clean_md_body(text)

    def test_nbsp_to_space(self) -> None:
        # \u00a0 (NBSP) 는 일반 공백이 되어야 한다
        out = self._clean("hello\u00a0world")
        assert "\u00a0" not in out
        assert "hello world" in out

    def test_zero_width_removed(self) -> None:
        # ZWSP / ZWNJ / ZWJ / BOM 모두 제거
        raw = "A\u200bB\u200cC\u200dD\ufeffE"
        out = self._clean(raw)
        assert out == "ABCDE"

    def test_idempotent(self) -> None:
        """동일 입력 → 동일 출력."""
        src = "foo\u00a0bar\u200bbaz\n\n\n두번째"
        assert self._clean(src) == self._clean(src)


class TestThreadId:
    def test_root_from_references(self) -> None:
        tid = make_thread_id(["<root@x>", "<a@x>"], "<a@x>", "<b@x>")
        # references[0] 가 우선
        assert tid == make_thread_id(["<root@x>"], None, "<other@x>")

    def test_fallback_to_in_reply_to(self) -> None:
        tid = make_thread_id([], "<a@x>", "<b@x>")
        assert tid == make_thread_id([], "<a@x>", "<other@x>")

    def test_fallback_to_msgid(self) -> None:
        tid = make_thread_id([], None, "<b@x>")
        assert tid.startswith("t_")
        assert len(tid) == 10  # "t_" + 8 hex
