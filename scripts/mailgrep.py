#!/usr/bin/env python3
"""
mailgrep — SQLite FTS5 기반 메일 아카이브 검색 (크로스플랫폼 Python CLI)

인덱스(index.sqlite)에 대해 전문 검색(FTS5)과 메타 필터를 조합해
조건에 맞는 메일을 빠르게 찾는다.

사용법:
  mailgrep <키워드> [옵션]
  mailgrep "견적서" --from 홍길동 --after 2023-01-01
  mailgrep "계약" --folder Inbox/Project --limit 20 --json

  # Windows (pip install 전)
  python mailgrep.py "견적서" --from 홍길동
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, db_path, archive_roots


# ---------------------------------------------------------------------------
# FTS5 쿼리 이스케이프
# ---------------------------------------------------------------------------

def _escape_fts5(query: str) -> str:
    """FTS5 쿼리 문자열을 이스케이프한다.

    큰따옴표를 이중 따옴표로 이스케이프하고,
    FTS5 연산자(*, (), ^)가 포함된 경우 구문 검색 따옴표로 감싼다.
    AND / OR / NOT 대문자 연산자는 그대로 허용한다.

    Args:
        query: 사용자 입력 검색어.

    Returns:
        FTS5 MATCH 절에 사용할 이스케이프된 문자열.
    """
    query = query.strip()
    if not query:
        return ""
    # " → "" (FTS5 리터럴 이스케이프)
    query = query.replace('"', '""')
    # 특수 연산자 문자가 있으면 구문 검색으로 처리
    if re.search(r'[*()\^]', query):
        return f'"{query}"'
    return query


# ---------------------------------------------------------------------------
# 스마트 쿼리 파서 (Feature 14)
# ---------------------------------------------------------------------------

def _expand_month(value: str) -> str:
    """'YYYY-MM' 형식 연월을 'YYYY-MM-01' 로 확장한다.

    'YYYY-MM-DD' 는 그대로 반환한다. 형식이 다르면 빈 문자열을 반환한다.

    Args:
        value: 날짜 문자열 ('YYYY-MM' 또는 'YYYY-MM-DD').

    Returns:
        'YYYY-MM-DD' 형식 날짜 문자열 또는 빈 문자열.
    """
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    if re.fullmatch(r"\d{4}-\d{2}", value):
        return f"{value}-01"
    return ""


_SMART_PREFIX_RE = re.compile(
    r"""(?x)
    (from|to|after|before|folder|subject|has)  # 지원하는 prefix
    :
    (\S+)                                       # 값 (공백 없이)
    """,
    re.IGNORECASE,
)


def parse_smart_query(raw: str) -> dict:
    """'key:value' 형식의 인라인 필터를 파싱해 검색 인수 dict 를 반환한다.

    지원 prefix:
      from:발신자     → from_filter
      to:수신자       → to_filter
      after:날짜      → after (YYYY-MM or YYYY-MM-DD)
      before:날짜     → before (YYYY-MM or YYYY-MM-DD)
      folder:폴더     → folder
      subject:키워드  → subject_query (FTS5 subject: 컬럼 한정)
      has:attachment  → has_attachment = True

    나머지 단어는 'query' 키에 공백으로 합친다.

    Args:
        raw: 사용자가 입력한 전체 검색 문자열.

    Returns:
        {
          "query":        str   — 나머지 FTS5 키워드,
          "from_filter":  str,
          "to_filter":    str,
          "after":        str,
          "before":       str,
          "folder":       str,
          "subject_query": str,
          "has_attachment": bool,
        }
    """
    result: dict = {
        "query":          "",
        "from_filter":    "",
        "to_filter":      "",
        "after":          "",
        "before":         "",
        "folder":         "",
        "subject_query":  "",
        "has_attachment": False,
    }
    remaining: list[str] = []

    for token in raw.split():
        m = _SMART_PREFIX_RE.fullmatch(token)
        if m:
            key, value = m.group(1).lower(), m.group(2)
            if key == "from":
                result["from_filter"] = value
            elif key == "to":
                result["to_filter"] = value
            elif key == "after":
                result["after"] = _expand_month(value)
            elif key == "before":
                result["before"] = _expand_month(value)
            elif key == "folder":
                result["folder"] = value
            elif key == "subject":
                result["subject_query"] = value
            elif key == "has" and value.lower() == "attachment":
                result["has_attachment"] = True
        else:
            remaining.append(token)

    result["query"] = " ".join(remaining)
    return result


# ---------------------------------------------------------------------------
# SQL 쿼리 빌더
# ---------------------------------------------------------------------------

def build_query(
    conn: sqlite3.Connection,
    query: str,
    from_filter: str,
    to_filter: str,
    after: str,
    before: str,
    folder: str,
    thread: str,
    limit: int,
    output_json: bool,
    paths_only: bool,
    body_filter: str = "",
    subject_query: str = "",
    has_attachment: bool = False,
) -> tuple[str, list]:
    """검색 조건에 맞는 SQL 쿼리와 바인딩 파라미터를 생성한다.

    FTS5 전문 검색과 메타 필드 필터(from / to / 날짜 / 폴더)를
    AND 조건으로 조합한다.

    FTS5 컬럼 한정 문법:
      query       → 모든 컬럼(subject, from_name, from_addr, to_addrs, body) 검색
      body_filter → body 컬럼만 검색 (``body:term``)
      둘 다 있으면 AND 로 조합 (``term body:bodyterm``)

    Args:
        conn:        활성 SQLite 연결 (현재는 사용하지 않지만 확장을 위해 유지).
        query:       전문 검색어. 빈 문자열이면 FTS5 전체 컬럼 검색을 건너뜀.
        from_filter: 발신자 부분 일치 필터.
        to_filter:   수신자 부분 일치 필터.
        after:       "YYYY-MM-DD" 이후 날짜 필터.
        before:      "YYYY-MM-DD" 이전 날짜 필터.
        folder:      폴더 경로 부분 일치 필터.
        thread:      스레드 ID 정확 일치 필터.
        limit:       최대 결과 수.
        output_json: True 이면 JSON 객체를 SELECT.
        paths_only:  True 이면 파일 경로만 SELECT.
        body_filter:    본문 전용 검색어. ``body:term`` 형식으로 FTS5 에 추가.
        subject_query:  제목 전용 검색어. ``subject:term`` 형식으로 FTS5 에 추가.
        has_attachment: True 이면 n_attachments > 0 조건을 추가.

    Returns:
        (sql_string, params_list) 튜플.
    """
    conditions: list[str] = []
    params: list = []

    # FTS5 MATCH 절 구성
    # query        → 전체 컬럼 검색 (term)
    # body_filter  → 본문 컬럼 한정 검색 (body:term)
    # subject_query→ 제목 컬럼 한정 검색 (subject:term)
    fts_parts: list[str] = []
    if query:
        fts_parts.append(_escape_fts5(query))
    if body_filter:
        fts_parts.append(f"body:{_escape_fts5(body_filter)}")
    if subject_query:
        fts_parts.append(f"subject:{_escape_fts5(subject_query)}")

    use_fts = bool(fts_parts)
    if use_fts:
        fts_q = " ".join(fts_parts)
        # FTS5 조인: messages_fts.rowid = messages.id
        conditions.append("fts.rowid = m.id AND messages_fts MATCH ?")
        params.append(fts_q)

    if from_filter:
        conditions.append("(m.from_name LIKE ? OR m.from_addr LIKE ?)")
        params += [f"%{from_filter}%", f"%{from_filter}%"]
    if to_filter:
        conditions.append("m.to_addrs LIKE ?")
        params.append(f"%{to_filter}%")
    if after:
        # ISO 8601 날짜 비교 — date 컬럼도 ISO 형식이므로 문자열 비교 가능
        conditions.append("m.date >= ?")
        params.append(f"{after}T00:00:00+00:00")
    if before:
        conditions.append("m.date <= ?")
        params.append(f"{before}T23:59:59+00:00")
    if folder:
        conditions.append("m.folder LIKE ?")
        params.append(f"%{folder}%")
    if thread:
        conditions.append("m.thread = ?")
        params.append(thread)
    if has_attachment:
        conditions.append("COALESCE(m.n_attachments, 0) > 0")

    from_clause = "FROM messages m" + (", messages_fts fts" if use_fts else "")
    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if output_json:
        select = """SELECT json_object(
            'date',      substr(m.date,1,10),
            'from',      m.from_name,
            'from_addr', m.from_addr,
            'subject',   m.subject,
            'folder',    m.folder,
            'thread',    m.thread,
            'path',      m.path
        )"""
    elif paths_only:
        select = "SELECT m.path"
    else:
        # 테이블 출력용: 날짜, 발신자, 제목, 파일 경로
        select = """SELECT
            substr(m.date,1,10),
            m.from_name || ' <' || m.from_addr || '>',
            m.subject,
            m.path"""

    sql = f"{select} {from_clause} {where_clause} ORDER BY m.date DESC LIMIT ?"
    params.append(limit)
    return sql, params


# ---------------------------------------------------------------------------
# CLI 커맨드
# ---------------------------------------------------------------------------

@click.command(
    name="mailgrep",
    epilog=(
        "예시:\n"
        "\n"
        "  mailgrep '계약서'                          제목·발신자·본문 전체 검색\n"
        "  mailgrep '계약' --from 홍길동              발신자 필터와 AND\n"
        "  mailgrep --body 'payment'                  본문 전용 검색\n"
        "  mailgrep 'invoice' --after 2023-01-01      날짜 범위\n"
        "  mailgrep '' --folder 'Inbox/계약'          폴더 필터만\n"
        "  mailgrep 'bug' --smart from:alice has:attachment\n"
        "  mailgrep 'TODO' --json                     JSON Lines 출력\n"
        "  mailgrep 'TODO' --paths-only | fzf         fzf 연동\n"
        "  mailgrep '키워드' --all-archives           설정된 모든 아카이브 검색\n"
    ),
)
@click.argument("query", default="")
@click.option("--from",      "from_filter", default="", metavar="NAME",
              help="발신자 필터 (부분 일치).")
@click.option("--to",        "to_filter",   default="", metavar="NAME",
              help="수신자 필터 (부분 일치).")
@click.option("--after",     default="", metavar="YYYY-MM-DD",
              help="이 날짜 이후 메일만 (ISO 형식).")
@click.option("--before",    default="", metavar="YYYY-MM-DD",
              help="이 날짜 이전 메일만 (ISO 형식).")
@click.option("--folder",    default="", metavar="PATH",
              help="폴더 경로 부분 일치 (예: 'Inbox/계약').")
@click.option("--thread",    default="", metavar="ID",
              help="스레드 ID 정확 일치 (예: t_abc123de).")
@click.option("--body",      "body_filter", default="", metavar="QUERY",
              help="본문 전용 검색 (FTS5 body 컬럼).")
@click.option("--limit",     default=50, show_default=True, metavar="N",
              help="최대 결과 수.")
@click.option("--json",      "output_json", is_flag=True,
              help="JSON Lines 포맷으로 출력 (파이프/자동화용).")
@click.option("--paths-only", is_flag=True,
              help="파일 경로만 출력 (fzf 파이프용).")
@click.option("--archive",   default="", metavar="DIR",
              help="아카이브 루트 (기본: config archive.root).")
@click.option("--smart",         is_flag=True,
              help="인라인 필터 파싱: from:/after:/subject:/has:attachment.")
@click.option("--all-archives",  is_flag=True,
              help="config 의 archive.roots 에 등록된 모든 아카이브 검색.")
def main(
    query, from_filter, to_filter, after, before, folder, thread,
    body_filter, limit, output_json, paths_only, archive, smart, all_archives,
):
    """SQLite FTS5 기반 메일 아카이브 검색.

    QUERY 는 제목·발신자·본문 전체를 검색한다. 본문만 검색하려면 --body 를 쓴다.

    \b
    스마트 쿼리(--smart):
      from:발신자  to:수신자  subject:키워드
      after:2023-01  before:2024-06-30
      has:attachment
      folder:'Inbox/계약'
    """
    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive

    db = db_path(cfg)
    if not db.exists():
        click.echo(f"오류: 인덱스 없음 → {db}", err=True)
        click.echo("먼저 실행: python build_index.py", err=True)
        sys.exit(1)

    # ── 스마트 쿼리 파싱 ────────────────────────────────────────────────
    subject_query = ""
    has_attachment = False
    if smart and query:
        parsed = parse_smart_query(query)
        query        = parsed["query"]
        from_filter  = from_filter  or parsed["from_filter"]
        to_filter    = to_filter    or parsed["to_filter"]
        after        = after        or parsed["after"]
        before       = before       or parsed["before"]
        folder       = folder       or parsed["folder"]
        subject_query = parsed["subject_query"]
        has_attachment = parsed["has_attachment"]

    # 검색 조건이 전혀 없으면 도움말 안내
    if (not query and not subject_query and not has_attachment and
            not any([from_filter, to_filter, after, before, folder, thread, body_filter])):
        click.echo("키워드 또는 필터를 지정하세요. --help 참고", err=True)
        sys.exit(1)

    # ── 검색 대상 DB 목록 결정 ──────────────────────────────────────────
    if all_archives:
        dbs = [
            r / "index.sqlite"
            for r in archive_roots(cfg)
            if (r / "index.sqlite").exists()
        ]
    else:
        if not db.exists():
            click.echo(f"오류: 인덱스 없음 → {db}", err=True)
            click.echo("먼저 실행: python build_index.py", err=True)
            sys.exit(1)
        dbs = [db]

    rows_all: list = []
    for target_db in dbs:
        conn = sqlite3.connect(str(target_db))
        conn.row_factory = sqlite3.Row
        sql, params = build_query(
            conn, query, from_filter, to_filter, after, before,
            folder, thread, limit, output_json, paths_only, body_filter,
            subject_query, has_attachment,
        )
        try:
            rows_all.extend(conn.execute(sql, params).fetchall())
        except sqlite3.OperationalError as e:
            click.echo(f"쿼리 오류 [{target_db}]: {e}", err=True)
        finally:
            conn.close()

    rows = rows_all[:limit]

    if not rows:
        if not paths_only and not output_json:
            click.echo("결과 없음.")
        return

    if output_json:
        # 한 줄씩 JSON 출력 (JSON Lines 형식)
        for row in rows:
            click.echo(row[0])
    elif paths_only:
        # 파일 경로만 출력 — mailview.py 파이프용
        for row in rows:
            if row[0]:
                click.echo(row[0])
    else:
        # 사람이 읽기 편한 테이블 형식
        click.echo()
        click.echo(f"{'날짜':<12} {'발신자':<32} {'제목':<50}")
        click.echo(f"{'-'*12} {'-'*32} {'-'*50}")
        for row in rows:
            date    = (row[0] or "")[:10]
            sender  = (row[1] or "")[:32]
            subject = (row[2] or "")[:50]
            path    = row[3] or ""
            click.echo(f"{date:<12} {sender:<32} {subject:<50}")
            click.echo(f"  → {path}")
        click.echo()


if __name__ == "__main__":
    main()
