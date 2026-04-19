"""
tests/test_md_io.py — md_io 유닛 테스트

검증 항목:
1. split()  — frontmatter / head / body / tail 분해 정확성
2. write()  — frontmatter LLM 필드 추가, LLM 섹션 삽입
3. body 바이트 불변성 — write() 후 split() 해서 body 동일 확인
4. 멱등성   — write() 2 회 호출해도 body 변경 없음
5. 에러 케이스 — frontmatter 없는 파일, 구분자 누락 파일
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.lib.md_io import MdParts, body_hash, split, write


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_md(
    fm_extra: str = "",
    head: str = "# 테스트 메일\n\n**보낸사람:** alice@example.com  \n**받는사람:** bob@example.com  \n**날짜:** 2024-01-01T00:00:00+00:00",
    body: str = "안녕하세요.\n\n이 메일은 테스트입니다.",
    tail: str = "관련: [[t_abc123]] · [[alice@example.com]]\n",
    llm_block: str = "",
) -> str:
    fm = (
        'msgid: "<test@example.com>"\n'
        'date: 2024-01-01T00:00:00+00:00\n'
        'from: "Alice"\n'
        'to: ["bob@example.com"]\n'
        'cc: []\n'
        'subject: "테스트"\n'
        'folder: "Root/Inbox"\n'
        'thread: "t_abc123"\n'
        'in_reply_to: ""\n'
        'references: []\n'
        'attachments:\n'
        'tags: ["inbox"]\n'
        'source_pst: "test.pst"\n'
    )
    if fm_extra:
        fm += fm_extra

    llm_part = (
        f"<!-- LLM-ENRICH:BEGIN -->\n{llm_block}<!-- LLM-ENRICH:END -->\n\n"
        if llm_block
        else ""
    )

    return (
        f"---\n{fm}---\n\n"
        f"{head}\n\n---\n\n"
        f"{body}\n\n---\n\n"
        f"{llm_part}"
        f"{tail}"
    )


# ---------------------------------------------------------------------------
# split() 테스트
# ---------------------------------------------------------------------------

class TestSplit:
    def test_basic(self, tmp_path: Path) -> None:
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")

        parts = split(md)

        assert 'msgid: "<test@example.com>"' in parts.frontmatter_raw
        assert parts.frontmatter["msgid"] == "<test@example.com>"
        assert "# 테스트 메일" in parts.head
        assert "안녕하세요." in parts.body
        assert parts.llm_block is None
        assert "관련:" in parts.tail

    def test_frontmatter_fields(self, tmp_path: Path) -> None:
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)

        assert parts.frontmatter["thread"] == "t_abc123"
        assert parts.frontmatter["tags"] == ["inbox"]
        assert parts.frontmatter["to"] == ["bob@example.com"]

    def test_existing_llm_block_extracted(self, tmp_path: Path) -> None:
        llm_content = "## 요약 (LLM)\n계약 이행 관련 메일.\n\n"
        md = tmp_path / "mail.md"
        md.write_text(
            _make_md(llm_block=llm_content),
            encoding="utf-8",
        )

        parts = split(md)

        assert parts.llm_block is not None
        assert "## 요약 (LLM)" in parts.llm_block
        assert "<!-- LLM-ENRICH:BEGIN -->" in parts.llm_block
        assert "계약 이행" in parts.llm_block
        # tail 에서 LLM 블록 제거됐는지
        assert "<!-- LLM-ENRICH" not in parts.tail

    def test_body_unchanged_after_llm_split(self, tmp_path: Path) -> None:
        body_text = "원본 본문 내용\n두 번째 줄"
        llm_content = "## 요약 (LLM)\n요약 내용\n\n"
        md = tmp_path / "mail.md"
        md.write_text(
            _make_md(body=body_text, llm_block=llm_content),
            encoding="utf-8",
        )

        parts = split(md)
        assert parts.body == body_text

    def test_no_frontmatter_raises(self, tmp_path: Path) -> None:
        md = tmp_path / "bad.md"
        md.write_text("# 그냥 마크다운\n내용", encoding="utf-8")
        with pytest.raises(ValueError, match="frontmatter"):
            split(md)

    def test_missing_body_separator_raises(self, tmp_path: Path) -> None:
        md = tmp_path / "bad.md"
        md.write_text("---\nmsgid: x\n---\n\n# Title\n본문", encoding="utf-8")
        with pytest.raises(ValueError):
            split(md)

    def test_body_with_triple_dash_inside(self, tmp_path: Path) -> None:
        """본문 안에 --- 가 있어도 rfind 로 인해 올바른 body 추출."""
        body_text = "서론\n\n---\n\n본문 중간\n\n계속"
        # 이 경우 after_head 에서 rfind 로 마지막 \n\n---\n\n 찾음
        tail = "관련: [[t_abc]]\n"
        content = (
            "---\nmsgid: x\n---\n\n"
            "# Title\n\n---\n\n"
            f"{body_text}\n\n---\n\n"
            f"{tail}"
        )
        md = tmp_path / "mail.md"
        md.write_text(content, encoding="utf-8")
        parts = split(md)
        assert parts.body == body_text
        assert parts.tail == tail


# ---------------------------------------------------------------------------
# body_hash() 테스트
# ---------------------------------------------------------------------------

class TestBodyHash:
    def test_hash_matches_sha256(self, tmp_path: Path) -> None:
        body = "본문 텍스트"
        md = tmp_path / "mail.md"
        md.write_text(_make_md(body=body), encoding="utf-8")
        parts = split(md)

        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert body_hash(parts) == expected

    def test_hash_stable_across_llm_enrich(self, tmp_path: Path) -> None:
        """LLM 블록 추가 전후로 body_hash 동일."""
        body = "변하지 않는 본문"
        md = tmp_path / "mail.md"
        md.write_text(_make_md(body=body), encoding="utf-8")

        parts_before = split(md)
        h_before = body_hash(parts_before)

        # LLM 블록 삽입
        write(md, {"llm_hash": h_before, "summary": "요약"}, "## 요약 (LLM)\n요약\n\n", parts_before)

        parts_after = split(md)
        h_after = body_hash(parts_after)

        assert h_before == h_after


# ---------------------------------------------------------------------------
# write() 테스트
# ---------------------------------------------------------------------------

class TestWrite:
    def test_frontmatter_llm_fields_added(self, tmp_path: Path) -> None:
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)
        h = body_hash(parts)

        write(
            md,
            {
                "summary": "계약 관련 요약",
                "llm_tags": ["계약", "법무"],
                "llm_hash": h,
                "llm_model": "gpt-4o-mini",
                "llm_enriched_at": "2026-04-19T00:00:00+00:00",
            },
            "## 요약 (LLM)\n계약 관련 요약\n\n",
            parts,
        )

        new_parts = split(md)
        assert new_parts.frontmatter["summary"] == "계약 관련 요약"
        assert new_parts.frontmatter["llm_hash"] == h
        assert new_parts.frontmatter["llm_model"] == "gpt-4o-mini"
        assert new_parts.frontmatter["llm_tags"] == ["계약", "법무"]

    def test_body_bytes_unchanged(self, tmp_path: Path) -> None:
        body = "불변 본문\n두 번째 줄\n세 번째 줄"
        md = tmp_path / "mail.md"
        md.write_text(_make_md(body=body), encoding="utf-8")
        parts = split(md)

        write(md, {"llm_hash": body_hash(parts)}, "## 요약 (LLM)\n내용\n\n", parts)

        new_parts = split(md)
        assert new_parts.body == body

    def test_tail_preserved(self, tmp_path: Path) -> None:
        tail = "## 첨부 파일\n\n![img.gif](attachments/img.gif)\n\n관련: [[t_xxx]] · [[alice@example.com]]\n"
        md = tmp_path / "mail.md"
        md.write_text(_make_md(tail=tail), encoding="utf-8")
        parts = split(md)

        write(md, {"llm_hash": "abc"}, "## 요약 (LLM)\n내용\n\n", parts)

        new_parts = split(md)
        assert new_parts.tail == tail

    def test_existing_llm_fields_overwritten(self, tmp_path: Path) -> None:
        """두 번째 write() 시 기존 LLM 필드가 교체된다."""
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)

        # 1차 enrichment
        write(md, {"summary": "첫 번째 요약", "llm_hash": "hash1"}, "## 요약 (LLM)\n첫 번째\n\n", parts)

        # 2차 enrichment (force 재실행 시나리오)
        parts2 = split(md)
        write(md, {"summary": "두 번째 요약", "llm_hash": "hash2"}, "## 요약 (LLM)\n두 번째\n\n", parts2)

        final = split(md)
        assert final.frontmatter["summary"] == "두 번째 요약"
        assert final.frontmatter["llm_hash"] == "hash2"
        # 첫 번째 summary 는 사라져야 함
        assert "첫 번째 요약" not in final.frontmatter_raw.replace("summary:", "")

    def test_existing_llm_block_replaced(self, tmp_path: Path) -> None:
        """2회차 write() 시 LLM 블록이 교체된다 (중복 없음)."""
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)
        write(md, {"llm_hash": "h1"}, "## 요약 (LLM)\n구 버전\n\n", parts)

        parts2 = split(md)
        write(md, {"llm_hash": "h2"}, "## 요약 (LLM)\n신 버전\n\n", parts2)

        text = md.read_text(encoding="utf-8")
        assert text.count("<!-- LLM-ENRICH:BEGIN -->") == 1
        assert "신 버전" in text
        assert "구 버전" not in text

    def test_tmp_cleaned_on_write_failure(self, tmp_path: Path) -> None:
        """write() 중 쓰기 실패(권한 오류) 시 .tmp 파일이 남지 않는다."""
        import os
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)

        # 디렉터리를 읽기 전용으로 만들어 tmp 파일 생성 실패 유발
        tmp_path.chmod(0o555)
        try:
            with pytest.raises(OSError):
                write(md, {}, "## 요약\n내용\n\n", parts)
            assert not (tmp_path / "mail.tmp").exists()
        finally:
            tmp_path.chmod(0o755)

    def test_atomic_write_preserves_original_on_error(self, tmp_path: Path) -> None:
        """write() 오류 시 원본 파일 내용이 훼손되지 않는다."""
        import os
        original_text = _make_md()
        md = tmp_path / "mail.md"
        md.write_text(original_text, encoding="utf-8")
        parts = split(md)

        tmp_path.chmod(0o555)
        try:
            with pytest.raises(OSError):
                write(md, {}, "", parts)
        finally:
            tmp_path.chmod(0o755)

        assert md.read_text(encoding="utf-8") == original_text


# ---------------------------------------------------------------------------
# 라운드트립 테스트
# ---------------------------------------------------------------------------

class TestRoundtrip:
    def test_split_write_split_idempotent(self, tmp_path: Path) -> None:
        """split → write → split 해도 비-LLM 필드 동일."""
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")
        parts = split(md)

        write(
            md,
            {"summary": "요약", "llm_tags": ["태그"], "llm_hash": body_hash(parts)},
            "## 요약 (LLM)\n요약\n\n## 관련 문서 (LLM)\n- [[t_abc]]\n\n",
            parts,
        )

        parts2 = split(md)
        assert parts2.body == parts.body
        assert parts2.head == parts.head
        assert parts2.tail == parts.tail
        assert parts2.frontmatter["msgid"] == parts.frontmatter["msgid"]

    def test_no_double_llm_block(self, tmp_path: Path) -> None:
        """2회 write 해도 LLM 블록이 하나만 존재한다."""
        md = tmp_path / "mail.md"
        md.write_text(_make_md(), encoding="utf-8")

        for i in range(2):
            p = split(md)
            write(md, {"llm_hash": f"h{i}"}, f"## 요약 (LLM)\n요약{i}\n\n", p)

        text = md.read_text(encoding="utf-8")
        assert text.count("<!-- LLM-ENRICH:BEGIN -->") == 1
        assert text.count("<!-- LLM-ENRICH:END -->") == 1
