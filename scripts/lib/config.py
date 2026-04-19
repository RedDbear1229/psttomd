"""
설정 로더 + 플랫폼 감지

설정 파일 위치: ~/.pst2md/config.toml
환경변수 MAIL_ARCHIVE 가 설정되어 있으면 archive.root를 오버라이드한다.

사용 예:
    from lib.config import load_config, archive_root, detect_platform

    cfg  = load_config()
    root = archive_root(cfg)   # Path 객체
    plat = detect_platform()   # 'windows' | 'wsl' | 'linux'
"""
from __future__ import annotations

import copy
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# tomllib: Python 3.11+ 내장, 이전 버전은 tomli 패키지로 fallback
# ---------------------------------------------------------------------------
try:
    import tomllib          # type: ignore[import]   # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib   # type: ignore[import]  # pip install tomli
    except ImportError:
        tomllib = None            # type: ignore[assignment]


# ---------------------------------------------------------------------------
# 플랫폼 감지
# ---------------------------------------------------------------------------

def detect_platform() -> str:
    """현재 실행 환경을 반환한다.

    반환값:
        'windows' — Windows Native (sys.platform == 'win32')
        'wsl'     — Windows Subsystem for Linux (/proc/version에 'microsoft' 포함)
        'linux'   — 그 외 Linux/macOS
    """
    if sys.platform == "win32":
        return "windows"
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8").lower()
        if "microsoft" in version_text:
            return "wsl"
    except OSError:
        # /proc/version이 없는 환경(macOS 등)은 linux로 처리
        pass
    return "linux"


# 모듈 로드 시 한 번만 감지하여 상수로 보관
PLATFORM: str = detect_platform()


# ---------------------------------------------------------------------------
# 기본 설정값
# ---------------------------------------------------------------------------

#: config.toml이 없거나 특정 키가 누락될 때 사용할 기본값
DEFAULT_CONFIG: dict[str, Any] = {
    "archive": {
        # 아카이브 루트 디렉터리 (~ 확장 지원)
        "root": str(Path.home() / "mail-archive"),
        # 추가 아카이브 루트 목록 (다중 아카이브 지원)
        # 예: roots = ["~/mail-archive", "~/work-archive"]
        "roots": [],
    },
    # PST 파서 백엔드
    # auto     — 플랫폼에 따라 자동 선택
    # pypff    — libpff-python (Linux/WSL)
    # readpst  — readpst CLI → EML (WSL/Linux)
    # win32com — Outlook COM API (Windows)
    "pst_backend": "auto",
    "tools": {
        "fzf":     "fzf",
        "glow":    "glow",
        "bat":     "bat",
        "sqlite3": "sqlite3",
        "rg":      "rg",
    },
    "win32com": {
        # 빈 문자열 = Outlook 기본 프로파일 사용
        "outlook_profile": "",
    },
    "mailview": {
        # fzf 미리보기에서 glow 가 사용할 스타일.
        # 기본 dark. 변경 시: dark | light | dracula | tokyo-night | notty
        # 또는 커스텀 JSON 절대 경로: "/절대/경로/my-theme.json"
        "glow_style": "dark",
        # True 이면 mailview 시작 시 새 MD 파일이 있을 때 인덱스를 자동 갱신한다.
        "auto_index": True,
    },
    "llm": {
        # LLM provider: openai | anthropic | ollama
        "provider": "openai",
        # API endpoint (ollama 의 경우 http://localhost:11434)
        "endpoint": "https://api.openai.com/v1",
        # API 토큰 (env LLM_TOKEN 이 우선)
        "token": "",
        # 사용할 모델 이름
        "model": "gpt-4o-mini",
        # 요청 타임아웃 (초)
        "timeout": 60,
        # 실패 시 최대 재시도 횟수
        "max_retries": 3,
        # 동시 LLM 호출 수
        "concurrency": 4,
        "scope": {
            # 요약 최대 글자 수
            "summary_max_chars": 300,
            # 태그 최대 개수
            "tag_max_count": 5,
            # 관련 문서 최대 개수
            "related_max_count": 5,
            # 본문 길이가 이 값 미만이면 enrichment skip
            "skip_body_shorter_than": 100,
            # enrichment skip 할 폴더 이름 목록
            "skip_folders": ["Junk", "Spam", "Deleted Items"],
        },
    },
}


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """두 dict를 재귀적으로 병합한다. override 값이 우선한다.

    중첩 dict는 키 단위로 병합하고, 그 외 타입은 override 값으로 교체한다.

    Args:
        base:     기본 설정 dict
        override: 덮어쓸 설정 dict

    Returns:
        병합된 새 dict (base 와 override 를 변경하지 않음)
    """
    result: dict[str, Any] = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            # 양쪽 모두 dict이면 재귀 병합
            result[key] = _deep_merge(result[key], value)
        else:
            # 스칼라·리스트는 override로 교체
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def load_config() -> dict[str, Any]:
    """설정 파일을 읽어 기본값과 병합한 dict를 반환한다.

    우선순위 (높은 순):
        1. 환경변수 MAIL_ARCHIVE  → archive.root 덮어씀
        2. ~/.pst2md/config.toml
        3. DEFAULT_CONFIG

    Returns:
        병합된 설정 dict. 반환값을 직접 수정해도 DEFAULT_CONFIG에 영향 없음.
    """
    cfg: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)

    config_path = Path.home() / ".pst2md" / "config.toml"
    if config_path.exists():
        if tomllib is None:
            # tomllib/tomli 미설치 시 경고만 출력하고 기본값으로 계속 진행
            print(
                "경고: tomllib/tomli 가 없어 config.toml 을 읽지 못합니다.\n"
                "  설치: pip install tomli",
                file=sys.stderr,
            )
        else:
            with open(config_path, "rb") as fh:
                user_cfg: dict[str, Any] = tomllib.load(fh)
            cfg = _deep_merge(cfg, user_cfg)

    # 환경변수 오버라이드: MAIL_ARCHIVE 가 있으면 archive.root 를 대체
    env_archive = os.environ.get("MAIL_ARCHIVE", "").strip()
    if env_archive:
        cfg["archive"]["root"] = env_archive

    # ~ 를 실제 홈 디렉터리로 확장
    cfg["archive"]["root"] = str(Path(cfg["archive"]["root"]).expanduser())

    return cfg


