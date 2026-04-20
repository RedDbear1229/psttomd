#!/usr/bin/env python3
"""
Obsidian 위키 강화 스크립트

아카이브 DB 를 기반으로 다음 MOC(Map of Content) 를 자동 생성한다:

  people/<email>.md   — 인물별 관련 스레드 타임라인
  threads/<id>.md     — 스레드 참여자 목록과 메시지 타임라인
  projects/<tag>.md   — 태그/정규식 규칙 기반 프로젝트 페이지

생성된 페이지는 Obsidian Wikilink 와 Dataview 쿼리를 포함해
Graph View 와 Dataview 플러그인과 연동된다.

사용법:
  python enrich.py [--archive ~/mail-archive]
  python enrich.py --people              # people 만 갱신
  python enrich.py --threads --projects  # threads + projects 갱신
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config

#: Obsidian Dataview 쿼리 템플릿 — 인물 페이지에 삽입
_DATAVIEW_PERSON = """\
```dataview
TABLE date, subject, thread
FROM "{folder}"
WHERE contains(from, this.file.name) OR contains(to, this.file.name)
SORT date DESC
LIMIT 50
```
"""

#: Obsidian Dataview 쿼리 템플릿 — 미답장 메일 목록
_DATAVIEW_UNANSWERED = """\
```dataview
TABLE date, subject, from
FROM "archive"
WHERE !contains(file.tags, "replied")
SORT date DESC
LIMIT 30
```
"""


# ---------------------------------------------------------------------------
# DB 연결
# ---------------------------------------------------------------------------

def get_conn(archive_root: Path) -> sqlite3.Connection:
    """인덱스 DB 에 연결한다.

    DB 가 없으면 설치 안내를 출력하고 종료한다.

    Args:
        archive_root: 아카이브 루트 디렉터리.

    Returns:
        활성 sqlite3.Connection.

    Raises:
        SystemExit: DB 파일이 없는 경우.
    """
    db_path = archive_root / "index.sqlite"
    if not db_path.exists():
        sys.exit(
            f"인덱스 없음: {db_path}\n"
            f"먼저: python build_index.py --archive {archive_root}"
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# people MOC
# ---------------------------------------------------------------------------

def build_people(conn: sqlite3.Connection, archive_root: Path) -> None:
    """발신자별 people/<email>.md 를 생성 또는 갱신한다.

    각 파일에는 인물 정보(이메일, 메일 수, 기간)와
    관련 스레드 목록, Dataview 쿼리가 포함된다.

    Args:
        conn:         활성 SQLite 연결.
        archive_root: people 디렉터리를 생성할 아카이브 루트.
    """
    people_dir = archive_root / "people"
    people_dir.mkdir(exist_ok=True)

    rows = conn.execute("""
        SELECT from_addr,
               from_name,
               COUNT(*)                    AS cnt,
               MIN(substr(date,1,10))      AS first_date,
               MAX(substr(date,1,10))      AS last_date
        FROM messages
        WHERE from_addr != ''
        GROUP BY from_addr
        ORDER BY cnt DESC
    """).fetchall()

    print(f"[people] {len(rows)}명 생성 중...")
    for row in rows:
        addr  = row["from_addr"].strip()
        name  = row["from_name"].strip() or addr
        cnt   = row["cnt"]
        first = row["first_date"] or ""
        last  = row["last_date"] or ""

        # 이 인물이 발신자 또는 수신자로 포함된 스레드 최대 100개
        thread_rows = conn.execute("""
            SELECT DISTINCT thread,
                   MIN(date)    AS tdate,
                   COUNT(*)     AS tmsg,
                   MAX(subject) AS subj
            FROM messages
            WHERE from_addr = ? OR to_addrs LIKE ? OR cc_addrs LIKE ?
            GROUP BY thread
            ORDER BY tdate DESC
            LIMIT 100
        """, (addr, f"%{addr}%", f"%{addr}%")).fetchall()

        thread_lines = [
            f"- [[{tr['thread']}]] `{(tr['tdate'] or '')[:10]}` "
            f"{(tr['subj'] or '')[:60]} ({tr['tmsg']}통)"
            for tr in thread_rows
        ]
        threads_section = "\n".join(thread_lines) if thread_lines else "_스레드 없음_"

        # Obsidian 파일명에 허용되지 않는 문자 치환
        safe_name = re.sub(r'[<>:"/\\|?*]', "_", addr)
        md_path = people_dir / f"{safe_name}.md"

        content = (
            f'---\n'
            f'type: person\n'
            f'email: "{addr}"\n'
            f'name: "{name}"\n'
            f'mail_count: {cnt}\n'
            f'first_mail: "{first}"\n'
            f'last_mail: "{last}"\n'
            f'---\n\n'
            f'# {name}\n\n'
            f'- 이메일: `{addr}`\n'
            f'- 총 메일: {cnt}통\n'
            f'- 기간: {first} ~ {last}\n\n'
            f'## 스레드 목록\n\n'
            f'{threads_section}\n\n'
            f'## Dataview\n\n'
            f'{_DATAVIEW_PERSON.format(folder="archive")}\n'
        )
        md_path.write_text(content, encoding="utf-8")

    print(f"[people] 완료: {people_dir}")


# ---------------------------------------------------------------------------
# threads MOC
# ---------------------------------------------------------------------------

def build_threads(conn: sqlite3.Connection, archive_root: Path) -> None:
    """스레드별 threads/<thread_id>.md 를 생성 또는 갱신한다.

    2통 이상의 메시지가 있는 스레드만 생성한다.
    각 파일에는 참여자 Wikilink 와 메시지 타임라인이 포함된다.

    Args:
        conn:         활성 SQLite 연결.
        archive_root: threads 디렉터리를 생성할 아카이브 루트.
    """
    threads_dir = archive_root / "threads"
    threads_dir.mkdir(exist_ok=True)

    thread_ids = conn.execute("""
        SELECT thread, COUNT(*) AS cnt
        FROM messages
        GROUP BY thread
        HAVING cnt > 1
        ORDER BY cnt DESC
    """).fetchall()

    print(f"[threads] {len(thread_ids)}개 스레드 생성 중...")
    for t in thread_ids:
        thread_id = t["thread"]
        msgs = conn.execute("""
            SELECT date, from_name, from_addr, subject, path, to_addrs
            FROM messages
            WHERE thread = ?
            ORDER BY date ASC
        """, (thread_id,)).fetchall()

        if not msgs:
            continue

        # 참여자 dict: {email: name} — 발신자 + 수신자 합산
        participants: dict[str, str] = {}
        for m in msgs:
            addr = m["from_addr"] or ""
            name = m["from_name"] or addr
            if addr:
                participants[addr] = name
            try:
                to_list = json.loads(m["to_addrs"] or "[]")
                for a in to_list:
                    if a not in participants:
                        participants[a] = a
            except (json.JSONDecodeError, ValueError):
                pass

        first_msg = msgs[0]
        last_msg  = msgs[-1]
        subject    = first_msg["subject"] or "(제목 없음)"
        start_date = (first_msg["date"] or "")[:10]
        end_date   = (last_msg["date"] or "")[:10]

        # 참여자 Wikilink (최대 8명)
        participant_links = " · ".join(
            f"[[{addr}|{name}]]"
            for addr, name in list(participants.items())[:8]
        )

        # 메시지 타임라인 — 상대 경로로 링크
        msg_lines = []
        for m in msgs:
            date   = (m["date"] or "")[:10]
            sender = m["from_name"] or m["from_addr"] or "?"
            subj   = (m["subject"] or "")[:50]
            path   = m["path"] or ""
            rel    = os.path.relpath(path, str(archive_root)) if path else ""
            msg_lines.append(f"- `{date}` **{sender}** — {subj}  \n  `{rel}`")

        timeline = "\n".join(msg_lines)

        md_path = threads_dir / f"{thread_id}.md"
        content = (
            f'---\n'
            f'type: thread\n'
            f'thread_id: "{thread_id}"\n'
            f'subject: "{subject.replace(chr(34), chr(39))}"\n'
            f'start_date: "{start_date}"\n'
            f'end_date: "{end_date}"\n'
            f'message_count: {len(msgs)}\n'
            f'participant_count: {len(participants)}\n'
            f'---\n\n'
            f'# {subject}\n\n'
            f'- 기간: {start_date} ~ {end_date}\n'
            f'- 메시지: {len(msgs)}통\n'
            f'- 참여자: {participant_links}\n\n'
            f'## 타임라인\n\n'
            f'{timeline}\n'
        )
        md_path.write_text(content, encoding="utf-8")

    print(f"[threads] 완료: {threads_dir}")


# ---------------------------------------------------------------------------
# projects MOC
# ---------------------------------------------------------------------------

#: 기본 프로젝트 분류 규칙: (태그명, 제목/폴더 정규식)
DEFAULT_RULES: list[tuple[str, str]] = [
    ("계약",   r"계약|contract"),
    ("견적",   r"견적|견적서|quote"),
    ("인사",   r"채용|입사|퇴사|휴가|인사|HR"),
    ("재무",   r"세금|세금계산서|invoice|정산|지출"),
    ("회의",   r"회의|미팅|meeting|agenda"),
]


def build_projects(
    conn: sqlite3.Connection,
    archive_root: Path,
    rules: list[tuple[str, str]] | None = None,
) -> None:
    """정규식 규칙 기반 projects/<tag>.md 를 생성 또는 갱신한다.

    각 규칙은 (태그명, 정규식) 튜플이며, 제목과 폴더 경로에 대해 매칭한다.
    매칭 결과가 없는 태그는 파일을 생성하지 않는다.

    Args:
        conn:         활성 SQLite 연결.
        archive_root: projects 디렉터리를 생성할 아카이브 루트.
        rules:        분류 규칙 리스트. None 이면 DEFAULT_RULES 사용.
    """
    projects_dir = archive_root / "projects"
    projects_dir.mkdir(exist_ok=True)

    if rules is None:
        rules = DEFAULT_RULES

    print(f"[projects] {len(rules)}개 규칙 적용 중...")

    # 전체 메시지를 한 번 로드 (규칙 수 × 쿼리 대신 메모리에서 필터링)
    all_messages = conn.execute("""
        SELECT msgid, date, from_name, from_addr, subject, thread, path, folder
        FROM messages
        ORDER BY date DESC
    """).fetchall()

    for tag, pattern in rules:
        regex = re.compile(pattern, re.IGNORECASE)

        hits = [
            m for m in all_messages
            if regex.search(m["subject"] or "") or regex.search(m["folder"] or "")
        ]

        if not hits:
            continue

        # 스레드 dedup: 각 스레드의 첫 번째 매칭 메시지를 대표로 보관
        threads_seen: dict[str, object] = {}
        for h in hits:
            t = h["thread"]
            if t not in threads_seen:
                threads_seen[t] = h

        msg_lines = [
            f"- `{(h['date'] or '')[:10]}` [[{h['thread'] or ''}]] "
            f"**{h['from_name'] or h['from_addr'] or '?'}** "
            f"{(h['subject'] or '')[:55]}"
            for h in hits[:200]
        ]

        thread_links = "\n".join(
            f"- [[{t}]]" for t in list(threads_seen.keys())[:50]
        )

        safe_tag = re.sub(r'[<>:"/\\|?*\s]', "_", tag)
        md_path = projects_dir / f"{safe_tag}.md"
        content = (
            f'---\n'
            f'type: project\n'
            f'tag: "{tag}"\n'
            f'pattern: "{pattern}"\n'
            f'match_count: {len(hits)}\n'
            f'thread_count: {len(threads_seen)}\n'
            f'---\n\n'
            f'# 프로젝트: {tag}\n\n'
            f'- 검색 패턴: `{pattern}`\n'
            f'- 매칭 메일: {len(hits)}통 / 스레드: {len(threads_seen)}개\n\n'
            f'## 관련 스레드\n\n'
            f'{thread_links}\n\n'
            f'## 최근 메일\n\n'
            + "\n".join(msg_lines[:50]) + "\n"
        )
        md_path.write_text(content, encoding="utf-8")

    print(f"[projects] 완료: {projects_dir}")


# ---------------------------------------------------------------------------
# Obsidian 설정 초기화
# ---------------------------------------------------------------------------

def write_obsidian_config(archive_root: Path) -> None:
    """Obsidian vault 최소 설정과 플러그인 안내 문서를 생성한다.

    .obsidian/app.json 이 이미 존재하면 덮어쓰지 않는다.
    docs/obsidian-setup.md 는 항상 최신 내용으로 덮어쓴다.

    Args:
        archive_root: Obsidian vault 루트 (= 아카이브 루트).
    """
    obsidian_dir = archive_root / ".obsidian"
    obsidian_dir.mkdir(exist_ok=True)

    # 플러그인 설치 안내 문서 (항상 최신 내용으로 갱신)
    plugins_note = archive_root / "docs" / "obsidian-setup.md"
    plugins_note.parent.mkdir(exist_ok=True)
    plugins_note.write_text("""\
