#!/usr/bin/env python3
"""
mailenrich — LLM Enrichment CLI

아카이브의 Markdown 파일을 LLM 으로 분석하여 요약 / 의미 태그 / 백링크를 자동 생성한다.

생성 항목:
  - frontmatter: summary, llm_tags, related, llm_hash, llm_model, llm_enriched_at
  - 섹션: <!-- LLM-ENRICH:BEGIN --> ... <!-- LLM-ENRICH:END -->

멱등성: body SHA-256 (llm_hash) 가 동일하면 재호출 안 함.
원자 쓰기: tmp 파일 + atomic rename, body 바이트 불변 assertion.

사용법:
  mailenrich                            # 전체 아카이브
  mailenrich --limit 100                # 최대 100개
  mailenrich --since 2023-01-01
  mailenrich --dry-run                  # LLM 호출 없이 예상 토큰/비용
  mailenrich --force                    # llm_hash 무시 재실행
  mailenrich --budget-usd 5.00          # 비용 한도 초과 시 중단
  mailenrich --folder "Inbox/계약"
  mailenrich --concurrency 8
"""
from __future__ import annotations

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import archive_root, llm_config, load_config
from lib.llm_client import LLMClient, LLMRequest, LLMResponse, get_client
from lib.md_io import MdParts, body_hash, split, write

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_STATUS_OK = "ok"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"

# dry-run 시 사용하는 평균 토큰 추정값
_DRY_RUN_INPUT_TOKENS = 800
_DRY_RUN_OUTPUT_TOKENS = 250

# 프롬프트 본문 최대 글자 수
_MAX_BODY_CHARS = 3000

# ---------------------------------------------------------------------------
# 비용 추정 (provider 별 USD / token)
# ---------------------------------------------------------------------------

_COST_PER_TOKEN: dict[str, tuple[float, float]] = {
    # (input_per_token, output_per_token)
    "gpt-4o-mini":               (0.15 / 1_000_000, 0.60 / 1_000_000),
    "gpt-4o":                    (5.00 / 1_000_000, 15.0 / 1_000_000),
    "claude-haiku-4-5-20251001": (0.80 / 1_000_000,  4.0 / 1_000_000),
    "claude-sonnet-4-6":         (3.00 / 1_000_000, 15.0 / 1_000_000),
}
_DEFAULT_COST_RATES: tuple[float, float] = (1.0 / 1_000_000, 3.0 / 1_000_000)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """모델별 단가로 USD 비용을 추정한다."""
    in_rate, out_rate = _COST_PER_TOKEN.get(model, _DEFAULT_COST_RATES)
    return input_tokens * in_rate + output_tokens * out_rate


# ---------------------------------------------------------------------------
# 프롬프트 빌더
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
당신은 비즈니스 이메일 분석 전문가입니다.
주어진 이메일을 분석하여 JSON 형식으로 응답하세요.

반환 형식:
{
  "summary": "이메일 내용을 한국어로 한 문단(최대 300자)으로 요약",
  "tags": ["의미 태그1", "태그2"],
  "related": [{"thread": "스레드ID", "reason": "연관 이유"}]
}

