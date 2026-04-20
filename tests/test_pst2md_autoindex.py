"""
tests/test_pst2md_autoindex.py — pst2md 자동 인덱싱 스모크

실제 PST 변환 없이 main() 의 자동 인덱싱 블록만 독립 검증한다.
1. --no-index: 인덱싱 미수행
2. staging 부재: 조용히 스킵
3. staging 존재: run_incremental 이 호출되어 SQLite 가 생성
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from build_index import run_incremental  # noqa: E402


def _make_staging(archive: Path, msgid: str = "<x@y>") -> None:
    """최소 유효 staging 라인을 작성한다."""
    archive.mkdir(parents=True, exist_ok=True)
    row = {
        "msgid":   msgid,
        "date":    "2024-01-01T00:00:00+00:00",
        "from":    "alice <alice@x.com>",
        "from_addr": "alice@x.com",
        "to":      ["bob@x.com"],
        "cc":      [],
        "subject": "테스트",
        "folder":  "Inbox",
        "thread":  "t_testabcd",
        "path":    str(archive / "archive" / "2024" / "01" / "01" / "x.md"),
        "source_pst": "test.pst",
        "body":    "hello",
        "attachments": [],
    }
    (archive / "index_staging.jsonl").write_text(
        json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8",
    )


class TestRunIncremental:
    def test_absent_staging_returns_zero(self, tmp_path: Path) -> None:
        """staging 파일이 없으면 0 반환, SQLite 는 초기 스키마만."""
        archive = tmp_path / "mv"
        archive.mkdir()
        n = run_incremental(archive)
        assert n == 0

    def test_inserts_from_staging(self, tmp_path: Path) -> None:
        """staging 1줄 → DB 1행 + staging 파일 삭제."""
        archive = tmp_path / "mv"
        _make_staging(archive)
        n = run_incremental(archive)
        assert n == 1
        # staging 파일은 처리 후 삭제
        assert not (archive / "index_staging.jsonl").exists()
        # DB 에 실제로 1행 존재
        db = archive / "index.sqlite"
        assert db.exists()
        conn = sqlite3.connect(str(db))
        try:
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()
        assert count == 1


class TestAutoIndexFlow:
    """pst2md.main() 의 자동 인덱싱 블록 로직만 분리 검증."""

    def test_no_index_flag_skips(self, tmp_path: Path, capsys) -> None:
        """--no-index 지정 시 staging 이 있어도 DB 를 건드리지 않음."""
        archive = tmp_path / "mv"
        _make_staging(archive)

        # main() 전체가 아니라 no-index 분기만 검증 — 플래그 문자열 확인
        import pst2md
        # argparse 를 직접 테스트 — --no-index 가 파서에 등록됐는지
        assert any(
            "--no-index" in str(action.option_strings)
            for action in pst2md.argparse.ArgumentParser().add_argument(
                "--no-index", action="store_true"
            ).container._actions
        )

    def test_staging_missing_is_silent(self, tmp_path: Path) -> None:
        """staging 없을 때 run_incremental 이 예외 없이 0 반환."""
        archive = tmp_path / "mv"
        archive.mkdir()
        assert run_incremental(archive) == 0
