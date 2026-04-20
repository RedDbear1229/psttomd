#!/usr/bin/env python3
"""
pst2md 설정 관리 CLI

~/.pst2md/config.toml 의 모든 설정을 조회·수정하는 서브커맨드 집합.

자주 쓰는 명령:
  pst2md-config show [SECTION]           유효 설정을 출력 (섹션 필터 선택)
  pst2md-config get KEY                  단일 키 값 조회
  pst2md-config set KEY VALUE            임의 키 값 저장
  pst2md-config unset KEY                키를 제거 (기본값으로 복귀)
  pst2md-config set-output PATH          archive.root 저장 (별칭)
  pst2md-config set-viewer glow|mdcat    mailview.preview_viewer 저장 (별칭)
  pst2md-config path                     config.toml 경로 출력
  pst2md-config edit                     $EDITOR 로 config.toml 열기
  pst2md-config init [--force]           config.toml 생성

설정 가능한 키 전체 목록은 `pst2md-config show` 또는 아래 참조.
"""
from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import (
    config_file_path,
    detect_platform,
    init_config_file,
    load_config,
    save_archive_root,
    save_setting,
    unset_setting,
)
from lib.config_schema import KNOWN_KEYS, KeySpec, convert_value, mask_sensitive


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _lookup_key(key: str) -> KeySpec:
    """KNOWN_KEYS 에서 key 를 조회. 없으면 difflib 제안을 포함해 에러로 종료."""
    spec = KNOWN_KEYS.get(key)
    if spec is not None:
        return spec
    suggestions = difflib.get_close_matches(key, KNOWN_KEYS.keys(), n=3, cutoff=0.5)
    hint = ""
    if suggestions:
        hint = "\n가까운 키: " + ", ".join(suggestions)
    raise click.ClickException(
        f"알 수 없는 키: {key!r}{hint}\n"
        f"전체 목록은 `pst2md-config show` 또는 `--help` 참고."
    )


def _get_value_from_cfg(cfg: dict[str, Any], spec: KeySpec) -> Any:
    """dotted path 를 따라 cfg dict 에서 값을 꺼낸다. 없으면 spec.default."""
    node: Any = cfg
    for part in spec.path.split("."):
        if not isinstance(node, dict) or part not in node:
            return spec.default
        node = node[part]
    return node


def _format_display(spec: KeySpec, value: Any) -> str:
    """민감 키는 마스킹하여 repr 같은 표현으로 반환."""
    if spec.sensitive:
        return repr(mask_sensitive(value))
    if isinstance(value, list):
        return "[" + ", ".join(repr(v) for v in value) + "]"
    return repr(value)


def _warn_if_sensitive(spec: KeySpec) -> None:
    if not spec.sensitive:
        return
    click.echo(
        f"⚠  {spec.path} 는 민감 값입니다. config.toml 에 평문 저장됩니다.\n"
        f"   권장: 이 파일 대신 환경변수 LLM_TOKEN 사용 + "
        f"`pst2md-config unset {spec.path}`.\n"
        f"   그대로 저장하려면 `chmod 600 ~/.pst2md/config.toml` 실행을 권장합니다.",
        err=True,
    )


# ---------------------------------------------------------------------------
# CLI 그룹
# ---------------------------------------------------------------------------

@click.group(
    help=(
        "pst2md 설정 파일(~/.pst2md/config.toml)을 관리한다.\n"
        "\n"
        "주요 명령:\n"
        "  show / get   — 현재 값 조회\n"
        "  set / unset  — 값 저장 · 제거\n"
        "  path / edit  — config.toml 위치 확인 및 편집\n"
        "  init         — 최초 생성\n"
        "\n"
        "전체 키 목록은 `pst2md-config show` 참고."
    )
)
def main() -> None:
    pass


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@main.command("show")
@click.argument("section", required=False)
def cmd_show(section: Optional[str]) -> None:
    """현재 유효 설정을 출력한다.

    인자:
        SECTION — (선택) 특정 섹션만 출력 (예: llm, mailview, tools).

    config.toml + 환경변수 MAIL_ARCHIVE 가 적용된 최종값을 보여준다.
    민감 키(llm.token)는 마스킹 처리된다.

    예시:\n
        pst2md-config show\n
        pst2md-config show llm
    """
    cfg = load_config()
    config_path = config_file_path()

    click.echo(f"설정 파일 : {config_path}")
    click.echo(
        f"  존재 여부: "
        f"{'있음' if config_path.exists() else '없음 (기본값 사용 중)'}"
    )
    click.echo()

    # section 별로 그룹화
    by_section: dict[str, list[KeySpec]] = {}
    for spec in KNOWN_KEYS.values():
        by_section.setdefault(spec.section or "(top-level)", []).append(spec)

    filter_section = section.lower() if section else None
    printed = False

    for sec_name, specs in by_section.items():
        # 필터 — 완전 일치 또는 접두 일치
        if filter_section and not (
            sec_name.lower() == filter_section
            or sec_name.lower().startswith(filter_section + ".")
        ):
            continue
        printed = True
        label = f"[{sec_name}]" if sec_name != "(top-level)" else "(top-level)"
        click.echo(label)
        for spec in specs:
            value = _get_value_from_cfg(cfg, spec)
            click.echo(f"  {spec.key:<24} = {_format_display(spec, value)}")
        click.echo()

    if filter_section and not printed:
        raise click.ClickException(
            f"섹션을 찾지 못했습니다: {section!r}\n"
            f"사용 가능한 섹션: {', '.join(sorted(by_section.keys()))}"
        )

    click.echo(f"플랫폼     : {detect_platform()}")


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

