"""tests/test_mailview.py — scripts/mailview.py 테스트"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import sqlite3

from mailview import (
    build_full_viewer_cmd,
    build_fzf_preview_cmd,
    resolve_glow_style,
    get_editor,
    get_attachments_from_md,
    get_folder_list,
    get_recent_paths,
    _print_fzf_lines,
    _FZF_COL_HEADER,
    _visual_width,
    _visual_truncate,
    _visual_pad,
    _read_frontmatter_fields,
    _build_fzf_exec_commands,
    handle_delete_message,
    handle_bulk_delete,
    auto_update_index,
    extract_urls,
    get_tag_list,
    _update_frontmatter_tags,
    handle_tag_message,
    find_duplicate_groups,
    format_stats_for_display,
    build_thread_tree,
    format_thread_tree,
)


# ---------------------------------------------------------------------------
# build_fzf_preview_cmd
# ---------------------------------------------------------------------------

class TestBuildFzfPreviewCmd:
    def test_linux_uses_single_quotes(self):
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", "/usr/bin/bat")
        assert "'{2}'" in cmd

    def test_windows_uses_double_quotes(self):
        with patch("mailview.detect_platform", return_value="windows"):
            cmd = build_fzf_preview_cmd("C:/glow.exe", "C:/bat.exe")
        assert '"{2}"' in cmd

    def test_glow_path_included(self):
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None)
        assert "/usr/bin/glow" in cmd

    def test_bat_fallback_included_when_present(self):
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", "/usr/bin/bat")
        assert "/usr/bin/bat" in cmd

    def test_cat_fallback_when_no_bat(self):
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None)
        assert "cat" in cmd

    def test_windows_type_fallback_when_no_bat(self):
        with patch("mailview.detect_platform", return_value="windows"):
            cmd = build_fzf_preview_cmd("C:/glow.exe", None)
        assert "type" in cmd

    def test_glow_style_flag_present(self):
        """-s 스타일 플래그가 항상 포함된다 (테마 파일 또는 dark 폴백)."""
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None)
        assert "-s" in cmd
        # mocha-glow.json 이 scripts/lib/ 에 있으므로 경로가 포함돼야 함
        assert "mocha-glow.json" in cmd or "-s 'dark'" in cmd

    def test_explicit_builtin_style_used(self):
        """glow_style 에 내장 테마명을 전달하면 그 값이 -s 에 사용된다."""
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None, glow_style="dracula")
        assert "-s 'dracula'" in cmd

    def test_explicit_path_style_used(self):
        """glow_style 에 파일 경로를 전달하면 그 경로가 -s 에 사용된다."""
        with patch("mailview.detect_platform", return_value="linux"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None, glow_style="/my/theme.json")
        assert "-s '/my/theme.json'" in cmd

    def test_wsl_uses_single_quotes(self):
        """WSL 은 linux 분기와 동일하게 단일 인용부호 사용"""
        with patch("mailview.detect_platform", return_value="wsl"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None)
        assert "'{2}'" in cmd


# ---------------------------------------------------------------------------
# build_full_viewer_cmd — Enter 로 전체 열람할 때의 argv
# ---------------------------------------------------------------------------

class TestBuildFullViewerCmd:
    def test_default_uses_glow_with_pager(self):
        cmd = build_full_viewer_cmd(
            "/tmp/mail.md", "/usr/bin/glow", "dark",
        )
        assert cmd == ["/usr/bin/glow", "-p", "-s", "dark", "/tmp/mail.md"]

    def test_glow_viewer_explicit(self):
        cmd = build_full_viewer_cmd(
            "/tmp/mail.md", "/usr/bin/glow", "dracula",
            mdcat_path="/usr/bin/mdcat", viewer="glow",
        )
        assert cmd[0] == "/usr/bin/glow"
        assert "-p" in cmd  # pager 유지

    def test_mdcat_viewer_no_pager_local_only(self):
        """mdcat 선택 시 pager 미사용 + --local 로 이미지 인라인 렌더."""
        cmd = build_full_viewer_cmd(
            "/tmp/mail.md", "/usr/bin/glow", "dark",
            mdcat_path="/usr/bin/mdcat", viewer="mdcat",
        )
        assert cmd == ["/usr/bin/mdcat", "--local", "/tmp/mail.md"]
        assert "-p" not in cmd   # pager 붙이면 less 경유 → 이미지 깨짐
        assert "glow" not in " ".join(cmd)

    def test_mdcat_missing_falls_back_to_glow(self):
        """viewer='mdcat' 이어도 mdcat_path=None 이면 glow 로 폴백."""
        cmd = build_full_viewer_cmd(
            "/tmp/mail.md", "/usr/bin/glow", "dark",
            mdcat_path=None, viewer="mdcat",
        )
        assert cmd[0] == "/usr/bin/glow"
        assert cmd[-1] == "/tmp/mail.md"

    def test_path_with_spaces_preserved_verbatim(self):
        """argv 리스트이므로 쉘 인용 불필요 — 공백 경로 그대로 전달."""
        cmd = build_full_viewer_cmd(
            "/tmp/my mail.md", "/usr/bin/glow", "dark",
        )
        assert cmd[-1] == "/tmp/my mail.md"


# ---------------------------------------------------------------------------
# get_editor
# ---------------------------------------------------------------------------

class TestGetEditor:
    def test_returns_string(self):
        editor = get_editor()
        assert isinstance(editor, str)
        assert editor  # 비어 있으면 안 됨

    def test_env_editor_used(self, monkeypatch):
        monkeypatch.setenv("EDITOR", "vim")
        assert get_editor() == "vim"

    def test_default_linux(self, monkeypatch):
        monkeypatch.delenv("EDITOR", raising=False)
        with patch("mailview.detect_platform", return_value="linux"):
            editor = get_editor()
        assert editor == "nano"

    def test_default_windows(self, monkeypatch):
        monkeypatch.delenv("EDITOR", raising=False)
        with patch("mailview.detect_platform", return_value="windows"):
            editor = get_editor()
        assert editor == "notepad"


# ---------------------------------------------------------------------------
# get_attachments_from_md
# ---------------------------------------------------------------------------

class TestGetAttachmentsFromMd:
    def _make_md(self, tmp_path: Path, frontmatter: str) -> str:
        md = tmp_path / "test.md"
        md.write_text(f"---\n{frontmatter}\n---\n\nbody", encoding="utf-8")
        return str(md)

    def test_no_frontmatter_returns_empty(self, tmp_path):
        md = tmp_path / "plain.md"
        md.write_text("no frontmatter here", encoding="utf-8")
        result = get_attachments_from_md(str(md))
        assert result == []

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_attachments_from_md(str(tmp_path / "nonexistent.md"))
        assert result == []

    def test_empty_attachments_returns_empty(self, tmp_path):
        fm = 'subject: "Test"\nattachments: []\n'
        path = self._make_md(tmp_path, fm)
        result = get_attachments_from_md(path)
        assert result == []

    def test_attachment_with_existing_file(self, tmp_path, monkeypatch):
        """실제로 존재하는 파일을 가리키는 attachment 는 반환된다."""
        # 실제 포맷: attachment_yaml_entry() 가 생성하는 한 줄 인라인 YAML
        # '  - {name: "doc.pdf", sha256: "abc123...", size: 1024, path: "attachments/abc/doc.pdf"}'
        (tmp_path / "attachments" / "abc").mkdir(parents=True, exist_ok=True)
        att_abs = tmp_path / "attachments" / "abc" / "doc.pdf"
        att_abs.write_bytes(b"%PDF-1.4")

        with patch("mailview.archive_root", return_value=tmp_path), \
             patch("mailview.load_config", return_value={}):
            fm = (
                'subject: "Test"\n'
                'attachments:\n'
                '  - {name: "doc.pdf", sha256: "abc12345678901...", size: 8, '
                'path: "attachments/abc/doc.pdf"}\n'
            )
            md_path = self._make_md(tmp_path, fm)
            result = get_attachments_from_md(md_path)

        assert len(result) == 1
        assert result[0]["name"] == "doc.pdf"
        assert "doc.pdf" in result[0]["abs_path"]

    def test_attachment_nonexistent_file_skipped(self, tmp_path):
        """존재하지 않는 파일을 가리키는 attachment 는 건너뜀"""
        with patch("mailview.archive_root", return_value=tmp_path), \
             patch("mailview.load_config", return_value={}):
            fm = (
                'subject: "Test"\n'
                'attachments:\n'
                '  - {name: "ghost.pdf", sha256: "abc123...", size: 0, '
                'path: "attachments/ghost/ghost.pdf"}\n'
            )
            md_path = self._make_md(tmp_path, fm)
            result = get_attachments_from_md(md_path)
        assert result == []


# ---------------------------------------------------------------------------
# _print_fzf_lines
# ---------------------------------------------------------------------------

class TestPrintFzfLines:
    def test_header_printed_first(self, tmp_path, capsys):
        db_file = tmp_path / "index.sqlite"
        import sqlite3
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "CREATE TABLE messages (path TEXT, date TEXT, from_name TEXT, from_addr TEXT, subject TEXT)"
        )
        conn.close()

        _print_fzf_lines([], db_file)
        captured = capsys.readouterr()
        assert _FZF_COL_HEADER in captured.out

    def test_nonexistent_paths_skipped(self, tmp_path, capsys):
        db_file = tmp_path / "index.sqlite"
        import sqlite3
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            "CREATE TABLE messages (path TEXT, date TEXT, from_name TEXT, from_addr TEXT, subject TEXT)"
        )
        conn.close()

        _print_fzf_lines(["/does/not/exist.md"], db_file)
        captured = capsys.readouterr()
        # 헤더만 출력되고 경로 라인은 없어야 함
        lines = [l for l in captured.out.splitlines() if l.strip()]
        assert len(lines) == 1  # 헤더 한 줄만


# ---------------------------------------------------------------------------
# _visual_width / _visual_truncate / _visual_pad
# ---------------------------------------------------------------------------

class TestResolveGlowStyle:
    def test_explicit_builtin_returned_as_is(self):
        assert resolve_glow_style("dracula") == "dracula"

    def test_explicit_path_returned_as_is(self):
        assert resolve_glow_style("/some/theme.json") == "/some/theme.json"

    def test_empty_prefers_bundled_mocha(self):
        """빈 문자열이면 번들된 mocha-glow.json 절대 경로를 반환한다."""
        result = resolve_glow_style("")
        assert result.endswith("mocha-glow.json")
        assert Path(result).is_file()

    def test_empty_falls_back_to_dark_when_no_file(self, monkeypatch):
        """bundled 파일이 없으면 'dark' 를 반환한다."""
        monkeypatch.setattr(Path, "is_file", lambda self: False)
        assert resolve_glow_style("") == "dark"


class TestVisualHelpers:
    def test_ascii_width(self):
        assert _visual_width("hello") == 5

    def test_korean_width(self):
        # 한글 1자 = 2 visual cols
        assert _visual_width("안녕") == 4

    def test_mixed_width(self):
        assert _visual_width("A안B") == 4  # 1 + 2 + 1

    def test_truncate_ascii(self):
        assert _visual_truncate("hello", 3) == "hel"

    def test_truncate_korean(self):
        # 한글 5자(10 visual), max=10 → 전부 유지
        assert _visual_truncate("안녕하세요", 10) == "안녕하세요"

    def test_truncate_korean_partial(self):
        # 한글 3자(6 visual), max=5 → 2자(4 visual) — 마지막 글자 잘림
        result = _visual_truncate("안녕하", 5)
        assert _visual_width(result) <= 5
        assert result == "안녕"

    def test_truncate_mixed(self):
        # "A안" = 3 visual, max=2 → "A"
        assert _visual_truncate("A안B", 2) == "A"

    def test_pad_ascii(self):
        result = _visual_pad("hi", 5)
        assert result == "hi   "
        assert _visual_width(result) == 5

    def test_pad_korean(self):
        # "안녕" = 4 visual, pad to 10
        result = _visual_pad("안녕", 10)
        assert _visual_width(result) == 10
        assert result.endswith(" " * 6)

    def test_pad_already_at_width(self):
        result = _visual_pad("hello", 5)
        assert result == "hello"

    def test_truncate_then_pad_gives_exact_width(self):
        # 한글 5자 → truncate to 10 → pad to 10
        result = _visual_pad(_visual_truncate("안녕하세요abc", 10), 10)
        assert _visual_width(result) == 10

    def test_col_header_sender_width(self):
        """_FZF_COL_HEADER 에서 보낸사람 열이 10 visual cols 임을 확인."""
        assert "보낸사람" in _FZF_COL_HEADER


# ---------------------------------------------------------------------------
# _read_frontmatter_fields
# ---------------------------------------------------------------------------

class TestReadFrontmatterFields:
    def _make_md(self, tmp_path: Path, frontmatter: str) -> str:
        md = tmp_path / "test.md"
        md.write_text(f"---\n{frontmatter}\n---\n\nbody", encoding="utf-8")
        return str(md)

    def test_reads_subject(self, tmp_path):
        path = self._make_md(tmp_path, 'subject: "테스트 제목"\n')
        assert _read_frontmatter_fields(path)["subject"] == "테스트 제목"

    def test_reads_from(self, tmp_path):
        path = self._make_md(tmp_path, 'from: "홍길동 <hong@example.com>"\n')
        assert "홍길동" in _read_frontmatter_fields(path)["from"]

    def test_reads_date(self, tmp_path):
        path = self._make_md(tmp_path, "date: 2024-01-15T09:00:00+09:00\n")
        assert "2024" in _read_frontmatter_fields(path)["date"]

    def test_no_frontmatter_returns_empty(self, tmp_path):
        md = tmp_path / "plain.md"
        md.write_text("no frontmatter", encoding="utf-8")
        assert _read_frontmatter_fields(str(md)) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        assert _read_frontmatter_fields(str(tmp_path / "none.md")) == {}


# ---------------------------------------------------------------------------
# handle_delete_message
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path, md_path: str) -> Path:
    """테스트용 SQLite DB 를 생성하고 메시지 행을 삽입한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT, path TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            subject, from_name, from_addr, to_addrs, body,
            content='', tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS fts_sync (msgid TEXT PRIMARY KEY, path TEXT);
    """)
    conn.execute(
        "INSERT INTO messages (msgid, subject, from_name, from_addr, to_addrs, path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("<test@example.com>", "테스트 제목", "홍길동", "hong@example.com", "[]", md_path),
    )
    rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO messages_fts(rowid, subject, from_name, from_addr, to_addrs, body) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (rowid, "테스트 제목", "홍길동", "hong@example.com", "[]", "본문 내용"),
    )
    conn.commit()
    conn.close()
    return db_file


class TestHandleDeleteMessage:
    def _make_md(self, tmp_path: Path) -> Path:
        md = tmp_path / "mail.md"
        md.write_text(
            '---\nsubject: "테스트"\nfrom: "홍길동"\ndate: 2024-01-01\n---\n\n본문 내용',
            encoding="utf-8",
        )
        return md

    def test_cancel_does_not_delete(self, tmp_path, monkeypatch):
        """입력 'n' 시 파일이 삭제되지 않는다."""
        md = self._make_md(tmp_path)
        db_file = _make_db(tmp_path, str(md))
        monkeypatch.setattr("builtins.input", lambda _: "n")
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(md), str(tmp_path))
        assert md.exists()

    def test_confirm_deletes_file(self, tmp_path, monkeypatch):
        """입력 'y' 시 MD 파일이 삭제된다."""
        md = self._make_md(tmp_path)
        db_file = _make_db(tmp_path, str(md))
        monkeypatch.setattr("builtins.input", lambda _: "y")
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(md), str(tmp_path))
        assert not md.exists()

    def test_confirm_removes_from_db(self, tmp_path, monkeypatch):
        """삭제 후 messages 테이블에서 행이 제거된다."""
        md = self._make_md(tmp_path)
        db_file = _make_db(tmp_path, str(md))
        monkeypatch.setattr("builtins.input", lambda _: "y")
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(md), str(tmp_path))
        conn = sqlite3.connect(str(db_file))
        count = conn.execute("SELECT COUNT(*) FROM messages WHERE path = ?", (str(md),)).fetchone()[0]
        conn.close()
        assert count == 0

    def test_nonexistent_file_returns_early(self, tmp_path, capsys):
        """존재하지 않는 파일은 조용히 리턴한다."""
        with patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(tmp_path / "ghost.md"), str(tmp_path))
        captured = capsys.readouterr()
        assert "파일 없음" in captured.out

    def test_attachment_deleted_when_confirmed(self, tmp_path, monkeypatch):
        """첨부 파일도 'y' 응답 시 삭제된다."""
        att_dir = tmp_path / "attachments" / "abc"
        att_dir.mkdir(parents=True)
        att_file = att_dir / "doc.pdf"
        att_file.write_bytes(b"%PDF")

        md = tmp_path / "mail.md"
        md.write_text(
            '---\nsubject: "첨부테스트"\nfrom: "A"\ndate: 2024-01-01\n'
            'attachments:\n'
            '  - {name: "doc.pdf", sha256: "abc123", size: 4, '
            'path: "attachments/abc/doc.pdf"}\n'
            '---\n\n본문',
            encoding="utf-8",
        )
        db_file = _make_db(tmp_path, str(md))
        # 첫 번째 input (MD 삭제 확인) → y, 두 번째 (첨부 삭제 확인) → y
        answers = iter(["y", "y"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(md), str(tmp_path))
        assert not att_file.exists()

    def test_attachment_kept_when_declined(self, tmp_path, monkeypatch):
        """첨부 파일 삭제를 'n' 으로 거부하면 첨부가 유지된다."""
        att_dir = tmp_path / "attachments" / "xyz"
        att_dir.mkdir(parents=True)
        att_file = att_dir / "keep.pdf"
        att_file.write_bytes(b"%PDF")

        md = tmp_path / "mail.md"
        md.write_text(
            '---\nsubject: "첨부유지"\nfrom: "B"\ndate: 2024-01-01\n'
            'attachments:\n'
            '  - {name: "keep.pdf", sha256: "xyz123", size: 4, '
            'path: "attachments/xyz/keep.pdf"}\n'
            '---\n\n본문',
            encoding="utf-8",
        )
        db_file = _make_db(tmp_path, str(md))
        answers = iter(["y", "n"])
        monkeypatch.setattr("builtins.input", lambda _: next(answers))
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_delete_message(str(md), str(tmp_path))
        assert att_file.exists()


# ---------------------------------------------------------------------------
# get_recent_paths — 정렬 파라미터
# ---------------------------------------------------------------------------

def _make_sort_db(tmp_path: Path) -> Path:
    """정렬 테스트용 SQLite DB (메시지 3개) 를 생성한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            subject, from_name, from_addr, to_addrs, body,
            content='', tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS fts_sync (msgid TEXT PRIMARY KEY, path TEXT);
    """)
    rows = [
        ("<a@test>", "2024-03-01", "Charlie", "charlie@x.com", "Zebra topic", "c.md"),
        ("<b@test>", "2024-01-15", "Alice",   "alice@x.com",   "Apple topic", "a.md"),
        ("<c@test>", "2024-02-20", "Bob",     "bob@x.com",     "Mango topic", "b.md"),
    ]
    for msgid, date, fname, faddr, subj, path in rows:
        conn.execute(
            "INSERT INTO messages (msgid, date, from_name, from_addr, subject, path) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (msgid, date, fname, faddr, subj, path),
        )
    conn.commit()
    conn.close()
    return db_file


class TestGetRecentPathsSort:
    def test_default_date_desc(self, tmp_path):
        db = _make_sort_db(tmp_path)
        paths = get_recent_paths(db, sort="date")
        assert paths == ["c.md", "b.md", "a.md"]

    def test_sort_from_asc(self, tmp_path):
        db = _make_sort_db(tmp_path)
        paths = get_recent_paths(db, sort="from")
        assert paths == ["a.md", "b.md", "c.md"]

    def test_sort_subject_asc(self, tmp_path):
        db = _make_sort_db(tmp_path)
        paths = get_recent_paths(db, sort="subject")
        assert paths == ["a.md", "b.md", "c.md"]  # Apple < Mango < Zebra

    def test_after_filter_with_sort(self, tmp_path):
        db = _make_sort_db(tmp_path)
        paths = get_recent_paths(db, after="2024-02-01", sort="date")
        assert "a.md" not in paths   # 2024-01-15 < 2024-02-01

    def test_unknown_sort_falls_back_to_date(self, tmp_path):
        db = _make_sort_db(tmp_path)
        paths = get_recent_paths(db, sort="invalid")
        assert paths == ["c.md", "b.md", "a.md"]


# ---------------------------------------------------------------------------
# handle_bulk_delete
# ---------------------------------------------------------------------------

class TestHandleBulkDelete:
    def _make_mds(self, tmp_path: Path, n: int = 2) -> list[Path]:
        mds = []
        for i in range(n):
            md = tmp_path / f"mail{i}.md"
            md.write_text(
                f'---\nsubject: "제목{i}"\nfrom: "발신자{i}"\ndate: 2024-01-0{i+1}\n---\n\n본문{i}',
                encoding="utf-8",
            )
            mds.append(md)
        return mds

    def _make_bulk_db(self, tmp_path: Path, md_paths: list[Path]) -> Path:
        db_file = tmp_path / "index.sqlite"
        conn = sqlite3.connect(str(db_file))
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
                date TEXT, from_name TEXT, from_addr TEXT,
                to_addrs TEXT, cc_addrs TEXT, subject TEXT,
                folder TEXT, thread TEXT, source_pst TEXT,
                path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0
            );
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                subject, from_name, from_addr, to_addrs, body,
                content='', tokenize='unicode61'
            );
            CREATE TABLE IF NOT EXISTS fts_sync (msgid TEXT PRIMARY KEY, path TEXT);
        """)
        for i, md in enumerate(md_paths):
            conn.execute(
                "INSERT INTO messages (msgid, subject, from_name, from_addr, to_addrs, path) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"<bulk{i}@test>", f"제목{i}", f"발신자{i}", f"sender{i}@x.com", "[]", str(md)),
            )
            rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute(
                "INSERT INTO messages_fts(rowid, subject, from_name, from_addr, to_addrs, body) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (rowid, f"제목{i}", f"발신자{i}", f"sender{i}@x.com", "[]", f"본문{i}"),
            )
        conn.commit()
        conn.close()
        return db_file

    def test_cancel_keeps_files(self, tmp_path, monkeypatch):
        mds = self._make_mds(tmp_path)
        db_file = self._make_bulk_db(tmp_path, mds)
        stdin_data = "\n".join(str(m) for m in mds) + "\n"
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}), \
             patch("mailview.detect_platform", return_value="linux"), \
             patch("builtins.open", side_effect=OSError), \
             patch("builtins.input", return_value="n"):
            import io
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin_data))
            handle_bulk_delete(str(tmp_path))
        assert all(m.exists() for m in mds)

    def test_confirm_deletes_all(self, tmp_path, monkeypatch):
        mds = self._make_mds(tmp_path)
        db_file = self._make_bulk_db(tmp_path, mds)
        stdin_data = "\n".join(str(m) for m in mds) + "\n"
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}), \
             patch("mailview.detect_platform", return_value="linux"), \
             patch("builtins.open", side_effect=OSError), \
             patch("builtins.input", return_value="y"):
            import io
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin_data))
            handle_bulk_delete(str(tmp_path))
        assert all(not m.exists() for m in mds)

    def test_confirm_removes_from_db(self, tmp_path, monkeypatch):
        mds = self._make_mds(tmp_path)
        db_file = self._make_bulk_db(tmp_path, mds)
        stdin_data = "\n".join(str(m) for m in mds) + "\n"
        with patch("mailview.db_path", return_value=db_file), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}), \
             patch("mailview.detect_platform", return_value="linux"), \
             patch("builtins.open", side_effect=OSError), \
             patch("builtins.input", return_value="y"):
            import io
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin_data))
            handle_bulk_delete(str(tmp_path))
        conn = sqlite3.connect(str(db_file))
        count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        conn.close()
        assert count == 0

    def test_empty_stdin_returns_early(self, tmp_path, monkeypatch, capsys):
        with patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            import io
            monkeypatch.setattr("sys.stdin", io.StringIO(""))
            handle_bulk_delete(str(tmp_path))
        captured = capsys.readouterr()
        assert "선택된 메일 없음" in captured.out


