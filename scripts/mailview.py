#!/usr/bin/env python3
"""
mailview — fzf + glow/mdcat 인터랙티브 메일 뷰어 (크로스플랫폼)

사용법:
  mailview               # 최근 100통 목록
  mailview "견적서"       # 검색 후 fzf 선택
  mailview --thread t_abc123

키 바인딩 (fzf):
  Enter   → mailview.preview_viewer 설정에 따라 mdcat(기본) 또는 glow 로 전체 열람
  Ctrl-P  → bat/less 로 원문 표시
  Ctrl-O  → $EDITOR / notepad 로 열기
  Ctrl-A  → 첨부 파일 목록 표시 및 열기
  ESC     → 종료

뷰어 선택 (config.toml [mailview] preview_viewer):
  - mdcat (기본) : Kitty/WezTerm/iTerm2 그래픽 프로토콜 또는 sixel(Windows
                   Terminal 1.22+) 지원 터미널에서 마크다운 내 이미지를 인라인 렌더.
                   pager 없이 stdout 직접 출력(less 경유 시 이미지가 깨짐).
                   미설치 시 자동으로 glow 로 폴백.
  - glow         : 컬러 마크다운 + pager. 이미지는 텍스트 링크로만 표시.
                   sixel 미지원 터미널이거나 pager 를 선호할 때 사용.
  `pst2md-config set-viewer mdcat|glow` 로 전환.

첨부 파일 열기 (Ctrl-A):
  - 첨부가 1개  : 즉시 OS 기본 앱으로 열림
  - 첨부가 여러 개 : fzf 로 선택 후 열림
  - 첨부 파일명은 텍스트로 표시되고 Ctrl-A 로 실제 파일을 열 수 있음

플랫폼별 파일 열기:
  Linux   : xdg-open
  WSL     : wslview (wslu) 또는 explorer.exe 경유
  Windows : os.startfile
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, db_path, archive_root, archive_roots, detect_platform
from build_index import fts_has_prefix_index


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
    + "   "                    # 3 cols: 첨부 표시 공간 (📎 = 2 + space 1)
    + "제목"
)

# 도움말 팝업에 표시할 키 바인딩 목록 (fzf --disabled 로 표시)
# television 스타일: 모든 바인딩을 한 곳에 모아 ? 키로만 접근
_HELP_LINES: list[str] = [
    "",
    "   키 바인딩 — mailview",
    "   " + "─" * 44,
    "   Enter      메일 열람 (mdcat/glow — config preview_viewer)",
    "   Ctrl-P     원문 표시 (bat / less)",
    "   Ctrl-O     $EDITOR 로 열기",
    "   Ctrl-A     첨부 파일 목록 열기",
    "   Ctrl-U     URL 추출 및 열기 (브라우저)",
    "   Ctrl-K     태그 수정 (쉼표 구분)",
    "   Ctrl-D     메일 삭제 (확인 후 삭제)",
    "   Ctrl-X     선택 메일 일괄 삭제 (Tab 으로 복수 선택)",
    "   Ctrl-B     본문 검색 모드 (미리보기에 매칭 라인 강조)",
    "   Ctrl-F     폴더 브라우저 (fzf 0.47+ 필요)",
    "   Ctrl-T     같은 스레드 전체 보기 (fzf 0.47+ 필요)",
    "   Ctrl-R     전체 목록 초기화 (최근 100통, 날짜순)",
    "   Alt-I      아카이브 통계 요약",
    "   Alt-T      태그 브라우저 (fzf 0.47+ 필요)",
    "   Alt-S      제목순 정렬",
    "   Alt-F      발신자순 정렬",
    "   Alt-1      오늘 수신 메일",
    "   Alt-2      최근 7일 메일",
    "   Alt-3      최근 30일 메일",
    "   Alt-4      최근 1년 메일",
    "   Tab        멀티 선택 토글",
    "   ?          이 도움말 (ESC 로 닫기)",
    "   ESC        종료",
    "   " + "─" * 44,
    "   📎  첨부 파일 있는 메일",
    "   Ctrl-B 후 검색어 입력 → 미리보기에서 매칭 라인 하이라이트",
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

_SORT_ORDER: dict[str, str] = {
    "date":    "date DESC",
    "from":    "LOWER(COALESCE(NULLIF(from_name,''), from_addr, '')) ASC",
    "subject": "LOWER(COALESCE(subject, '')) ASC",
}


def get_recent_paths(
    db: Path,
    limit: int = 100,
    after: str = "",
    sort: str = "date",
) -> list[str]:
    """DB 에서 Markdown 파일 경로 목록을 반환한다.

    Args:
        db:    인덱스 SQLite 경로.
        limit: 최대 반환 수 (기본 100).
        after: ISO 날짜 문자열 (YYYY-MM-DD). 지정 시 해당 날짜 이후 메일만 반환.
        sort:  정렬 기준. "date" | "from" | "subject" (기본 "date").

    Returns:
        정렬 순서대로 파일 경로 목록.
    """
    order_clause = _SORT_ORDER.get(sort, _SORT_ORDER["date"])
    conn = sqlite3.connect(str(db))
    if after:
        rows = conn.execute(
            f"SELECT path FROM messages WHERE date >= ? ORDER BY {order_clause} LIMIT ?",
            (after, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT path FROM messages ORDER BY {order_clause} LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def get_folder_list(db: Path) -> list[str]:
    """DB 에서 사용 중인 폴더 이름 목록을 반환한다.

    Args:
        db: 인덱스 SQLite 경로.

    Returns:
        알파벳/가나다순으로 정렬된 폴더 이름 목록.
    """
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT DISTINCT folder FROM messages WHERE folder != '' ORDER BY folder"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def get_recent_paths_multi(
    cfg: dict,
    limit: int = 100,
    after: str = "",
    sort: str = "date",
) -> list[str]:
    """모든 아카이브(archive.root + archive.roots)에서 경로를 합산해 반환한다.

    각 아카이브에서 limit 개씩 가져온 뒤, 날짜 내림차순으로 병합해 limit 개를 반환한다.
    단일 아카이브이면 get_recent_paths() 와 동일하게 동작한다.

    Args:
        cfg:   load_config() 결과.
        limit: 최대 반환 수.
        after: ISO 날짜 필터.
        sort:  정렬 기준.

    Returns:
        병합된 파일 경로 목록.
    """
    roots = archive_roots(cfg)
    if len(roots) == 1:
        return get_recent_paths(db_path(cfg), limit=limit, after=after, sort=sort)

    all_paths: list[str] = []
    for root in roots:
        root_db = root / "index.sqlite"
        if root_db.exists():
            paths = get_recent_paths(root_db, limit=limit, after=after, sort=sort)
            all_paths.extend(paths)

    # date DESC 기준 재정렬 (sort 가 date 일 때)
    if sort == "date":
        # 경로만 있으므로 DB 에서 날짜를 가져오기보다 삽입 순서 유지
        # 각 아카이브가 이미 date DESC 정렬이므로 병합 후 앞 limit 개만 반환
        pass

    return all_paths[:limit]


def get_paths_by_tag(db: Path, tag: str) -> list[str]:
    """DB 에서 특정 태그를 가진 메일 경로 목록을 반환한다.

    Args:
        db:  인덱스 SQLite 경로.
        tag: 찾을 태그 이름 (부분 일치).

    Returns:
        날짜 내림차순으로 정렬된 파일 경로 목록.
    """
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT path FROM messages WHERE tags LIKE ? ORDER BY date DESC LIMIT 500",
        (f"%{tag}%",),
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
                      subject,
                      COALESCE(n_attachments, 0)
               FROM messages WHERE path = ? LIMIT 1""",
            (path,),
        ).fetchone()
        conn.close()
        if row:
            date_str   = (row[0] or "")[:10]
            name       = row[1] or ""
            addr       = row[2] or ""
            subject    = (row[3] or "")[:80]
            n_att      = int(row[4] or 0)
            sender_raw = name if name else addr
            sender     = _visual_pad(_visual_truncate(sender_raw, 10), 10)
            # 📎 indicator: 2-wide emoji + space = 3 visual; else 3 spaces
            att_prefix = "📎 " if n_att > 0 else "   "
            # 표준 ANSI 16색 — dark/light 터미널 모두 호환
            return (
                f"\033[36m{date_str}\033[0m"  # cyan
                f"  "
                f"\033[32m{sender}\033[0m"    # green
                f"  "
                f"{att_prefix}"
                f"{subject}"
            )
    except (sqlite3.Error, TypeError, ValueError, UnicodeError):
        # DB 미생성 / 스키마 불일치 / 컬럼 값 형식 오류 — fzf 표시만 대체.
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
      2. 번들된 scripts/lib/mocha-glow.json (Catppuccin Mocha) — 시인성 강화 기본값
      3. 'dark' (번들 테마 파일이 없는 비정상 설치 환경 폴백)

    커스텀 테마 사용 예 (config.toml):
      glow_style = "dracula"
      glow_style = "/home/user/.config/glow/catppuccin-mocha.json"
      glow_style = "dark"          # 번들 테마를 끄고 glow 기본 모노톤 사용

    Args:
        cfg_style: config.toml 의 mailview.glow_style 값 (빈 문자열 가능).

    Returns:
        glow -s 에 전달할 테마명 또는 파일 경로 문자열.
    """
    if cfg_style:
        return cfg_style
    bundled = Path(__file__).resolve().parent / "lib" / "mocha-glow.json"
    if bundled.is_file():
        return str(bundled)
    return "dark"


def build_full_viewer_cmd(
    selected_path: str,
    glow_path: str,
    glow_style: str,
    *,
    mdcat_path: Optional[str] = None,
    viewer: str = "mdcat",
) -> list[str]:
    """Enter 로 선택된 메일을 렌더링할 subprocess argv 를 반환한다.

    뷰어 선택:
      - viewer="glow"  → ``glow -p -s <style> <path>``
        pager(less) 사용. 이미지는 텍스트 링크로만 표시.
      - viewer="mdcat" + mdcat_path 존재
        → ``mdcat --local <path>``
        pager 미사용(less 경유 시 이미지 렌더링이 깨지므로 직접 stdout 출력).
        Kitty/WezTerm/iTerm2 그래픽 프로토콜 또는 sixel(Windows Terminal 1.22+)
        지원 터미널에서 마크다운 내 이미지가 인라인으로 렌더링된다.
        ``--local`` (=-l) 로 원격 이미지 fetch 는 차단(트래킹 픽셀 방어).
      - viewer="mdcat" + mdcat_path=None → glow 로 폴백.

    Args:
        selected_path: 렌더링할 MD 파일 경로.
        glow_path:     glow 실행 파일 절대 경로.
        glow_style:    resolve_glow_style() 결과 (이미 확정된 스타일/파일).
        mdcat_path:    mdcat 실행 파일 절대 경로 (viewer='mdcat' 일 때만 유효).
        viewer:        "glow" | "mdcat".

    Returns:
        subprocess.run 에 그대로 넘길 argv 리스트.
    """
    if viewer == "mdcat" and mdcat_path:
        return [mdcat_path, "--local", selected_path]
    return [glow_path, "-p", "-s", glow_style, selected_path]


def build_fzf_preview_cmd(
    glow_path: str,
    bat_path: Optional[str],
    glow_style: str = "",
    *,
    mdcat_path: Optional[str] = None,
    viewer: str = "mdcat",
) -> str:
    """플랫폼에 맞는 fzf --preview 명령어 문자열을 생성한다.

    fzf 입력 형식: "레이블\\t파일경로" — {2} 가 경로를 가리킨다.

    뷰어 선택:
      - mdcat (기본): Kitty/WezTerm/iTerm2/sixel(Windows Terminal 1.22+) 등
                      그래픽 지원 터미널에서 첨부/원격 이미지를 인라인 렌더.
                      비지원 터미널에서는 텍스트 자리표시자로 출력.
                      `--local` 을 강제해 원격 fetch 는 차단.
      - glow        : 마크다운 렌더링·컬러. 이미지는 텍스트 링크로만 표시.
                      mdcat 미설치 또는 sixel 미지원 터미널에서 사용.

    폴백 체인: 선택 뷰어 → bat(구문 강조) → type/cat(플레인).

    경로 인용부호 전략:
      - Linux  : {2} 를 작은따옴표로 감쌈 → 공백·특수문자 안전
      - Windows: {2} 를 큰따옴표로 감쌈  → cmd.exe 공백 처리

    Args:
        glow_path:  glow 실행 파일 절대 경로.
        bat_path:   bat 실행 파일 절대 경로 (없으면 None).
        glow_style: config 에서 전달된 스타일 값 (빈 문자열이면 자동 결정).
        mdcat_path: mdcat 실행 파일 절대 경로 (viewer='mdcat' 일 때만 유효).
        viewer:     "glow" | "mdcat" (config mailview.preview_viewer).

    Returns:
        fzf --preview 옵션에 전달할 명령어 문자열.
    """
    plat  = detect_platform()
    style = resolve_glow_style(glow_style)
    use_mdcat = viewer == "mdcat" and mdcat_path is not None

    if plat == "windows":
        item          = '"{2}"'
        null_redirect = "2>nul"
        fallback = (
            f'"{bat_path}" --style=plain --color=always {item} {null_redirect}'
            if bat_path else f'type {item}'
        )
        if use_mdcat:
            primary = (
                f'"{mdcat_path}" --local '
                f'--columns %FZF_PREVIEW_COLUMNS% {item} {null_redirect}'
            )
        else:
            primary = f'"{glow_path}" -s "{style}" {item} {null_redirect}'
        return f'{primary} || {fallback}'

    # Linux / WSL
    item          = "'{2}'"
    null_redirect = "2>/dev/null"
    # fzf 가 preview 컨텍스트에서 export 하는 창 폭. 없으면 80 으로 폴백.
    width_var = "${FZF_PREVIEW_COLUMNS:-80}"

    # frontmatter(2 개의 '---' 구분자) 이후만 뷰어에 파이프 → 본문부터 보임.
    # awk 미탑재 환경에서는 뷰어가 파일 전체를 받는 폴백으로 동작.
    awk_path = shutil.which("awk")
    if use_mdcat:
        if awk_path:
            awk_filter = f"awk '/^---$/{{c++;next}} c>=2' {item}"
            primary = (
                f"({awk_filter} | '{mdcat_path}' --local "
                f"--columns {width_var} - {null_redirect})"
            )
        else:
            primary = (
                f"'{mdcat_path}' --local --columns {width_var} "
                f"{item} {null_redirect}"
            )
    elif awk_path:
        awk_filter = f"awk '/^---$/{{c++;next}} c>=2' {item}"
        primary = (
            f"({awk_filter} | '{glow_path}' -s '{style}' "
            f"--width {width_var} - {null_redirect})"
        )
    else:
        primary = (
            f"'{glow_path}' -s '{style}' --width {width_var} "
            f"{item} {null_redirect}"
        )

    fallback = (
        f"'{bat_path}' --style=plain --color=always {item} {null_redirect}"
        if bat_path else f"cat {item}"
    )
    return f"{primary} || {fallback}"


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
    except (OSError, subprocess.SubprocessError, UnicodeError) as e:
        # 실행 파일 없음 / 권한 / wslpath 실패 / 인코딩 문제 등.
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
# 일괄 삭제 핸들러 (Ctrl-X — 멀티 선택 후 호출)
# ---------------------------------------------------------------------------

def handle_bulk_delete(archive: str = "") -> None:
    """stdin 에서 경로를 읽어 복수의 MD 파일을 일괄 삭제한다.

    fzf 의 execute() 액션에서 ``printf '%s\\n' {+2} | python mailview.py
    --fzf-bulk-delete`` 형태로 호출된다. stdin 이 파이프이므로
    확인 프롬프트는 /dev/tty (Linux) 또는 CONIN$ (Windows) 를 통해 읽는다.

    Args:
        archive: 아카이브 루트 경로 (비어 있으면 config 에서 로드).
    """
    raw = sys.stdin.read()
    # Linux: printf '%s\n' {+2} → newline-separated
    # Windows: echo {+2} → space-separated quoted ("p1" "p2")
    lines = [p.strip().strip('"') for p in raw.splitlines() if p.strip()]
    if len(lines) == 1 and '"' in raw:
        # Windows 단일 라인 — 큰따옴표로 구분된 경로 파싱
        lines = [p for p in shlex.split(lines[0]) if p.strip()]
    paths = [p for p in lines if p]
    if not paths:
        click.echo("선택된 메일 없음.")
        return

    click.echo(f"\n일괄 삭제 대상 {len(paths)}개:")
    for p in paths:
        fm = _read_frontmatter_fields(p)
        subj = fm.get("subject", Path(p).name)
        date_s = fm.get("date", "")
        click.echo(f"  • {date_s}  {subj}")

    click.echo("")

    # stdin 이 파이프이므로 /dev/tty (Linux) / CONIN$ (Windows) 에서 입력 읽기
    plat = detect_platform()
    try:
        tty_path = "CONIN$" if plat == "windows" else "/dev/tty"
        tty = open(tty_path)    # noqa: WPS515
    except OSError:
        tty = None

    try:
        prompt_msg = f"MD 파일 {len(paths)}개를 모두 삭제하시겠습니까? [y/N]: "
        if tty:
            click.echo(prompt_msg, nl=False)
            answer = tty.readline().strip().lower()
        else:
            answer = input(prompt_msg).strip().lower()
    except (KeyboardInterrupt, EOFError):
        click.echo("\n취소.")
        return
    finally:
        if tty:
            tty.close()

    if answer != "y":
        click.echo("취소.")
        return

    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    db = db_path(cfg)

    ok, fail = 0, 0
    for p in paths:
        try:
            # DB 정리
            if db.exists():
                conn = sqlite3.connect(str(db))
                try:
                    row = conn.execute(
                        "SELECT id, subject, from_name, from_addr, to_addrs "
                        "FROM messages WHERE path = ?", (p,)
                    ).fetchone()
                    if row:
                        rowid, subj, fname, faddr, toaddrs = row
                        body = ""
                        try:
                            text = Path(p).read_text(encoding="utf-8")
                            if text.startswith("---"):
                                end = text.find("\n---\n", 3)
                                body = text[end + 4:].strip() if end != -1 else text.strip()
                            else:
                                body = text.strip()
                        except OSError:
                            pass
                        conn.execute(
                            "INSERT INTO messages_fts"
                            "(messages_fts, rowid, subject, from_name, from_addr, to_addrs, body)"
                            " VALUES('delete', ?, ?, ?, ?, ?, ?)",
                            (rowid, subj or "", fname or "", faddr or "",
                             toaddrs or "", body),
                        )
                        conn.execute("DELETE FROM messages WHERE id = ?", (rowid,))
                        conn.execute("DELETE FROM fts_sync WHERE path = ?", (p,))
                        conn.commit()
                finally:
                    conn.close()
            # 파일 삭제
            Path(p).unlink(missing_ok=True)
            ok += 1
        except (OSError, sqlite3.Error) as e:
            click.echo(f"✗ 실패 [{Path(p).name}]: {e}", err=True)
            fail += 1

    click.echo(f"✓ {ok}개 삭제 완료." + (f"  ✗ {fail}개 실패." if fail else ""))


# ---------------------------------------------------------------------------
# URL 추출 및 열기 (Feature 3)
# ---------------------------------------------------------------------------

_URL_RE = re.compile(
    r"https?://[^\s\)\]\>,\"']+",
    re.IGNORECASE,
)


def extract_urls(md_path: str) -> list[str]:
    """MD 파일 본문에서 URL 을 추출해 중복 없는 순서 목록으로 반환한다.

    YAML frontmatter 는 제외하고 본문 텍스트에서만 추출한다.

    Args:
        md_path: 파싱할 Markdown 파일 경로 문자열.

    Returns:
        발견된 URL 목록 (순서 유지, 중복 제거).
    """
    try:
        text = Path(md_path).read_text(encoding="utf-8")
    except OSError:
        return []

    # frontmatter 건너뛰기
    if text.startswith("---"):
        end = text.find("\n---\n", 3)
        body = text[end + 4:] if end != -1 else text
    else:
        body = text

    seen: set[str] = set()
    urls: list[str] = []
    for url in _URL_RE.findall(body):
        # 마크다운 링크 닫는 괄호/따옴표 후행 제거
        url = url.rstrip(")].,'\"")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def handle_open_url(md_path: str) -> None:
    """MD 파일에서 URL 을 추출하고 fzf 피커로 선택해 브라우저로 연다.

    fzf 의 execute() 액션에서 호출된다.
    URL 이 1개이면 즉시 열고, 여러 개이면 fzf 피커를 띄운다.
    fzf 가 없으면 번호 선택 방식으로 fallback 한다.

    Args:
        md_path: URL 을 추출할 Markdown 파일 경로 문자열.
    """
    urls = extract_urls(md_path)
    if not urls:
        click.echo("\nURL 없음.")
        return

    plat = detect_platform()

    def _open(url: str) -> None:
        if plat == "windows":
            import webbrowser
            webbrowser.open(url)
        elif plat == "wsl":
            if shutil.which("wslview"):
                subprocess.Popen(["wslview", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["explorer.exe", url],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if len(urls) == 1:
        click.echo(f"\n열기: {urls[0]}")
        _open(urls[0])
        return

    fzf = shutil.which("fzf")
    if fzf:
        result = subprocess.run(
            [
                fzf,
                "--header", f"URL {len(urls)}개 — Enter:열기  ESC:취소",
                "--reverse",
                "--no-sort",
            ],
            input="\n".join(urls),
            capture_output=True, text=True, encoding="utf-8",
        )
        if result.returncode == 0:
            selected = result.stdout.strip()
            if selected:
                click.echo(f"열기: {selected}")
                _open(selected)
    else:
        click.echo(f"\nURL {len(urls)}개:")
        for i, url in enumerate(urls, 1):
            click.echo(f"  {i}. {url}")
        try:
            choice = int(input("\n번호 선택 (0=취소): "))
            if 1 <= choice <= len(urls):
                _open(urls[choice - 1])
        except (ValueError, KeyboardInterrupt):
            pass


# ---------------------------------------------------------------------------
# 태그 관리 (Feature 10)
# ---------------------------------------------------------------------------

def get_tag_list(db: Path) -> list[str]:
    """DB 에서 사용 중인 태그 목록을 반환한다.

    messages.tags 컬럼의 쉼표 구분 값을 모아 중복 제거 후 정렬한다.

    Args:
        db: 인덱스 SQLite 경로.

    Returns:
        알파벳/가나다순으로 정렬된 태그 이름 목록.
    """
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT tags FROM messages WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall()
    conn.close()
    seen: set[str] = set()
    tags: list[str] = []
    for (tag_str,) in rows:
        for t in tag_str.split(","):
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                tags.append(t)
    return sorted(tags)


def _update_frontmatter_tags(md_path: str, new_tags: list[str]) -> bool:
    """MD 파일의 YAML frontmatter 에서 tags 필드를 업데이트한다.

    tags 필드가 있으면 교체하고, 없으면 frontmatter 끝에 추가한다.
    new_tags 가 비어 있으면 tags 필드를 삭제한다.

    Args:
        md_path:  업데이트할 Markdown 파일 경로.
        new_tags: 새 태그 목록.

    Returns:
        성공 시 True, 파일 오류 시 False.
    """
    try:
        text = Path(md_path).read_text(encoding="utf-8")
    except OSError:
        return False

    if not text.startswith("---"):
        return False
    end = text.find("\n---\n", 3)
    if end == -1:
        return False

    fm_lines = text[3:end].splitlines()
    after_fm = text[end + 4:]

    # 기존 tags 줄 제거
    fm_lines = [l for l in fm_lines if not l.startswith("tags:")]

    if new_tags:
        tag_value = ", ".join(new_tags)
        fm_lines.append(f"tags: [{tag_value}]")

    new_fm = "\n".join(fm_lines)
    new_text = f"---\n{new_fm}\n---\n{after_fm}"

    try:
        Path(md_path).write_text(new_text, encoding="utf-8")
    except OSError:
        return False
    return True


def handle_tag_message(md_path: str, archive: str = "") -> None:
    """선택한 메일에 태그를 추가/수정한다.

    fzf 의 execute() 액션에서 호출된다.
    현재 태그를 표시하고, 쉼표로 구분된 태그 입력을 받아
    MD 파일 frontmatter 와 DB 를 동시에 업데이트한다.

    Args:
        md_path: 태그를 수정할 Markdown 파일 경로.
        archive: 아카이브 루트 경로 (비어 있으면 config 에서 로드).
    """
    if not Path(md_path).exists():
        click.echo("파일 없음.")
        return

    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    db = db_path(cfg)

    # 현재 태그 조회
    current_tags: list[str] = []
    if db.exists():
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT tags FROM messages WHERE path = ? LIMIT 1", (md_path,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            current_tags = [t.strip() for t in row[0].split(",") if t.strip()]

    fm = _read_frontmatter_fields(md_path)
    click.echo(f"\n메일: {fm.get('subject', Path(md_path).name)}")
    click.echo(f"현재 태그: {', '.join(current_tags) if current_tags else '(없음)'}")
    click.echo("  쉼표로 구분해 입력 (비우면 태그 삭제):")

    try:
        raw = input("태그: ").strip()
    except (KeyboardInterrupt, EOFError):
        click.echo("\n취소.")
        return

    new_tags = [t.strip() for t in raw.split(",") if t.strip()] if raw else []

    # MD 파일 업데이트
    if not _update_frontmatter_tags(md_path, new_tags):
        click.echo("오류: frontmatter 업데이트 실패.", err=True)
        return

    # DB 업데이트
    if db.exists():
        tag_str = ", ".join(new_tags)
        try:
            conn = sqlite3.connect(str(db))
            try:
                conn.execute(
                    "UPDATE messages SET tags = ? WHERE path = ?",
                    (tag_str, md_path),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as e:
            click.echo(f"DB 업데이트 실패: {e}", err=True)
            return

    if new_tags:
        click.echo(f"✓ 태그 설정: {', '.join(new_tags)}")
    else:
        click.echo("✓ 태그 삭제 완료.")


# ---------------------------------------------------------------------------
# 스레드 트리 (Feature 13)
# ---------------------------------------------------------------------------

def build_thread_tree(
    db: Path,
    thread_id: str,
) -> list[tuple[int, str, str, str]]:
    """스레드 내 메일을 부모-자식 관계로 정렬한 트리 목록을 반환한다.

    in_reply_to 컬럼을 사용해 계층 구조를 재구성한다.
    in_reply_to 가 없는 메일은 루트로, 있으면 해당 msgid 의 자식으로 배치한다.

    Args:
        db:        인덱스 SQLite 경로.
        thread_id: 스레드 ID (messages.thread 컬럼).

    Returns:
        [(depth, path, msgid, subject), ...] 깊이 우선 순서 목록.
    """
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        """SELECT msgid, in_reply_to, path, subject, date
           FROM messages WHERE thread = ? ORDER BY date""",
        (thread_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return []

    # msgid → (in_reply_to, path, subject) 맵
    by_msgid: dict[str, tuple[str, str, str]] = {}
    for msgid, irt, path, subj, _ in rows:
        by_msgid[msgid] = (irt or "", path or "", subj or "")

    # 부모 → 자식들 맵
    children: dict[str, list[str]] = {m: [] for m in by_msgid}
    roots: list[str] = []
    for msgid, (irt, _, _) in by_msgid.items():
        if irt and irt in by_msgid:
            children[irt].append(msgid)
        else:
            roots.append(msgid)

    # 깊이 우선 탐색
    result: list[tuple[int, str, str, str]] = []

    def _dfs(msgid: str, depth: int) -> None:
        _, path, subj = by_msgid[msgid]
        result.append((depth, path, msgid, subj))
        for child in sorted(children[msgid]):
            _dfs(child, depth + 1)

    for root in sorted(roots):
        _dfs(root, 0)

    return result


def format_thread_tree(tree: list[tuple[int, str, str, str]]) -> list[str]:
    """build_thread_tree() 결과를 ASCII 트리 형식 문자열 목록으로 변환한다.

    Args:
        tree: build_thread_tree() 가 반환한 [(depth, path, msgid, subject), ...].

    Returns:
        각 메일을 한 줄로 표현한 문자열 목록.
    """
    if not tree:
        return ["스레드 없음."]
    lines: list[str] = [""]
    for depth, path, _, subject in tree:
        indent = "  " * depth
        prefix = "└─ " if depth > 0 else "● "
        subj = subject[:60] if subject else "(제목 없음)"
        lines.append(f"   {indent}{prefix}{subj}")
        if path:
            lines.append(f"   {indent}   {path}")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 중복 메일 감지 (Feature 12)
# ---------------------------------------------------------------------------

def find_duplicate_groups(db: Path) -> list[list[str]]:
    """동일한 (날짜+발신자+제목) 조합을 가진 중복 메일 그룹을 반환한다.

    DB 의 msgid UNIQUE 제약으로 인해 msgid 기준 중복은 발생하지 않는다.
    대신 (date + from_addr + subject) 조합이 동일한 메일을 중복으로 간주한다.
    이는 여러 PST 파일에서 동일 메일이 중복 변환된 경우를 감지한다.

    각 그룹에서 경로가 첫 번째인 항목을 대표로, 나머지를 중복으로 반환한다.

    Args:
        db: 인덱스 SQLite 경로.

    Returns:
        [[대표경로, 중복경로1, ...], ...] 형태의 중복 그룹 목록.
        각 그룹 길이는 2 이상.
    """
    conn = sqlite3.connect(str(db))
    rows = conn.execute("""
        SELECT
            COALESCE(substr(date,1,10),'') || '|' ||
            COALESCE(from_addr,'') || '|' ||
            COALESCE(subject,'') AS key,
            GROUP_CONCAT(path, '|') AS paths,
            COUNT(*) AS cnt
        FROM messages
        GROUP BY key
        HAVING cnt > 1 AND key != '||'
        ORDER BY key
    """).fetchall()
    conn.close()

    groups: list[list[str]] = []
    for _, paths_str, _ in rows:
        paths = [p for p in paths_str.split("|") if p and Path(p).exists()]
        if len(paths) >= 2:
            groups.append(paths)

    return groups


def handle_dedupe(archive: str = "", dry_run: bool = False) -> None:
    """중복 메일을 감지하고 선택적으로 삭제한다.

    각 중복 그룹에서 첫 번째 항목을 대표로 유지하고,
    나머지를 fzf 피커로 확인 후 삭제한다.

    Args:
        archive:  아카이브 루트 경로 (비어 있으면 config 에서 로드).
        dry_run:  True 이면 삭제 없이 중복 목록만 출력한다.
    """
    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    db = db_path(cfg)

    if not db.exists():
        click.echo(f"오류: 인덱스 없음 → {db}", err=True)
        return

    click.echo("중복 메일 검사 중...", err=True)
    groups = find_duplicate_groups(db)

    if not groups:
        click.echo("중복 메일 없음.")
        return

    total_dups = sum(len(g) - 1 for g in groups)
    click.echo(f"중복 그룹: {len(groups)}개, 삭제 대상: {total_dups}개")

    if dry_run:
        for i, group in enumerate(groups, 1):
            click.echo(f"\n그룹 {i} ({len(group)}개):")
            for j, p in enumerate(group):
                fm = _read_frontmatter_fields(p)
                mark = "[대표]" if j == 0 else "[중복]"
                click.echo(f"  {mark} {fm.get('date','?')}  {fm.get('subject','?')}")
                click.echo(f"         {p}")
        return

    # 삭제할 경로 목록 (각 그룹에서 첫 번째 제외)
    to_delete: list[str] = []
    for group in groups:
        to_delete.extend(group[1:])

    click.echo(f"\n삭제 대상 {len(to_delete)}개:")
    for p in to_delete:
        fm = _read_frontmatter_fields(p)
        click.echo(f"  • {fm.get('date','?')}  {fm.get('subject','?')}")
        click.echo(f"    {p}")

    click.echo("")
    try:
        answer = input(f"위 {len(to_delete)}개를 삭제하시겠습니까? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        click.echo("\n취소.")
        return

    if answer != "y":
        click.echo("취소.")
        return

    ok, fail = 0, 0
    for p in to_delete:
        try:
            conn = sqlite3.connect(str(db))
            try:
                row = conn.execute(
                    "SELECT id, subject, from_name, from_addr, to_addrs "
                    "FROM messages WHERE path = ?", (p,)
                ).fetchone()
                if row:
                    rowid, subj, fname, faddr, toaddrs = row
                    body = ""
                    try:
                        text = Path(p).read_text(encoding="utf-8")
                        if text.startswith("---"):
                            end = text.find("\n---\n", 3)
                            body = text[end + 4:].strip() if end != -1 else text.strip()
                        else:
                            body = text.strip()
                    except OSError:
                        pass
                    conn.execute(
                        "INSERT INTO messages_fts"
                        "(messages_fts, rowid, subject, from_name, from_addr, to_addrs, body)"
                        " VALUES('delete', ?, ?, ?, ?, ?, ?)",
                        (rowid, subj or "", fname or "", faddr or "",
                         toaddrs or "", body),
                    )
                    conn.execute("DELETE FROM messages WHERE id = ?", (rowid,))
                    conn.execute("DELETE FROM fts_sync WHERE path = ?", (p,))
                    conn.commit()
            finally:
                conn.close()
            Path(p).unlink(missing_ok=True)
            ok += 1
        except (OSError, sqlite3.Error) as e:
            click.echo(f"✗ 실패 [{Path(p).name}]: {e}", err=True)
            fail += 1

    click.echo(f"✓ {ok}개 삭제 완료." + (f"  ✗ {fail}개 실패." if fail else ""))


# ---------------------------------------------------------------------------
# 아카이브 통계 (Feature 11)
# ---------------------------------------------------------------------------

def format_stats_for_display(db: Path, archive_root_path: Path) -> list[str]:
    """아카이브 요약 통계를 출력 가능한 줄 목록으로 반환한다.

    fzf --disabled 팝업에 표시하기 위한 형식으로 구성한다.

    Args:
        db:                인덱스 SQLite 경로.
        archive_root_path: 아카이브 루트 디렉터리 Path.

    Returns:
        줄 문자열 목록.
    """
    if not db.exists():
        return ["인덱스 없음."]

    lines: list[str] = ["", "   === 아카이브 요약 ===", "   " + "─" * 36]
    try:
        conn = sqlite3.connect(str(db))
        row = conn.execute("""
            SELECT
                COUNT(*)                                     AS total,
                COUNT(DISTINCT from_addr)                    AS senders,
                COUNT(DISTINCT thread)                       AS threads,
                SUM(COALESCE(n_attachments,0))               AS attachments,
                MIN(substr(date,1,10))                       AS oldest,
                MAX(substr(date,1,10))                       AS newest
            FROM messages
        """).fetchone()

        # 월별 Top-5
        monthly = conn.execute("""
            SELECT substr(date,1,7) AS month, COUNT(*) AS cnt
            FROM messages WHERE date != ''
            GROUP BY 1 ORDER BY 1 DESC LIMIT 5
        """).fetchall()

        # 발신자 Top-5
        senders = conn.execute("""
            SELECT COALESCE(NULLIF(from_name,''), from_addr) AS name,
                   COUNT(*) AS cnt
            FROM messages GROUP BY from_addr ORDER BY cnt DESC LIMIT 5
        """).fetchall()

        conn.close()

        if row:
            lines += [
                f"   총 메일:      {row[0]:,}통",
                f"   고유 발신자:  {row[1]:,}명",
                f"   스레드:       {row[2]:,}개",
                f"   첨부 파일:    {row[3]:,}개",
                f"   기간:         {row[4] or '?'} ~ {row[5] or '?'}",
            ]

        if monthly:
            lines += ["", "   최근 월별 메일 수:", "   " + "─" * 20]
            for m, c in monthly:
                bar = "█" * min(c // max(1, (monthly[0][1] // 10 + 1)), 20)
                lines.append(f"   {m}  {bar:<20}  {c:>5}통")

        if senders:
            lines += ["", "   상위 발신자:", "   " + "─" * 36]
            for name, cnt in senders:
                truncated = (name or "")[:24]
                lines.append(f"   {truncated:<24}  {cnt:>5}통")

    except sqlite3.Error as e:
        lines.append(f"   DB 오류: {e}")

    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 인덱스 자동 갱신 (Feature 5)
# ---------------------------------------------------------------------------

def auto_update_index(archive_root_path: Path, cfg: dict) -> None:
    """아카이브에 새 MD 파일이 있으면 인덱스를 자동으로 증분 갱신한다.

    감지 신호 두 가지를 함께 사용한다:

    1. **mtime 비교** — ``archive/**/*.md`` 중 DB 의 mtime 보다 최신인
       파일이 있으면 ``has_new`` 로 본다.
    2. **행수 비교** — ``archive/**/*.md`` 파일 수와 ``messages`` 테이블
       행 수가 다르면 ``count_drift`` 로 본다. ``cp -p`` / ``rsync -a`` /
       백업 복원으로 mtime 이 보존된 파일은 mtime 신호로는 감지되지
       않으므로 행수 차이만으로도 mismatch 를 잡는다 (P5 잔여 보강).

    동작:

    - ``mailview.auto_index = false`` 면 즉시 반환.
    - DB 가 없으면 스킵 (사용자가 ``build-index`` 를 먼저 실행해야 한다).
    - ``has_new`` 가 있고 ``staging.jsonl`` 이 있으면 ``build-index`` 를
      증분 모드로 호출.
    - ``staging.jsonl`` 이 없는데 새 MD 가 보이면 rebuild 권장 메시지.
    - ``count_drift`` 만 있고 mtime 신호가 없으면 (mtime 보존 복원 의심)
      증분으로는 잡히지 않으므로 rebuild 권장 메시지만 출력하고 종료.

    Args:
        archive_root_path: 아카이브 루트 디렉터리 Path.
        cfg:               load_config() 결과.
    """
    if not cfg.get("mailview", {}).get("auto_index", True):
        return

    db = db_path(cfg)
    if not db.exists():
        return  # DB 없음 — 사용자가 직접 build-index 실행 필요

    archive_dir = archive_root_path / "archive"
    if not archive_dir.exists():
        return

    db_mtime = db.stat().st_mtime
    has_new = False
    file_count = 0
    for p in archive_dir.rglob("*.md"):
        file_count += 1
        if p.stat().st_mtime > db_mtime:
            has_new = True

    # 행수 비교 (mtime 보존 복원 케이스 보강).  DB 손상이면 count_drift 검사 skip.
    try:
        conn = sqlite3.connect(str(db))
        try:
            row_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        row_count = file_count

    count_drift = file_count != row_count

    if not has_new and not count_drift:
        return

    # mtime 신호 없이 행수만 어긋나면 staging 도 stale 일 가능성이 높다.
    # 증분 인덱싱은 무의미 — 사용자에게 rebuild 만 권장.
    if count_drift and not has_new:
        click.echo(
            f"[auto-index] 인덱스 행수 mismatch — files={file_count} "
            f"vs DB={row_count} (diff={file_count - row_count:+d}).\n"
            "             cp -p / rsync -a / 백업 복원으로 mtime 이 보존된\n"
            "             파일이 있을 수 있습니다. 권장: build-index --rebuild",
            err=True,
        )
        return

    staging = archive_root_path / "index_staging.jsonl"
    if not staging.exists():
        click.echo(
            "[auto-index] 새 MD 파일이 감지되었으나 staging.jsonl 이 없습니다.\n"
            f"             archive/={file_count} vs DB={row_count} "
            f"(diff={file_count - row_count:+d}).\n"
            "             외부 복사 / 복원 / pst2md --no-index 후라면 인덱스가\n"
            "             누락된 상태일 수 있습니다. 권장: build-index --rebuild",
            err=True,
        )
        return

    build_script = Path(__file__).parent / "build_index.py"
    result = subprocess.run(
        [sys.executable, str(build_script), "--archive", str(archive_root_path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        click.echo(f"[auto-index] 갱신 실패: {result.stderr.strip()}", err=True)
    else:
        out = (result.stdout or "").strip()
        if out:
            # 마지막 줄(요약)만 표시
            click.echo(f"[auto-index] {out.splitlines()[-1]}", err=True)


# ---------------------------------------------------------------------------
# --doctor 진단 커맨드
# ---------------------------------------------------------------------------

def _bin_version(path: Optional[str], flag: str = "--version") -> str:
    """실행 파일의 버전 문자열 한 줄을 반환한다. 실패 시 빈 문자열."""
    if not path:
        return ""
    try:
        result = subprocess.run(
            [path, flag], capture_output=True, text=True, timeout=3,
        )
        out = (result.stdout or result.stderr or "").strip()
        return out.splitlines()[0] if out else ""
    except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
        return ""


def _doctor_index_health(db: Path, root: Path) -> list[str]:
    """인덱스 무결성 진단 — DB 행수 vs 파일수 mismatch + prefix index 보유.

    rebuild 권장 상황을 사용자가 즉시 알 수 있도록 명확한 권장 명령을
    출력한다. 읽기 전용 — 수정 작업은 하지 않는다.

    Args:
        db:   index.sqlite 경로.
        root: 아카이브 루트 (archive/ 의 상위).

    Returns:
        출력할 진단 라인 목록.
    """
    lines: list[str] = []
    try:
        conn = sqlite3.connect(str(db))
        try:
            db_rows = conn.execute(
                "SELECT COUNT(*) FROM messages"
            ).fetchone()[0]
            has_prefix = fts_has_prefix_index(conn)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        lines.append(f"index health   : DB 읽기 실패 — {exc}")
        return lines

    archive_dir = root / "archive"
    md_count = (
        sum(1 for _ in archive_dir.rglob("*.md")) if archive_dir.exists() else 0
    )
    diff = md_count - db_rows
    mark = "✓" if diff == 0 else "⚠"
    lines.append(
        f"index rows     : DB={db_rows:>6}  files={md_count:>6}  diff={diff:+d} {mark}"
    )
    if diff != 0:
        if diff > 0:
            lines.append(
                "                 → MD 가 DB 보다 많음. build-index --rebuild 권장."
            )
        else:
            lines.append(
                "                 → DB 가 MD 보다 많음 (고아 행). build-index --rebuild 권장."
            )
    if not has_prefix:
        lines.append(
            "fts prefix     : ⚠ prefix index 없음 — 한글 부분일치 검색이 약함.\n"
            "                 권장: build-index --rebuild (prefix='2 3 4' 적용)"
        )
    else:
        lines.append("fts prefix     : prefix='2 3 4' ✓")
    return lines


def run_doctor() -> None:
    """mailview 환경 진단 결과를 stdout 에 출력한다.

    점검 항목:
      - 플랫폼 / locale / 터미널 환경변수
      - fzf / glow / bat / awk 경로 및 버전
      - 아카이브 루트 및 SQLite 인덱스 존재 여부
      - 한글 입력 체크리스트 (docs/hangul-input.md 참조)

    파일 쓰기 / 네트워크 호출 없음. 읽기 전용 진단.
    """
    plat = detect_platform()
    click.echo("mailview --doctor")
    click.echo("=" * 60)

    # 플랫폼 정보
    click.echo(f"Platform       : {plat} ({sys.platform})")
    click.echo(f"Python         : {sys.version.split()[0]}")

    # 환경변수
    for key in ("LANG", "LC_CTYPE", "LC_ALL", "TERM"):
        val = os.environ.get(key, "")
        mark = "✓" if val else "-"
        click.echo(f"{key:<15}: {val or '(unset)':<30} {mark}")

    # 바이너리
    click.echo("-" * 60)
    for name in ("fzf", "glow", "mdcat", "bat", "awk"):
        path = shutil.which(name)
        ver  = _bin_version(path) if path else ""
        mark = "✓" if path else "⚠"
        click.echo(f"{name:<15}: {path or '(not found)':<30} {mark}")
        if ver:
            click.echo(f"{'':<15}  {ver}")

    # 아카이브 / 인덱스
    click.echo("-" * 60)
    try:
        cfg  = load_config()
        root = archive_root(cfg)
        db   = db_path(cfg)
    except (OSError, ValueError, KeyError) as exc:
        click.echo(f"archive_root   : 설정 로드 실패 — {exc}")
    else:
        click.echo(f"archive_root   : {root}  {'✓' if root.exists() else '⚠ (없음)'}")
        if db.exists():
            size_mb = db.stat().st_size / (1024 * 1024)
            click.echo(f"index.sqlite   : {db} ({size_mb:.1f} MB) ✓")
            # 인덱스 무결성 — DB 행수 vs archive/ 의 .md 파일 수 (P5)
            for line in _doctor_index_health(db, root):
                click.echo(line)
        else:
            click.echo(f"index.sqlite   : {db} ⚠ (없음 — build-index 실행 필요)")

    # 한글 입력 안내
    click.echo("-" * 60)
    click.echo("한글 입력 체크리스트:")
    click.echo("  - 터미널 폰트에 CJK 글리프 포함 (예: Noto Sans CJK, D2Coding)")
    click.echo("  - IME 조합중 문자가 정상 전송되는지 (Windows MS-IME / WSL fcitx5-hangul)")
    click.echo("  - TERM 이 xterm-256color / tmux-256color 계열인지")
    click.echo("  자세한 내용: docs/hangul-input.md")


# ---------------------------------------------------------------------------
# CLI 커맨드
# ---------------------------------------------------------------------------

def _load_cfg_db(archive: str) -> tuple[dict, Path]:
    """공통 패턴: load_config + --archive 오버라이드 + db_path 계산.

    main() 의 서브커맨드 분기에서 7번 반복되던 보일러플레이트를 통합한다.
    테스트 patch 가 작동하도록 load_config/db_path 를 모듈 레벨로 참조한다.
    """
    cfg = load_config()
    if archive:
        cfg["archive"]["root"] = archive
    return cfg, db_path(cfg)


def _build_fzf_exec_commands(
    plat: str, py: str, script_path: str, archive_path: str,
    editor: str, bat_path: Optional[str], fzf_path: str,
    iso_dates: dict[str, str], fzf_colors: str,
) -> dict[str, Optional[str]]:
    """fzf --bind execute/reload 에 전달할 쉘 명령어 문자열들을 조립한다.

    plat 에 따라 quote char (Linux: ', Windows: ") 과 bulk 파이프 / pager 만 다르고,
    나머지 12 개 명령어는 동일한 템플릿을 공유한다.

    Args:
        plat:         "linux" | "wsl" | "windows".
        py:           sys.executable.
        script_path:  mailview.py 절대 경로.
        archive_path: 아카이브 루트.
        editor:       $EDITOR 또는 플랫폼 기본.
        bat_path:     bat 경로 (없으면 None → bat_cmd=None).
        fzf_path:     fzf 실행 파일 경로.
        iso_dates:    {"today": "2026-01-01", "week": "...", "month": "...", "year": "..."}.
        fzf_colors:   fzf --color 인자 값.

    Returns:
        각 바인딩 이름 → 쉘 명령어 문자열 dict. ``bat_cmd`` 는 bat_path 가 없으면 None.
    """
    q = '"' if plat == "windows" else "'"
    bulk_pipe = "echo {+2} |" if plat == "windows" else "printf '%s\\n' {+2} |"
    pager_name = "more" if plat == "windows" else "less"
    base = f"{q}{py}{q} {q}{script_path}{q}"          # python + script 경로 (인용)
    arc  = f"--archive {q}{archive_path}{q}"          # 공통 --archive 인자

    def fzf_input(extra: str = "") -> str:
        tail = f" {extra}" if extra else ""
        return f"{base} --fzf-input{tail} {arc}"

    def popup(inner: str, label: str) -> str:
        """sub-fzf --disabled 팝업 (도움말 / 통계)."""
        return (
            f"{base} {inner} "
            f"| {q}{fzf_path}{q} --disabled --no-info "
            f"--border=rounded --border-label={q} {label} {q} "
            f"--color {q}{fzf_colors}{q}"
        )

    return {
        "open_att_cmd":     f"{base} --open-att {q}{{2}}{q}",
        "open_url_cmd":     f"{base} --open-url {q}{{2}}{q}",
        "delete_cmd":       f"{base} --delete-msg {q}{{2}}{q} {arc}",
        "bulk_del_cmd":     f"{bulk_pipe} {base} --fzf-bulk-delete {arc}",
        "editor_cmd":       f"{q}{editor}{q} {q}{{2}}{q}",
        "bat_cmd":          f"{q}{bat_path}{q} --style=full {q}{{2}}{q}" if bat_path else None,
        "pager_cmd":        f"{pager_name} {q}{{2}}{q}",
        "body_reload":      fzf_input(f"--body {q}{{q}}{q}"),
        "subject_reload":   fzf_input(f"--subject {q}{{q}}{q}"),
        "reset_reload":     fzf_input(),
        "sort_from_reload": fzf_input("--sort from"),
        "sort_subj_reload": fzf_input("--sort subject"),
        "today_reload":     fzf_input(f"--after {q}{iso_dates['today']}{q}"),
        "week_reload":      fzf_input(f"--after {q}{iso_dates['week']}{q}"),
        "month_reload":     fzf_input(f"--after {q}{iso_dates['month']}{q}"),
        "year_reload":      fzf_input(f"--after {q}{iso_dates['year']}{q}"),
        "help_popup":       popup("--show-help", "도움말"),
        "tag_cmd":          f"{base} --tag-msg {q}{{2}}{q} {arc}",
        "stats_popup":      popup(f"--show-stats {arc}", "통계"),
    }


@click.command(
    name="mailview",
    epilog=(
        "예시:\n"
        "\n"
        "  mailview                              모든 메일 대상 fzf 뷰어\n"
        "  mailview '계약'                       초기 쿼리로 '계약' 입력\n"
        "  mailview --from alice --after 2024    발신자+기간 필터\n"
        "  mailview --thread t_abc123de          스레드 전체 보기\n"
        "  mailview --doctor                     환경 진단 (plat/locale/바이너리)\n"
        "  mailview --dedupe --dry-run           중복 메일 감지 (삭제 없음)\n"
        "\n"
        "fzf 내부 키:\n"
        "  Enter  전체 열람 (mdcat 또는 glow — preview_viewer 설정)\n"
        "  Esc    쿼리 · 필터 초기화 (Ctrl-R 동일)\n"
        "  :q+Enter  종료 (Linux/WSL, vim 스타일)\n"
        "  Ctrl-S  제목검색(DB)   Ctrl-B  본문검색(DB)\n"
        "  Alt-F/T  폴더/태그 필터 (sub-fzf)\n"
        "  Ctrl-A/U   첨부/URL 열기    Ctrl-X 삭제\n"
        "\n"
        "한글 입력 문제: docs/hangul-input.md 참고."
    ),
)
@click.argument("query", default="")
@click.option("--from",    "from_filter", default="", metavar="NAME",
              help="발신자 필터 (부분 일치).")
@click.option("--after",   default="", metavar="YYYY-MM-DD",
              help="이 날짜 이후 메일만.")
@click.option("--before",  default="", metavar="YYYY-MM-DD",
              help="이 날짜 이전 메일만.")
@click.option("--folder",  default="", metavar="PATH",
              help="폴더 경로 필터 (부분 일치).")
@click.option("--thread",  default="", metavar="ID",
              help="스레드 ID 정확 일치 (예: t_abc123de).")
@click.option("--body",    "body_filter", default="", metavar="QUERY",
              help="본문 전용 검색 (FTS5 body 컬럼).")
@click.option("--subject", "subject_filter", default="", metavar="QUERY",
              help="제목 전용 검색 (FTS5 subject 컬럼).")
@click.option("--archive", default="", metavar="DIR",
              help="아카이브 루트 (기본: config archive.root).")
@click.option("--dedupe",  is_flag=True,
              help="중복 메일 감지 및 정리 (fzf 미표시).")
@click.option("--dry-run", "dry_run", is_flag=True,
              help="--dedupe 와 함께: 삭제 없이 목록만 출력.")
@click.option("--doctor",  is_flag=True,
              help="환경 진단 (플랫폼/locale/fzf·glow·mdcat/아카이브) 후 종료.")
# ── 내부 히든 모드 (fzf execute/reload 에서 호출) ──────────────────────
@click.option("--open-att",   "_open_att",    default="", hidden=True,
              help="내부용: 지정 MD 파일의 첨부 파일 열기")
@click.option("--open-url",   "_open_url",    default="", hidden=True,
              help="내부용: 지정 MD 파일의 URL 추출 후 선택 열기")
@click.option("--delete-msg", "_delete_msg",  default="", hidden=True,
              help="내부용: 지정 MD 파일 삭제")
@click.option("--fzf-input",       "_fzf_input",       is_flag=True, hidden=True,
              help="내부용: fzf reload 용 레이블\\t경로 출력")
@click.option("--show-help",       "_show_help",       is_flag=True, hidden=True,
              help="내부용: 키 바인딩 도움말 출력")
@click.option("--fzf-bulk-delete", "_fzf_bulk_delete", is_flag=True, hidden=True,
              help="내부용: stdin 에서 경로를 읽어 일괄 삭제")
@click.option("--list-folders",    "_list_folders",    is_flag=True, hidden=True,
              help="내부용: 폴더 목록 출력 (sub-fzf 용)")
@click.option("--get-thread",      "_get_thread",      default="", hidden=True,
              help="내부용: 지정 MD 파일의 스레드 ID 출력")
@click.option("--tag-msg",         "_tag_msg",         default="", hidden=True,
              help="내부용: 지정 MD 파일 태그 수정")
@click.option("--list-tags",       "_list_tags",       is_flag=True, hidden=True,
              help="내부용: 태그 목록 출력 (sub-fzf 용)")
@click.option("--tag-filter",      default="", hidden=True,
              help="내부용: 태그 필터 (fzf-input 에서 사용)")
@click.option("--show-stats",      "_show_stats",      is_flag=True, hidden=True,
              help="내부용: 아카이브 통계 출력 (Alt-I 팝업용)")
@click.option("--thread-tree",     "_thread_tree",     default="", hidden=True,
              help="내부용: 지정 MD 파일의 스레드 트리 출력")
@click.option("--sort", default="date", hidden=True,
              help="내부용: fzf-input 정렬 기준 (date|from|subject)")
def main(
    query, from_filter, after, before, folder, thread,
    body_filter, subject_filter, archive, dedupe, dry_run, doctor,
    _open_att, _open_url, _delete_msg,
    _fzf_input, _show_help, _fzf_bulk_delete, _list_folders, _get_thread,
    _tag_msg, _list_tags, tag_filter, _show_stats, _thread_tree, sort,
):
    """fzf + glow 인터랙티브 메일 뷰어."""

    # ── --doctor 진단 모드 ───────────────────────────────────────────────
    if doctor:
        run_doctor()
        return

    # ── --dedupe 중복 감지 모드 ──────────────────────────────────────────
    if dedupe:
        handle_dedupe(archive, dry_run)
        return

    # ── Ctrl-A 첨부 열기 모드 ────────────────────────────────────────────
    if _open_att:
        handle_open_attachments(_open_att)
        return

    # ── Ctrl-U URL 열기 모드 ─────────────────────────────────────────────
    if _open_url:
        handle_open_url(_open_url)
        return

    # ── Ctrl-D 메일 삭제 모드 ────────────────────────────────────────────
    if _delete_msg:
        handle_delete_message(_delete_msg, archive)
        return

    # ── 스레드 트리 출력 모드 (Ctrl-T 확장용) ────────────────────────────
    if _thread_tree:
        _cfg, db = _load_cfg_db(archive)
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT thread FROM messages WHERE path = ? LIMIT 1", (_thread_tree,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            tree = build_thread_tree(db, row[0])
            for line in format_thread_tree(tree):
                click.echo(line)
        else:
            click.echo("스레드 없음.")
        return

    # ── 통계 출력 모드 (Alt-I → fzf --disabled 팝업) ────────────────────
    if _show_stats:
        cfg, db = _load_cfg_db(archive)
        for line in format_stats_for_display(db, archive_root(cfg)):
            click.echo(line)
        return

    # ── 도움말 출력 모드 (? 키 → fzf --disabled 팝업) ───────────────────
    if _show_help:
        for line in _HELP_LINES:
            click.echo(line)
        return

    # ── 일괄 삭제 모드 (Ctrl-X) ─────────────────────────────────────────
    if _fzf_bulk_delete:
        handle_bulk_delete(archive)
        return

    # ── 폴더 목록 출력 모드 (Ctrl-F 서브 fzf 용) ────────────────────────
    if _list_folders:
        _cfg, db = _load_cfg_db(archive)
        for folder_name in get_folder_list(db):
            click.echo(folder_name)
        return

    # ── 태그 수정 모드 (Ctrl-K) ──────────────────────────────────────────
    if _tag_msg:
        handle_tag_message(_tag_msg, archive)
        return

    # ── 태그 목록 출력 모드 (Alt-T 서브 fzf 용) ─────────────────────────
    if _list_tags:
        _cfg, db = _load_cfg_db(archive)
        for tag_name in get_tag_list(db):
            click.echo(tag_name)
        return

    # ── 스레드 ID 출력 모드 (Ctrl-T 서브 fzf 용) ────────────────────────
    if _get_thread:
        _cfg, db = _load_cfg_db(archive)
        conn = sqlite3.connect(str(db))
        row = conn.execute(
            "SELECT thread FROM messages WHERE path = ? LIMIT 1", (_get_thread,)
        ).fetchone()
        conn.close()
        if row and row[0]:
            click.echo(row[0])
        return

    # ── fzf reload 출력 모드 (Ctrl-B / Ctrl-S / Ctrl-F / Ctrl-T / Ctrl-R / Alt-* ) ──
    if _fzf_input:
        cfg, db = _load_cfg_db(archive)
        archive_arg = ["--archive", cfg["archive"]["root"]] if archive else []
        if tag_filter:
            # 태그 필터는 직접 DB 쿼리 (mailgrep 경유 불필요)
            paths = get_paths_by_tag(db, tag_filter)
        elif body_filter or subject_filter or folder or thread:
            extra: list[str] = []
            if body_filter:    extra += ["--body",    body_filter]
            if subject_filter: extra += ["--subject", subject_filter]
            if folder:         extra += ["--folder",  folder]
            if thread:         extra += ["--thread",  thread]
            paths = get_paths_from_query(archive_arg + extra)
        else:
            paths = get_recent_paths(db, after=after, sort=sort)
        _print_fzf_lines(paths, db)
        return

    # ── 일반 뷰어 모드 ───────────────────────────────────────────────────
    cfg, db = _load_cfg_db(archive)
    if not db.exists():
        click.echo(f"오류: 인덱스 없음 → {db}", err=True)
        sys.exit(1)

    # ── 인덱스 자동 갱신 (새 MD 파일이 있을 때만) ────────────────────────
    auto_update_index(archive_root(cfg), cfg)

    plat     = detect_platform()
    fzf_hint  = "winget install fzf"                if plat == "windows" else "sudo apt install fzf"
    glow_hint = "winget install charmbracelet.glow" if plat == "windows" else "sudo snap install glow"

    fzf_path  = _require_tool("fzf",  cfg, fzf_hint)
    glow_path = _require_tool("glow", cfg, glow_hint)
    bat_path  = _check_tool("bat", cfg)

    # ── 경로 목록 수집 ────────────────────────────────────────────────────
    if query or from_filter or after or before or folder or thread or body_filter or subject_filter:
        extra: list[str] = []
        if from_filter:    extra += ["--from",    from_filter]
        if after:          extra += ["--after",   after]
        if before:         extra += ["--before",  before]
        if folder:         extra += ["--folder",  folder]
        if thread:         extra += ["--thread",  thread]
        if body_filter:    extra += ["--body",    body_filter]
        if subject_filter: extra += ["--subject", subject_filter]
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
        cfg_viewer     = cfg.get("mailview", {}).get("preview_viewer", "mdcat").lower()
        mdcat_path: Optional[str] = None
        if cfg_viewer == "mdcat":
            mdcat_path = _check_tool("mdcat", cfg)
            if not mdcat_path:
                click.echo(
                    "경고: preview_viewer='mdcat' 인데 mdcat 바이너리를 찾을 수 없음 → glow 로 폴백",
                    err=True,
                )
                cfg_viewer = "glow"
        preview_cmd  = build_fzf_preview_cmd(
            glow_path, bat_path, cfg_glow_style,
            mdcat_path=mdcat_path, viewer=cfg_viewer,
        )
        _glow_style  = resolve_glow_style(cfg_glow_style)
        editor       = get_editor()
        script_path  = str(Path(__file__).resolve())
        py           = sys.executable
        archive_path = str(cfg["archive"]["root"])

        # 날짜 필터 프리셋 ISO 문자열 계산
        _today  = date.today()
        _today_iso  = _today.isoformat()
        _week_iso   = (_today - timedelta(days=7)).isoformat()
        _month_iso  = (_today - timedelta(days=30)).isoformat()
        _year_iso   = (_today - timedelta(days=365)).isoformat()

        # ── execute()/reload() 바인딩 경로 인용부호 ───────────────────────
        # fzf 는 execute("cmd {2}") 에서 {2} 를 그대로 치환한다.
        # 공백 포함 경로를 안전하게 전달하려면 플랫폼별 인용부호가 필요하다.
        #   Linux  (sh -c)   : 작은따옴표  '{2}'
        #   Windows (cmd /c) : 큰따옴표   "{2}"
        # 자세한 조립 규칙은 _build_fzf_exec_commands 참고.
        _FZF_COLORS = "dark"
        q = '"' if plat == "windows" else "'"
        _cmds = _build_fzf_exec_commands(
            plat, py, script_path, archive_path, editor, bat_path, fzf_path,
            iso_dates={
                "today": _today_iso, "week":  _week_iso,
                "month": _month_iso, "year":  _year_iso,
            },
            fzf_colors=_FZF_COLORS,
        )
        open_att_cmd     = _cmds["open_att_cmd"]
        open_url_cmd     = _cmds["open_url_cmd"]
        delete_cmd       = _cmds["delete_cmd"]
        bulk_del_cmd     = _cmds["bulk_del_cmd"]
        editor_cmd       = _cmds["editor_cmd"]
        bat_cmd          = _cmds["bat_cmd"]
        pager_cmd        = _cmds["pager_cmd"]
        body_reload      = _cmds["body_reload"]
        subject_reload   = _cmds["subject_reload"]
        reset_reload     = _cmds["reset_reload"]
        sort_from_reload = _cmds["sort_from_reload"]
        sort_subj_reload = _cmds["sort_subj_reload"]
        today_reload     = _cmds["today_reload"]
        week_reload      = _cmds["week_reload"]
        month_reload     = _cmds["month_reload"]
        year_reload      = _cmds["year_reload"]
        help_popup       = _cmds["help_popup"]
        tag_cmd          = _cmds["tag_cmd"]
        stats_popup      = _cmds["stats_popup"]

        # ── 3단계: 검색 하이라이트 / 폴더 브라우저 / 스레드 뷰 ─────────────
        # Linux/WSL 전용 (transform action: fzf 0.47+ 필요)
        if plat in ("linux", "wsl"):
            # Ctrl-B 활성 시: grep 으로 {q} 매칭 라인 강조 (fallback: glow)
            search_preview_cmd = (
                f"grep --color=always -i -n -A2 -B2 '{{q}}' '{{2}}' 2>/dev/null"
                f" || '{glow_path}' -s '{_glow_style}' '{{2}}' 2>/dev/null"
            )
            # Ctrl-F: 폴더 브라우저 → 선택 폴더로 목록 reload
            folder_transform = (
                f"FOLDER=$({q}{py}{q} {q}{script_path}{q} --list-folders"
                f" --archive {q}{archive_path}{q} | fzf --prompt={q}폴더> {q}"
                f" --header={q}Enter:선택  ESC:취소{q});"
                f" [ -n \"$FOLDER\" ] && printf"
                f" \"change-prompt(%s> )+reload({q}{py}{q} {q}{script_path}{q}"
                f" --fzf-input --folder '%s' --archive {q}{archive_path}{q})\""
                f" \"$FOLDER\" \"$FOLDER\""
            )
            # Ctrl-T: 스레드 트리 팝업 표시 후 스레드 필터로 reload
            thread_transform = (
                f"THREAD=$({q}{py}{q} {q}{script_path}{q} --get-thread {q}{{2}}{q}"
                f" --archive {q}{archive_path}{q});"
                f" [ -n \"$THREAD\" ] &&"
                f" {q}{py}{q} {q}{script_path}{q} --thread-tree {q}{{2}}{q}"
                f" --archive {q}{archive_path}{q}"
                f" | {q}{fzf_path}{q} --disabled --no-info"
                f" --border=rounded --border-label=' 스레드 트리 '"
                f" --color {q}{_FZF_COLORS}{q};"
                f" [ -n \"$THREAD\" ] && printf"
                f" \"change-prompt(스레드> )+reload({q}{py}{q} {q}{script_path}{q}"
                f" --fzf-input --thread '%s' --archive {q}{archive_path}{q})\""
                f" \"$THREAD\""
            )
            # Alt-T: 태그 브라우저 → 선택 태그로 목록 reload
            tag_transform = (
                f"TAG=$({q}{py}{q} {q}{script_path}{q} --list-tags"
                f" --archive {q}{archive_path}{q} | fzf --prompt={q}태그> {q}"
                f" --header={q}Enter:선택  ESC:취소{q});"
                f" [ -n \"$TAG\" ] && printf"
                f" \"change-prompt(태그:%%s> )+reload({q}{py}{q} {q}{script_path}{q}"
                f" --fzf-input --tag-filter '%%s' --archive {q}{archive_path}{q})\""
                f" \"$TAG\" \"$TAG\""
            )
            # change: 키 입력마다 prompt 를 보고 본문/제목 모드면 DB reload.
            # 일반 모드(검색>) 는 fzf 로컬 필터링만 — transform 은 빈 문자열 출력.
            # 빈 쿼리는 reload 생략(mailgrep 인수 없음 에러 방지).
            mode_transform = (
                'case "$FZF_PROMPT" in '
                '"본문검색> ") '
                '[ -n "$FZF_QUERY" ] && '
                f'echo \"reload({body_reload})\" ;; '
                '"제목검색> ") '
                '[ -n "$FZF_QUERY" ] && '
                f'echo \"reload({subject_reload})\" ;; '
                'esac'
            )
        else:
            # Windows: 검색 하이라이트 없이 glow 미리보기 유지
            search_preview_cmd = preview_cmd
            folder_transform   = ""
            thread_transform   = ""
            tag_transform      = ""
            mode_transform     = ""

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
            # ── 멀티 선택 ────────────────────────────────────────────────
            "--multi",
            # ── 목록 구성 ────────────────────────────────────────────────
            "--delimiter", "\t",
            "--with-nth", "1",
            "--header-lines", "1",
            "--header", "Enter:열람  Tab:선택  Ctrl-S:제목검색  Ctrl-B:본문검색  Ctrl-F:폴더  Esc:필터초기화  :q+Enter:종료  ?:도움말",
            # ── 미리보기 ─────────────────────────────────────────────────
            "--preview", preview_cmd,
            "--preview-window", "right:48%:border-left:wrap",
            "--preview-label", " 미리보기 ",
            # ── 키 바인딩 ────────────────────────────────────────────────
            "--bind", "focus:change-preview-label( {2} )",
            "--bind", "tab:toggle+down",
            "--bind", f"ctrl-o:execute({editor_cmd})+abort",
            "--bind", f"ctrl-a:execute({open_att_cmd})",
            "--bind", f"ctrl-u:execute({open_url_cmd})",
            "--bind", f"ctrl-k:execute({tag_cmd})+reload({reset_reload})",
            "--bind", f"ctrl-d:execute({delete_cmd})+reload({reset_reload})",
            "--bind", f"ctrl-x:execute({bulk_del_cmd})+reload({reset_reload})",
            # Ctrl-B 본문검색: prompt 변경 + clear-query 로 change 이벤트 트리거 →
            # mode_transform 이 prompt 보고 매 입력마다 DB reload (Linux/WSL).
            # Windows 는 transform 미지원 — 1회성 reload + clear-query 동작 유지.
            "--bind", (
                f"ctrl-b:change-prompt(본문검색> )"
                f"+reload({body_reload})"
                f"+clear-query"
                f"+change-preview({search_preview_cmd})"
            ),
            # Ctrl-S 제목검색: body 와 동일한 패턴, FTS5 subject 컬럼 한정.
            "--bind", (
                f"ctrl-s:change-prompt(제목검색> )"
                f"+reload({subject_reload})"
                f"+clear-query"
                f"+change-preview({preview_cmd})"
            ),
            "--bind", (
                f"ctrl-r:change-prompt(검색> )"
                f"+reload({reset_reload})"
                f"+clear-query"
                f"+change-preview({preview_cmd})"
            ),
            # Esc: abort 대신 Ctrl-R 과 동일한 필터 초기화 (메인 뷰어 한정)
            "--bind", (
                f"esc:change-prompt(검색> )"
                f"+reload({reset_reload})"
                f"+clear-query"
                f"+change-preview({preview_cmd})"
            ),
            "--bind", (
                f"alt-s:change-prompt(제목순> )"
                f"+reload({sort_subj_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"alt-f:change-prompt(발신자순> )"
                f"+reload({sort_from_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"alt-1:change-prompt(오늘> )"
                f"+reload({today_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"alt-2:change-prompt(7일> )"
                f"+reload({week_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"alt-3:change-prompt(30일> )"
                f"+reload({month_reload})"
                f"+clear-query"
            ),
            "--bind", (
                f"alt-4:change-prompt(1년> )"
                f"+reload({year_reload})"
                f"+clear-query"
            ),
            "--bind", f"alt-i:execute({stats_popup})",
            "--bind", f"?:execute({help_popup})",
        ]

        # Ctrl-F / Ctrl-T / Alt-T: transform action 필요 (fzf 0.47+, Linux/WSL 전용)
        if folder_transform:
            fzf_cmd += ["--bind", f"ctrl-f:transform({folder_transform})"]
        if thread_transform:
            fzf_cmd += ["--bind", f"ctrl-t:transform({thread_transform})"]
        if tag_transform:
            fzf_cmd += ["--bind", f"alt-t:transform({tag_transform})"]
        # change: 본문/제목 모드에서 입력 변경마다 DB reload (P1/P2 — Linux/WSL).
        if mode_transform:
            fzf_cmd += ["--bind", f"change:transform({mode_transform})"]

        if bat_cmd:
            fzf_cmd += ["--bind", f"ctrl-p:execute({bat_cmd})+abort"]
        else:
            fzf_cmd += ["--bind", f"ctrl-p:execute({pager_cmd})+abort"]

        # :q / :quit / :x + Enter → 종료 (vim 스타일).
        # fzf transform 은 자식 셸 출력을 fzf 액션으로 해석. bash 정규식 의존 → Linux/WSL 전용.
        if plat in ("linux", "wsl"):
            fzf_cmd += [
                "--bind",
                'enter:transform('
                r'[[ "$FZF_QUERY" =~ ^:(q|quit|x)$ ]]'
                ' && echo abort || echo accept'
                ')',
            ]

        with open(tmp_file.name, encoding="utf-8") as stdin_fh:
            result = subprocess.run(
                fzf_cmd,
                stdin=stdin_fh,
                stdout=subprocess.PIPE,   # 선택 항목 캡처 (fzf TUI 는 /dev/tty 직접 출력)
                text=True,
                encoding="utf-8",
            )

        # Enter 로 선택된 메일 렌더링 — config mailview.preview_viewer 에 따라
        # mdcat(기본, 이미지 인라인) 또는 glow 로 분기.
        if result.returncode == 0:
            selected_line = (result.stdout or "").strip()
            if "\t" in selected_line:
                selected_path = selected_line.split("\t", 1)[1].strip()
                if selected_path and Path(selected_path).exists():
                    glow_style = resolve_glow_style(cfg_glow_style)
                    viewer_cmd = build_full_viewer_cmd(
                        selected_path,
                        glow_path,
                        glow_style,
                        mdcat_path=mdcat_path,
                        viewer=cfg_viewer,
                    )
                    subprocess.run(viewer_cmd)

    finally:
        Path(tmp_file.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