@main.command("get")
@click.argument("key")
def cmd_get(key: str) -> None:
    """단일 설정 키의 현재 값을 출력한다.

    인자:
        KEY — dotted 형식의 설정 키 (예: llm.model).

    민감 키는 마스킹된다. 모르는 키는 가까운 키를 제안한다.

    예시:\n
        pst2md-config get llm.model\n
        pst2md-config get mailview.preview_viewer
    """
    spec = _lookup_key(key)
    cfg = load_config()
    value = _get_value_from_cfg(cfg, spec)
    click.echo(_format_display(spec, value))


# ---------------------------------------------------------------------------
# set — 범용 + 구 문법 브릿지
# ---------------------------------------------------------------------------

_LEGACY_VIEWER_TOKENS = {"glow", "mdcat"}


@main.command("set")
@click.argument("key")
@click.argument("value", required=False)
@click.pass_context
def cmd_set(ctx: click.Context, key: str, value: Optional[str]) -> None:
    """임의 설정 키에 값을 저장한다.

    인자:
        KEY   — dotted 형식의 설정 키 (예: llm.model).
        VALUE — 새 값. bool 은 true/false/yes/no, list 는 쉼표로 구분.

    특수 케이스:\n
        `set glow` / `set mdcat` — 구 문법. set-viewer 로 자동 위임되며
                                    다음 릴리스에서 제거된다.

    예시:\n
        pst2md-config set llm.model gpt-4o\n
        pst2md-config set mailview.auto_index false\n
        pst2md-config set llm.scope.skip_folders "Junk,Spam,Archive"\n
        pst2md-config set archive.root ~/mail-archive
    """
    # 구 문법 브릿지: `pst2md-config set glow` / `pst2md-config set mdcat`
    if value is None and key in _LEGACY_VIEWER_TOKENS:
        click.echo(
            f"⚠  `set {key}` 형식은 deprecated 입니다. "
            f"다음에는 `set-viewer {key}` 를 사용하세요.",
            err=True,
        )
        ctx.invoke(cmd_set_viewer, viewer=key)
        return

    if value is None:
        raise click.UsageError(
            "VALUE 가 필요합니다. 사용: `pst2md-config set KEY VALUE`"
        )

    spec = _lookup_key(key)
    try:
        converted = convert_value(spec, value)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    _warn_if_sensitive(spec)

    # archive.root 는 별도 헬퍼가 있음 (~ 확장 + 정규화)
    if spec.path == "archive.root":
        resolved = Path(str(converted)).expanduser().resolve()
        saved = save_archive_root(resolved)
    else:
        saved = save_setting(spec.section, spec.key, converted)

    click.echo(f"{spec.path} = {_format_display(spec, converted)} 저장 완료: {saved}")


# ---------------------------------------------------------------------------
# unset
# ---------------------------------------------------------------------------

@main.command("unset")
@click.argument("key")
def cmd_unset(key: str) -> None:
    """config.toml 에서 키를 제거한다 (기본값으로 복귀).

    인자:
        KEY — dotted 형식의 설정 키.

    예시:\n
        pst2md-config unset llm.token\n
        pst2md-config unset mailview.preview_viewer
    """
    spec = _lookup_key(key)
    saved, removed = unset_setting(spec.section, spec.key)
    if removed:
        click.echo(f"{spec.path} 제거 완료: {saved}")
    else:
        click.echo(
            f"{spec.path} 줄이 config.toml 에 없습니다 (이미 기본값): {saved}"
        )


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------