# ---------------------------------------------------------------------------
# get_folder_list
# ---------------------------------------------------------------------------

def _make_folder_db(tmp_path: Path) -> Path:
    """폴더 목록 테스트용 SQLite DB 를 생성한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0
        );
    """)
    rows = [
        ("<a>", "Inbox",        "a.md"),
        ("<b>", "Sent Items",   "b.md"),
        ("<c>", "Inbox",        "c.md"),  # 중복 폴더
        ("<d>", "",             "d.md"),  # 빈 폴더명
        ("<e>", "Deleted",      "e.md"),
    ]
    for msgid, folder_name, path in rows:
        conn.execute(
            "INSERT INTO messages (msgid, folder, path) VALUES (?, ?, ?)",
            (msgid, folder_name, path),
        )
    conn.commit()
    conn.close()
    return db_file


class TestGetFolderList:
    def test_returns_unique_folders(self, tmp_path):
        db = _make_folder_db(tmp_path)
        folders = get_folder_list(db)
        assert folders.count("Inbox") == 1

    def test_excludes_empty_folder(self, tmp_path):
        db = _make_folder_db(tmp_path)
        folders = get_folder_list(db)
        assert "" not in folders

    def test_sorted_alphabetically(self, tmp_path):
        db = _make_folder_db(tmp_path)
        folders = get_folder_list(db)
        assert folders == sorted(folders)

    def test_all_distinct_non_empty_folders(self, tmp_path):
        db = _make_folder_db(tmp_path)
        folders = get_folder_list(db)
        assert set(folders) == {"Deleted", "Inbox", "Sent Items"}

    def test_empty_db_returns_empty_list(self, tmp_path):
        db_file = tmp_path / "empty.sqlite"
        conn = sqlite3.connect(str(db_file))
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
                folder TEXT, path TEXT NOT NULL
            );
        """)
        conn.commit()
        conn.close()
        assert get_folder_list(db_file) == []


# ---------------------------------------------------------------------------
# auto_update_index
# ---------------------------------------------------------------------------

class TestAutoUpdateIndex:
    """auto_update_index() — 인덱스 자동 갱신."""

    def _make_db(self, tmp_path: Path) -> Path:
        db_file = tmp_path / "index.sqlite"
        db_file.write_bytes(b"")  # 빈 DB 파일 (존재만 하면 됨)
        return db_file

    def _cfg(self, tmp_path: Path, auto_index: bool = True) -> dict:
        return {
            "archive": {"root": str(tmp_path)},
            "mailview": {"auto_index": auto_index},
        }

    def test_disabled_skips_subprocess(self, tmp_path):
        """auto_index=False 이면 subprocess 를 호출하지 않는다."""
        db = self._make_db(tmp_path)
        cfg = self._cfg(tmp_path, auto_index=False)
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.subprocess.run") as mock_run:
            auto_update_index(tmp_path, cfg)
        mock_run.assert_not_called()

    def test_no_db_skips_subprocess(self, tmp_path):
        """DB 파일이 없으면 subprocess 를 호출하지 않는다."""
        cfg = self._cfg(tmp_path)
        with patch("mailview.subprocess.run") as mock_run:
            auto_update_index(tmp_path, cfg)
        mock_run.assert_not_called()

    def test_no_new_files_skips_subprocess(self, tmp_path):
        """새 MD 파일이 없으면 subprocess 를 호출하지 않는다."""
        db = self._make_db(tmp_path)
        archive_dir = tmp_path / "archive" / "2024" / "01" / "01"
        archive_dir.mkdir(parents=True)
        md = archive_dir / "test.md"
        md.write_text("---\nsubject: test\n---\nbody", encoding="utf-8")
        import os
        # MD 파일의 mtime 을 DB 보다 이전으로 설정
        old_time = db.stat().st_mtime - 10
        os.utime(md, (old_time, old_time))
        cfg = self._cfg(tmp_path)
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.subprocess.run") as mock_run:
            auto_update_index(tmp_path, cfg)
        mock_run.assert_not_called()

    def test_new_file_with_staging_triggers_subprocess(self, tmp_path):
        """DB 보다 새 MD + staging.jsonl 있으면 build_index.py 를 호출한다."""
        import time
        db = self._make_db(tmp_path)
        archive_dir = tmp_path / "archive" / "2024" / "01" / "01"
        archive_dir.mkdir(parents=True)
        md = archive_dir / "test.md"
        # MD 파일을 DB 보다 나중에 생성
        time.sleep(0.01)
        md.write_text("---\nsubject: test\n---\nbody", encoding="utf-8")
        # staging 파일 존재 — pst2md 가 만든 정상 시나리오
        (tmp_path / "index_staging.jsonl").write_text(
            '{"msgid":"<x>","path":"x.md"}\n', encoding="utf-8"
        )
        cfg = self._cfg(tmp_path)
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.subprocess.run",
                   return_value=type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
                   ) as mock_run:
            auto_update_index(tmp_path, cfg)
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]  # argv list
        assert any("build_index.py" in a for a in call_args)
        assert str(tmp_path) in call_args

    def test_missing_staging_with_new_files_warns_no_subprocess(self, tmp_path, capsys):
        """P5: staging 누락 + 새 MD → subprocess 미호출 + rebuild 권장 경고."""
        import time
        db = self._make_db(tmp_path)
        archive_dir = tmp_path / "archive" / "2024" / "01" / "01"
        archive_dir.mkdir(parents=True)
        md = archive_dir / "test.md"
        time.sleep(0.01)
        md.write_text("---\nsubject: test\n---\nbody", encoding="utf-8")
        # staging.jsonl 없음
        cfg = self._cfg(tmp_path)
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.subprocess.run") as mock_run:
            auto_update_index(tmp_path, cfg)
        mock_run.assert_not_called()
        captured = capsys.readouterr()
        assert "staging.jsonl" in captured.err
        assert "rebuild" in captured.err


# ---------------------------------------------------------------------------
# extract_urls
# ---------------------------------------------------------------------------

class TestExtractUrls:
    def _make_md(self, tmp_path: Path, body: str) -> str:
        md = tmp_path / "test.md"
        md.write_text(f"---\nsubject: test\n---\n{body}", encoding="utf-8")
        return str(md)

    def test_finds_http_url(self, tmp_path):
        path = self._make_md(tmp_path, "참조: http://example.com/page")
        assert "http://example.com/page" in extract_urls(path)

    def test_finds_https_url(self, tmp_path):
        path = self._make_md(tmp_path, "링크: https://example.com/path?a=1")
        assert "https://example.com/path?a=1" in extract_urls(path)

    def test_deduplicates_urls(self, tmp_path):
        body = "https://example.com\nhttps://example.com"
        path = self._make_md(tmp_path, body)
        urls = extract_urls(path)
        assert urls.count("https://example.com") == 1

    def test_excludes_frontmatter_urls(self, tmp_path):
        md = tmp_path / "fm.md"
        md.write_text(
            "---\nsource: https://frontmatter.url\n---\n본문 텍스트",
            encoding="utf-8",
        )
        urls = extract_urls(str(md))
        assert "https://frontmatter.url" not in urls

    def test_strips_trailing_punctuation(self, tmp_path):
        path = self._make_md(tmp_path, "링크: https://example.com/page.")
        urls = extract_urls(path)
        assert "https://example.com/page" in urls

    def test_no_urls_returns_empty(self, tmp_path):
        path = self._make_md(tmp_path, "URL 없는 본문 텍스트")
        assert extract_urls(path) == []

    def test_missing_file_returns_empty(self, tmp_path):
        assert extract_urls(str(tmp_path / "nonexistent.md")) == []


# ---------------------------------------------------------------------------
# 태그 관리: get_tag_list / _update_frontmatter_tags / handle_tag_message
# ---------------------------------------------------------------------------

def _make_tag_db(tmp_path: Path) -> Path:
    """태그 테스트용 SQLite DB 를 생성한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0,
            tags TEXT DEFAULT ''
        );
        CREATE TABLE fts_sync (msgid TEXT PRIMARY KEY, path TEXT);
    """)
    rows = [
        ("<a>", "work, urgent",  "a.md"),
        ("<b>", "work",         "b.md"),
        ("<c>", "personal",     "c.md"),
        ("<d>", "",             "d.md"),
    ]
    for msgid, tags, path in rows:
        conn.execute(
            "INSERT INTO messages (msgid, tags, path) VALUES (?, ?, ?)",
            (msgid, tags, path),
        )
    conn.commit()
    conn.close()
    return db_file


class TestGetTagList:
    def test_returns_unique_tags(self, tmp_path):
        db = _make_tag_db(tmp_path)
        tags = get_tag_list(db)
        assert tags.count("work") == 1

    def test_excludes_empty_tags(self, tmp_path):
        db = _make_tag_db(tmp_path)
        assert "" not in get_tag_list(db)

    def test_sorted_alphabetically(self, tmp_path):
        db = _make_tag_db(tmp_path)
        tags = get_tag_list(db)
        assert tags == sorted(tags)

    def test_all_distinct_tags(self, tmp_path):
        db = _make_tag_db(tmp_path)
        assert set(get_tag_list(db)) == {"personal", "urgent", "work"}


class TestUpdateFrontmatterTags:
    def _make_md(self, tmp_path: Path, extra_fm: str = "") -> Path:
        md = tmp_path / "test.md"
        md.write_text(
            f"---\nsubject: Test\n{extra_fm}---\n본문\n",
            encoding="utf-8",
        )
        return md

    def test_adds_tags_when_none(self, tmp_path):
        md = self._make_md(tmp_path)
        assert _update_frontmatter_tags(str(md), ["work", "urgent"])
        text = md.read_text(encoding="utf-8")
        assert "tags: [work, urgent]" in text

    def test_replaces_existing_tags(self, tmp_path):
        md = self._make_md(tmp_path, "tags: [old]\n")
        assert _update_frontmatter_tags(str(md), ["new"])
        text = md.read_text(encoding="utf-8")
        assert "tags: [new]" in text
        assert "old" not in text

    def test_removes_tags_when_empty(self, tmp_path):
        md = self._make_md(tmp_path, "tags: [work]\n")
        assert _update_frontmatter_tags(str(md), [])
        text = md.read_text(encoding="utf-8")
        assert "tags:" not in text

    def test_missing_file_returns_false(self, tmp_path):
        assert not _update_frontmatter_tags(str(tmp_path / "x.md"), ["a"])


class TestHandleTagMessage:
    def _make_md(self, tmp_path: Path) -> Path:
        md = tmp_path / "msg.md"
        md.write_text("---\nsubject: Test\n---\n본문\n", encoding="utf-8")
        return md

    def _make_db(self, tmp_path: Path, md_path: str) -> Path:
        db_file = tmp_path / "index.sqlite"
        conn = sqlite3.connect(str(db_file))
        conn.executescript("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
                subject TEXT, path TEXT NOT NULL, tags TEXT DEFAULT ''
            );
        """)
        conn.execute(
            "INSERT INTO messages (msgid, subject, path, tags) VALUES (?, ?, ?, ?)",
            ("<test>", "Test", md_path, ""),
        )
        conn.commit()
        conn.close()
        return db_file

    def test_sets_tags(self, tmp_path):
        md = self._make_md(tmp_path)
        db = self._make_db(tmp_path, str(md))
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}), \
             patch("builtins.input", return_value="work, urgent"):
            handle_tag_message(str(md), str(tmp_path))
        text = md.read_text(encoding="utf-8")
        assert "tags: [work, urgent]" in text

    def test_clears_tags_on_empty_input(self, tmp_path):
        md = self._make_md(tmp_path)
        db = self._make_db(tmp_path, str(md))
        with patch("mailview.db_path", return_value=db), \
             patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}), \
             patch("builtins.input", return_value=""):
            handle_tag_message(str(md), str(tmp_path))
        text = md.read_text(encoding="utf-8")
        assert "tags:" not in text

    def test_missing_file_returns_early(self, tmp_path, capsys):
        with patch("mailview.load_config", return_value={"archive": {"root": str(tmp_path)}}):
            handle_tag_message(str(tmp_path / "none.md"), str(tmp_path))
        captured = capsys.readouterr()
        assert "파일 없음" in captured.out


