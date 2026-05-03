"""tests/test_mailgrep.py — scripts/mailgrep.py 테스트"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from mailgrep import (  # noqa: E402
    _build_fts_match,
    _escape_fts5,
    build_query,
    parse_smart_query,
    _expand_month,
)


# ---------------------------------------------------------------------------
# _escape_fts5 — 안전 모드: 모든 토큰을 phrase 로 인용
# ---------------------------------------------------------------------------

class TestEscapeFts5:
    def test_plain_keyword_quoted(self):
        """일반 토큰도 일관성을 위해 인용한다."""
        assert _escape_fts5("hello") == '"hello"'

    def test_empty_string(self):
        assert _escape_fts5("") == ""

    def test_whitespace_only(self):
        assert _escape_fts5("   ") == ""

    def test_double_quote_escaped(self):
        """입력 따옴표는 ""로 이스케이프되고 phrase 로 감싸진다."""
        result = _escape_fts5('say "hello"')
        assert '""' in result
        assert result.startswith('"') and result.endswith('"')

    def test_special_chars_safe(self):
        """+, :, /, ., @, *, (), ^ 같은 문자도 안전하게 인용된다."""
        for tok in ("C++", "a@b.com", "2024-05", "foo/bar", "x*y", "(test)", "^start"):
            assert _escape_fts5(tok) == f'"{tok}"', tok

    def test_and_operator_quoted_in_safe_mode(self):
        """안전 모드에서 AND/OR/NOT 도 일반 토큰으로 인용 (raw-fts 가 아닌 한)."""
        result = _escape_fts5("hello AND world")
        assert result == '"hello" "AND" "world"'

    def test_korean_keyword_quoted(self):
        assert _escape_fts5("견적서") == '"견적서"'

    def test_multiple_tokens_each_quoted(self):
        result = _escape_fts5("foo bar baz")
        assert result == '"foo" "bar" "baz"'

    def test_strips_leading_trailing_spaces(self):
        assert _escape_fts5("  keyword  ") == '"keyword"'


# ---------------------------------------------------------------------------
# _build_fts_match — raw vs safe 모드 디스패처
# ---------------------------------------------------------------------------

class TestBuildFtsMatch:
    def test_safe_mode_quotes_token(self):
        assert _build_fts_match("hello", raw_fts=False) == '"hello"'

    def test_raw_mode_passes_through(self):
        """raw 모드에선 사용자가 직접 FTS5 연산자를 쓸 수 있다."""
        assert _build_fts_match("foo OR bar*", raw_fts=True) == "foo OR bar*"

    def test_raw_mode_strips_whitespace(self):
        assert _build_fts_match("  foo AND bar  ", raw_fts=True) == "foo AND bar"


# ---------------------------------------------------------------------------
# build_query
# ---------------------------------------------------------------------------

@pytest.fixture
def dummy_conn():
    """인메모리 SQLite 연결 (쿼리 빌더 테스트용, 실제 실행 안 함)"""
    conn = sqlite3.connect(":memory:")
    yield conn
    conn.close()


class TestBuildQuery:
    def test_empty_query_no_fts(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False
        )
        assert "messages_fts" not in sql
        assert "LIMIT" in sql

    def test_fts_match_added_for_query(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "invoice", "", "", "", "", "", "", 50, False, False
        )
        assert "MATCH" in sql
        # 안전 모드에서 토큰은 phrase 로 인용됨
        assert '"invoice"' in params[0]

    def test_body_filter_prefix(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            body_filter="payment"
        )
        # body:(...) 컬럼 한정 phrase
        assert 'body:("payment")' in params[0]

    def test_subject_filter_prefix(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            subject_query="견적"
        )
        assert 'subject:("견적")' in params[0]

    def test_combined_query_and_body(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "invoice", "", "", "", "", "", "", 50, False, False,
            body_filter="amount"
        )
        match_param = params[0]
        assert '"invoice"' in match_param
        assert 'body:("amount")' in match_param

    def test_special_chars_in_query_do_not_break(self, dummy_conn):
        """C++, 이메일 주소, 날짜 형식 같은 punctuation 이 OperationalError 없이 통과."""
        sql, params = build_query(
            dummy_conn, "C++ a@b.com 2024-05", "", "", "", "",
            "", "", 50, False, False,
        )
        # 모든 토큰이 phrase 로 인용되어야 함
        match_param = params[0]
        assert '"C++"' in match_param
        assert '"a@b.com"' in match_param
        assert '"2024-05"' in match_param

    def test_raw_fts_passes_through(self, dummy_conn):
        """raw_fts=True 면 사용자 입력이 그대로 FTS 절에 들어간다."""
        sql, params = build_query(
            dummy_conn, "foo OR bar*", "", "", "", "",
            "", "", 50, False, False, raw_fts=True,
        )
        assert params[0] == "foo OR bar*"

    def test_from_filter_like(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "홍길동", "", "", "", "", "", 50, False, False
        )
        assert "from_name LIKE" in sql
        assert "%홍길동%" in params

    def test_to_filter(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "alice@example.com", "", "", "", "", 50, False, False
        )
        assert "to_addrs LIKE" in sql
        assert "%alice@example.com%" in params

    def test_after_filter(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "2023-01-01", "", "", "", 50, False, False
        )
        assert "m.date >=" in sql
        assert "2023-01-01T00:00:00+00:00" in params

    def test_before_filter(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "2023-12-31", "", "", 50, False, False
        )
        assert "m.date <=" in sql
        assert "2023-12-31T23:59:59+00:00" in params

    def test_folder_filter(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "Inbox/Project", "", 50, False, False
        )
        assert "folder LIKE" in sql
        assert "%Inbox/Project%" in params

    def test_thread_exact_match(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "t_abc123", 50, False, False
        )
        assert "m.thread = ?" in sql
        assert "t_abc123" in params

    def test_limit_appended(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 25, False, False
        )
        assert params[-1] == 25

    def test_json_select(self, dummy_conn):
        sql, _ = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, True, False
        )
        assert "json_object" in sql

    def test_paths_only_select(self, dummy_conn):
        sql, _ = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, True
        )
        assert "SELECT m.path" in sql

    def test_order_by_date_desc(self, dummy_conn):
        sql, _ = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False
        )
        assert "ORDER BY m.date DESC" in sql

    def test_no_conditions_no_where(self, dummy_conn):
        sql, _ = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False
        )
        assert "WHERE" not in sql


# ---------------------------------------------------------------------------
# _expand_month
# ---------------------------------------------------------------------------

class TestExpandMonth:
    def test_full_date_unchanged(self):
        assert _expand_month("2023-05-15") == "2023-05-15"

    def test_month_expanded(self):
        assert _expand_month("2023-05") == "2023-05-01"

    def test_invalid_returns_empty(self):
        assert _expand_month("2023") == ""

    def test_invalid_text_returns_empty(self):
        assert _expand_month("last-week") == ""


# ---------------------------------------------------------------------------
# parse_smart_query
# ---------------------------------------------------------------------------

class TestParseSmartQuery:
    def test_plain_query_unchanged(self):
        result = parse_smart_query("invoice")
        assert result["query"] == "invoice"
        assert result["from_filter"] == ""

    def test_from_prefix(self):
        result = parse_smart_query("from:홍길동 invoice")
        assert result["from_filter"] == "홍길동"
        assert result["query"] == "invoice"

    def test_after_prefix_date(self):
        result = parse_smart_query("after:2023-05-01")
        assert result["after"] == "2023-05-01"

    def test_after_prefix_month_expanded(self):
        result = parse_smart_query("after:2023-05")
        assert result["after"] == "2023-05-01"

    def test_has_attachment(self):
        result = parse_smart_query("has:attachment invoice")
        assert result["has_attachment"] is True
        assert result["query"] == "invoice"

    def test_subject_prefix(self):
        result = parse_smart_query("subject:견적서")
        assert result["subject_query"] == "견적서"
        assert result["query"] == ""

    def test_folder_prefix(self):
        result = parse_smart_query("folder:Inbox keyword")
        assert result["folder"] == "Inbox"
        assert result["query"] == "keyword"

    def test_multiple_prefixes(self):
        result = parse_smart_query("from:alice after:2023-01 has:attachment report")
        assert result["from_filter"] == "alice"
        assert result["after"] == "2023-01-01"
        assert result["has_attachment"] is True
        assert result["query"] == "report"

    def test_unknown_prefix_kept_as_query(self):
        result = parse_smart_query("size:large invoice")
        assert "size:large" in result["query"]

    def test_has_attachment_adds_constraint(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            has_attachment=True,
        )
        assert "n_attachments" in sql
