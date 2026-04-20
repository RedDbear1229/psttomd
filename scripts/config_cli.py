#!/usr/bin/env python3
"""
pst2md 설정 관리 CLI

~/.pst2md/config.toml 을 조회·수정하는 서브커맨드 집합.

사용법:
  pst2md-config show                  # 현재 유효 설정 출력
  pst2md-config set-output <path>     # 아웃풋 폴더(archive.root) 저장
  pst2md-config set glow|mdcat        # fzf preview 뷰어 전환
  pst2md-config init                  # config.toml 최초 생성
  pst2md-config init --force          # config.toml 강제 재생성
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import (
    config_file_path,
    detect_platform,
    init_config_file,
    load_config,
    save_archive_root,
    save_setting,
)


@click.group()
def main() -> None:
    """pst2md 설정 파일(~/.pst2md/config.toml)을 관리한다."""


@main.command("show")
def cmd_show() -> None:
    """현재 유효 설정을 출력한다.

    config.toml + 환경변수 MAIL_ARCHIVE 가 적용된 최종값을 보여준다.
    """
    cfg = load_config()
    config_path = config_file_path()

    click.echo(f"설정 파일 : {config_path}")
    click.echo(f"  존재 여부: {'있음' if config_path.exists() else '없음 (기본값 사용 중)'}")
    click.echo()
    click.echo(f"[archive]")
    click.echo(f"  root       = {cfg['archive']['root']}")
    click.echo()
    click.echo(f"pst_backend  = {cfg.get('pst_backend', 'auto')}")
    click.echo()
    click.echo(f"[tools]")
    for tool, path in cfg.get("tools", {}).items():
        click.echo(f"  {tool:<8} = {path}")

    mv = cfg.get("mailview", {})
    if mv:
        click.echo()
        click.echo(f"[mailview]")
        click.echo(f"  preview_viewer = {mv.get('preview_viewer', 'glow')}")
        click.echo(f"  glow_style     = {mv.get('glow_style', '')!r}")
        click.echo(f"  auto_index     = {mv.get('auto_index', True)}")

    win32 = cfg.get("win32com", {})
    if win32:
        click.echo()
        click.echo(f"[win32com]")
        click.echo(f"  outlook_profile = {win32.get('outlook_profile', '')!r}")

    click.echo()
    click.echo(f"플랫폼     : {detect_platform()}")


@main.command("set-output")
@click.argument("path", type=click.Path())
def cmd_set_output(path: str) -> None:
    """아웃풋 폴더(archive.root)를 config.toml 에 저장한다.

    PATH 는 절대 경로 또는 ~ 를 포함한 경로를 지정한다.

    예시:\n
        pst2md-config set-output ~/mail-archive\n
        pst2md-config set-output C:/Users/me/mail-archive
    """
    resolved = Path(path).expanduser().resolve()
    saved = save_archive_root(resolved)
    click.echo(f"아웃풋 폴더를 저장했습니다.")
    click.echo(f"  경로      : {resolved}")
    click.echo(f"  config.toml: {saved}")


@main.command("set")
@click.argument("viewer", type=click.Choice(["glow", "mdcat"]))
def cmd_set(viewer: str) -> None:
    """fzf preview 뷰어를 glow / mdcat 중 선택해 저장한다.

    glow  — 기본. 마크다운 렌더링 + 컬러. 이미지는 텍스트 링크로만 표시.\n
    mdcat — Kitty/WezTerm/iTerm2 그래픽 프로토콜 또는 sixel 지원 터미널에서
            마크다운 내 이미지를 인라인으로 렌더링. 비지원 터미널에서는
            자리표시자 텍스트만 보임.

    예시:\n
        pst2md-config set glow\n
        pst2md-config set mdcat
    """
    if viewer == "mdcat" and shutil.which("mdcat") is None:
        click.echo(
            "경고: mdcat 바이너리를 PATH 에서 찾지 못했습니다.\n"
            "  설치: cargo install mdcat  |  brew install mdcat  |  winget install mdcat",
            err=True,
        )
    saved = save_setting("mailview", "preview_viewer", viewer)
    click.echo(f"preview_viewer = {viewer!r} 저장 완료: {saved}")


@main.command("init")
@click.option(
    "--force", is_flag=True, default=False,
    help="기존 config.toml 이 있어도 덮어씀",
)
@click.option(
    "--output", "archive", default="",
    help="아카이브 루트 경로 (기본: ~/mail-archive)",
)
@click.option(
    "--backend", default="",
    help="PST 백엔드 (auto|pypff|readpst|win32com, 기본: 플랫폼 자동)",
)
def cmd_init(force: bool, archive: str, backend: str) -> None:
    """~/.pst2md/config.toml 을 생성한다.

    이미 파일이 존재하면 아무 작업도 하지 않는다. --force 를 사용하면 덮어쓴다.
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
