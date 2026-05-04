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
# _escape_fts5 — 안전 모드: 모든 토큰을 phrase 로 인용 + prefix wildcard
# ---------------------------------------------------------------------------

class TestEscapeFts5:
    def test_plain_keyword_quoted_with_wildcard(self):
        """기본 모드: phrase 인용 + prefix wildcard 부착."""
        assert _escape_fts5("hello") == '"hello"*'

    def test_prefix_match_disabled(self):
        """prefix_match=False 면 wildcard 미부착."""
        assert _escape_fts5("hello", prefix_match=False) == '"hello"'

    def test_empty_string(self):
        assert _escape_fts5("") == ""

    def test_whitespace_only(self):
        assert _escape_fts5("   ") == ""

    def test_double_quote_escaped(self):
        """입력 따옴표는 ""로 이스케이프되고 phrase 로 감싸진다."""
        result = _escape_fts5('say "hello"')
        assert '""' in result
        assert '"' in result

    def test_special_chars_safe(self):
        """+, :, /, ., @, *, (), ^ 같은 문자도 안전하게 인용된다."""
        for tok in ("C++", "a@b.com", "2024-05", "foo/bar", "x*y", "(test)", "^start"):
            assert _escape_fts5(tok, prefix_match=False) == f'"{tok}"', tok

    def test_and_operator_quoted_in_safe_mode(self):
        """안전 모드에서 AND/OR/NOT 도 일반 토큰으로 인용 (raw-fts 가 아닌 한)."""
        result = _escape_fts5("hello AND world", prefix_match=False)
        assert result == '"hello" "AND" "world"'

    def test_korean_keyword_quoted_with_wildcard(self):
        """P3: 한글 짧은 query 는 prefix wildcard 로 견적서/견적가 잡는다."""
        assert _escape_fts5("견적") == '"견적"*'

    def test_multiple_tokens_each_quoted(self):
        result = _escape_fts5("foo bar baz")
        assert result == '"foo"* "bar"* "baz"*'

    def test_strips_leading_trailing_spaces(self):
        assert _escape_fts5("  keyword  ") == '"keyword"*'


# ---------------------------------------------------------------------------
# _build_fts_match — raw vs safe 모드 디스패처
# ---------------------------------------------------------------------------

class TestBuildFtsMatch:
    def test_safe_mode_quotes_token_with_wildcard(self):
        assert _build_fts_match("hello", raw_fts=False) == '"hello"*'

    def test_safe_mode_can_disable_prefix(self):
        assert _build_fts_match("hello", raw_fts=False, prefix_match=False) == '"hello"'

    def test_raw_mode_passes_through(self):
        """raw 모드에선 사용자가 직접 FTS5 연산자를 쓸 수 있다."""
        assert _build_fts_match("foo OR bar*", raw_fts=True) == "foo OR bar*"

    def test_raw_mode_strips_whitespace(self):
        assert _build_fts_match("  foo AND bar  ", raw_fts=True) == "foo AND bar"


# ---------------------------------------------------------------------------
# 한글 부분일치 — 실제 SQLite FTS5 + prefix index 통합 (P3)
# ---------------------------------------------------------------------------

class TestKoreanPartialMatch:
    """unicode61 + prefix='2 3 4' + _escape_fts5 wildcard 의 종단 동작 검증."""

    @pytest.fixture
    def fts_db(self):
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE m USING fts5("
            "subject, body, tokenize='unicode61', prefix='2 3 4')"
        )
        rows = [
            ("견적서 발송", "내일까지 견적서 보내드립니다."),
            ("계약서 검토", "계약서 첨부 드립니다."),
            ("회의록 정리", "회의록 작성 완료."),
            ("invoice", "Please review the invoice for 2024-05."),
        ]
        conn.executemany("INSERT INTO m(subject, body) VALUES (?, ?)", rows)
        yield conn
        conn.close()

    def _match_count(self, conn, query: str) -> int:
        match = _escape_fts5(query)
        return conn.execute("SELECT COUNT(*) FROM m WHERE m MATCH ?", (match,)).fetchone()[0]

    def test_partial_korean_two_chars(self, fts_db):
        """'견적' 2글자 query 가 견적서 토큰을 잡아야 한다."""
        assert self._match_count(fts_db, "견적") == 1

    def test_partial_korean_other_words(self, fts_db):
        """계약·회의 같은 다른 한글 prefix 도 동일하게 동작."""
        assert self._match_count(fts_db, "계약") == 1
        assert self._match_count(fts_db, "회의") == 1

    def test_full_token_still_matches(self, fts_db):
        """완전한 토큰도 prefix wildcard 와 함께 정상 매칭."""
        assert self._match_count(fts_db, "견적서") == 1

    def test_english_prefix(self, fts_db):
        """영어 prefix 도 동일 — 'invo' → invoice."""
        assert self._match_count(fts_db, "invo") == 1

    def test_no_match_for_unknown(self, fts_db):
        assert self._match_count(fts_db, "없는단어") == 0


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
        # 안전 모드에서 토큰은 phrase 로 인용 + prefix wildcard
        assert '"invoice"*' in params[0]

    def test_body_filter_prefix(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            body_filter="payment"
        )
        # body:(...) 컬럼 한정 phrase + wildcard
        assert 'body:("payment"*)' in params[0]

    def test_subject_filter_prefix(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "", "", "", "", "", "", "", 50, False, False,
            subject_query="견적"
        )
        assert 'subject:("견적"*)' in params[0]

    def test_combined_query_and_body(self, dummy_conn):
        sql, params = build_query(
            dummy_conn, "invoice", "", "", "", "", "", "", 50, False, False,
            body_filter="amount"
        )
        match_param = params[0]
        assert '"invoice"*' in match_param
        assert 'body:("amount"*)' in match_param

    def test_special_chars_in_query_do_not_break(self, dummy_conn):
        """C++, 이메일 주소, 날짜 형식 같은 punctuation 이 OperationalError 없이 통과."""
        sql, params = build_query(
            dummy_conn, "C++ a@b.com 2024-05", "", "", "", "",
            "", "", 50, False, False,
        )
        # 모든 토큰이 phrase 로 인용 + wildcard 부착
        match_param = params[0]
        assert '"C++"*' in match_param
        assert '"a@b.com"*' in match_param
        assert '"2024-05"*' in match_param

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


