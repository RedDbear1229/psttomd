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
from pathlib import Path
from typing import Optional

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, db_path, archive_root, detect_platform


# ---------------------------------------------------------------------------
# fzf 컬럼 헤더
# ---------------------------------------------------------------------------
# get_label() 포맷: f"{date}  {sender:<28}  {subject}"
#   date   = 10 cols  (YYYY-MM-DD)
#   sender = 28 cols  (left-padded ASCII)
#   subject = 나머지
#
# 한글은 터미널에서 2칸 너비로 출력되므로 시각적 정렬에 맞게 공백을 조정한다.
#   '날짜'   = 4 visual cols → date   10 cols 맞추기 위해 6 spaces 추가
#   '보낸사람' = 8 visual cols → sender 28 cols 맞추기 위해 20 spaces 추가
_FZF_COL_HEADER: str = (
    "날짜" + " " * 6           # 4 + 6 = 10 visual cols (date 열)
    + "  "                     # 2 cols gap
    + "보낸사람" + " " * 20    # 8 + 20 = 28 visual cols (sender 열)
    + "  "                     # 2 cols gap
    + "제목"
)

# 도움말 팝업에 표시할 키 바인딩 목록 (fzf --disabled 로 표시)
_HELP_LINES: list[str] = [
    "  키 바인딩 도움말 — mailview",
    "  " + "─" * 40,
    "  Enter      glow 로 메일 열람",
    "  Ctrl-P     bat / less 로 원문 표시",
    "  Ctrl-O     $EDITOR 로 편집",
    "  Ctrl-A     첨부 파일 열기",
    "  Ctrl-B     현재 검색어로 본문 검색 후 목록 갱신",
    "  Ctrl-R     전체 목록 초기화 (최근 100통)",
    "  ?          이 도움말",
    "  ESC        닫기 / 종료",
    "  " + "─" * 40,
    "  검색창 텍스트 입력 후 Ctrl-B → 본문에서 해당 키워드 검색",
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
    """DB 에서 파일 경로에 해당하는 ANSI 컬러 '날짜  발신자  제목' 레이블을 반환한다."""
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            """SELECT substr(date,1,10),
                      from_name || ' <' || from_addr || '>',
                      subject
               FROM messages WHERE path = ? LIMIT 1""",
            (path,),
        ).fetchone()
        conn.close()
        if row:
            date    = (row[0] or "")[:10]
            sender  = (row[1] or "")[:28]
            subject = (row[2] or "")[:55]
            # ANSI 컬러: 날짜=청록, 발신자=초록, 제목=기본색
            # fzf --ansi 플래그가 이미 활성화돼 있으므로 코드가 그대로 렌더링된다.
            # sender 는 ANSI 코드를 포함하므로 시각적 28칸을 맞추기 위해
            # 공백을 직접 계산한다 (ANSI 코드는 출력 폭에 포함되지 않음).
            sender_plain = f"{sender:<28}"
            return (
                f"\033[36m{date}\033[0m"          # 청록
                f"  "
                f"\033[32m{sender_plain}\033[0m"  # 초록
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

def build_fzf_preview_cmd(glow_path: str, bat_path: Optional[str]) -> str:
    """플랫폼에 맞는 fzf --preview 명령어 문자열을 생성한다.

    fzf 입력 형식: "레이블\\t파일경로" — {2} 가 경로를 가리킨다.

    우선순위: glow(마크다운 렌더링·컬러) → bat(구문 강조) → type/cat(플레인)
    bat 은 Ctrl-P 원문 보기 전용으로 분리한다.

    경로 인용부호 전략:
      - Linux  : {2} 를 작은따옴표로 감쌈 → 공백·특수문자 안전
      - Windows: {2} 를 큰따옴표로 감쌈  → cmd.exe 공백 처리
        (fzf 가 {2} 확장 후 따옴표가 적용되므로 이중 따옴표 불필요)

    Args:
        glow_path: glow 실행 파일 절대 경로.
        bat_path:  bat 실행 파일 절대 경로 (없으면 None, 폴백으로만 사용).

    Returns:
        fzf --preview 옵션에 전달할 명령어 문자열.
    """
    plat = detect_platform()

    if plat == "windows":
        # cmd.exe: 큰따옴표, 2>nul, type
        item = '"{2}"'
        null_redirect = "2>nul"
        # glow 우선: 마크다운을 렌더링하여 컬러로 표시
        # bat 은 Ctrl-P 원문 보기 전용으로 사용
        fallback = f'"{bat_path}" --style=plain --color=always {item} {null_redirect}' if bat_path else f'type {item}'
        return f'"{glow_path}" -s dark {item} {null_redirect} || {fallback}'
    else:
        # sh: 작은따옴표, 2>/dev/null, cat
        # fzf 가 {2} 를 확장한 뒤 작은따옴표로 감싸므로 공백 경로 안전
        item = "'{2}'"
        null_redirect = "2>/dev/null"
        # glow 우선: 마크다운을 렌더링하여 컬러로 표시
        # bat 은 Ctrl-P 원문 보기 전용으로 사용
        fallback = f"'{bat_path}' --style=plain --color=always {item} {null_redirect}" if bat_path else f"cat {item}"
        return f"'{glow_path}' -s dark {item} {null_redirect} || {fallback}"


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
@click.option("--open-att",  "_open_att",   default="", hidden=True,
              help="내부용: 지정 MD 파일의 첨부 파일 열기")
@click.option("--fzf-input", "_fzf_input",  is_flag=True, hidden=True,
              help="내부용: fzf reload 용 레이블\\t경로 출력")
@click.option("--show-help", "_show_help",  is_flag=True, hidden=True,
              help="내부용: 키 바인딩 도움말 출력")
def main(
    query, from_filter, after, before, folder, thread,
    body_filter, archive, _open_att, _fzf_input, _show_help,
):
    """fzf + glow 인터랙티브 메일 뷰어."""

    # ── Ctrl-A 첨부 열기 모드 ────────────────────────────────────────────
    if _open_att:
        handle_open_attachments(_open_att)
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

        preview_cmd  = build_fzf_preview_cmd(glow_path, bat_path)
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
        if plat == "windows":
            q = '"'
            open_att_cmd  = f'{q}{py}{q} {q}{script_path}{q} --open-att {q}{{2}}{q}'
            editor_cmd    = f'{q}{editor}{q} {q}{{2}}{q}'
            bat_cmd       = f'{q}{bat_path}{q} --style=full {q}{{2}}{q}' if bat_path else None
            pager_cmd     = f'more {q}{{2}}{q}'
            # fzf-input reload: --body {q} 는 fzf 가 검색창 텍스트로 치환
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
                f'--border=rounded --border-label=" 키 바인딩 도움말 " '
                f'--color "border:#7aa2f7,label:#7aa2f7" '
                f'--header {q}ESC 로 닫기{q}'
            )
        else:
            q = "'"
            open_att_cmd  = f"{q}{py}{q} {q}{script_path}{q} --open-att {q}{{2}}{q}"
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
                f"--border=rounded --border-label={q} 키 바인딩 도움말 {q} "
                f"--color {q}border:#7aa2f7,label:#7aa2f7{q} "
                f"--header {q}ESC 로 닫기{q}"
            )

        fzf_cmd = [
            fzf_path,
            # ── Telescope 스타일 레이아웃 ────────────────────────────────
            "--ansi",
            "--layout=reverse",
            "--border=rounded",
            "--border-label= mailview ",
            "--prompt", "검색> ",
            "--info=inline",
            # ── Tokyo Night 계열 색상 ────────────────────────────────────
            "--color",
            (
                "bg:#1a1b26,bg+:#292e42,fg:#c0caf5,fg+:#c0caf5,"
                "hl:#7aa2f7,hl+:#7aa2f7,border:#3b4261,label:#7aa2f7,"
                "prompt:#7aa2f7,pointer:#ff9e64,marker:#9ece6a,"
                "info:#9ece6a,header:#565f89,spinner:#7aa2f7"
            ),
            # ── 목록 구성 ────────────────────────────────────────────────
            "--delimiter", "\t",
            "--with-nth", "1",
            "--header-lines", "1",
            "--header",
            "Enter:열람  Ctrl-P:원문  Ctrl-O:편집  Ctrl-A:첨부  "
            "Ctrl-B:본문검색  Ctrl-R:초기화  ?:도움말  ESC:종료",
            # ── 미리보기 ─────────────────────────────────────────────────
            "--preview", preview_cmd,
            "--preview-window", "right:55%:border-left:wrap",
            # ── 키 바인딩 ────────────────────────────────────────────────
            "--bind", f"ctrl-o:execute({editor_cmd})+abort",
            "--bind", f"ctrl-a:execute({open_att_cmd})",
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
