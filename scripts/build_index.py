#!/usr/bin/env python3
"""
SQLite FTS5 인덱스 빌더

아카이브 Markdown 파일의 메타데이터를 SQLite 데이터베이스에 삽입하고,
FTS5 전문 검색 인덱스를 관리한다.

두 가지 동작 모드:
  기본(증분)  — pst2md.py 가 생성한 index_staging.jsonl 만 처리 (빠름)
  --rebuild   — 아카이브 전체를 재스캔해 인덱스 재구축 (느리지만 확실)

사용법:
  python build_index.py [--archive ~/mail-archive] [--incremental]
  python build_index.py --rebuild   # 인덱스 완전 재구축
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

#: 데이터베이스 스키마 — 최초 실행 시 한 번만 적용
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS messages (
    id             INTEGER PRIMARY KEY,
    msgid          TEXT UNIQUE NOT NULL,
    date           TEXT,
    from_name      TEXT,
    from_addr      TEXT,
    to_addrs       TEXT,   -- JSON 배열
    cc_addrs       TEXT,   -- JSON 배열
    subject        TEXT,
    folder         TEXT,
    thread         TEXT,
    source_pst     TEXT,
    path           TEXT NOT NULL,
    n_attachments  INTEGER DEFAULT 0,
    indexed_at     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_date      ON messages(date);
CREATE INDEX IF NOT EXISTS idx_messages_from_addr ON messages(from_addr);
CREATE INDEX IF NOT EXISTS idx_messages_thread    ON messages(thread);
CREATE INDEX IF NOT EXISTS idx_messages_folder    ON messages(folder);

-- FTS5 contentless 가상 테이블: 본문 자체는 저장하지 않고 인덱스만 유지
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    subject,
    from_name,
    from_addr,
    to_addrs,
    body,
    content='',
    tokenize='unicode61'
);

-- FTS5 rowid ↔ msgid 매핑 테이블 (rebuild 시 중복 방지용)
CREATE TABLE IF NOT EXISTS fts_sync (
    msgid TEXT PRIMARY KEY,
    path  TEXT
);
"""

#: pst2md.py 가 생성하는 증분 인덱스 파일
STAGING_FILE = "index_staging.jsonl"


# ---------------------------------------------------------------------------
# DB 연결 및 스키마 초기화
# ---------------------------------------------------------------------------

def get_conn(archive_root: Path) -> sqlite3.Connection:
    """SQLite 연결을 열고 성능 최적화 PRAGMA 를 설정한다.

    WAL 모드: 읽기/쓰기 동시성 향상.
    NORMAL 동기화: fsync 빈도 감소로 쓰기 속도 향상.

    Args:
        archive_root: 아카이브 루트 디렉터리 (index.sqlite 위치).

    Returns:
        설정이 완료된 sqlite3.Connection.
    """
    db_path = archive_root / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """데이터베이스에 스키마를 적용한다 (CREATE TABLE IF NOT EXISTS).

    기존 DB 에 n_attachments 열이 없으면 마이그레이션으로 추가한다.

    Args:
        conn: 활성 SQLite 연결.
    """
    conn.executescript(SCHEMA_SQL)
    # 마이그레이션: n_attachments 컬럼 추가 (기존 DB 호환)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    if "n_attachments" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN n_attachments INTEGER DEFAULT 0")
    conn.commit()


# ---------------------------------------------------------------------------
# 본문 추출
# ---------------------------------------------------------------------------

