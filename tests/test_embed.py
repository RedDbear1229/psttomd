"""
tests/test_embed.py — embed CLI 통합 테스트

검증 항목:
1. 새 MD 파일 → embedding 생성 + DB 저장
2. 동일 (body_hash, model) → skip (중복 분석 방지)
3. 모델만 다르면 재생성
4. body 변경 → 재생성 (body_hash mismatch)
5. --force → 항상 재생성
6. body 짧은 파일 skip
7. msgid 누락 skip
8. dry-run 모드 — DB 미변경
9. float32 BLOB 라운드트립
10. 다중 파일 배치 → 한 HTTP 요청에 묶임
"""
from __future__ import annotations

import array
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.embed import (
    _collect_candidates,
    _existing_signatures,
    _open_db,
    _upsert_results,
    _vector_to_blob,
    main as embed_main,
)
from scripts.lib.embed_client import EmbeddingResponse


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _write_md(
    archive: Path,
    msgid: str,
    body: str = "테스트 본문입니다.\n" + "긴 내용 " * 30,
    name: str = "mail.md",
    sub: str = "2024/01/01",
) -> Path:
    """msgid 와 body 만 다른 최소 MD 파일을 생성한다."""
    md_dir = archive / "archive" / sub
    md_dir.mkdir(parents=True, exist_ok=True)
    md = md_dir / name
    fm = (
        f'msgid: "{msgid}"\n'
        'date: 2024-01-01T00:00:00+00:00\n'
        'from: "Alice <alice@example.com>"\n'
        'to: ["bob@example.com"]\n'
        'cc: []\n'
        'subject: "테스트 메일"\n'
        'folder: "Inbox"\n'
        'thread: "t_abc"\n'
        'in_reply_to: ""\n'
        'references: []\n'
        'attachments:\n'
        'tags: ["inbox"]\n'
        'source_pst: "test.pst"\n'
    )
    content = (
        f"---\n{fm}---\n\n"
        "# 테스트 메일\n\n"
        "**보낸사람:** Alice\n\n"
        "---\n\n"
        f"{body}\n\n"
        "---\n\n"
        "관련: [[t_abc]]\n"
    )
    md.write_text(content, encoding="utf-8")
    return md


def _archive(tmp_path: Path) -> Path:
    """archive 디렉터리를 만들어 둔 루트를 반환한다."""
    (tmp_path / "archive").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _mock_embed_response(n: int, dim: int = 4, model: str = "text-embedding-3-small"):
    """n 개 요청에 대한 가짜 EmbeddingResponse."""
    return EmbeddingResponse(
        vectors=[[float(i + 1)] * dim for i in range(n)],
        model=model,
        dim=dim,
        input_tokens=n * 10,
    )


# ---------------------------------------------------------------------------
# DB 헬퍼 / 직렬화 단위 테스트
# ---------------------------------------------------------------------------

class TestVectorBlob:
    def test_roundtrip_float32(self) -> None:
        vec = [0.1, -0.2, 1.5, -3.14]
        blob = _vector_to_blob(vec)
        assert len(blob) == 4 * 4  # 4 floats × 4 bytes
        restored = list(array.array("f", blob))
        for orig, got in zip(vec, restored):
            assert abs(orig - got) < 1e-5  # float32 정밀도


class TestOpenDb:
    def test_creates_embeddings_table(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        conn = _open_db(archive)
        try:
            cur = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='embeddings'"
            )
            assert cur.fetchone() is not None
            cols = {r[1] for r in conn.execute("PRAGMA table_info(embeddings)")}
            assert {"msgid", "body_hash", "model", "dim", "vector", "created_at"} <= cols
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# _collect_candidates — skip 규칙
# ---------------------------------------------------------------------------