# Obsidian 설정 가이드

## 권장 플러그인 (Community Plugins)

1. **Dataview** — `blacksmithgu/obsidian-dataview`
   - 메일 쿼리, 통계 대시보드에 사용
2. **Omnisearch** — `scambier/obsidian-omnisearch`
   - vault 내 전문검색 (SQLite FTS5 대체 가능)
3. **Graph Analysis** — `SkepticMystic/graph-analysis`
   - 인물·프로젝트 중심성 분석
4. **Templater** — `SilentVoid13/Templater`
   - 새 메일 수동 입력 템플릿

## Vault 열기

Windows Obsidian에서:
1. "Open folder as vault" 선택
2. 경로 입력: `\\\\wsl$\\Ubuntu\\home\\<사용자명>\\mail-archive`

## 유용한 Dataview 쿼리

### 최근 30일 수신

```dataview
TABLE date, from, subject
FROM "archive"
WHERE date >= date(today) - dur(30 days)
SORT date DESC
```

### 첨부가 있는 메일

```dataview
TABLE date, from, subject
FROM "archive"
WHERE attachments != []
SORT date DESC
LIMIT 50
```

### 미답장 메일 (태그 기반)

```dataview
TABLE date, from, subject
FROM "archive"
WHERE !contains(tags, "replied") AND date >= date(today) - dur(90 days)
SORT date DESC
```
""", encoding="utf-8")

    # 기본 app.json — 이미 존재하면 사용자 설정 유지
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        app_json.write_text(json.dumps({
            "useMarkdownLinks": False,    # Wikilink 형식 사용
            "newLinkFormat": "shortest",  # 최단 경로 링크
            "promptDelete": True,         # 삭제 전 확인
            "showFrontmatter": True,      # frontmatter 표시
        }, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """명령행 인자를 파싱하고 MOC 생성을 실행한다."""
    _default_archive = load_config()["archive"]["root"]
    parser = argparse.ArgumentParser(
        prog="enrich",
        description=(
            "Obsidian MOC(Map of Content) 를 자동 생성한다. "
            "people/threads/projects 3종류의 인덱스 노트가 아카이브 루트에 생성된다."
        ),
        epilog=(
            "예시:\n"
            "  enrich                     # people + threads + projects 전부\n"
            "  enrich --people            # 발신자 MOC 만\n"
            "  enrich --threads           # 스레드 MOC 만\n"
            "  enrich --projects          # 프로젝트 MOC 만\n"
            "\n"
            "LLM 기반 요약·태그가 필요하면 `mailenrich` 를 사용한다.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--archive",  default=_default_archive, metavar="DIR",
                        help="아카이브 루트 (기본: config archive.root).")
    parser.add_argument("--people",   action="store_true",
                        help="people MOC 만 생성 (발신자별 인덱스).")
    parser.add_argument("--threads",  action="store_true",
                        help="threads MOC 만 생성 (스레드별 인덱스).")
    parser.add_argument("--projects", action="store_true",
                        help="projects MOC 만 생성 (프로젝트별 인덱스).")
    args = parser.parse_args()

    archive_root = Path(args.archive)
    # 아무 플래그도 지정하지 않으면 전체 실행
    run_all = not (args.people or args.threads or args.projects)

    conn = get_conn(archive_root)
    write_obsidian_config(archive_root)

    if run_all or args.people:
        build_people(conn, archive_root)
    if run_all or args.threads:
        build_threads(conn, archive_root)
    if run_all or args.projects:
        build_projects(conn, archive_root)

    conn.close()
    print("\n완료. Obsidian에서 vault를 새로고침(Ctrl+R)하세요.")


if __name__ == "__main__":
    main()
