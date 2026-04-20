#!/usr/bin/env python3
"""
mailstat — 메일 아카이브 통계 (크로스플랫폼 Python CLI)

SQLite 인덱스와 파일시스템을 조합해 아카이브 현황을 요약하는
다양한 서브커맨드를 제공한다.

사용법:
  mailstat summary              # 전체 요약
  mailstat monthly              # 월별 메일 수
  mailstat senders --top 20     # 상위 발신자
  mailstat folders              # 폴더별 통계
  mailstat threads --top 20     # 긴 스레드
  mailstat attachments          # 첨부 파일 용량
  mailstat range                # 날짜 범위 확인
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, db_path, archive_root


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def _human_size(size_bytes: int) -> str:
    """바이트를 사람이 읽기 쉬운 크기 문자열로 변환한다 (du -sh 대체).

    Args:
        size_bytes: 바이트 단위 크기.

    Returns:
        "1.2 GB", "345.0 MB" 등의 문자열.
    """
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024  # type: ignore[assignment]
    return f"{size_bytes:.1f} PB"


def _dir_size(path: Path) -> tuple[int, int]:
    """디렉터리 전체의 (총 바이트, 파일 수) 를 반환한다.

    du -sb 대체 함수. 심볼릭 링크는 따라가지 않는다.

    Args:
        path: 계산할 디렉터리 경로.

    Returns:
        (total_bytes, file_count) 튜플.
    """
    total_bytes = 0
    count = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total_bytes += f.stat().st_size
                count += 1
            except OSError:
                pass   # 파일 삭제 등 경쟁 조건 무시
    return total_bytes, count


def get_conn(cfg: dict) -> sqlite3.Connection:
    """인덱스 DB 에 연결한다.

    DB 파일이 없으면 설치 안내를 출력하고 종료한다.

    Args:
        cfg: load_config() 결과 dict.

    Returns:
        활성 sqlite3.Connection.

    Raises:
        SystemExit: DB 파일이 존재하지 않는 경우.
    """
    db = db_path(cfg)
    if not db.exists():
        click.echo(f"오류: 인덱스 없음 → {db}", err=True)
        click.echo("먼저 실행: python build_index.py", err=True)
        sys.exit(1)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    return conn


def print_table(
    headers: list[str],
    rows,
    col_widths: list[int] | None = None,
) -> None:
    """헤더와 행 데이터를 고정 폭 테이블로 출력한다.

    col_widths 가 None 이면 각 열의 최대 내용 길이를 자동으로 계산한다.

    Args:
        headers:    열 헤더 문자열 리스트.
        rows:       (tuple 또는 Row) 데이터 행 iterable.
        col_widths: 각 열 폭 리스트. None 이면 자동 계산.
    """
    rows = list(rows)   # reiterable 보장
    if not rows:
        click.echo("데이터 없음.")
        return
    if col_widths is None:
        col_widths = [
            max(len(str(h)), max(len(str(r[i])) for r in rows))
            for i, h in enumerate(headers)
        ]
    click.echo("  ".join(str(h).ljust(w) for h, w in zip(headers, col_widths)))
    click.echo("  ".join("-" * w for w in col_widths))
    for row in rows:
        click.echo("  ".join(str(v).ljust(w) for v, w in zip(row, col_widths)))


# ---------------------------------------------------------------------------
# CLI 그룹
# ---------------------------------------------------------------------------

_MAILSTAT_EPILOG = (
    "\b\n"
    "서브커맨드:\n"
    "  summary       전체 요약 (메일 수 · 발신자 · 기간 · 디스크)\n"
    "  monthly       월별 메일 수 (최근 36개월)\n"
    "  senders       상위 발신자 Top N\n"
    "  folders       폴더별 메일 수 Top 30\n"
    "  threads       긴 스레드 Top N\n"
    "  attachments   첨부 파일 용량 통계\n"
    "  range         아카이브 날짜 범위\n"
    "\n"
    "\b\n"
    "예시:\n"
    "  mailstat summary\n"
    "  mailstat senders --top 20\n"
    "  mailstat threads --top 10\n"
    "  mailstat --archive ~/work-archive monthly"
)


@click.group(
    name="mailstat",
    epilog=_MAILSTAT_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--archive", default="", metavar="DIR",
              help="아카이브 루트 (기본: config archive.root).")
@click.pass_context
def main(ctx, archive):
    """메일 아카이브 통계 대시보드.

    SQLite 인덱스와 파일시스템을 조합해 요약·월별·발신자·폴더·스레드·
    첨부·날짜범위 통계를 출력한다. 서브커맨드별로 결과가 다르다.
    """
    ctx.ensure_object(dict)
    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    ctx.obj["cfg"] = cfg


# ---------------------------------------------------------------------------
# summary 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def summary(ctx):
    """전체 아카이브 요약 (메일 수, 발신자, 기간, 디스크 사용량)."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    row = conn.execute("""
        SELECT
            COUNT(*)                       AS total,
            COUNT(DISTINCT from_addr)      AS senders,
            COUNT(DISTINCT thread)         AS threads,
            COUNT(DISTINCT source_pst)     AS pst_files,
            MIN(substr(date,1,10))         AS oldest,
            MAX(substr(date,1,10))         AS newest
        FROM messages
    """).fetchone()
    conn.close()

    click.echo("\n=== 아카이브 요약 ===")
    click.echo(f"  총 메일:      {row['total']:,}통")
    click.echo(f"  고유 발신자:  {row['senders']:,}명")
    click.echo(f"  스레드 수:    {row['threads']:,}개")
    click.echo(f"  PST 파일:     {row['pst_files']:,}개")
    click.echo(f"  기간:         {row['oldest']} ~ {row['newest']}")

    root = archive_root(cfg)
    for subdir in ("archive", "attachments", "attachments_large"):
        d = root / subdir
        if d.exists():
            size, count = _dir_size(d)
            click.echo(f"  {subdir:<18}: {_human_size(size)} ({count:,}파일)")
    click.echo()


# ---------------------------------------------------------------------------
# monthly 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def monthly(ctx):
    """월별 메일 수 (최근 36개월)."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    rows = conn.execute("""
        SELECT substr(date,1,7) AS month, COUNT(*) AS count
        FROM messages WHERE date != ''
        GROUP BY 1 ORDER BY 1 DESC LIMIT 36
    """).fetchall()
    conn.close()

    click.echo("\n=== 월별 메일 수 ===")
    print_table(["월", "메일수"], [(r["month"], r["count"]) for r in rows], [10, 8])
    click.echo()


# ---------------------------------------------------------------------------
# senders 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.option("--top", default=20, show_default=True, metavar="N",
              help="상위 N명 (기본: 20).")
@click.pass_context
def senders(ctx, top):
    """발신자별 메일 수 상위 N명."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    rows = conn.execute("""
        SELECT from_addr,
               MAX(from_name) AS name,
               COUNT(*)        AS cnt,
               MAX(substr(date,1,10)) AS last
        FROM messages WHERE from_addr != ''
        GROUP BY from_addr ORDER BY cnt DESC LIMIT ?
    """, (top,)).fetchall()
    conn.close()

    click.echo(f"\n=== 상위 발신자 (Top {top}) ===")
    print_table(
        ["발신자", "이름", "메일수", "마지막"],
        [(r["from_addr"], (r["name"] or "")[:20], r["cnt"], r["last"]) for r in rows],
        [32, 22, 7, 12],
    )
    click.echo()


# ---------------------------------------------------------------------------
# folders 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def folders(ctx):
    """폴더별 메일 수 상위 30개."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    rows = conn.execute("""
        SELECT folder, COUNT(*) AS cnt
        FROM messages GROUP BY folder ORDER BY cnt DESC LIMIT 30
    """).fetchall()
    conn.close()

    click.echo("\n=== 폴더별 통계 ===")
    print_table(["폴더", "메일수"], [(r["folder"], r["cnt"]) for r in rows], [50, 7])
    click.echo()


# ---------------------------------------------------------------------------
# threads 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.option("--top", default=20, show_default=True, metavar="N",
              help="상위 N개 (기본: 20).")
@click.pass_context
def threads(ctx, top):
    """메시지 수 기준 긴 스레드 Top N."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    rows = conn.execute("""
        SELECT thread,
               COUNT(*)                    AS cnt,
               MIN(substr(date,1,10))      AS start,
               MAX(substr(date,1,10))      AS end,
               COUNT(DISTINCT from_addr)   AS participants
        FROM messages GROUP BY thread ORDER BY cnt DESC LIMIT ?
    """, (top,)).fetchall()
    conn.close()

    click.echo(f"\n=== 긴 스레드 Top {top} ===")
    print_table(
        ["스레드ID", "메일수", "시작", "종료", "참여자"],
        [(r["thread"], r["cnt"], r["start"], r["end"], r["participants"]) for r in rows],
        [14, 7, 12, 12, 7],
    )
    click.echo()


# ---------------------------------------------------------------------------
# attachments 서브커맨드
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def attachments(ctx):
    """첨부 파일 디렉터리별 용량 통계."""
    cfg = ctx.obj["cfg"]
    root = archive_root(cfg)

    click.echo("\n=== 첨부 파일 통계 ===")
    for subdir in ("attachments", "attachments_large"):
        d = root / subdir
        if d.exists():
            size, count = _dir_size(d)
            label = "일반 첨부" if subdir == "attachments" else "대용량 첨부 (>50MB)"
            click.echo(f"  {label}: {_human_size(size)} ({count:,}파일)")
        else:
            click.echo(f"  {subdir}: 없음")
    click.echo()


# ---------------------------------------------------------------------------
# range 서브커맨드
# ---------------------------------------------------------------------------

@main.command(name="range")
@click.pass_context
def date_range(ctx):
    """아카이브의 날짜 범위와 총 메일 수를 확인한다."""
    cfg = ctx.obj["cfg"]
    conn = get_conn(cfg)
    row = conn.execute("""
        SELECT MIN(substr(date,1,10)) AS start,
               MAX(substr(date,1,10)) AS end,
               COUNT(*)               AS total
        FROM messages WHERE date != ''
    """).fetchone()
    conn.close()

    click.echo("\n=== 날짜 범위 ===")
    click.echo(f"  시작: {row['start']}")
    click.echo(f"  종료: {row['end']}")
    click.echo(f"  총:   {row['total']:,}통")
    click.echo()


if __name__ == "__main__":
    main()