class TestCollectCandidates:
    def test_new_file_is_candidate(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>")
        cands, skipped = _collect_candidates(
            [md], existing={}, model="m", min_body=10, force=False
        )
        assert len(cands) == 1
        assert skipped == 0
        assert cands[0]["msgid"] == "<m1@x>"

    def test_existing_match_skipped(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>")
        # body_hash 를 미리 계산
        from scripts.lib.md_io import body_hash, split
        bh = body_hash(split(md))

        cands, skipped = _collect_candidates(
            [md], existing={"<m1@x>": (bh, "m")}, model="m",
            min_body=10, force=False,
        )
        assert cands == []
        assert skipped == 1

    def test_different_model_is_candidate(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>")
        from scripts.lib.md_io import body_hash, split
        bh = body_hash(split(md))

        cands, skipped = _collect_candidates(
            [md], existing={"<m1@x>": (bh, "old-model")}, model="new-model",
            min_body=10, force=False,
        )
        assert len(cands) == 1
        assert skipped == 0

    def test_different_body_hash_is_candidate(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>")
        cands, skipped = _collect_candidates(
            [md], existing={"<m1@x>": ("stale-hash", "m")}, model="m",
            min_body=10, force=False,
        )
        assert len(cands) == 1
        assert skipped == 0

    def test_force_ignores_existing(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>")
        from scripts.lib.md_io import body_hash, split
        bh = body_hash(split(md))

        cands, skipped = _collect_candidates(
            [md], existing={"<m1@x>": (bh, "m")}, model="m",
            min_body=10, force=True,
        )
        assert len(cands) == 1
        assert skipped == 0

    def test_short_body_skipped(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>", body="짧음")
        cands, skipped = _collect_candidates(
            [md], existing={}, model="m", min_body=100, force=False,
        )
        assert cands == []
        assert skipped == 1

    def test_missing_msgid_skipped(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "")  # empty msgid
        cands, skipped = _collect_candidates(
            [md], existing={}, model="m", min_body=10, force=False,
        )
        assert cands == []
        assert skipped == 1


# ---------------------------------------------------------------------------
# _upsert_results — DB 라운드트립
# ---------------------------------------------------------------------------

class TestUpsert:
    def test_insert_then_signatures_match(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        conn = _open_db(archive)
        try:
            batch = [
                {"msgid": "<a>", "body_hash": "h1", "text": "x"},
                {"msgid": "<b>", "body_hash": "h2", "text": "y"},
            ]
            resp = _mock_embed_response(2, dim=3, model="m1")
            _upsert_results(conn, resp, batch)

            sigs = _existing_signatures(conn)
            assert sigs == {"<a>": ("h1", "m1"), "<b>": ("h2", "m1")}

            row = conn.execute(
                "SELECT dim, vector FROM embeddings WHERE msgid='<a>'"
            ).fetchone()
            assert row[0] == 3
            restored = list(array.array("f", row[1]))
            assert restored == [1.0, 1.0, 1.0]  # mock 첫 행
        finally:
            conn.close()

    def test_replace_overwrites(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        conn = _open_db(archive)
        try:
            batch = [{"msgid": "<a>", "body_hash": "h1", "text": "x"}]
            _upsert_results(conn, _mock_embed_response(1, model="m1"), batch)

            # 같은 msgid 로 다른 모델·해시 재호출
            batch2 = [{"msgid": "<a>", "body_hash": "h2", "text": "x"}]
            _upsert_results(conn, _mock_embed_response(1, model="m2"), batch2)

            sigs = _existing_signatures(conn)
            assert sigs == {"<a>": ("h2", "m2")}
            count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            assert count == 1
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# CLI 통합 — 중복 분석 방지 시나리오
# ---------------------------------------------------------------------------

class TestEmbedCli:
    def _run(self, archive: Path, *args: str):
        runner = CliRunner()
        return runner.invoke(
            embed_main,
            ["--archive", str(archive), *args],
            catch_exceptions=False,
        )

    def test_dry_run_does_not_write_db(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        _write_md(archive, "<m1@x>")
        with patch("scripts.embed.EmbeddingClient") as mock_cls:
            result = self._run(archive, "--dry-run")
        assert result.exit_code == 0
        assert "[DRY-RUN]" in result.output
        mock_cls.assert_not_called()

        conn = sqlite3.connect(str(archive / "index.sqlite"))
        try:
            count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            assert count == 0
        finally:
            conn.close()

    def test_first_run_creates_rows(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        _write_md(archive, "<m1@x>", name="m1.md")
        _write_md(archive, "<m2@x>", name="m2.md")

        mock_client = MagicMock()
        mock_client.embed.return_value = _mock_embed_response(2)
        with patch("scripts.embed.EmbeddingClient", return_value=mock_client):
            result = self._run(archive)

        assert result.exit_code == 0, result.output
        assert mock_client.embed.call_count == 1  # 한 배치
        assert len(mock_client.embed.call_args[0][0]) == 2  # 2개 묶임

        conn = sqlite3.connect(str(archive / "index.sqlite"))
        try:
            count = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            assert count == 2
        finally:
            conn.close()

    def test_second_run_skips_unchanged(self, tmp_path: Path) -> None:
        """두 번째 실행은 본문이 같으면 LLM 을 호출하지 않아야 한다."""
        archive = _archive(tmp_path)
        _write_md(archive, "<m1@x>")

        mock_client = MagicMock()
        mock_client.embed.return_value = _mock_embed_response(1)
        with patch("scripts.embed.EmbeddingClient", return_value=mock_client):
            r1 = self._run(archive)
            assert r1.exit_code == 0
            assert mock_client.embed.call_count == 1

            # 두 번째 실행: 같은 body, 같은 모델 → skip
            r2 = self._run(archive)
            assert r2.exit_code == 0
            assert mock_client.embed.call_count == 1  # 변화 없음
            assert "새로 처리할 항목 없음" in r2.output

    def test_force_re_runs(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        _write_md(archive, "<m1@x>")

        mock_client = MagicMock()
        mock_client.embed.return_value = _mock_embed_response(1)
        with patch("scripts.embed.EmbeddingClient", return_value=mock_client):
            self._run(archive)
            assert mock_client.embed.call_count == 1
            self._run(archive, "--force")
            assert mock_client.embed.call_count == 2

    def test_body_change_re_runs(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        md = _write_md(archive, "<m1@x>", body="원본 본문 " * 30)

        mock_client = MagicMock()
        mock_client.embed.return_value = _mock_embed_response(1)
        with patch("scripts.embed.EmbeddingClient", return_value=mock_client):
            self._run(archive)
            assert mock_client.embed.call_count == 1

            # body 만 변경 — frontmatter 는 그대로
            _write_md(archive, "<m1@x>", body="새로운 본문 " * 30)
            self._run(archive)
            assert mock_client.embed.call_count == 2

    def test_limit_caps_files(self, tmp_path: Path) -> None:
        archive = _archive(tmp_path)
        for i in range(5):
            _write_md(archive, f"<m{i}@x>", name=f"m{i}.md")

        mock_client = MagicMock()
        mock_client.embed.return_value = _mock_embed_response(2)
        with patch("scripts.embed.EmbeddingClient", return_value=mock_client):
            self._run(archive, "--limit", "2")
        assert mock_client.embed.call_count == 1
        assert len(mock_client.embed.call_args[0][0]) == 2
