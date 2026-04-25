#!/usr/bin/env python3
"""
embed — Embedding 생성 CLI

아카이브 Markdown 파일 본문을 OpenAI 호환 /v1/embeddings 엔드포인트로
float 벡터화해 ``index.sqlite`` 의 ``embeddings`` 테이블에 저장한다.

중복 분석 방지:
  msgid 별로 (body_hash, model) 쌍을 저장하므로,
  본문이 변하지 않고 모델도 동일하면 자동 skip 한다.
  --force 로 강제 재실행 가능.

사용법:
  embed --dry-run                    예상 토큰/비용만 출력
  embed --limit 100                  최대 100개
  embed --since 2024-01-01
  embed --force                      body_hash 무시하고 재실행
  embed --folder 'Inbox/계약'

설정:
  pst2md-config set embedding.endpoint http://localhost:11434/v1
  pst2md-config set embedding.model nomic-embed-text
  EMBEDDING_TOKEN=sk-xxxx embed
"""
from __future__ import annotations

import array
import json
import logging
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import archive_root, embedding_config, load_config
from lib.embed_client import EmbeddingClient, EmbeddingResponse
from lib.md_io import body_hash, split

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

_STATUS_OK = "ok"
_STATUS_SKIPPED = "skipped"
_STATUS_FAILED = "failed"

# 본문이 너무 길면 OpenAI 토큰 제한(8191) 을 넘을 수 있으므로 자른다.
# 글자수 ~ 토큰의 대략 1/3 ~ 1/4 비율.
_MAX_BODY_CHARS = 24000

# dry-run 시 한 입력당 평균 토큰 추정값
_DRY_RUN_TOKENS_PER_INPUT = 600

_COST_PER_TOKEN: dict[str, float] = {
    # USD per token
    "text-embedding-3-small": 0.02 / 1_000_000,
    "text-embedding-3-large": 0.13 / 1_000_000,
    "text-embedding-ada-002": 0.10 / 1_000_000,
}
_DEFAULT_COST_RATE = 0.02 / 1_000_000

