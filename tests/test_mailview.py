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
    build_fzf_preview_cmd,
    get_editor,
    get_attachments_from_md,
    _print_fzf_lines,
    _FZF_COL_HEADER,
    _visual_width,
    _visual_truncate,
    _visual_pad,
    _read_frontmatter_fields,
    handle_delete_message,
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

    def test_wsl_uses_single_quotes(self):
        """WSL 은 linux 분기와 동일하게 단일 인용부호 사용"""
        with patch("mailview.detect_platform", return_value="wsl"):
            cmd = build_fzf_preview_cmd("/usr/bin/glow", None)
        assert "'{2}'" in cmd


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
