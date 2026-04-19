"""
tests/test_mailenrich.py — mailenrich 통합 테스트

검증 항목:
1. 멱등성    — llm_hash 일치 시 skip
2. 해시 delta — body 변경 시 재실행
3. dry-run   — LLM 미호출, 통계만 출력
4. body 바이트 불변성 — enrichment 후 body 동일
5. --force   — llm_hash 무시 재실행
6. skip 조건 — body 너무 짧은 경우
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.lib.llm_client import LLMResponse
from scripts.lib.md_io import body_hash, split


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _make_archive(tmp_path: Path, body: str = "이 메일은 테스트입니다.\n두 번째 줄.\n" + "내용 " * 30) -> Path:
    """최소 아카이브 구조를 만들고 MD 파일 경로를 반환한다."""
    archive = tmp_path / "archive" / "2024" / "01" / "01"
    archive.mkdir(parents=True)
    md = archive / "test_mail.md"
    fm = (
        'msgid: "<test@example.com>"\n'
        'date: 2024-01-01T00:00:00+00:00\n'
        'from: "Alice <alice@example.com>"\n'
        'to: ["bob@example.com"]\n'
        'cc: []\n'
        'subject: "테스트 메일"\n'
        'folder: "Inbox"\n'
        'thread: "t_abc123"\n'
        'in_reply_to: ""\n'
        'references: []\n'
        'attachments:\n'
        'tags: ["inbox"]\n'
        'source_pst: "test.pst"\n'
    )
    content = (
        f"---\n{fm}---\n\n"
        "# 테스트 메일\n\n"
        "**보낸사람:** Alice  \n"
        "**받는사람:** bob@example.com  \n"
        "**날짜:** 2024-01-01T00:00:00+00:00\n\n"
        "---\n\n"
        f"{body}\n\n"
        "---\n\n"
        "관련: [[t_abc123]]\n"
    )
    md.write_text(content, encoding="utf-8")
    return tmp_path


def _mock_llm_response(
    summary: str = "테스트 요약",
    tags: list | None = None,
    related: list | None = None,
) -> MagicMock:
    payload = {
        "summary": summary,
        "tags": tags or ["테스트"],
        "related": related or [],
    }
    mock = MagicMock()
    mock.complete.return_value = LLMResponse(
        text=json.dumps(payload, ensure_ascii=False),
        input_tokens=500,
        output_tokens=150,
        model="gpt-4o-mini",
    )
    return mock


def _run_mailenrich(archive_root: Path, **kwargs) -> None:
    """mailenrich main 을 직접 호출한다 (Click CLI runner)."""
    from click.testing import CliRunner
    from scripts.mailenrich import main

    runner = CliRunner()
    args = [f"--archive={archive_root}"]
    for k, v in kwargs.items():
        flag = "--" + k.replace("_", "-")
        if isinstance(v, bool):
            if v:
                args.append(flag)
        else:
            args.append(f"{flag}={v}")

    result = runner.invoke(main, args, catch_exceptions=False)
    return result


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_skips_when_hash_matches(self, tmp_path: Path) -> None:
        """llm_hash 가 이미 설정된 파일은 skip 된다."""
        arch = _make_archive(tmp_path)
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)
            first_call_count = mock_client.complete.call_count

            # 2차 실행 — skip 해야 함
            _run_mailenrich(arch)
            second_call_count = mock_client.complete.call_count

        assert first_call_count == 1
        assert second_call_count == 1  # 추가 호출 없음

    def test_reruns_when_hash_changes(self, tmp_path: Path) -> None:
        """body 가 바뀌면 llm_hash 불일치로 재실행된다."""
        arch = _make_archive(tmp_path)
        mock_client = _mock_llm_response()
        md = next((arch / "archive").rglob("*.md"))

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

            # body 변경 시뮬레이션: frontmatter 의 llm_hash 만 오염
            text = md.read_text(encoding="utf-8")
            text = text.replace('llm_hash: "', 'llm_hash: "CHANGED_')
            md.write_text(text, encoding="utf-8")

            _run_mailenrich(arch)

        assert mock_client.complete.call_count == 2


class TestForceFlag:
    def test_force_reruns_despite_hash_match(self, tmp_path: Path) -> None:
        """--force 시 llm_hash 일치해도 재실행된다."""
        arch = _make_archive(tmp_path)
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)
            _run_mailenrich(arch, force=True)

        assert mock_client.complete.call_count == 2


class TestDryRun:
    def test_dry_run_does_not_call_llm(self, tmp_path: Path) -> None:
        """--dry-run 은 LLM 을 호출하지 않는다."""
        arch = _make_archive(tmp_path)
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            result = _run_mailenrich(arch, dry_run=True)

        assert mock_client.complete.call_count == 0

    def test_dry_run_does_not_modify_file(self, tmp_path: Path) -> None:
        """--dry-run 후 파일이 변경되지 않는다."""
        arch = _make_archive(tmp_path)
        md = next((arch / "archive").rglob("*.md"))
        original = md.read_text(encoding="utf-8")

        with patch("scripts.mailenrich.get_client", return_value=MagicMock()):
            _run_mailenrich(arch, dry_run=True)

        assert md.read_text(encoding="utf-8") == original


class TestBodyImmutability:
    def test_body_unchanged_after_enrich(self, tmp_path: Path) -> None:
        """enrichment 후 body 바이트가 동일하다."""
        arch = _make_archive(tmp_path)
        md = next((arch / "archive").rglob("*.md"))
        parts_before = split(md)
        original_body = parts_before.body
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

        parts_after = split(md)
        assert parts_after.body == original_body

    def test_hash_stable_after_enrich(self, tmp_path: Path) -> None:
        """enrichment 전후 body_hash 가 동일하다."""
        arch = _make_archive(tmp_path)
        md = next((arch / "archive").rglob("*.md"))
        h_before = body_hash(split(md))
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

        h_after = body_hash(split(md))
        assert h_before == h_after


class TestFrontmatterUpdated:
    def test_llm_fields_written_to_frontmatter(self, tmp_path: Path) -> None:
        """enrichment 후 frontmatter 에 LLM 필드가 기록된다."""
        arch = _make_archive(tmp_path)
        md = next((arch / "archive").rglob("*.md"))
        mock_client = _mock_llm_response(
            summary="계약 관련 요약",
            tags=["계약", "법무"],
        )

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

        parts = split(md)
        assert parts.frontmatter["summary"] == "계약 관련 요약"
        assert parts.frontmatter["llm_tags"] == ["계약", "법무"]
        assert "llm_hash" in parts.frontmatter
        assert "llm_enriched_at" in parts.frontmatter

    def test_llm_block_inserted(self, tmp_path: Path) -> None:
        """enrichment 후 <!-- LLM-ENRICH:BEGIN --> 블록이 삽입된다."""
        arch = _make_archive(tmp_path)
        md = next((arch / "archive").rglob("*.md"))
        mock_client = _mock_llm_response(summary="요약 내용")

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

        text = md.read_text(encoding="utf-8")
        assert "<!-- LLM-ENRICH:BEGIN -->" in text
        assert "요약 내용" in text


class TestSkipConditions:
    def test_skip_short_body(self, tmp_path: Path) -> None:
        """본문이 너무 짧으면 skip 된다."""
        arch = _make_archive(tmp_path, body="짧음")
        mock_client = _mock_llm_response()

        with patch("scripts.mailenrich.get_client", return_value=mock_client):
            _run_mailenrich(arch)

        assert mock_client.complete.call_count == 0


class TestDateFilter:
    def test_parse_date_filter_valid(self) -> None:
        from scripts.mailenrich import _parse_date_filter

        assert _parse_date_filter("", "--since") is None
        assert _parse_date_filter("2024-03-15", "--since") == (2024, 3, 15)

    def test_parse_date_filter_rejects_bad_format(self) -> None:
        import click
        from scripts.mailenrich import _parse_date_filter

        with pytest.raises(click.BadParameter):
            _parse_date_filter("2024/03/15", "--since")
        with pytest.raises(click.BadParameter):
            _parse_date_filter("not-a-date", "--since")

    def test_path_date_extraction(self) -> None:
        from scripts.mailenrich import _path_date

        assert _path_date(Path("2024/03/15/m.md")) == (2024, 3, 15)
        assert _path_date(Path("undated/m.md")) is None
        assert _path_date(Path("abc/03/15/m.md")) is None

    def test_since_until_filter(self, tmp_path: Path) -> None:
        """date 필터로 대상 파일이 좁혀진다."""
        from scripts.mailenrich import _iter_md_files

        archive = tmp_path / "archive"
        for day in ("2024/01/05", "2024/03/20", "2024/06/10", "undated"):
            d = archive / day
            d.mkdir(parents=True)
            (d / "mail.md").write_text("x", encoding="utf-8")

        all_files = _iter_md_files(tmp_path, (), 0, [], None, None)
        assert len(all_files) == 4  # undated 포함

        since_only = _iter_md_files(tmp_path, (), 0, [], (2024, 3, 1), None)
        # undated 는 날짜 필터 적용 시 제외
        assert len(since_only) == 2
        assert all("2024/01" not in str(p) and "undated" not in str(p) for p in since_only)

        windowed = _iter_md_files(tmp_path, (), 0, [], (2024, 2, 1), (2024, 5, 31))
        assert len(windowed) == 1
        assert "2024/03/20" in str(windowed[0]).replace("\\", "/")