# ---------------------------------------------------------------------------
# 스키마 / DB
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS embeddings (
    msgid      TEXT PRIMARY KEY,
    body_hash  TEXT NOT NULL,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON embeddings(body_hash);
"""


def _open_db(archive: Path) -> sqlite3.Connection:
    """index.sqlite 를 열고 embeddings 테이블 스키마를 보장한다.

    Args:
        archive: 아카이브 루트.

    Returns:
        WAL 모드로 설정된 sqlite3 연결.
    """
    db_path = archive / "index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    return conn


def _vector_to_blob(vec: list[float]) -> bytes:
    """float 벡터를 float32 BLOB 으로 직렬화한다.

    array.array('f') 는 32-bit IEEE float 로 저장된다 (사실상 모든 플랫폼 동일).
    numpy 의존성 없이 로딩 시 ``array('f', blob)`` 으로 복원 가능.
    """
    return array.array("f", vec).tobytes()


def _existing_signatures(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """저장된 (msgid → (body_hash, model)) 매핑을 메모리에 로드한다.

    파일 수가 많을 때 N번 SELECT 하지 않도록 한 번에 캐시한다.
    """
    cur = conn.execute("SELECT msgid, body_hash, model FROM embeddings")
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# 비용 추정
# ---------------------------------------------------------------------------

def _estimate_cost(model: str, tokens: int) -> float:
    """모델별 단가로 USD 비용을 추정한다."""
    rate = _COST_PER_TOKEN.get(model, _DEFAULT_COST_RATE)
    return tokens * rate


# ---------------------------------------------------------------------------
# 파일 이터레이터 (mailenrich 와 동일한 필터 규약)
# ---------------------------------------------------------------------------

def _parse_date_filter(s: str, label: str) -> tuple[int, int, int] | None:
    """YYYY-MM-DD 문자열을 (y, m, d) 튜플로 파싱한다."""
    if not s:
        return None
    parts = s.split("-")
    if len(parts) != 3:
        raise click.BadParameter(f"{label} 형식이 잘못되었습니다: {s!r} (YYYY-MM-DD 필요)")
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError as exc:
        raise click.BadParameter(f"{label} 숫자 변환 실패: {s!r}") from exc


def _path_date(rel: Path) -> tuple[int, int, int] | None:
    """archive/ 상대경로에서 (Y, M, D) 튜플을 추출한다. undated 는 None."""
    parts = rel.parts
    if len(parts) < 4:
        return None
    try:
        return (int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def _iter_md_files(
    archive: Path,
    folders: tuple[str, ...],
    limit: int,
    skip_folders: list[str],
    since: tuple[int, int, int] | None,
    until: tuple[int, int, int] | None,
) -> list[Path]:
    """필터 조건에 맞는 MD 파일 목록을 정렬해 반환한다."""
    md_dir = archive / "archive"
    if not md_dir.exists():
        return []

    files: list[Path] = []
    for md_path in sorted(md_dir.rglob("*.md")):
        rel = md_path.relative_to(md_dir)
        rel_str = str(rel)

        if folders and not any(f.lower() in rel_str.lower() for f in folders):
            continue
        if any(sf.lower() in rel_str.lower() for sf in skip_folders):
            continue

        if since is not None or until is not None:
            d = _path_date(rel)
            if d is None:
                continue
            if since is not None and d < since:
                continue
            if until is not None and d > until:
                continue

        files.append(md_path)
        if limit and len(files) >= limit:
            break

    return files


# ---------------------------------------------------------------------------
# 후보 수집 (split + body_hash + skip 판정)
# ---------------------------------------------------------------------------

def _collect_candidates(
    md_files: list[Path],
    existing: dict[str, tuple[str, str]],
    model: str,
    min_body: int,
    force: bool,
) -> tuple[list[dict[str, Any]], int]:
    """embedding 대상 후보 목록과 skip 수를 반환한다.

    Returns:
        (candidates, skipped) — candidates 의 각 dict 는
        ``{path, msgid, body_hash, text}`` 키를 가진다.
    """
    candidates: list[dict[str, Any]] = []
    skipped = 0

    for md_path in md_files:
        try:
            parts = split(md_path)
        except (OSError, ValueError) as exc:
            log.warning("split 실패 [%s]: %s", md_path.name, exc)
            skipped += 1
            continue

        msgid = str(parts.frontmatter.get("msgid", "")).strip()
        if not msgid:
            log.warning("msgid 없음 — skip [%s]", md_path.name)
            skipped += 1
            continue

        if len(parts.body.encode("utf-8")) < min_body:
            skipped += 1
            continue

        bh = body_hash(parts)
        if not force:
            prev = existing.get(msgid)
            if prev is not None and prev == (bh, model):
                skipped += 1
                continue

        text = parts.body[:_MAX_BODY_CHARS]
        candidates.append(
            {"path": md_path, "msgid": msgid, "body_hash": bh, "text": text}
        )

    return candidates, skipped


# ---------------------------------------------------------------------------
# 배치 실행
# ---------------------------------------------------------------------------

def _process_batch(
    client: EmbeddingClient,
    batch: list[dict[str, Any]],
) -> tuple[EmbeddingResponse, list[dict[str, Any]]]:
    """한 배치를 embed 하고 (응답, 입력 후보) 를 함께 반환한다."""
    texts = [c["text"] for c in batch]
    resp = client.embed(texts)
    return resp, batch


def _upsert_results(
    conn: sqlite3.Connection,
    resp: EmbeddingResponse,
    batch: list[dict[str, Any]],
) -> None:
    """배치 결과를 embeddings 테이블에 UPSERT 한다."""
    now_iso = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    rows = []
    for cand, vec in zip(batch, resp.vectors):
        rows.append(
            (
                cand["msgid"],
                cand["body_hash"],
                resp.model,
                len(vec),
                _vector_to_blob(vec),
                now_iso,
            )
        )
    conn.executemany(
        "INSERT OR REPLACE INTO embeddings "
        "(msgid, body_hash, model, dim, vector, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 로그
# ---------------------------------------------------------------------------

def _append_log(log_path: Path, entry: dict[str, Any]) -> None:
    """결과를 .embed.log.jsonl 에 추가한다."""
    try:
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.warning("로그 기록 실패: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_EMBED_EPILOG = (
    "\b\n"
    "예시:\n"
    "  embed --dry-run                          예상 토큰/비용만 출력\n"
    "  embed --limit 100                        최대 100개만 처리\n"
    "  embed --since 2024-01-01                 2024년 이후 메일만\n"
    "  embed --folder 'Inbox/계약'              특정 폴더만 (중복 지정 가능)\n"
    "  embed --force                            body_hash 무시 강제 재실행\n"
    "  embed --concurrency 8 --batch-size 128   병렬 + 배치 크기 조정\n"
    "\n"
    "\b\n"
    "동작:\n"
    "  msgid 별 (body_hash, model) 쌍을 index.sqlite 의 embeddings 테이블에\n"
    "  저장한다. 본문이 변하지 않고 모델도 같으면 자동 skip — 대량 아카이브\n"
    "  여러 번 실행해도 중복 분석이 발생하지 않는다.\n"
    "\n"
    "\b\n"
    "설정:\n"
    "  pst2md-config set embedding.endpoint http://localhost:11434/v1\n"
    "  pst2md-config set embedding.model    nomic-embed-text\n"
    "  pst2md-config set embedding.token    sk-xxxx     # 또는 env EMBEDDING_TOKEN\n"
    "\n"
    "OpenAI 호환 /v1/embeddings 면 어떤 서버든 동작 (OpenAI · Ollama · LM Studio)."
)


@click.command(
    name="embed",
    epilog=_EMBED_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option("--archive", "archive_path", default="", metavar="DIR",
              help="아카이브 루트 (기본: config archive.root).")
@click.option("--since", default="", metavar="YYYY-MM-DD",
              help="시작 날짜 필터 (inclusive).")
@click.option("--until", default="", metavar="YYYY-MM-DD",
              help="종료 날짜 필터 (inclusive).")
@click.option("--limit", default=0, type=int, metavar="N",
              help="처리 상한. 0=무제한 (기본).")
@click.option("--dry-run", is_flag=True, default=False,
              help="embedding 호출 없이 후보 수와 예상 토큰/비용만 출력.")
@click.option("--force", is_flag=True, default=False,
              help="body_hash 일치 무시하고 강제 재실행.")
@click.option("--folder", "folders", multiple=True, metavar="PATH",
              help="처리할 폴더 필터 (중복 지정 가능).")
@click.option("--concurrency", default=0, type=int, metavar="N",
              help="동시 embedding 호출 수. 0=config 값 사용.")
@click.option("--batch-size", default=0, type=int, metavar="N",
              help="한 HTTP 요청에 묶을 텍스트 개수. 0=config 값 사용.")
@click.option("-v", "--verbose", is_flag=True, default=False,
              help="상세 로그 출력 (DEBUG level).")
def main(
    archive_path: str,
    since: str,
    until: str,
    limit: int,
    dry_run: bool,
    force: bool,
    folders: tuple[str, ...],
    concurrency: int,
    batch_size: int,
    verbose: bool,
) -> None:
    """MD 본문을 embedding 벡터로 변환해 index.sqlite 에 저장한다.

    \b
    중복 방지: msgid 별 (body_hash, model) 매칭이 같으면 skip.
    저장 형식: float32 BLOB (numpy 의존성 없음).

    \b
    예시:
        embed --dry-run
        embed --limit 100 --folder Inbox/계약
        embed --force --concurrency 8
    """
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = load_config()
    emb_cfg = embedding_config(cfg)

    root = Path(archive_path).expanduser() if archive_path else archive_root(cfg)
    if not root.exists():
        click.echo(f"아카이브 없음: {root}", err=True)
        sys.exit(1)

    model = emb_cfg.get("model", "text-embedding-3-small")
    concurrency = concurrency or int(emb_cfg.get("concurrency", 4))
    batch_size = batch_size or int(emb_cfg.get("batch_size", 64))
    min_body = int(emb_cfg.get("skip_body_shorter_than", 100))
    skip_folders: list[str] = list(emb_cfg.get("skip_folders", []))

    since_dt = _parse_date_filter(since, "--since")
    until_dt = _parse_date_filter(until, "--until")
    md_files = _iter_md_files(root, folders, limit, skip_folders, since_dt, until_dt)
    if not md_files:
        click.echo("처리할 MD 파일이 없습니다.")
        return

    conn = _open_db(root)
    try:
        existing = _existing_signatures(conn)
        click.echo(
            f"{'[DRY-RUN] ' if dry_run else ''}MD 후보 스캔: {len(md_files)}개  "
            f"| 기존 embedding: {len(existing)}개"
        )

        candidates, skipped_pre = _collect_candidates(
            md_files, existing, model, min_body, force
        )
        if not candidates:
            click.echo(
                f"새로 처리할 항목 없음 (skip {skipped_pre}: 본문 짧음·중복·msgid 누락)."
            )
            return

        click.echo(
            f"대상: {len(candidates)}개  | 사전 skip: {skipped_pre}개  "
            f"| 모델: {model}  | endpoint: {emb_cfg.get('endpoint', '')}"
        )

        if dry_run:
            est_tokens = len(candidates) * _DRY_RUN_TOKENS_PER_INPUT
            est_cost = _estimate_cost(model, est_tokens)
            click.echo(
                f"[DRY-RUN] 예상 입력 ~{est_tokens:,} 토큰 | "
                f"예상 비용 ~${est_cost:.4f} (모델 단가 미등록 시 0.02/1M 가정)"
            )
            return

        try:
            client = EmbeddingClient(cfg)
        except (ValueError, ImportError) as exc:
            click.echo(f"EmbeddingClient 초기화 실패: {exc}", err=True)
            sys.exit(1)

        # 배치 분할
        batches: list[list[dict[str, Any]]] = [
            candidates[i:i + batch_size]
            for i in range(0, len(candidates), batch_size)
        ]

        log_path = root / ".embed.log.jsonl"
        n_ok = n_failed = 0
        total_tokens = 0
        total_cost = 0.0

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_process_batch, client, b): b for b in batches
            }
            with tqdm(total=len(candidates), unit="mail") as pbar:
                for future in as_completed(futures):
                    batch = futures[future]
                    try:
                        resp, _ = future.result()
                        _upsert_results(conn, resp, batch)
                        n_ok += len(batch)
                        total_tokens += resp.input_tokens
                        total_cost += _estimate_cost(resp.model, resp.input_tokens)
                        _append_log(
                            log_path,
                            {
                                "status": _STATUS_OK,
                                "count": len(batch),
                                "model": resp.model,
                                "dim": resp.dim,
                                "input_tokens": resp.input_tokens,
                                "msgids": [c["msgid"] for c in batch],
                            },
                        )
                    except (RuntimeError, ValueError, OSError) as exc:
                        n_failed += len(batch)
                        log.warning("배치 실패 (%d개): %s", len(batch), exc)
                        _append_log(
                            log_path,
                            {
                                "status": _STATUS_FAILED,
                                "count": len(batch),
                                "error": str(exc),
                                "msgids": [c["msgid"] for c in batch],
                            },
                        )
                    pbar.update(len(batch))

        click.echo()
        click.echo(
            f"완료: {n_ok} 건  |  사전 skip: {skipped_pre} 건  |  실패: {n_failed} 건\n"
            f"입력 토큰: {total_tokens:,}  |  비용 ${total_cost:.4f}"
        )
        if n_failed:
            click.echo(f"실패 상세: {log_path}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