# ---------------------------------------------------------------------------
# find_duplicate_groups
# ---------------------------------------------------------------------------

def _make_dedupe_db(tmp_path: Path, rows: list[tuple]) -> Path:
    """중복 감지 테스트용 DB 와 MD 파일을 생성한다.

    rows: [(msgid, date, from_addr, subject, path_name), ...]
    """
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0,
            tags TEXT DEFAULT ''
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
            subject, from_name, from_addr, to_addrs, body,
            content='', tokenize='unicode61'
        );
        CREATE TABLE fts_sync (msgid TEXT PRIMARY KEY, path TEXT);
    """)
    for msgid, date, from_addr, subject, path_name in rows:
        p = tmp_path / path_name
        p.write_text(f"---\nsubject: {subject}\n---\n", encoding="utf-8")
        conn.execute(
            "INSERT INTO messages (msgid, date, from_addr, subject, path) VALUES (?,?,?,?,?)",
            (msgid, date, from_addr, subject, str(p)),
        )
    conn.commit()
    conn.close()
    return db_file


class TestFindDuplicateGroups:
    def test_same_content_detected(self, tmp_path):
        """동일한 날짜+발신자+제목을 가진 메일은 중복으로 감지된다."""
        rows = [
            ("<aaa>", "2024-01-01", "a@b.com", "Test", "a.md"),
            ("<bbb>", "2024-01-01", "a@b.com", "Test", "b.md"),
        ]
        db = _make_dedupe_db(tmp_path, rows)
        groups = find_duplicate_groups(db)
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_no_duplicates_returns_empty(self, tmp_path):
        rows = [
            ("<aaa>", "2024-01-01", "a@b.com", "A", "a.md"),
            ("<bbb>", "2024-01-02", "b@c.com", "B", "b.md"),
        ]
        db = _make_dedupe_db(tmp_path, rows)
        groups = find_duplicate_groups(db)
        assert groups == []

    def test_nonexistent_paths_excluded(self, tmp_path):
        """존재하지 않는 파일 경로는 그룹에서 제외된다."""
        rows = [
            ("<aaa>", "2024-01-01", "a@b.com", "Test", "a.md"),
            ("<bbb>", "2024-01-01", "a@b.com", "Test", "b.md"),
        ]
        db = _make_dedupe_db(tmp_path, rows)
        # b.md 를 삭제해 존재하지 않게 함
        (tmp_path / "b.md").unlink()
        groups = find_duplicate_groups(db)
        # 파일이 하나뿐이면 그룹 미포함
        assert groups == []


# ---------------------------------------------------------------------------
# format_stats_for_display
# ---------------------------------------------------------------------------

def _make_stats_db(tmp_path: Path) -> Path:
    """통계 테스트용 SQLite DB 를 생성한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0,
            tags TEXT DEFAULT ''
        );
    """)
    rows = [
        ("<a>", "2024-01-15T10:00:00+00:00", "Alice", "a@b.com", "Hello",  1),
        ("<b>", "2024-02-10T10:00:00+00:00", "Bob",   "b@c.com", "World",  0),
        ("<c>", "2024-02-20T10:00:00+00:00", "Alice", "a@b.com", "Again",  2),
    ]
    for msgid, date, name, addr, subj, n_att in rows:
        conn.execute(
            "INSERT INTO messages (msgid, date, from_name, from_addr, subject, path, n_attachments)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msgid, date, name, addr, subj, f"{msgid}.md", n_att),
        )
    conn.commit()
    conn.close()
    return db_file


class TestFormatStatsForDisplay:
    def test_returns_list_of_strings(self, tmp_path):
        db = _make_stats_db(tmp_path)
        lines = format_stats_for_display(db, tmp_path)
        assert isinstance(lines, list)
        assert all(isinstance(l, str) for l in lines)

    def test_contains_total_count(self, tmp_path):
        db = _make_stats_db(tmp_path)
        lines = format_stats_for_display(db, tmp_path)
        text = "\n".join(lines)
        assert "3" in text  # 총 3통

    def test_no_db_returns_error_message(self, tmp_path):
        lines = format_stats_for_display(tmp_path / "noexist.sqlite", tmp_path)
        assert any("인덱스 없음" in l for l in lines)

    def test_contains_sender_info(self, tmp_path):
        db = _make_stats_db(tmp_path)
        lines = format_stats_for_display(db, tmp_path)
        text = "\n".join(lines)
        assert "Alice" in text or "a@b.com" in text


# ---------------------------------------------------------------------------
# build_thread_tree / format_thread_tree
# ---------------------------------------------------------------------------

def _make_thread_db(tmp_path: Path) -> Path:
    """스레드 트리 테스트용 DB 를 생성한다."""
    db_file = tmp_path / "index.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, msgid TEXT UNIQUE NOT NULL,
            date TEXT, from_name TEXT, from_addr TEXT,
            to_addrs TEXT, cc_addrs TEXT, subject TEXT,
            folder TEXT, thread TEXT, source_pst TEXT,
            path TEXT NOT NULL, n_attachments INTEGER DEFAULT 0,
            tags TEXT DEFAULT '', in_reply_to TEXT DEFAULT ''
        );
    """)
    # root → reply1 → reply2 chain
    rows = [
        ("<root>",   "",       "2024-01-01T10:00:00", "Root message",  "t1", "root.md"),
        ("<r1>",     "<root>", "2024-01-02T10:00:00", "Reply 1",       "t1", "r1.md"),
        ("<r2>",     "<r1>",   "2024-01-03T10:00:00", "Reply 2",       "t1", "r2.md"),
        ("<other>",  "",       "2024-01-01T10:00:00", "Other thread",  "t2", "other.md"),
    ]
    for msgid, irt, date, subj, thread, path in rows:
        conn.execute(
            "INSERT INTO messages (msgid, in_reply_to, date, subject, thread, path)"
            " VALUES (?,?,?,?,?,?)",
            (msgid, irt, date, subj, thread, str(tmp_path / path)),
        )
    conn.commit()
    conn.close()
    return db_file