def archive_root(cfg: dict[str, Any] | None = None) -> Path:
    """아카이브 루트 디렉터리를 Path 객체로 반환한다.

    Args:
        cfg: load_config() 결과. None 이면 내부에서 새로 로드한다.

    Returns:
        아카이브 루트 Path (존재 여부는 검사하지 않음)
    """
    if cfg is None:
        cfg = load_config()
    return Path(cfg["archive"]["root"])


def db_path(cfg: dict[str, Any] | None = None) -> Path:
    """SQLite FTS5 인덱스 파일 경로를 반환한다.

    Args:
        cfg: load_config() 결과. None 이면 내부에서 새로 로드한다.

    Returns:
        <archive_root>/index.sqlite 경로
    """
    return archive_root(cfg) / "index.sqlite"


def archive_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    """모든 아카이브 루트 디렉터리 목록을 반환한다.

    archive.root (단일 기본값) 와 archive.roots (추가 목록) 를 합산한다.
    중복 경로는 제거한다.

    Args:
        cfg: load_config() 결과. None 이면 내부에서 새로 로드한다.

    Returns:
        중복 없는 아카이브 루트 Path 목록. 항상 1개 이상.
    """
    if cfg is None:
        cfg = load_config()
    primary = archive_root(cfg)
    extras = [
        Path(r).expanduser()
        for r in cfg.get("archive", {}).get("roots", [])
    ]
    seen: set[Path] = set()
    result: list[Path] = []
    for p in [primary] + extras:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def save_archive_root(path: str | Path) -> Path:
    """config.toml 의 archive.root 값을 업데이트한다.

    파일이 없으면 init_config_file() 로 먼저 생성한 뒤 path 를 기록한다.
    파일이 있으면 ``root = "..."`` 줄만 정규식으로 교체하여 나머지 주석을 보존한다.

    Args:
        path: 저장할 아카이브 루트 경로.
              Windows 역슬래시는 슬래시로 변환하여 TOML 호환성을 유지한다.

    Returns:
        업데이트된 config.toml 파일 경로.
    """
    config_file = Path.home() / ".pst2md" / "config.toml"
    normalized = str(path).replace("\\", "/")

    if not config_file.exists():
        # 파일이 없으면 기본 템플릿 생성 후 경로 반영
        init_config_file(archive=normalized)
        return config_file

    original = config_file.read_text(encoding="utf-8")

    # [archive] 섹션 내의 root = "..." 줄을 교체한다.
    # 섹션 전환([xxx])이 나타나기 전까지만 치환하도록 두 단계로 처리한다.
    archive_section_re = re.compile(
        r"(\[archive\].*?)(\broot\s*=\s*\"[^\"]*\")",
        re.DOTALL,
    )
    new_line = f'root = "{normalized}"'
    updated, count = archive_section_re.subn(
        lambda m: m.group(1) + new_line,
        original,
        count=1,
    )

    if count == 0:
        # [archive] 섹션 자체가 없으면 파일 끝에 추가
        updated = original.rstrip() + f"\n\n[archive]\n{new_line}\n"

    config_file.write_text(updated, encoding="utf-8")
    return config_file


