"""tests/test_mailgrep.py — scripts/mailgrep.py 테스트"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from mailgrep import _escape_fts5, build_query


# ---------------------------------------------------------------------------
# _escape_fts5
# ---------------------------------------------------------------------------

class TestEscapeFts5:
    def test_plain_keyword(self):
        assert _escape_fts5("hello") == "hello"

    def test_empty_string(self):
        assert _escape_fts5("") == ""

    def test_whitespace_only(self):
        assert _escape_fts5("   ") == ""

    def test_double_quote_escaped(self):
        result = _escape_fts5('say "hello"')
        assert '""' in result

    def test_asterisk_wrapped(self):
        result = _escape_fts5("hello*")
        assert result.startswith('"') and result.endswith('"')

    def test_parenthesis_wrapped(self):
        result = _escape_fts5("(test)")
        assert result.startswith('"') and result.endswith('"')

    def test_caret_wrapped(self):
        result = _escape_fts5("^start")
        assert result.startswith('"') and result.endswith('"')

    def test_and_operator_not_wrapped(self):
        """AND/OR/NOT 연산자는 그대로 허용"""
        result = _escape_fts5("hello AND world")
        assert result == "hello AND world"

    def test_korean_keyword(self):
        result = _escape_fts5("견적서")
        assert result == "견적서"

    def test_strips_leading_trailing_spaces(self):
        result = _escape_fts5("  keyword  ")
        assert result == "keyword"


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
        assert "invoice" in params

    def test_body_filter_prefix(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            body_filter="payment"
        )
        assert "body:payment" in params[0]

    def test_combined_query_and_body(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "invoice", "", "", "", "", "", "", 50, False, False,
            body_filter="amount"
        )
        match_param = params[0]
        assert "invoice" in match_param
        assert "body:amount" in match_param

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