class TestBuildThreadTree:
    def test_returns_all_thread_messages(self, tmp_path):
        db = _make_thread_db(tmp_path)
        tree = build_thread_tree(db, "t1")
        assert len(tree) == 3

    def test_root_has_depth_zero(self, tmp_path):
        db = _make_thread_db(tmp_path)
        tree = build_thread_tree(db, "t1")
        depths = [t[0] for t in tree]
        assert 0 in depths

    def test_child_has_greater_depth(self, tmp_path):
        db = _make_thread_db(tmp_path)
        tree = build_thread_tree(db, "t1")
        depth_map = {t[2]: t[0] for t in tree}  # msgid → depth
        assert depth_map["<r1>"] > depth_map["<root>"]
        assert depth_map["<r2>"] > depth_map["<r1>"]

    def test_other_thread_not_included(self, tmp_path):
        db = _make_thread_db(tmp_path)
        tree = build_thread_tree(db, "t1")
        msgids = [t[2] for t in tree]
        assert "<other>" not in msgids

    def test_empty_thread_id_returns_empty(self, tmp_path):
        db = _make_thread_db(tmp_path)
        assert build_thread_tree(db, "nonexistent") == []


class TestFormatThreadTree:
    def test_returns_list_of_strings(self):
        tree = [(0, "/a.md", "<a>", "Root"), (1, "/b.md", "<b>", "Reply")]
        lines = format_thread_tree(tree)
        assert isinstance(lines, list)
        assert all(isinstance(l, str) for l in lines)

    def test_empty_tree_returns_message(self):
        lines = format_thread_tree([])
        assert any("없음" in l or len(l) > 0 for l in lines)

    def test_contains_subject(self):
        tree = [(0, "/a.md", "<a>", "Important subject")]
        lines = format_thread_tree(tree)
        assert any("Important subject" in l for l in lines)


