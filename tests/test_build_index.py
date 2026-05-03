"""
tests/test_build_index.py — build_index.extract_frontmatter() 단위 테스트

특히 첨부 파일 카운트(n_attachments)가 인라인 YAML 포맷
(``- {name: ..., sha256: ...}``)과 블록 YAML 포맷
(``- name: ...``)을 모두 인식하는지 검증한다. P6 회귀 방지.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import sqlite3  # noqa: E402

from build_index import (  # noqa: E402
    extract_frontmatter,
    fts_has_prefix_index,
    init_schema,
)


def _write_md(tmp_path: Path, frontmatter: str, body: str = "본문\n") -> Path:
    """frontmatter 와 body 로 md 파일을 만들어 경로를 반환한다."""
    md = tmp_path / "msg.md"
    md.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return md


class TestAttachmentCount:
    def test_inline_yaml_count(self, tmp_path: Path) -> None:
        """pst2md 가 출력하는 인라인 포맷을 카운트해야 한다."""
        fm = (
            'msgid: "<a@b>"\n'
            'subject: "two attachments"\n'
            "attachments:\n"
            '  - {name: "report.pdf", sha256: "abc...", size: 100, path: "att/r.pdf"}\n'
            '  - {name: "image.png", sha256: "def...", size: 50, path: "att/i.png"}\n'
        )
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 2

    def test_block_yaml_count(self, tmp_path: Path) -> None:
        """블록 포맷도 동일하게 카운트해야 한다 (외부 편집 호환)."""
        fm = (
            'msgid: "<a@b>"\n'
            "attachments:\n"
            '  - name: "report.pdf"\n'
            '    sha256: "abc..."\n'
            "    size: 100\n"
            '  - name: "image.png"\n'
            '    sha256: "def..."\n'
            "    size: 50\n"
        )
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 2

    def test_empty_attachments_list(self, tmp_path: Path) -> None:
        """``attachments: []`` 형식은 0개로 카운트한다."""
        fm = 'msgid: "<a@b>"\nattachments: []\n'
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 0

    def test_no_attachments_field(self, tmp_path: Path) -> None:
        """attachments 필드가 없으면 0개."""
        fm = 'msgid: "<a@b>"\nsubject: "no att"\n'
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 0

    def test_mixed_inline_and_block(self, tmp_path: Path) -> None:
        """인라인 + 블록 혼용 (마이그레이션 중간 상태 호환)."""
        fm = (
            'msgid: "<a@b>"\n'
            "attachments:\n"
            '  - {name: "first.pdf", sha256: "a...", size: 1, path: "x"}\n'
            '  - name: "second.pdf"\n'
            '    sha256: "b..."\n'
            "    size: 2\n"
        )
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 2

    def test_attachments_section_terminates(self, tmp_path: Path) -> None:
        """attachments 섹션 뒤에 다른 필드가 와도 카운트는 정확해야 한다."""
        fm = (
            'msgid: "<a@b>"\n'
            "attachments:\n"
            '  - {name: "a.pdf", sha256: "x", size: 1, path: "p"}\n'
            'tags: ["foo"]\n'
            'thread: "t_abc"\n'
        )
        meta = extract_frontmatter(_write_md(tmp_path, fm))
        assert meta is not None
        assert meta["n_attachments"] == 1


# ---------------------------------------------------------------------------
# FTS5 prefix index 감지 (P3)
# ---------------------------------------------------------------------------

class TestFtsPrefixIndex:
    def test_init_schema_creates_prefix_index(self) -> None:
        """init_schema 가 새 DB 에 prefix='2 3 4' 옵션을 적용한다."""
        conn = sqlite3.connect(":memory:")
        try:
            init_schema(conn)
            assert fts_has_prefix_index(conn) is True
        finally:
            conn.close()

    def test_legacy_fts_without_prefix_detected(self) -> None:
        """구버전 (prefix 없음) 스키마는 fts_has_prefix_index 가 False."""
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE messages_fts USING fts5("
                "subject, body, content='', tokenize='unicode61')"
            )
            assert fts_has_prefix_index(conn) is False
        finally:
            conn.close()

    def test_no_table_returns_false(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            assert fts_has_prefix_index(conn) is False
        finally:
            conn.close()