@main.command("path")
def cmd_path() -> None:
    """config.toml 경로를 출력한다.

    스크립트 자동화에 유용 (예: `cat $(pst2md-config path)`).
    """
    click.echo(str(config_file_path()))


# ---------------------------------------------------------------------------
# edit
# ---------------------------------------------------------------------------

@main.command("edit")
def cmd_edit() -> None:
    """$EDITOR 로 config.toml 을 연다.

    $EDITOR 가 없으면 플랫폼별 기본 에디터를 시도한다 (vi/notepad).
    파일이 없으면 먼저 init 을 실행한다.
    """
    config_path = config_file_path()
    if not config_path.exists():
        init_config_file()

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        editor = "notepad" if detect_platform() == "windows" else "vi"

    try:
        subprocess.call([editor, str(config_path)])
    except OSError as exc:
        raise click.ClickException(
            f"에디터 실행 실패 ({editor}): {exc}.\n"
            f"$EDITOR 환경변수를 설정하거나 직접 열어 수정하세요."
        ) from exc


# ---------------------------------------------------------------------------
# set-output (별칭)
# ---------------------------------------------------------------------------

@main.command("set-output")
@click.argument("path", type=click.Path())
def cmd_set_output(path: str) -> None:
    """아웃풋 폴더(archive.root)를 config.toml 에 저장한다.

    `pst2md-config set archive.root PATH` 의 편의 별칭이다.

    예시:\n
        pst2md-config set-output ~/mail-archive\n
        pst2md-config set-output C:/Users/me/mail-archive
    """
    resolved = Path(path).expanduser().resolve()
    saved = save_archive_root(resolved)
    click.echo(f"아웃풋 폴더를 저장했습니다.")
    click.echo(f"  경로      : {resolved}")
    click.echo(f"  config.toml: {saved}")


# ---------------------------------------------------------------------------
# set-viewer (별칭, 구 `set glow|mdcat` 대체)
# ---------------------------------------------------------------------------

@main.command("set-viewer")
@click.argument("viewer", type=click.Choice(["glow", "mdcat"]))
def cmd_set_viewer(viewer: str) -> None:
    """fzf preview 및 Enter 전체 열람 뷰어를 선택한다.

    glow  — 기본. 마크다운 렌더링 + 컬러. 이미지는 텍스트 링크로만 표시.\n
    mdcat — Kitty/WezTerm/iTerm2 그래픽 프로토콜 또는 sixel 지원 터미널에서
            이미지를 인라인으로 렌더링.

    `pst2md-config set mailview.preview_viewer VALUE` 의 편의 별칭이다.

    예시:\n
        pst2md-config set-viewer glow\n
        pst2md-config set-viewer mdcat
    """
    if viewer == "mdcat" and shutil.which("mdcat") is None:
        click.echo(
            "경고: mdcat 바이너리를 PATH 에서 찾지 못했습니다.\n"
            "  설치: cargo install mdcat  |  brew install mdcat  |  winget install mdcat",
            err=True,
        )
    saved = save_setting("mailview", "preview_viewer", viewer)
    click.echo(f"preview_viewer = {viewer!r} 저장 완료: {saved}")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@main.command("init")
@click.option(
    "--force", is_flag=True, default=False,
    help="기존 config.toml 이 있어도 덮어쓴다.",
)
@click.option(
    "--output", "archive", default="",
    help="아카이브 루트 경로 (기본: ~/mail-archive).",
)
@click.option(
    "--backend", default="",
    help="PST 백엔드 (auto|pypff|readpst|win32com). 기본: 플랫폼 자동.",
)
def cmd_init(force: bool, archive: str, backend: str) -> None:
    """~/.pst2md/config.toml 을 생성한다.

    이미 파일이 존재하면 아무 작업도 하지 않는다. --force 로 덮어쓴다.

    예시:\n
        pst2md-config init\n
        pst2md-config init --force --output ~/work-archive
    """
    config_path = config_file_path()
    if config_path.exists() and not force:
        click.echo(f"config.toml 이 이미 존재합니다: {config_path}")
        click.echo("덮어쓰려면 --force 옵션을 사용하세요.")
        return

    result = init_config_file(archive=archive, backend=backend, force=force)
    click.echo(f"config.toml 을 생성했습니다: {result}")


if __name__ == "__main__":
    main()