def read_body(path: str) -> str:
    """Markdown 파일에서 YAML frontmatter 를 제외한 본문을 추출한다.

    첫 번째 '---' 구분자 쌍 사이의 내용을 건너뛰고 이후 텍스트를 반환한다.
    FTS5 인덱싱에 사용된다.

    Args:
        path: Markdown 파일 경로 문자열.

    Returns:
        frontmatter 를 제외한 본문 문자열. 읽기 실패 시 빈 문자열.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---\n", 3)
            if end != -1:
                return text[end + 4:].strip()
        return text.strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# 행 삽입
# ---------------------------------------------------------------------------

def insert_row(conn: sqlite3.Connection, row: dict) -> None:
    """messages 테이블과 FTS5 인덱스에 단일 행을 삽입한다.

    msgid 가 이미 존재하면 조용히 무시한다(멱등 삽입).
    FTS5 삽입은 messages 삽입에 성공했을 때만 수행한다.

    Args:
        conn: 활성 SQLite 연결.
        row:  pst2md.py 또는 extract_frontmatter() 가 반환한 메타데이터 dict.
    """
    try:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO messages
                (msgid, date, from_name, from_addr, to_addrs, cc_addrs,
                 subject, folder, thread, source_pst, path, n_attachments)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("msgid", ""),
                row.get("date", ""),
                row.get("from", ""),
                row.get("from_addr", ""),
                json.dumps(row.get("to", []), ensure_ascii=False),
                json.dumps(row.get("cc", []), ensure_ascii=False),
                row.get("subject", ""),
                row.get("folder", ""),
                row.get("thread", ""),
                row.get("source_pst", ""),
                row.get("path", ""),
                int(row.get("n_attachments", 0)),
            ),
        )
        # rowcount > 0 이면 실제로 새 행이 삽입된 경우 → FTS5 도 삽입
        if cursor.lastrowid and cursor.rowcount:
            body = read_body(row.get("path", ""))
            conn.execute(
                """
                INSERT INTO messages_fts(rowid, subject, from_name, from_addr, to_addrs, body)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    cursor.lastrowid,
                    row.get("subject", ""),
                    row.get("from", ""),
                    row.get("from_addr", ""),
                    " ".join(row.get("to", [])),
                    body,
                ),
            )
    except sqlite3.IntegrityError:
        pass  # UNIQUE 제약 위반(중복 msgid) — 정상적인 경우


# ---------------------------------------------------------------------------
# 증분 처리 (index_staging.jsonl)
# ---------------------------------------------------------------------------

def process_staging(
    conn: sqlite3.Connection,
    archive_root: Path,
    remove_after: bool = True,
) -> int:
    """index_staging.jsonl 을 읽어 DB 에 삽입하고 행 수를 반환한다.

    pst2md.py 실행 후 새로 변환된 메시지만 빠르게 인덱싱할 때 사용한다.

    Args:
        conn:          활성 SQLite 연결.
        archive_root:  index_staging.jsonl 이 있는 아카이브 루트.
        remove_after:  처리 후 스테이징 파일을 삭제할지 여부 (기본 True).

    Returns:
        삽입 시도한 행 수 (중복 제외 실제 삽입 수는 더 적을 수 있음).
    """
    staging = archive_root / STAGING_FILE
    if not staging.exists():
        log.info("스테이징 파일 없음: %s", staging)
        return 0

    count = 0
    with staging.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                insert_row(conn, row)
                count += 1
            except json.JSONDecodeError as e:
                log.warning("JSON 파싱 실패: %s | %.80s", e, line)

    conn.commit()

    if remove_after:
        staging.unlink()
        log.info("스테이징 파일 삭제")

    return count


# ---------------------------------------------------------------------------
# 전체 재구축
# ---------------------------------------------------------------------------

def rebuild_from_archive(conn: sqlite3.Connection, archive_root: Path) -> int:
    """아카이브 디렉터리를 전체 스캔해 인덱스를 처음부터 재구축한다.

    기존 데이터를 모두 삭제한 뒤 archive/ 하위의 모든 .md 파일을 읽는다.
    대규모 아카이브에서는 수 분이 걸릴 수 있다.

    Args:
        conn:         활성 SQLite 연결.
        archive_root: Markdown 파일들이 있는 아카이브 루트.

    Returns:
        인덱싱 성공한 파일 수.
    """
    log.info("인덱스 초기화...")
    conn.execute("DELETE FROM messages")
    # FTS5 contentless 테이블은 DELETE 미지원 → 전용 delete-all 커맨드 사용
    conn.execute("INSERT INTO messages_fts(messages_fts) VALUES('delete-all')")
    conn.execute("DELETE FROM fts_sync")
    conn.commit()

    md_files = list((archive_root / "archive").rglob("*.md"))
    log.info("MD 파일 %d개 발견, 인덱싱 중...", len(md_files))

    count = 0
    for md_path in tqdm(md_files, unit="file"):
        meta = extract_frontmatter(md_path)
        if meta:
            meta["path"] = str(md_path)
            insert_row(conn, meta)
            count += 1
        # 5000개마다 커밋해 메모리 사용량 제한
        if count % 5000 == 0 and count > 0:
            conn.commit()

    conn.commit()
    return count