def init_config_file(
    archive: str = "",
    backend: str = "",
    force: bool = False,
) -> Path:
    """~/.pst2md/config.toml 을 생성한다.

    이미 파일이 존재하면 아무 작업도 하지 않는다(force=True 시 덮어씀).
    설치 스크립트(install_linux.sh, install_windows.ps1)에서 호출한다.

    Args:
        archive: 아카이브 루트 경로. 비어 있으면 플랫폼 기본값 사용.
        backend: PST 백엔드 이름. 비어 있으면 플랫폼 기본값 사용.
        force:   True 이면 기존 파일을 덮어씀.

    Returns:
        생성된(또는 기존) config.toml 경로
    """
    config_dir = Path.home() / ".pst2md"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"

    if config_file.exists() and not force:
        return config_file

    plat = detect_platform()

    # 아카이브 경로 기본값: Windows는 역슬래시를 슬래시로 변환(TOML 호환)
    if not archive:
        raw = str(Path.home() / "mail-archive")
        archive = raw.replace("\\", "/") if plat == "windows" else raw

    # 백엔드 기본값: Windows → win32com, 그 외 → pypff
    if not backend:
        backend = "win32com" if plat == "windows" else "pypff"

    created_at = datetime.now().strftime("%Y-%m-%d")
    content = (
        f"# pst2md 설정 파일\n"
        f"# 생성: {created_at}  플랫폼: {plat}\n"
        f"\n"
        f"[archive]\n"
        f'root = "{archive}"\n'
        f"\n"
        f"# PST 파서 백엔드: auto | pypff | readpst | win32com\n"
        f'pst_backend = "{backend}"\n'
        f"\n"
        f"[tools]\n"
        f'fzf     = "fzf"\n'
        f'glow    = "glow"\n'
        f'bat     = "bat"\n'
        f'sqlite3 = "sqlite3"\n'
        f'rg      = "rg"\n'
        f"\n"
        f"[mailview]\n"
        f"# fzf 미리보기 glow 테마: dark | light | dracula | tokyo-night | notty\n"
        f"# 또는 커스텀 JSON 절대 경로. 비워두면 mocha-glow.json 자동 사용.\n"
        f'glow_style = ""\n'
        f"# mailview 시작 시 새 MD 파일이 있으면 인덱스를 자동 갱신한다.\n"
        f"auto_index = true\n"
    )

    if plat == "windows":
        content += (
            f"\n"
            f"[win32com]\n"
            f"# Outlook 프로파일 이름 (빈 문자열 = 기본 프로파일)\n"
            f'outlook_profile = ""\n'
        )

    config_file.write_text(content, encoding="utf-8")
    return config_file


def llm_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """LLM 설정 dict 를 반환한다.

    Args:
        cfg: load_config() 결과. None 이면 내부에서 새로 로드한다.

    Returns:
        cfg["llm"] dict (DEFAULT_CONFIG["llm"] 와 병합된 값).
    """
    if cfg is None:
        cfg = load_config()
    return cfg.get("llm", copy.deepcopy(DEFAULT_CONFIG["llm"]))


# ---------------------------------------------------------------------------
# TOML 편집 헬퍼
# ---------------------------------------------------------------------------

def _toml_key_line(key: str, value: str) -> str:
    """TOML key = "value" 줄을 생성한다. 역슬래시를 슬래시로 정규화한다."""
    return f'{key} = "{str(value).replace(chr(92), "/")}"'


def _replace_in_section(text: str, section: str, key: str, new_line: str) -> tuple[str, bool]:
    """TOML 텍스트의 특정 섹션 내 key 줄을 교체한다.

    Args:
        text:     TOML 파일 전체 내용.
        section:  섹션 이름 (예: "llm").
        key:      교체할 키 이름.
        new_line: 새 key = "value" 줄.

    Returns:
        (updated_text, replaced) — replaced 가 False 이면 key 줄이 없었음.
    """
    pattern = re.compile(
        r"(\[" + re.escape(section) + r"\].*?)(\b" + re.escape(key) + r'\s*=\s*"[^"]*")',
        re.DOTALL,
    )
    updated, count = pattern.subn(lambda m: m.group(1) + new_line, text, count=1)
    return updated, count > 0


def save_llm_setting(key: str, value: str) -> Path:
    """config.toml 의 [llm] 섹션 단일 키를 업데이트한다.

    파일이 없으면 먼저 생성한다. [llm] 섹션이 없으면 파일 끝에 추가한다.
    기존 주석은 보존하며, 해당 key = "..." 줄만 교체/추가한다.

    Args:
        key:   변경할 LLM 설정 키 (provider | endpoint | model | token | ...).
        value: 새 값 (문자열).

    Returns:
        업데이트된 config.toml 경로.

    Raises:
        OSError: 파일 읽기/쓰기 실패.
    """
    config_file = Path.home() / ".pst2md" / "config.toml"
    if not config_file.exists():
        init_config_file()

    original = config_file.read_text(encoding="utf-8")
    new_line = _toml_key_line(key, value)

    updated, replaced = _replace_in_section(original, "llm", key, new_line)

    if not replaced:
        # key 줄 없음 — [llm] 섹션 끝에 추가하거나 섹션 자체를 신규 생성
        section_re = re.compile(r"(\[llm\][^\[]*)", re.DOTALL)
        m = section_re.search(updated)
        if m:
            insertion = m.group(1).rstrip() + f"\n{new_line}\n"
            updated = updated[: m.start()] + insertion + updated[m.end():]
        else:
            updated = original.rstrip() + f"\n\n[llm]\n{new_line}\n"

    config_file.write_text(updated, encoding="utf-8")
    return config_file
