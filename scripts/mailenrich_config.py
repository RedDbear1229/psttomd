#!/usr/bin/env python3
"""
mailenrich-config — LLM 설정 관리 CLI

~/.pst2md/config.toml 의 [llm] 섹션을 조회·수정한다.

사용법:
  mailenrich-config show
  mailenrich-config set-provider openai
  mailenrich-config set-endpoint https://api.openai.com/v1
  mailenrich-config set-model gpt-4o-mini
  mailenrich-config set-token sk-xxxxx
  mailenrich-config init
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import click

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import (
    config_file_path,
    init_config_file,
    llm_config,
    load_config,
    save_llm_setting,
)

# [llm] + [llm.scope] 기본 섹션 템플릿
_LLM_SECTION_TEMPLATE = """\

[llm]
# LLM provider: openai | anthropic | ollama
provider = "openai"
# API endpoint (ollama: http://localhost:11434)
endpoint = "https://api.openai.com/v1"
# 모델 이름
model = "gpt-4o-mini"
# API 토큰 (env LLM_TOKEN 이 우선)
token = ""
timeout = 60
max_retries = 3
concurrency = 4

[llm.scope]
summary_max_chars = 300
tag_max_count = 5
related_max_count = 5
skip_body_shorter_than = 100
skip_folders = ["Junk", "Spam", "Deleted Items"]
"""

# 기존 [llm] 섹션 전체를 제거하는 패턴 (다음 최상위 섹션 직전까지)
_LLM_SECTION_RE = re.compile(r"\n\[llm[^\[]*", re.DOTALL)


def _mask_token(token: str) -> str:
    """토큰 앞 4자만 노출하고 나머지를 마스킹한다."""
    return token[:4] + "****" if len(token) > 4 else "****"


@click.group()
def main() -> None:
    """mailenrich LLM 설정 파일(~/.pst2md/config.toml [llm])을 관리한다."""


@main.command("show")
def cmd_show() -> None:
    """현재 LLM 설정을 출력한다.

    토큰은 마스킹해서 표시한다 (앞 4자리만 노출).
    """
    cfg = load_config()
    llm = llm_config(cfg)
    config_path = config_file_path()

    click.echo(f"설정 파일 : {config_path}")
    click.echo(f"  존재 여부: {'있음' if config_path.exists() else '없음 (기본값)'}")
    click.echo()
    click.echo("[llm]")
    click.echo(f"  provider    = {llm.get('provider', 'openai')}")
    click.echo(f"  endpoint    = {llm.get('endpoint', '')}")
    click.echo(f"  model       = {llm.get('model', '')}")

    env_token = os.environ.get("LLM_TOKEN", "").strip()
    cfg_token = llm.get("token", "")
    if env_token:
        token_display = _mask_token(env_token) + "  (env LLM_TOKEN)"
    elif cfg_token:
        token_display = _mask_token(cfg_token)
    else:
        token_display = "(없음 — env LLM_TOKEN 또는 set-token 으로 설정)"
    click.echo(f"  token       = {token_display}")

    click.echo(f"  timeout     = {llm.get('timeout', 60)}")
    click.echo(f"  max_retries = {llm.get('max_retries', 3)}")
    click.echo(f"  concurrency = {llm.get('concurrency', 4)}")

    scope = llm.get("scope", {})
    if scope:
        click.echo()
        click.echo("[llm.scope]")
        click.echo(f"  summary_max_chars      = {scope.get('summary_max_chars', 300)}")
        click.echo(f"  tag_max_count          = {scope.get('tag_max_count', 5)}")
        click.echo(f"  related_max_count      = {scope.get('related_max_count', 5)}")
        click.echo(f"  skip_body_shorter_than = {scope.get('skip_body_shorter_than', 100)}")
        click.echo(f"  skip_folders           = {scope.get('skip_folders', [])}")


@main.command("set-provider")
@click.argument("provider", type=click.Choice(["openai", "anthropic", "ollama"]))
def cmd_set_provider(provider: str) -> None:
    """LLM provider 를 설정한다.

    PROVIDER: openai | anthropic | ollama\n
    예시:\n
        mailenrich-config set-provider ollama
    """
    saved = save_llm_setting("provider", provider)
    click.echo(f"provider = {provider!r} 저장 완료: {saved}")


@main.command("set-endpoint")
@click.argument("endpoint")
def cmd_set_endpoint(endpoint: str) -> None:
    """LLM API 엔드포인트를 설정한다.

    예시:\n
        mailenrich-config set-endpoint https://api.openai.com/v1\n
        mailenrich-config set-endpoint http://localhost:11434
    """
    saved = save_llm_setting("endpoint", endpoint)
    click.echo(f"endpoint = {endpoint!r} 저장 완료: {saved}")


@main.command("set-model")
@click.argument("model")
def cmd_set_model(model: str) -> None:
    """사용할 LLM 모델 이름을 설정한다.

    예시:\n
        mailenrich-config set-model gpt-4o-mini\n
        mailenrich-config set-model claude-haiku-4-5-20251001\n
        mailenrich-config set-model llama3.1:8b
    """
    saved = save_llm_setting("model", model)
    click.echo(f"model = {model!r} 저장 완료: {saved}")


@main.command("set-token")
@click.argument("token")
def cmd_set_token(token: str) -> None:
    """API 토큰을 config.toml 에 저장한다.

    보안 권고: 토큰은 환경변수 LLM_TOKEN 으로 설정하는 것을 권장한다.
    config.toml 에 저장하면 파일 권한을 반드시 600 으로 설정하라.

    예시:\n
        export LLM_TOKEN=sk-xxxxx      # 권장\n
        mailenrich-config set-token sk-xxxxx   # 대안
    """
    saved = save_llm_setting("token", token)
    click.echo(f"token = {_mask_token(token)} 저장 완료: {saved}")
    click.echo("  보안 권고: chmod 600 ~/.pst2md/config.toml")


@main.command("init")
@click.option(
    "--force", is_flag=True, default=False,
    help="기존 config.toml 이 있어도 [llm] 섹션을 추가",
)
def cmd_init(force: bool) -> None:
    """config.toml 에 [llm] 섹션이 없으면 추가한다.

    파일 자체가 없으면 기본 템플릿으로 새로 생성한다.
    """
    config_file = config_file_path()

    if not config_file.exists():
        result = init_config_file()
        click.echo(f"config.toml 을 생성했습니다: {result}")
        return

    text = config_file.read_text(encoding="utf-8")
    if "[llm]" in text and not force:
        click.echo("[llm] 섹션이 이미 존재합니다.")
        click.echo("재설정하려면 --force 를 사용하세요.")
        return

    _append_llm_section(config_file, force)
    click.echo(f"[llm] 섹션을 추가했습니다: {config_file}")


def _append_llm_section(config_file: Path, force: bool) -> None:
    """config.toml 에 기본 [llm] 섹션을 추가하거나 교체한다."""
    original = config_file.read_text(encoding="utf-8")

    if force and "[llm]" in original:
        cleaned = _LLM_SECTION_RE.sub("", original)
        updated = cleaned.rstrip() + _LLM_SECTION_TEMPLATE
    else:
        updated = original.rstrip() + _LLM_SECTION_TEMPLATE

    config_file.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