rules:
- summary: 핵심 내용 중심, 개인정보 포함 가능
- tags: 최대 5개, 업무 맥락 중심 (예: 계약, 법무, 재무, 인사, 기술지원)
- related: 이 이메일과 주제/프로젝트가 유사한 다른 스레드 ID (없으면 빈 배열)
- 반드시 valid JSON 만 반환"""


def _build_prompt(parts: MdParts, scope: dict[str, Any]) -> LLMRequest:
    """MdParts 에서 LLM 요청을 생성한다."""
    fm = parts.frontmatter
    header_block = (
        f"제목: {fm.get('subject', '')}\n"
        f"보낸사람: {fm.get('from', '')}\n"
        f"날짜: {fm.get('date', '')}\n"
        f"폴더: {fm.get('folder', '')}\n"
    )
    body_snippet = parts.body[:_MAX_BODY_CHARS]
    if len(parts.body) > _MAX_BODY_CHARS:
        body_snippet += "\n...(이하 생략)"

    summary_chars = scope.get("summary_max_chars", 300)
    return LLMRequest(
        system=_SYSTEM_PROMPT,
        user=f"{header_block}\n---\n{body_snippet}",
        max_tokens=summary_chars + 300,
        temperature=0.2,
    )


# ---------------------------------------------------------------------------
# LLM 결과 렌더러
# ---------------------------------------------------------------------------

def _render_sections(parsed: dict[str, Any], scope: dict[str, Any]) -> str:
    """파싱된 LLM 결과를 Markdown 섹션 텍스트로 변환한다."""
    lines: list[str] = []

    summary = str(parsed.get("summary", "")).strip()
    if summary:
        lines.append("## 요약 (LLM)\n\n")
        lines.append(summary + "\n\n")

    tags = parsed.get("tags", [])
    if isinstance(tags, list) and tags:
        max_tags = scope.get("tag_max_count", 5)
        tag_str = " ".join(f"`{t}`" for t in tags[:max_tags])
        lines.append(f"**태그:** {tag_str}\n\n")

    related = parsed.get("related", [])
    if isinstance(related, list) and related:
        max_rel = scope.get("related_max_count", 5)
        lines.append("## 관련 문서 (LLM)\n\n")
        for item in related[:max_rel]:
            if isinstance(item, dict):
                thread = item.get("thread", "")
                reason = item.get("reason", "")
                lines.append(f"- [[{thread}]] — {reason}\n")
        lines.append("\n")

    return "".join(lines)


# ---------------------------------------------------------------------------
# 파일 이터레이터
# ---------------------------------------------------------------------------

def _iter_md_files(
    archive: Path,
    folders: tuple[str, ...],
    limit: int,
    skip_folders: list[str],
) -> list[Path]:
    """필터 조건에 맞는 MD 파일 목록을 반환한다."""
    md_dir = archive / "archive"
    if not md_dir.exists():
        return []

    files: list[Path] = []
    for md_path in sorted(md_dir.rglob("*.md")):
        rel = str(md_path.relative_to(md_dir))

        if folders and not any(f.lower() in rel.lower() for f in folders):
            continue
        if any(sf.lower() in rel.lower() for sf in skip_folders):
            continue

        files.append(md_path)
        if limit and len(files) >= limit:
            break

    return files


# ---------------------------------------------------------------------------
# LLM 호출 + JSON 파싱
# ---------------------------------------------------------------------------

def _call_llm(client: LLMClient, req: LLMRequest) -> tuple[LLMResponse, dict[str, Any]]:
    """LLM 을 호출하고 JSON 을 파싱한다. 실패 시 1회 재시도.

    Args:
        client: LLMClient 인스턴스.
        req:    LLMRequest.

    Returns:
        (LLMResponse, parsed_dict) 튜플.

    Raises:
        ValueError: JSON 파싱이 2회 모두 실패한 경우.
        RuntimeError: LLM 호출 자체가 실패한 경우.
    """
    resp = client.complete(req)
    try:
        return resp, json.loads(resp.text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 1회 재시도
    resp = client.complete(req)
    try:
        return resp, json.loads(resp.text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"JSON 파싱 2회 실패: {exc}") from exc


def _build_fm_updates(
    parsed: dict[str, Any],
    resp: LLMResponse,
    bh: str,
) -> dict[str, Any]:
    """LLM 파싱 결과를 frontmatter 업데이트 dict 로 변환한다."""
    now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "llm_tags": parsed.get("tags", []),
        "related": parsed.get("related", []),
        "llm_hash": bh,
        "llm_model": resp.model,
        "llm_enriched_at": now_iso,
    }


# ---------------------------------------------------------------------------
# 단일 파일 처리
# ---------------------------------------------------------------------------

def _process_one(
    md_path: Path,
    client: LLMClient | None,
    scope: dict[str, Any],
    force: bool,
    dry_run: bool,
    model: str,
) -> dict[str, Any]:
    """단일 MD 파일을 enrichment 처리하고 결과 dict 를 반환한다.

    Returns:
        {"status": "ok"|"skipped"|"failed", "input_tokens": int, ...}
    """
    result: dict[str, Any] = {
        "path": str(md_path),
        "status": _STATUS_OK,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "error": None,
    }

    try:
        parts = split(md_path)
        bh = body_hash(parts)

        if not force and parts.frontmatter.get("llm_hash") == bh:
            result["status"] = _STATUS_SKIPPED
            return result

        if len(parts.body) < scope.get("skip_body_shorter_than", 100):
            result["status"] = _STATUS_SKIPPED
            return result

        if dry_run:
            result["input_tokens"] = _DRY_RUN_INPUT_TOKENS
            result["output_tokens"] = _DRY_RUN_OUTPUT_TOKENS
            result["cost_usd"] = _estimate_cost(
                model, _DRY_RUN_INPUT_TOKENS, _DRY_RUN_OUTPUT_TOKENS
            )
            return result

        assert client is not None
        req = _build_prompt(parts, scope)
        resp, parsed = _call_llm(client, req)

        fm_updates = _build_fm_updates(parsed, resp, bh)
        llm_sections = _render_sections(parsed, scope)
        write(md_path, fm_updates, llm_sections, parts)

        result["input_tokens"] = resp.input_tokens
        result["output_tokens"] = resp.output_tokens
        result["cost_usd"] = _estimate_cost(resp.model, resp.input_tokens, resp.output_tokens)

    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        result["status"] = _STATUS_FAILED
        result["error"] = str(exc)
        log.warning("enrichment 실패 [%s]: %s", md_path.name, exc)

    return result


# ---------------------------------------------------------------------------
# 로그 기록
# ---------------------------------------------------------------------------

def _append_log(log_path: Path, entry: dict[str, Any]) -> None:
    """결과를 .mailenrich.log.jsonl 에 추가한다."""
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("로그 기록 실패: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--archive", "archive_path", default="", help="아카이브 루트 경로")
@click.option("--since", default="", help="시작 날짜 필터 (YYYY-MM-DD, 현재 미구현)")
@click.option("--until", default="", help="종료 날짜 필터 (YYYY-MM-DD, 현재 미구현)")
@click.option("--limit", default=0, type=int, help="처리 상한 (0=무제한)")
@click.option("--dry-run", is_flag=True, default=False, help="LLM 호출 없이 예상 토큰/비용만 출력")
@click.option("--force", is_flag=True, default=False, help="llm_hash 무시하고 강제 재실행")
@click.option("--budget-usd", default=0.0, type=float, help="비용 한도 (0=무제한)")
@click.option("--folder", "folders", multiple=True, help="처리할 폴더 필터 (여러 번 지정 가능)")
@click.option("--concurrency", default=0, type=int, help="동시 LLM 호출 수 (0=config 값 사용)")
@click.option("-v", "--verbose", is_flag=True, default=False, help="상세 로그 출력")
def main(
    archive_path: str,
    since: str,
    until: str,
    limit: int,
    dry_run: bool,
    force: bool,
    budget_usd: float,
    folders: tuple[str, ...],
    concurrency: int,
    verbose: bool,
) -> None:
    """LLM 으로 메일 아카이브를 enrichment 한다 (요약 / 태그 / 백링크).

    \b
    멱등성: llm_hash 가 동일하면 재호출하지 않는다.
    비용 추정: --dry-run 으로 먼저 확인 후 실행을 권장한다.

    \b
    예시:
        mailenrich --dry-run
        mailenrich --limit 50 --folder Inbox/계약
        mailenrich --force --concurrency 8
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = load_config()
    llm_cfg = llm_config(cfg)
    scope = llm_cfg.get("scope", {})

    root = Path(archive_path).expanduser() if archive_path else archive_root(cfg)
    if not root.exists():
        click.echo(f"아카이브 없음: {root}", err=True)
        sys.exit(1)

    model = llm_cfg.get("model", "gpt-4o-mini")
    concurrency = concurrency or llm_cfg.get("concurrency", 4)
    skip_folders: list[str] = scope.get("skip_folders", [])

    client: LLMClient | None = None
    if not dry_run:
        try:
            client = get_client(cfg)
        except (ValueError, ImportError) as exc:
            click.echo(f"LLM 클라이언트 초기화 실패: {exc}", err=True)
            sys.exit(1)

    md_files = _iter_md_files(root, folders, limit, skip_folders)
    if not md_files:
        click.echo("처리할 MD 파일이 없습니다.")
        return

    click.echo(
        f"{'[DRY-RUN] ' if dry_run else ''}대상 파일: {len(md_files)}개  "
        f"| 아카이브: {root}  | 모델: {model}"
    )

    log_path = root / ".mailenrich.log.jsonl"
    n_ok = n_skipped = n_failed = 0
    total_input_tokens = total_output_tokens = 0
    total_cost = 0.0
    budget_exceeded = False

    def _worker(md_path: Path) -> dict[str, Any]:
        return _process_one(md_path, client, scope, force, dry_run, model)

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_worker, p): p for p in md_files}
        with tqdm(total=len(md_files), unit="mail") as pbar:
            for future in as_completed(futures):
                res = future.result()
                pbar.update(1)

                status = res["status"]
                if status == _STATUS_OK:
                    n_ok += 1
                elif status == _STATUS_SKIPPED:
                    n_skipped += 1
                else:
                    n_failed += 1

                total_input_tokens += res.get("input_tokens", 0)
                total_output_tokens += res.get("output_tokens", 0)
                total_cost += res.get("cost_usd", 0.0)

                if not dry_run:
                    _append_log(log_path, res)

                if verbose and status == _STATUS_FAILED:
                    click.echo(
                        f"  FAIL {Path(res['path']).name}: {res['error']}", err=True
                    )

                if budget_usd > 0 and total_cost >= budget_usd:
                    click.echo(f"\n예산 한도 ${budget_usd:.2f} 초과 — 중단합니다.")
                    budget_exceeded = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

    click.echo()
    if dry_run:
        click.echo(
            f"[DRY-RUN] 예상  입력 {total_input_tokens:,} 토큰 | "
            f"출력 {total_output_tokens:,} 토큰 | 비용 ${total_cost:.4f}"
        )
    else:
        click.echo(
            f"완료: {n_ok}  건  |  스킵: {n_skipped}  건  |  실패: {n_failed}  건\n"
            f"토큰  입력 {total_input_tokens:,} | 출력 {total_output_tokens:,} | "
            f"비용 ${total_cost:.4f}"
        )
        if n_failed:
            click.echo(f"실패 상세: {log_path}")


if __name__ == "__main__":
    main()