# ---------------------------------------------------------------------------
# _build_fzf_exec_commands — P1/P2 body/subject reload 명령
# ---------------------------------------------------------------------------

class TestBuildFzfExecCommands:
    """fzf bind 에 들어가는 reload 명령 문자열 조립을 검증한다.

    Ctrl-B 본문 / Ctrl-S 제목 모드는 ``--fzf-input --body {q}`` /
    ``--fzf-input --subject {q}`` 형식의 명령을 매 입력마다 fzf 의
    ``change:transform`` 핸들러가 평가해 DB reload 를 트리거한다.
    """

    @staticmethod
    def _cmds(plat: str = "linux") -> dict:
        return _build_fzf_exec_commands(
            plat=plat,
            py="/usr/bin/python",
            script_path="/p/mailview.py",
            archive_path="/arc",
            editor="vim",
            bat_path="/usr/bin/bat",
            fzf_path="/usr/bin/fzf",
            iso_dates={
                "today": "2026-01-01", "week": "2025-12-25",
                "month": "2025-12-04", "year": "2025-01-01",
            },
            fzf_colors="dark",
        )

    def test_body_reload_includes_q_placeholder(self):
        """body_reload 에 fzf {q} 플레이스홀더가 들어 있어야 매 입력마다 평가된다."""
        cmds = self._cmds("linux")
        assert "--body '{q}'" in cmds["body_reload"]
        assert "--fzf-input" in cmds["body_reload"]

    def test_subject_reload_present_and_uses_subject_flag(self):
        """P2 신규: subject_reload 명령이 --subject {q} 를 포함해야 한다."""
        cmds = self._cmds("linux")
        assert "subject_reload" in cmds
        assert "--subject '{q}'" in cmds["subject_reload"]
        assert "--fzf-input" in cmds["subject_reload"]

    def test_body_and_subject_reload_differ_only_in_flag(self):
        """body 와 subject reload 는 플래그만 달라야 한다 (대칭성)."""
        cmds = self._cmds("linux")
        body = cmds["body_reload"]
        subj = cmds["subject_reload"]
        assert body.replace("--body", "--subject") == subj

    def test_windows_uses_double_quotes(self):
        """Windows 분기에서는 큰따옴표를 사용해야 한다 (cmd /c 호환)."""
        cmds = self._cmds("windows")
        assert '--body "{q}"' in cmds["body_reload"]
        assert '--subject "{q}"' in cmds["subject_reload"]

    def test_reset_reload_has_no_filter_args(self):
        """reset_reload 는 추가 필터 없이 fzf-input 만 호출 (기본 recent 목록)."""
        cmds = self._cmds("linux")
        assert "--body" not in cmds["reset_reload"]
        assert "--subject" not in cmds["reset_reload"]
        assert "--fzf-input" in cmds["reset_reload"]
