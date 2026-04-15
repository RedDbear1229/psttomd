#!/usr/bin/env python3
"""
mailview — fzf + glow 인터랙티브 메일 뷰어 (크로스플랫폼)

사용법:
  mailview               # 최근 100통 목록
  mailview "견적서"       # 검색 후 fzf 선택
  mailview --thread t_abc123

키 바인딩 (fzf):
  Enter   → glow 로 전체 열람
  Ctrl-P  → bat/less 로 원문 표시
  Ctrl-O  → $EDITOR / notepad 로 열기
  Ctrl-A  → 첨부 파일 목록 표시 및 열기
  ESC     → 종료

첨부 파일 열기 (Ctrl-A):
  - 첨부가 1개  : 즉시 OS 기본 앱으로 열림
  - 첨부가 여러 개 : fzf 로 선택 후 열림
  - glow 에서는 첨부 파일명이 텍스트로만 표시되며, Ctrl-A 로 실제 파일을 열 수 있음

플랫폼별 파일 열기:
  Linux   : xdg-open
  WSL     : wslview (wslu) 또는 explorer.exe 경유
  Windows : os.startfile
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Optional

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, db_path, archive_root, detect_platform


# ---------------------------------------------------------------------------
# CJK 시각적 너비 헬퍼
# ---------------------------------------------------------------------------

def _visual_width(s: str) -> int:
    """문자열의 터미널 시각적 너비를 반환한다 (CJK 문자는 2칸).

    Args:
        s: 너비를 측정할 문자열.

    Returns:
        터미널 출력 시 차지하는 칸 수.
    """
    width = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        width += 2 if eaw in ("W", "F") else 1
    return width


def _visual_truncate(s: str, max_width: int) -> str:
    """시각적 너비 기준으로 문자열을 자른다 (CJK 인식).

    Args:
        s:         원본 문자열.
        max_width: 최대 시각적 너비.

    Returns:
        max_width 를 초과하지 않는 최대 길이의 문자열.
    """
    result: list[str] = []
    w = 0
    for ch in s:
        eaw = unicodedata.east_asian_width(ch)
        cw = 2 if eaw in ("W", "F") else 1
        if w + cw > max_width:
            break
        result.append(ch)
        w += cw
    return "".join(result)


def _visual_pad(s: str, width: int) -> str:
    """시각적 너비 기준으로 오른쪽에 공백을 채워 지정 너비로 맞춘다.

    Args:
        s:     원본 문자열.
        width: 목표 시각적 너비.

    Returns:
        오른쪽 공백으로 채워진 문자열.
    """
    current = _visual_width(s)
    if current < width:
        s += " " * (width - current)
    return s


# ---------------------------------------------------------------------------
# fzf 컬럼 헤더
# ---------------------------------------------------------------------------
# get_label() 포맷: f"{date}  {sender_padded}  {subject}"
#   date   = 10 visual cols  (YYYY-MM-DD)
#   sender = 10 visual cols  (한글 약 5자)
#   subject = 나머지
#
# 헤더 한글 시각적 너비:
#   '날짜'    = 4 visual → 10 맞추기 위해 6 spaces 추가
#   '보낸사람' = 8 visual → 10 맞추기 위해 2 spaces 추가
_FZF_COL_HEADER: str = (
    "날짜" + " " * 6           # 4 + 6 = 10 visual cols (date 열)
    + "  "                     # 2 cols gap
    + "보낸사람" + " " * 2     # 8 + 2 = 10 visual cols (sender 열)
    + "  "                     # 2 cols gap
    + "제목"
)

# 도움말 팝업에 표시할 키 바인딩 목록 (fzf --disabled 로 표시)
# television 스타일: 모든 바인딩을 한 곳에 모아 ? 키로만 접근
_HELP_LINES: list[str] = [
    "",
    "   키 바인딩 — mailview",
    "   " + "─" * 36,
    "   Enter      메일 열람 (glow 렌더링)",
    "   Ctrl-P     원문 표시 (bat / less)",
    "   Ctrl-O     $EDITOR 로 열기",
    "   Ctrl-A     첨부 파일 목록 열기",
    "   Ctrl-D     메일 삭제 (확인 후 삭제)",
    "   Ctrl-B     본문 검색 모드로 전환",
    "   Ctrl-R     전체 목록 초기화 (최근 100통)",
    "   ?          이 도움말 (ESC 로 닫기)",
    "   ESC        종료",
    "   " + "─" * 36,
    "   검색창에 텍스트 입력 후 Ctrl-B → 본문 키워드 검색",
    "",
]


# ---------------------------------------------------------------------------
# 도구 경로 확인
# ---------------------------------------------------------------------------

def _check_tool(name: str, cfg: dict) -> Optional[str]:
    """도구 실행 경로를 반환한다. 없으면 None."""
    cmd = cfg.get("tools", {}).get(name, name)
    return shutil.which(cmd)


def _require_tool(name: str, cfg: dict, install_hint: str = "") -> str:
    """도구가 없으면 설치 안내와 함께 종료한다."""
    path = _check_tool(name, cfg)
    if not path:
        msg = f"오류: '{name}' 가 설치되어 있지 않습니다."
        if install_hint:
            msg += f"\n  설치: {install_hint}"
        click.echo(msg, err=True)
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# 경로 목록 수집
# ---------------------------------------------------------------------------

def get_recent_paths(db: Path, limit: int = 100) -> list[str]:
    """DB 에서 최근 발송일 기준 Markdown 파일 경로 목록을 반환한다."""
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT path FROM messages ORDER BY date DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def get_paths_from_query(args: list[str]) -> list[str]:
    """mailgrep.py --paths-only 를 서브프로세스로 호출해 경로 목록을 반환한다."""
    script = Path(__file__).parent / "mailgrep.py"
    cmd = [sys.executable, str(script)] + args + ["--paths-only", "--limit", "500"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    return [p for p in result.stdout.splitlines() if p.strip()]


# ---------------------------------------------------------------------------
# fzf 레이블 생성
# ---------------------------------------------------------------------------

def get_label(path: str, db: Path) -> str:
    """DB 에서 파일 경로에 해당하는 ANSI 컬러 '날짜  발신자  제목' 레이블을 반환한다.

    표준 ANSI 16색 (dark 터미널 호환):
      날짜    — 청록 (ANSI 36 cyan)
      발신자  — 초록 (ANSI 32 green)
      제목    — 기본색

    발신자는 from_name 우선 (없으면 from_addr), 10 visual cols 으로 잘라 패딩.
    """
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            """SELECT substr(date,1,10),
                      from_name,
                      from_addr,
                      subject
               FROM messages WHERE path = ? LIMIT 1""",
            (path,),
        ).fetchone()
        conn.close()
        if row:
            date       = (row[0] or "")[:10]
            name       = row[1] or ""
            addr       = row[2] or ""
            subject    = (row[3] or "")[:80]
            sender_raw = name if name else addr
            sender     = _visual_pad(_visual_truncate(sender_raw, 10), 10)
            # 표준 ANSI 16색 — dark/light 터미널 모두 호환
            return (
                f"\033[36m{date}\033[0m"    # cyan
                f"  "
                f"\033[32m{sender}\033[0m"  # green
                f"  "
                f"{subject}"
            )
    except Exception:
        pass
    return path


# ---------------------------------------------------------------------------
# fzf reload 전용 출력기
# ---------------------------------------------------------------------------

def _print_fzf_lines(paths: list[str], db: Path) -> None:
    """fzf reload 명령이 사용할 ``레이블\\t경로`` 형식을 stdout 에 출력한다.

    첫 줄은 항상 컬럼 헤더(_FZF_COL_HEADER)를 출력한다.
    존재하지 않는 경로는 건너뛴다.

    Args:
        paths: Markdown 파일 경로 문자열 목록.
        db:    인덱스 SQLite 경로 (get_label 용).
    """
    print(f"{_FZF_COL_HEADER}\t", flush=True)
    for p in paths:
        if Path(p).exists():
            label = get_label(p, db)
            print(f"{label}\t{p}", flush=True)


# ---------------------------------------------------------------------------
# 플랫폼별 fzf/에디터 설정
# ---------------------------------------------------------------------------

def resolve_glow_style(cfg_style: str) -> str:
    """config 의 glow_style 값을 실제 사용할 스타일 문자열로 변환한다.

    우선순위:
      1. config 에 값이 있으면 그대로 사용 (내장 테마명 또는 절대 경로)
      2. 기본값 'dark'

    커스텀 테마 사용 예 (config.toml):
      glow_style = "dracula"
      glow_style = "/home/user/.config/glow/catppuccin-mocha.json"
      glow_style = "/path/to/psttomd/scripts/lib/mocha-glow.json"

    Args:
        cfg_style: config.toml 의 mailview.glow_style 값 (빈 문자열 가능).

    Returns:
        glow -s 에 전달할 테마명 또는 파일 경로 문자열.
    """
    return cfg_style if cfg_style else "dark"


def build_fzf_preview_cmd(
    glow_path: str,
    bat_path: Optional[str],
    glow_style: str = "",
) -> str:
    """플랫폼에 맞는 fzf --preview 명령어 문자열을 생성한다.

    fzf 입력 형식: "레이블\\t파일경로" — {2} 가 경로를 가리킨다.

    우선순위: glow(마크다운 렌더링·컬러) → bat(구문 강조) → type/cat(플레인)
    bat 은 Ctrl-P 원문 보기 전용으로 분리한다.

    glow 스타일 결정:
      glow_style 인자 → scripts/lib/mocha-glow.json 자동 탐지 → dark 폴백
      (resolve_glow_style() 위임)

    경로 인용부호 전략:
      - Linux  : {2} 를 작은따옴표로 감쌈 → 공백·특수문자 안전
      - Windows: {2} 를 큰따옴표로 감쌈  → cmd.exe 공백 처리

    Args:
        glow_path:  glow 실행 파일 절대 경로.
        bat_path:   bat 실행 파일 절대 경로 (없으면 None).
        glow_style: config 에서 전달된 스타일 값 (빈 문자열이면 자동 결정).

    Returns:
        fzf --preview 옵션에 전달할 명령어 문자열.
    """
    plat  = detect_platform()
    style = resolve_glow_style(glow_style)

    if plat == "windows":
        item          = '"{2}"'
        null_redirect = "2>nul"
        fallback = (
            f'"{bat_path}" --style=plain --color=always {item} {null_redirect}'
            if bat_path else f'type {item}'
        )
        return f'"{glow_path}" -s "{style}" {item} {null_redirect} || {fallback}'
    else:
        item          = "'{2}'"
        null_redirect = "2>/dev/null"
        fallback = (
            f"'{bat_path}' --style=plain --color=always {item} {null_redirect}"
            if bat_path else f"cat {item}"
        )
        return f"'{glow_path}' -s '{style}' {item} {null_redirect} || {fallback}"


def get_editor() -> str:
    """현재 환경의 텍스트 에디터 경로를 반환한다."""
    plat    = detect_platform()
    default = "notepad" if plat == "windows" else "nano"
    return os.environ.get("EDITOR", default)


# ---------------------------------------------------------------------------
# 첨부 파일 파싱
# ---------------------------------------------------------------------------

def get_attachments_from_md(md_path: str) -> list[dict]:
    """MD 파일의 YAML frontmatter 에서 첨부 파일 목록을 파싱한다.

    frontmatter 의 attachments 섹션에서 name 과 path 를 추출하고,
    config 에서 읽은 archive_root 기준으로 절대 경로를 계산한다.
    파일이 실제로 존재하는 항목만 반환한다.

    Args:
        md_path: 파싱할 Markdown 파일 경로 문자열.

    Returns:
        [{"name": "파일명", "abs_path": "/절대/경로"}] 형태의 리스트.
        첨부가 없거나 파싱 실패 시 빈 리스트.
    """
    try:
        text = Path(md_path).read_text(encoding="utf-8")
        if not text.startswith("---"):
            return []
        end = text.find("\n---\n", 3)
        if end == -1:
            return []
        fm = text[3:end]
    except OSError:
        return []

    # archive_root 는 config 에서 읽어 절대 경로 계산에 사용
    cfg  = load_config()
    root = archive_root(cfg)

    attachments: list[dict] = []
    in_att = False
    for line in fm.splitlines():
        stripped = line.strip()
        if stripped == "attachments:":
            in_att = True
            continue
        if in_att:
            if stripped == "attachments: []":
                break
            # 들여쓰기가 없으면 attachments 섹션 종료
            if line and not line.startswith(" "):
                break
            name_m = re.search(r'name:\s*"([^"]+)"', line)
            path_m = re.search(r'path:\s*"([^"]+)"', line)
            if name_m and path_m:
                abs_path = root / path_m.group(1)
                if abs_path.exists():
                    attachments.append({
                        "name":     name_m.group(1),
                        "abs_path": str(abs_path),
                    })

    return attachments


# ---------------------------------------------------------------------------
# 파일 열기 (플랫폼별)
# ---------------------------------------------------------------------------

def open_file(path: str, plat: str) -> None:
    """파일을 플랫폼 기본 애플리케이션으로 연다.

    플랫폼별 동작:
      Linux   : xdg-open (백그라운드 실행)
      WSL     : wslview (wslu 패키지) 우선 시도,
                없으면 wslpath + explorer.exe 경유
      Windows : os.startfile

    Args:
        path: 열 파일의 절대 경로 문자열.
        plat: detect_platform() 결과 ("linux" | "wsl" | "windows").
    """
    try:
        if plat == "windows":
            os.startfile(path)   # type: ignore[attr-defined]
        elif plat == "wsl":
            if shutil.which("wslview"):
                # wslu 패키지의 wslview: WSL 파일을 Windows 앱으로 직접 열기
                subprocess.Popen(
                    ["wslview", path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                # wslpath 로 Windows 경로 변환 후 explorer.exe 경유
                win_path = subprocess.run(
                    ["wslpath", "-w", path],
                    capture_output=True, text=True,
                ).stdout.strip()
                subprocess.Popen(
                    ["explorer.exe", win_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
        else:
            subprocess.Popen(
                ["xdg-open", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except Exception as e:
        click.echo(f"파일 열기 실패: {e}", err=True)


# ---------------------------------------------------------------------------
# 첨부 파일 열기 핸들러 (Ctrl-A 에서 호출)
# ---------------------------------------------------------------------------

def handle_open_attachments(md_path: str) -> None:
    """MD 파일의 첨부 파일 목록을 표시하고 선택한 파일을 연다.

    fzf 의 execute() 액션에서 호출된다.
    첨부가 1개면 즉시 열고, 여러 개면 중첩 fzf 로 선택한다.
    fzf 가 없으면 번호 입력 방식으로 fallback 한다.

    Args:
        md_path: 첨부 파일을 열 Markdown 파일 경로 문자열.
    """
    plat = detect_platform()
    attachments = get_attachments_from_md(md_path)

    if not attachments:
        click.echo("\n첨부 파일 없음.")
        return

    # 단일 첨부: 바로 열기
    if len(attachments) == 1:
        att = attachments[0]
        click.echo(f"\n열기: {att['name']}")
        open_file(att["abs_path"], plat)
        return

    # 복수 첨부: fzf 피커 또는 번호 선택
    fzf = shutil.which("fzf")
    if fzf:
        # 중첩 fzf: 외부 fzf 의 execute() 안에서 실행 가능
        input_lines = "\n".join(
            f"{att['name']}\t{att['abs_path']}" for att in attachments
        )
        result = subprocess.run(
            [
                fzf,
                "--delimiter", "\t",
                "--with-nth", "1",          # 파일명만 표시
                "--header", f"첨부 파일 {len(attachments)}개 — Enter:열기  ESC:취소",
                "--reverse",
            ],
            input=input_lines,
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode == 0:
            selected = result.stdout.strip()
            if "\t" in selected:
                path = selected.split("\t", 1)[1].strip()
                click.echo(f"열기: {Path(path).name}")
                open_file(path, plat)
    else:
        # fzf 없는 환경: 번호 선택
        click.echo(f"\n첨부 파일 {len(attachments)}개:")
        for i, att in enumerate(attachments, 1):
            size_kb = Path(att["abs_path"]).stat().st_size // 1024
            click.echo(f"  {i}. {att['name']}  ({size_kb:,} KB)")
        try:
            choice = int(input("\n번호 선택 (0=취소): "))
            if 1 <= choice <= len(attachments):
                open_file(attachments[choice - 1]["abs_path"], plat)
        except (ValueError, KeyboardInterrupt):
            pass


# ---------------------------------------------------------------------------
# 메일 삭제 핸들러 (Ctrl-D 에서 호출)
# ---------------------------------------------------------------------------

def _read_frontmatter_fields(md_path: str) -> dict[str, str]:
    """MD 파일에서 삭제 확인 표시에 필요한 frontmatter 필드를 읽는다.

    Args:
        md_path: Markdown 파일 경로 문자열.

    Returns:
        {"subject": ..., "from": ..., "date": ..., "msgid": ...} 딕셔너리.
        읽기 실패 시 빈 딕셔너리.
    """
    fields: dict[str, str] = {}
    try:
        text = Path(md_path).read_text(encoding="utf-8")
        if not text.startswith("---"):
            return fields
        end = text.find("\n---\n", 3)
        if end == -1:
            return fields
        for line in text[3:end].splitlines():
            for key in ("subject", "from", "date", "msgid"):
                if line.startswith(f"{key}:"):
                    fields[key] = line.partition(":")[2].strip().strip('"')
    except OSError:
        pass
    return fields


def handle_delete_message(md_path: str, archive: str = "") -> None:
    """선택한 메일의 MD 파일을 삭제하고 SQLite 인덱스에서 제거한다.

    fzf 의 execute() 액션에서 호출된다.
    삭제 전 메일 정보와 확인 프롬프트를 표시하고,
    첨부 파일이 있으면 함께 삭제할지 추가로 묻는다.

    FTS5 인덱스 정리:
      messages 행 삭제 전에 subject/from_name/from_addr/to_addrs 와
      MD 본문을 읽어 FTS5 'delete' 커맨드로 정확하게 제거한다.

    Args:
        md_path: 삭제할 Markdown 파일 경로 문자열.
        archive: 아카이브 루트 경로 (비어 있으면 config 에서 로드).
    """
    path = Path(md_path)
    if not path.exists():
        click.echo("파일 없음.")
        return

    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    db = db_path(cfg)

    # ── 메일 정보 표시 ────────────────────────────────────────────────────
    fm = _read_frontmatter_fields(md_path)
    click.echo("\n삭제할 메일")
    click.echo("  " + "─" * 40)
    click.echo(f"  날짜: {fm.get('date', '(없음)')}")
    click.echo(f"  발신: {fm.get('from', '(없음)')}")
    click.echo(f"  제목: {fm.get('subject', '(없음)')}")
    click.echo(f"  경로: {md_path}")

    # ── 첨부 파일 목록 ────────────────────────────────────────────────────
    attachments = get_attachments_from_md(md_path)
    if attachments:
        click.echo(f"\n  첨부 파일 {len(attachments)}개:")
        for att in attachments:
            size_kb = Path(att["abs_path"]).stat().st_size // 1024
            click.echo(f"    • {att['name']}  ({size_kb:,} KB)")

    # ── 삭제 확인 ────────────────────────────────────────────────────────
    click.echo("")
    try:
        answer = input("MD 파일을 삭제하시겠습니까? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        click.echo("\n취소.")
        return

    if answer != "y":
        click.echo("취소.")
        return

    # ── 첨부 파일 삭제 여부 확인 ─────────────────────────────────────────
    delete_attachments = False
    if attachments:
        try:
            att_answer = input(
                f"첨부 파일 {len(attachments)}개도 함께 삭제하시겠습니까? [y/N]: "
            ).strip().lower()
            delete_attachments = att_answer == "y"
        except (KeyboardInterrupt, EOFError):
            delete_attachments = False

    # ── FTS5 인덱스 정리 (파일 삭제 전에 본문 읽기) ──────────────────────
    fts_cleaned = False
    if db.exists():
        try:
            conn = sqlite3.connect(str(db))
            try:
                row = conn.execute(
                    "SELECT id, subject, from_name, from_addr, to_addrs "
                    "FROM messages WHERE path = ?",
                    (md_path,),
                ).fetchone()
                if row:
                    rowid, subj, fname, faddr, toaddrs = row
                    # MD 본문 읽기 (파일이 아직 존재하는 시점)
                    body = ""
                    try:
                        text = path.read_text(encoding="utf-8")
                        if text.startswith("---"):
                            end = text.find("\n---\n", 3)
                            body = text[end + 4:].strip() if end != -1 else text.strip()
                        else:
                            body = text.strip()
                    except OSError:
                        pass
                    # FTS5 contentless delete: 삽입 시와 동일한 값 필요
                    conn.execute(
                        "INSERT INTO messages_fts"
                        "(messages_fts, rowid, subject, from_name, from_addr, to_addrs, body)"
                        " VALUES('delete', ?, ?, ?, ?, ?, ?)",
                        (rowid, subj or "", fname or "", faddr or "",
                         toaddrs or "", body),
                    )
                    conn.execute("DELETE FROM messages WHERE id = ?", (rowid,))
                    conn.execute(
                        "DELETE FROM fts_sync WHERE path = ?", (md_path,)
                    )
                    conn.commit()
                    fts_cleaned = True
            finally:
                conn.close()
        except sqlite3.Error as e:
            click.echo(f"DB 업데이트 실패: {e}", err=True)

    # ── MD 파일 삭제 ──────────────────────────────────────────────────────
    try:
        path.unlink()
    except OSError as e:
        click.echo(f"파일 삭제 실패: {e}", err=True)
        return

    # ── 첨부 파일 삭제 ────────────────────────────────────────────────────
    deleted_atts: list[str] = []
    failed_atts:  list[str] = []
    if delete_attachments:
        for att in attachments:
            try:
                Path(att["abs_path"]).unlink()
                deleted_atts.append(att["name"])
                # 빈 디렉터리 정리 (sha256 디렉터리)
                parent = Path(att["abs_path"]).parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except OSError as e:
                failed_atts.append(f"{att['name']} ({e})")

    # ── 결과 출력 ────────────────────────────────────────────────────────
    click.echo("✓ MD 파일 삭제 완료.")
    if fts_cleaned:
        click.echo("✓ 인덱스에서 제거.")
    if deleted_atts:
        click.echo(f"✓ 첨부 파일 {len(deleted_atts)}개 삭제: {', '.join(deleted_atts)}")
    if failed_atts:
        click.echo(f"✗ 첨부 파일 삭제 실패: {', '.join(failed_atts)}", err=True)


# ---------------------------------------------------------------------------
# CLI 커맨드
# ---------------------------------------------------------------------------

@click.command(name="mailview")
@click.argument("query", default="")
@click.option("--from",    "from_filter", default="", help="발신자 필터")
@click.option("--after",   default="", metavar="YYYY-MM-DD")
@click.option("--before",  default="", metavar="YYYY-MM-DD")
@click.option("--folder",  default="", help="폴더 필터")
@click.option("--thread",  default="", help="스레드 ID")
@click.option("--body",    "body_filter", default="", help="본문 내용 전용 검색")
@click.option("--archive", default="", help="아카이브 루트")
# ── 내부 히든 모드 (fzf execute/reload 에서 호출) ──────────────────────
@click.option("--open-att",   "_open_att",    default="", hidden=True,
              help="내부용: 지정 MD 파일의 첨부 파일 열기")
@click.option("--delete-msg", "_delete_msg",  default="", hidden=True,
              help="내부용: 지정 MD 파일 삭제")
@click.option("--fzf-input",  "_fzf_input",   is_flag=True, hidden=True,
              help="내부용: fzf reload 용 레이블\\t경로 출력")
@click.option("--show-help",  "_show_help",   is_flag=True, hidden=True,
              help="내부용: 키 바인딩 도움말 출력")
def main(
    query, from_filter, after, before, folder, thread,
    body_filter, archive, _open_att, _delete_msg, _fzf_input, _show_help,
):
    """fzf + glow 인터랙티브 메일 뷰어."""

    # ── Ctrl-A 첨부 열기 모드 ────────────────────────────────────────────
    if _open_att:
        handle_open_attachments(_open_att)
        return

    # ── Ctrl-D 메일 삭제 모드 ────────────────────────────────────────────
    if _delete_msg:
        handle_delete_message(_delete_msg, archive)
        return

    # ── 도움말 출력 모드 (? 키 → fzf --disabled 팝업) ───────────────────
    if _show_help:
        for line in _HELP_LINES:
            click.echo(line)
        return

    # ── fzf reload 출력 모드 (Ctrl-B / Ctrl-R) ───────────────────────────
    if _fzf_input:
        cfg = load_config()
        if archive:
            cfg["archive"]["root"] = archive
        db = db_path(cfg)
        if body_filter:
            archive_arg = ["--archive", cfg["archive"]["root"]] if archive else []
            paths = get_paths_from_query(archive_arg + ["--body", body_filter])
        else:
            paths = get_recent_paths(db)
        _print_fzf_lines(paths, db)
        return

    # ── 일반 뷰어 모드 ───────────────────────────────────────────────────
    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive

    db = db_path(cfg)
    if not db.exists():
        click.echo(f"오류: 인덱스 없음 → {db}", err=True)
        sys.exit(1)

    plat     = detect_platform()
    fzf_hint  = "winget install fzf"                if plat == "windows" else "sudo apt install fzf"
    glow_hint = "winget install charmbracelet.glow" if plat == "windows" else "sudo snap install glow"

    fzf_path  = _require_tool("fzf",  cfg, fzf_hint)
    glow_path = _require_tool("glow", cfg, glow_hint)
    bat_path  = _check_tool("bat", cfg)

    # ── 경로 목록 수집 ────────────────────────────────────────────────────
    if query or from_filter or after or before or folder or thread or body_filter:
        extra: list[str] = []
        if from_filter:  extra += ["--from",   from_filter]
        if after:        extra += ["--after",  after]
        if before:       extra += ["--before", before]
        if folder:       extra += ["--folder", folder]
        if thread:       extra += ["--thread", thread]
        if body_filter:  extra += ["--body",   body_filter]
        paths = get_paths_from_query([query] + extra if query else extra)
    else:
        paths = get_recent_paths(db)

    valid_paths = [p for p in paths if Path(p).exists()]
    if not valid_paths:
        click.echo("결과 없음.")
        return

    # ── fzf 입력 파일: "레이블\t경로" 형식 ──────────────────────────────
    tmp_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False,
        encoding="utf-8", prefix="mailview_",
    )
    try:
        tmp_file.write(f"{_FZF_COL_HEADER}\t\n")
        for p in valid_paths:
            label = get_label(p, db)
            tmp_file.write(f"{label}\t{p}\n")
        tmp_file.close()

        cfg_glow_style = cfg.get("mailview", {}).get("glow_style", "")
        preview_cmd  = build_fzf_preview_cmd(glow_path, bat_path, cfg_glow_style)
        editor       = get_editor()
        script_path  = str(Path(__file__).resolve())
        py           = sys.executable
        archive_path = str(cfg["archive"]["root"])

        # ── execute()/reload() 바인딩 경로 인용부호 ───────────────────────
        # fzf 는 execute("cmd {2}") 에서 {2} 를 그대로 치환한다.
        # 공백 포함 경로를 안전하게 전달하려면 플랫폼별 인용부호가 필요하다.
        #
        #   Linux  (sh -c)   : 작은따옴표  '{2}'
        #   Windows (cmd /c) : 큰따옴표   "{2}"
        #
        # Python/스크립트 경로도 동일하게 인용부호로 감싼다.
        # fzf 색상: dark 기본 (터미널 16색 기반)
        # 커스텀 색상 예: config.toml 에서 직접 fzf 옵션을 확장하거나
        # 아래 _FZF_COLORS 를 Catppuccin Mocha 등으로 교체해 사용
        _FZF_COLORS = "dark"

        if plat == "windows":
            q = '"'
            open_att_cmd  = f'{q}{py}{q} {q}{script_path}{q} --open-att {q}{{2}}{q}'
            delete_cmd    = (
                f'{q}{py}{q} {q}{script_path}{q} --delete-msg {q}{{2}}{q} '
                f'--archive {q}{archive_path}{q}'
            )
            editor_cmd    = f'{q}{editor}{q} {q}{{2}}{q}'
            bat_cmd       = f'{q}{bat_path}{q} --style=full {q}{{2}}{q}' if bat_path else None
            pager_cmd     = f'more {q}{{2}}{q}'
            body_reload   = (
                f'{q}{py}{q} {q}{script_path}{q} --fzf-input '
                f'--body {q}{{q}}{q} --archive {q}{archive_path}{q}'
            )
            reset_reload  = (
                f'{q}{py}{q} {q}{script_path}{q} --fzf-input '
                f'--archive {q}{archive_path}{q}'
            )
            help_popup    = (
                f'{q}{py}{q} {q}{script_path}{q} --show-help '
                f'| {q}{fzf_path}{q} --disabled --no-info '
                f'--border=rounded --border-label=" 도움말 " '
                f'--color "{_FZF_COLORS}"'
            )
        else:
            q = "'"
            open_att_cmd  = f"{q}{py}{q} {q}{script_path}{q} --open-att {q}{{2}}{q}"
            delete_cmd    = (
                f"{q}{py}{q} {q}{script_path}{q} --delete-msg {q}{{2}}{q} "
                f"--archive {q}{archive_path}{q}"
            )
            editor_cmd    = f"{q}{editor}{q} {q}{{2}}{q}"
            bat_cmd       = f"{q}{bat_path}{q} --style=full {q}{{2}}{q}" if bat_path else None
            pager_cmd     = f"less {q}{{2}}{q}"
            body_reload   = (
                f"{q}{py}{q} {q}{script_path}{q} --fzf-input "
                f"--body {q}{{q}}{q} --archive {q}{archive_path}{q}"
            )
            reset_reload  = (
                f"{q}{py}{q} {q}{script_path}{q} --fzf-input "
                f"--archive {q}{archive_path}{q}"
            )
            help_popup    = (
                f"{q}{py}{q} {q}{script_path}{q} --show-help "
                f"| {q}{fzf_path}{q} --disabled --no-info "
                f"--border=rounded --border-label={q} 도움말 {q} "
                f"--color {q}{_FZF_COLORS}{q}"
            )

        fzf_cmd = [
            fzf_path,
            # ── television 스타일 레이아웃 ───────────────────────────────
            "--ansi",
            "--layout=reverse",
            "--border=rounded",
            "--border-label= ✉ mailview ",
            "--header-first",
            "--prompt", "검색> ",
            "--info=right",
            "--pointer", "▶",
            "--scrollbar", "▌",
            "--padding", "0,1",
            # ── Catppuccin Mocha 색상 ────────────────────────────────────
            "--color", _FZF_COLORS,
            # ── 목록 구성 ────────────────────────────────────────────────
            "--delimiter", "\t",
            "--with-nth", "1",
            "--header-lines", "1",
            "--header", "Enter:열람  Ctrl-B:본문검색  ?:도움말",
            # ── 미리보기 ─────────────────────────────────────────────────
            "--preview", preview_cmd,
            "--preview-window", "right:48%:border-left:wrap",
            "--preview-label", " 미리보기 ",
            # ── 키 바인딩 ────────────────────────────────────────────────
            "--bind", f"ctrl-o:execute({editor_cmd})+abort",
            "--bind", f"ctrl-a:execute({open_att_cmd})",
            "--bind", f"ctrl-d:execute({delete_cmd})+reload({reset_reload})",
            "--bind", (
                f"ctrl-b:change-prompt(본문검색> )"
                f"+reload({body_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"ctrl-r:change-prompt(검색> )"
                f"+reload({reset_reload})"
                f"+clear-query"
            ),
            "--bind", f"?:execute({help_popup})",
        ]

        if bat_cmd:
            fzf_cmd += ["--bind", f"ctrl-p:execute({bat_cmd})+abort"]
        else:
            fzf_cmd += ["--bind", f"ctrl-p:execute({pager_cmd})+abort"]

        with open(tmp_file.name, encoding="utf-8") as stdin_fh:
            result = subprocess.run(
                fzf_cmd,
                stdin=stdin_fh,
                capture_output=False,
                text=True,
                encoding="utf-8",
            )

        # Enter 로 선택된 메일을 glow 로 렌더링
        if result.returncode == 0:
            selected_line = (result.stdout or "").strip()
            if "\t" in selected_line:
                selected_path = selected_line.split("\t", 1)[1].strip()
                if selected_path and Path(selected_path).exists():
                    subprocess.run([glow_path, "-p", "-s", "dark", selected_path])

    finally:
        Path(tmp_file.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