# ---------------------------------------------------------------------------
# --all-archives — 기본 DB 가 없어도 다른 archive.roots 를 검색한다 (P7)
# ---------------------------------------------------------------------------

class TestAllArchivesPrecheck:
    """P7: 기본 archive.root 의 index.sqlite 가 없어도 --all-archives 는
    archive.roots 에 등록된 다른 DB 들을 정상 검색해야 한다."""

    @staticmethod
    def _make_archive(root: Path, msgid: str = "<m1>", subject: str = "test") -> None:
        """root/index.sqlite 에 messages 1행이 있는 최소 DB 를 만든다."""
        import mailgrep as mg
        from build_index import init_schema

        root.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(root / "index.sqlite"))
        try:
            init_schema(conn)
            conn.execute(
                "INSERT INTO messages (msgid, path, subject, date) VALUES (?, ?, ?, ?)",
                (msgid, str(root / "m.md"), subject, "2024-01-01T00:00:00+00:00"),
            )
            conn.execute(
                "INSERT INTO messages_fts (rowid, subject, body) VALUES "
                "((SELECT rowid FROM messages WHERE msgid=?), ?, ?)",
                (msgid, subject, "body content"),
            )
            conn.commit()
        finally:
            conn.close()
        del mg  # noqa: F841

    def test_all_archives_works_when_default_db_missing(
        self, tmp_path, monkeypatch,
    ) -> None:
        """기본 archive.root 의 DB 가 없고 archive.roots 의 DB 만 있을 때
        --all-archives 가 조기 종료하지 않고 검색을 수행한다."""
        from click.testing import CliRunner

        import mailgrep as mg

        primary = tmp_path / "primary"     # DB 없음
        secondary = tmp_path / "secondary"  # DB 있음
        primary.mkdir()
        self._make_archive(secondary, msgid="<m1>", subject="invoice")

        cfg = {
            "archive": {
                "root": str(primary),
                "roots": [str(secondary)],
            },
            "tools": {},
        }
        monkeypatch.setattr(mg, "load_config", lambda: cfg)

        runner = CliRunner()
        result = runner.invoke(mg.main, ["invoice", "--all-archives"])
        assert result.exit_code == 0, result.output
        assert "인덱스 없음" not in result.output
        assert "invoice" in result.output

    def test_all_archives_fails_when_no_db_anywhere(
        self, tmp_path, monkeypatch,
    ) -> None:
        """archive.root / archive.roots 모두 DB 가 없으면 명확한 오류로 종료."""
        from click.testing import CliRunner

        import mailgrep as mg

        primary = tmp_path / "primary"
        secondary = tmp_path / "secondary"
        primary.mkdir()
        secondary.mkdir()

        cfg = {
            "archive": {
                "root": str(primary),
                "roots": [str(secondary)],
            },
            "tools": {},
        }
        monkeypatch.setattr(mg, "load_config", lambda: cfg)

        runner = CliRunner()
        result = runner.invoke(mg.main, ["invoice", "--all-archives"])
        assert result.exit_code != 0
        assert "사용 가능한 아카이브 인덱스가 없습니다" in result.output

    def test_default_mode_still_requires_default_db(
        self, tmp_path, monkeypatch,
    ) -> None:
        """--all-archives 없을 때는 기존처럼 기본 DB 가 없으면 즉시 실패."""
        from click.testing import CliRunner

        import mailgrep as mg

        primary = tmp_path / "primary"
        primary.mkdir()
        cfg = {
            "archive": {"root": str(primary), "roots": []},
            "tools": {},
        }
        monkeypatch.setattr(mg, "load_config", lambda: cfg)

        runner = CliRunner()
        result = runner.invoke(mg.main, ["invoice"])
        assert result.exit_code != 0
        assert "인덱스 없음" in result.output
