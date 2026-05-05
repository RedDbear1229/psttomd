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
    read_body,
)


def _write_md(tmp_path: Path, frontmatter: str, body: str = "본문\n") -> Path:
    """frontmatter 와 body 로 md 파일을 만들어 경로를 반환한다."""
    md = tmp_path / "msg.md"
    md.write_text(f"---\n{frontmatter}---\n\n{body}", encoding="utf-8")
    return md


# ---------------------------------------------------------------------------
# read_body — pristine 본문만 FTS 인덱싱 (P3 회귀 방지)
# ---------------------------------------------------------------------------

def _make_full_md(body: str = "이메일 본문입니다.", tail: str = "") -> str:
    """4-구분자 MD 구조를 반환한다."""
    return (
        "---\nmsgid: '<a@b>'\nsubject: '테스트'\n---\n\n"
        "# 테스트\n**보낸사람:** a@b\n\n---\n\n"
        f"{body}\n\n---\n\n"
        f"{tail}"
    )


class TestReadBody:
    def test_returns_pristine_body(self, tmp_path: Path) -> None:
        """read_body 는 pristine body 만 반환한다."""
        md = tmp_path / "mail.md"
        md.write_text(_make_full_md("핵심 본문"), encoding="utf-8")
        assert read_body(str(md)) == "핵심 본문"

    def test_excludes_attachment_section(self, tmp_path: Path) -> None:
        """첨부 섹션은 FTS 본문에 포함되지 않아야 한다."""
        tail = "## 첨부 파일\n- [report.pdf](att/r.pdf)\n"
        md = tmp_path / "mail.md"
        md.write_text(_make_full_md("순수 본문", tail), encoding="utf-8")
        body = read_body(str(md))
        assert "순수 본문" in body
        assert "첨부 파일" not in body
        assert "report.pdf" not in body

    def test_excludes_llm_block(self, tmp_path: Path) -> None:
        """LLM enrichment 블록은 FTS 본문에 포함되지 않아야 한다."""
        tail = (
            "<!-- LLM-ENRICH:BEGIN -->\n"
            "## 요약 (LLM)\nLLM 생성 요약\n"
            "<!-- LLM-ENRICH:END -->\n"
        )
        md = tmp_path / "mail.md"
        md.write_text(_make_full_md("원본 본문", tail), encoding="utf-8")
        body = read_body(str(md))
        assert "원본 본문" in body
        assert "LLM 생성 요약" not in body

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """파일이 없으면 빈 문자열을 반환한다."""
        assert read_body(str(tmp_path / "nonexistent.md")) == ""

    def test_invalid_structure_returns_empty(self, tmp_path: Path) -> None:
        """4-구분자 구조가 아닌 파일은 빈 문자열을 반환한다."""
        md = tmp_path / "bad.md"
        md.write_text("frontmatter만 있고 본문 없음", encoding="utf-8")
        assert read_body(str(md)) == ""


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