# ---------------------------------------------------------------------------
# Frontmatter 파서 (YAML 라이브러리 없이 최소 파싱)
# ---------------------------------------------------------------------------

def extract_frontmatter(md_path: Path) -> dict | None:
    """Markdown 파일의 YAML frontmatter 를 간단하게 파싱한다.

    PyYAML 없이 동작하는 최소 파서다.
    to / cc / references / tags 필드는 JSON 배열로 저장되어 있다고 가정한다.
    from 필드에서 이메일 주소를 추출해 from_addr 를 자동으로 추가한다.

    Args:
        md_path: 파싱할 Markdown 파일 경로.

    Returns:
        필드명 → 값 dict. frontmatter 가 없거나 파싱 실패 시 None.
    """
    try:
        text = md_path.read_text(encoding="utf-8")
        if not text.startswith("---"):
            return None
        end = text.find("\n---\n", 3)
        if end == -1:
            return None

        fm_text = text[3:end]
        meta: dict = {}
        for line in fm_text.splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip().strip('"')
            # JSON 배열 필드 처리
            if key in ("to", "cc", "references", "tags"):
                try:
                    meta[key] = json.loads(val)
                except (json.JSONDecodeError, ValueError):
                    meta[key] = []
            else:
                meta[key] = val

        # from 헤더에서 이메일 주소 추출 → from_addr 생성
        if "from" in meta and "from_addr" not in meta:
            m = re.search(r"<([^>]+)>", meta.get("from", ""))
            meta["from_addr"] = (
                m.group(1).lower() if m else meta.get("from", "").lower()
            )

        # 첨부 파일 수 계산: "  - name:" 패턴 카운트
        n_att = 0
        in_att = False
        for line in fm_text.splitlines():
            stripped = line.strip()
            if stripped == "attachments:":
                in_att = True
                continue
            if in_att:
                if not line.startswith(" ") and stripped:
                    break
                if re.match(r"\s+-\s+name:", line):
                    n_att += 1
        meta["n_attachments"] = n_att

        return meta

    except OSError as e:
        log.warning("frontmatter 파싱 실패 [%s]: %s", md_path, e)
        return None


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """명령행 인자를 파싱하고 인덱싱을 실행한다."""
    _default_archive = load_config()["archive"]["root"]
    parser = argparse.ArgumentParser(
        description="SQLite FTS5 인덱스 빌더",
        epilog=(
            "동작 모드:\n"
            "  기본(증분)   index_staging.jsonl 만 처리 — pst2md 변환 직후 사용\n"
            "  --rebuild    아카이브 전체 재스캔 — 인덱스 손상 복구 또는 최초 구축 시\n"
            "\n"
            "관련 도구:\n"
            "  pst2md       PST → MD 변환 (변환 완료 후 index_staging.jsonl 생성)\n"
            "  mailgrep     FTS5 전문 검색: mailgrep '키워드' --after 2023-01-01\n"
            "  mailview     fzf + glow 인터랙티브 뷰어: mailview '키워드'\n"
            "  mailstat     아카이브 통계 요약: mailstat summary\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archive",     default=_default_archive, help="아카이브 루트")
    parser.add_argument("--incremental", action="store_true", help="스테이징 파일만 처리")
    parser.add_argument("--rebuild",     action="store_true", help="전체 재구축")
    args = parser.parse_args()

    archive_root = Path(args.archive)
    if not archive_root.exists():
        sys.exit(f"아카이브 없음: {archive_root}")

    conn = get_conn(archive_root)
    init_schema(conn)

    if args.rebuild:
        count = rebuild_from_archive(conn, archive_root)
        log.info("재구축 완료: %d개", count)
    else:
        count = process_staging(conn, archive_root)
        log.info("인덱싱 완료: %d개", count)

    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    log.info("DB 총 메시지: %d개", total)
    conn.close()


if __name__ == "__main__":
    main()
