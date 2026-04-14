#!/usr/bin/env python3
"""
PST → Markdown 변환기 (크로스플랫폼)

Outlook PST 파일의 모든 메일을 YAML frontmatter 포함 Markdown 파일로 변환한다.

주요 기능:
  - HTML 본문 내 CID 인라인 이미지를 로컬 상대 경로로 교체
    → Obsidian / VS Code 에서 이미지가 정상 표시됨
  - 첨부 파일 섹션을 본문 끝에 자동 삽입
    → 이미지: ![name](path) — Obsidian 인라인 표시
    → 일반 파일: [name](path) — Obsidian 클릭 시 열림
    → glow: 파일명 텍스트로 표시 (클릭 불가)
  - SHA-256 CAS 첨부 저장 (중복 제거)
  - Message-ID 기반 체크포인트로 재시작 지원

사용법:
  python pst2md.py --pst /mnt/c/Users/YOU/Documents/Outlook/archive.pst \\
                   --out ~/mail-archive \\
                   [--cutoff 2024-01-01] [--dry-run] [--resume]

  # Windows (PowerShell)
  python pst2md.py --pst "C:\\Users\\YOU\\Documents\\Outlook\\archive.pst"

옵션:
  --pst       PST 파일 경로 (필수)
  --out       출력 루트 디렉터리 (기본: config.toml 또는 ~/mail-archive)
  --cutoff    이 날짜 이후 메일은 변환 제외 (ISO 8601, 예: 2024-01-01)
  --dry-run   파일을 쓰지 않고 통계만 출력
  --resume    .state.json 체크포인트를 읽어 이어 시작
  --folder    특정 폴더명 패턴만 변환 (정규식)
  --backend   PST 백엔드 강제 지정 (pypff|readpst|win32com|auto)
  --save-out  --out 경로를 ~/.pst2md/config.toml 에 영구 저장
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import html2text
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_config, save_archive_root
from lib.pst_backend import get_backend, MessageData
from lib.normalize import (
    decode_mime_header,
    safe_decode,
    normalize_address,
    parse_address_list,
    address_display,
    normalize_date,
    date_to_iso,
    make_filename,
    make_thread_id,
    make_slug,
)
from lib.attachments import store_attachment, attachment_yaml_entry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

STATE_FILE = ".state.json"
ERRORS_DIR = "errors"

#: 인라인 이미지로 취급할 확장자 집합 (소문자)
IMAGE_EXTS: frozenset[str] = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".svg", ".tiff", ".tif", ".ico", ".heic", ".avif",
})


# ---------------------------------------------------------------------------
# HTML → Markdown 변환기
# ---------------------------------------------------------------------------

def _yaml_str(value: str) -> str:
    """YAML 인라인 문자열 값에서 큰따옴표를 작은따옴표로 치환한다.

    frontmatter 의 'key: "value"' 형식에서 value 안에 큰따옴표가 있으면
    YAML 파싱 오류가 발생하므로 작은따옴표로 대체한다.

    Args:
        value: 원본 문자열.

    Returns:
        큰따옴표가 작은따옴표로 교체된 문자열.
    """
    return value.replace('"', "'")


def _make_html2text() -> html2text.HTML2Text:
    """html2text 변환기 인스턴스를 초기화한다."""
    h = html2text.HTML2Text()
    h.ignore_images = False
    h.ignore_links = False
    h.body_width = 0
    h.protect_links = True
    h.wrap_links = False
    return h


_h2t = _make_html2text()


def html_to_md(html: str) -> str:
    """HTML 문자열을 Markdown 으로 변환한다.

    변환 실패 시 HTML 태그를 단순 제거한 텍스트를 반환한다.

    Args:
        html: HTML 형식 문자열.

    Returns:
        Markdown 문자열.
    """
    try:
        return _h2t.handle(html).strip()
    except Exception as e:
        log.warning("HTML 변환 실패: %s", e)
        return re.sub(r"<[^>]+>", "", html).strip()


# ---------------------------------------------------------------------------
# CID 인라인 이미지 처리
# ---------------------------------------------------------------------------

def _is_image(filename: str) -> bool:
    """파일 확장자가 이미지 형식인지 확인한다.

    Args:
        filename: 파일명 또는 경로 문자열.

    Returns:
        이미지 확장자이면 True.
    """
    return Path(filename).suffix.lower() in IMAGE_EXTS


def _replace_cid_refs(
    html_bytes: bytes,
    attachment_metas: list[dict],
    md_dir: Path,
    out_root: Path,
) -> bytes:
    """HTML 본문 내 cid: 참조를 MD 파일 위치 기준 상대 경로로 교체한다.

    이메일 표준에서 인라인 이미지는 'src="cid:<filename>@<domain>"' 형식으로
    첨부 파트를 참조한다. 이를 실제 CAS 파일의 상대 경로로 교체하면
    Obsidian / VS Code 등 외부 뷰어에서 이미지가 정상 표시된다.

    매핑 방식: CID 의 '@' 앞 부분(파일명)을 첨부 메타의 name 과 대조한다.
    대소문자를 무시해 비교한다.

    Args:
        html_bytes:       원본 HTML 바이트.
        attachment_metas: store_attachment() 가 반환한 메타 dict 리스트.
        md_dir:           생성될 MD 파일이 위치할 디렉터리.
        out_root:         아카이브 루트 (CAS 절대 경로 계산 기준).

    Returns:
        cid: 참조가 상대 경로로 교체된 HTML 바이트.
        교체 대상이 없으면 원본 그대로 반환.
    """
    if not html_bytes or not attachment_metas:
        return html_bytes

    html = safe_decode(html_bytes)

    # 파일명(소문자) → MD 파일 기준 상대 경로 매핑 생성
    name_to_rel: dict[str, str] = {}
    for meta in attachment_metas:
        if "path" not in meta:
            continue
        abs_path = out_root / meta["path"]
        # Windows 역슬래시를 슬래시로 정규화 (Markdown 링크 호환)
        rel = os.path.relpath(abs_path, md_dir).replace("\\", "/")
        name_to_rel[meta["name"].lower()] = rel

    def _sub(match: re.Match) -> str:
        cid = match.group(1)            # "image001.jpg@01D9F3A2..."
        filename = cid.split("@")[0].lower()
        rel = name_to_rel.get(filename)
        return f'src="{rel}"' if rel else match.group(0)

    result = re.sub(r'src="cid:([^"]+)"', _sub, html, flags=re.IGNORECASE)
    return result.encode("utf-8")


# ---------------------------------------------------------------------------
# 첨부 파일 섹션 생성
# ---------------------------------------------------------------------------

def _build_attachment_section(
    attachment_metas: list[dict],
    md_dir: Path,
    out_root: Path,
) -> str:
    """첨부 파일 Markdown 섹션 문자열을 생성한다.

    이미지는 '![name](path)' 로 삽입해 Obsidian/VS Code 에서 인라인 표시되고,
    일반 파일은 '[name](path)' 링크로 삽입해 클릭 시 파일이 열린다.
    glow 에서는 파일명 텍스트만 표시된다.

    경로는 MD 파일 위치 기준 상대 경로를 사용해 vault 이동 후에도 동작한다.

    dry_run 메타 (path 키 없음) 는 자동으로 제외된다.

    Args:
        attachment_metas: store_attachment() 반환 메타 dict 리스트.
        md_dir:           MD 파일이 위치할 디렉터리.
        out_root:         아카이브 루트.

    Returns:
        "## 첨부 파일\\n\\n..." 형태의 Markdown 문자열.
        실제 첨부가 없으면 빈 문자열.
    """
    # dry_run 항목(path 없음)은 제외
    real_atts = [m for m in attachment_metas if "path" in m]
    if not real_atts:
        return ""

    lines: list[str] = ["## 첨부 파일", ""]
    for meta in real_atts:
        name = meta["name"]
        abs_path = out_root / meta["path"]
        rel = os.path.relpath(abs_path, md_dir).replace("\\", "/")

        if _is_image(name):
            # 이미지: Obsidian/VS Code 인라인 표시
            lines.append(f"![{name}]({rel})")
        else:
            # 일반 파일: Obsidian 클릭 열기 / glow 텍스트 표시
            size_kb = meta.get("size", 0) // 1024
            size_str = f" ({size_kb:,} KB)" if size_kb else ""
            lines.append(f"[{name}]({rel}){size_str}")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 메시지 본문 추출
# ---------------------------------------------------------------------------

def extract_body(
    msg: MessageData,
    html_override: Optional[bytes] = None,
) -> str:
    """MessageData 에서 Markdown 형식 본문을 추출한다.

    우선순위: HTML 본문 → 평문 본문 → RTF 본문.
    html_override 가 주어지면 msg.html_body 대신 사용한다
    (CID 교체 후 HTML 을 전달할 때 사용).

    Args:
        msg:           변환할 메시지 데이터.
        html_override: CID 교체 등으로 전처리된 HTML 바이트 (선택).

    Returns:
        Markdown 형식 본문 문자열.
    """
    html_body = html_override if html_override is not None else msg.html_body

    if html_body:
        text = safe_decode(html_body) if isinstance(html_body, bytes) else html_body
        return html_to_md(text)

    if msg.plain_text_body:
        text = (
            safe_decode(msg.plain_text_body)
            if isinstance(msg.plain_text_body, bytes)
            else msg.plain_text_body
        )
        return text.strip()

    if msg.rtf_body:
        raw = safe_decode(msg.rtf_body) if isinstance(msg.rtf_body, bytes) else msg.rtf_body
        plain_rtf = re.sub(r"\\[a-z]+\d* ?|[{}]", "", raw)
        return plain_rtf.strip()

    return ""


# ---------------------------------------------------------------------------
# 메시지 → Markdown 파일
# ---------------------------------------------------------------------------

def message_to_md(
    msg: MessageData,
    folder_path: str,
    out_root: Path,
    pst_filename: str,
    backend,
    dry_run: bool = False,
) -> Optional[dict]:
    """메시지 1통을 Markdown 파일로 변환하고 메타데이터 dict 를 반환한다.

    변환 흐름:
      1. 헤더 파싱 (from/to/date/subject/thread)
      2. 첨부 파일 CAS 저장
      3. CID 인라인 이미지 → 로컬 상대 경로 교체
      4. 본문 변환 (HTML/plain/RTF → Markdown)
      5. 첨부 파일 섹션 본문 끝에 삽입
      6. YAML frontmatter + 본문 조립 → 파일 저장

    dry_run=True 이면 파일 저장과 CID 교체를 건너뛴다.

    Args:
        msg:          변환할 MessageData.
        folder_path:  PST 내 폴더 경로 (예: "Inbox/ProjectX").
        out_root:     아카이브 루트 디렉터리.
        pst_filename: 원본 PST 파일명 (frontmatter 기록용).
        backend:      첨부 파일 추출에 사용할 PSTBackend 인스턴스.
        dry_run:      True 이면 파일 쓰기 생략.

    Returns:
        메타데이터 dict. 변환 오류 시 None.
    """
    try:
        # ── 헤더 추출 ──────────────────────────────────────────────────────
        msgid       = decode_mime_header(msg.message_identifier or "")
        subject_raw = decode_mime_header(msg.subject or "")
        subject     = subject_raw.strip() or "(제목 없음)"

        from_raw  = address_display(decode_mime_header(msg.sender_name or ""))
        from_addr = normalize_address(decode_mime_header(msg.sender_email_address or ""))
        if from_addr and not from_raw:
            from_raw = from_addr

        to_raw  = decode_mime_header(msg.display_to or "")
        cc_raw  = decode_mime_header(msg.display_cc or "")
        to_list = parse_address_list(to_raw)
        cc_list = parse_address_list(cc_raw)

        date_val = msg.client_submit_time
        if date_val:
            if isinstance(date_val, datetime):
                dt = date_val if date_val.tzinfo else date_val.replace(tzinfo=timezone.utc)
            else:
                dt = normalize_date(str(date_val))
        else:
            dt = None

        in_reply_to   = decode_mime_header(msg.in_reply_to_identifier or "")
        references_raw = decode_mime_header(msg.references or "")
        references    = [r.strip() for r in references_raw.split() if r.strip()]

        if not msgid:
            seed  = f"{from_addr}{subject}{date_to_iso(dt)}"
            msgid = f"<generated-{hashlib.sha1(seed.encode()).hexdigest()[:16]}@pst2md>"

        thread_id = make_thread_id(references, in_reply_to, msgid)

        # ── 출력 경로 결정 (첨부 상대 경로 계산에 필요) ───────────────────
        if dt:
            date_dir = out_root / "archive" / dt.strftime("%Y/%m/%d")
        else:
            date_dir = out_root / "archive" / "undated"

        filename = make_filename(dt, subject, msgid)
        filepath = date_dir / filename

        # ── 첨부 파일 처리 ─────────────────────────────────────────────────
        attachment_metas: list[dict] = []
        for i in range(msg.number_of_attachments):
            try:
                att_name, att_data = backend.get_attachment_data(msg, i)
                att_name = decode_mime_header(att_name) or f"attachment_{i}"
                if att_data and not dry_run:
                    meta = store_attachment(att_data, att_name, out_root / "attachments")
                    attachment_metas.append(meta)
                elif att_data:
                    # dry_run: 파일 저장 없이 이름·크기만 기록
                    attachment_metas.append({"name": att_name, "size": len(att_data)})
            except Exception as e:
                log.warning("첨부 추출 실패 [%s]: %s", subject, e)

        # ── CID 인라인 이미지 → 로컬 상대 경로 교체 ──────────────────────
        # 파일이 실제로 저장된 경우(not dry_run)에만 교체 가능
        html_for_body: Optional[bytes] = None
        if msg.html_body and attachment_metas and not dry_run:
            html_for_body = _replace_cid_refs(
                msg.html_body, attachment_metas, date_dir, out_root
            )

        # ── 본문 변환 ──────────────────────────────────────────────────────
        body = extract_body(msg, html_override=html_for_body)

        # ── 첨부 파일 섹션 본문 끝에 추가 ────────────────────────────────
        # glow: 파일명 텍스트만 보임 / Obsidian·VS Code: 이미지 표시 + 파일 열기
        if not dry_run:
            att_section = _build_attachment_section(attachment_metas, date_dir, out_root)
            if att_section:
                body = f"{body}\n\n{att_section}"

        # ── 태그 (폴더 경로 기반) ──────────────────────────────────────────
        tags: list[str] = []
        for part in folder_path.split("/"):
            slug = make_slug(part, max_len=20)
            if slug and slug not in ("root", "no-subject"):
                tags.append(slug)

        # ── Wikilink 섹션 ──────────────────────────────────────────────────
        people_links = []
        if from_addr:
            people_links.append(f"[[{from_addr}|{from_raw}]]")
        for addr in to_list[:5]:
            people_links.append(f"[[{addr}]]")

        related_parts = [f"[[{thread_id}]]"]
        if people_links:
            related_parts.extend(people_links[:3])
        for tag in tags[:3]:
            related_parts.append(f"[[{tag}]]")
        related_line = " · ".join(related_parts)

        # ── YAML frontmatter 조립 ──────────────────────────────────────────
        att_yaml = "\n".join(
            attachment_yaml_entry(m) for m in attachment_metas if "sha256" in m
        )
        att_fm_section = f"attachments:\n{att_yaml}" if att_yaml else "attachments: []"

        to_yaml   = json.dumps(to_list, ensure_ascii=False)
        cc_yaml   = json.dumps(cc_list, ensure_ascii=False)
        tags_yaml = json.dumps(tags, ensure_ascii=False)
        refs_yaml = json.dumps(references, ensure_ascii=False)

        frontmatter = (
            f'---\n'
            f'msgid: "{_yaml_str(msgid)}"\n'
            f'date: {date_to_iso(dt) or "null"}\n'
            f'from: "{_yaml_str(from_raw)}"\n'
            f'to: {to_yaml}\n'
            f'cc: {cc_yaml}\n'
            f'subject: "{_yaml_str(subject)}"\n'
            f'folder: "{_yaml_str(folder_path)}"\n'
            f'thread: "{_yaml_str(thread_id)}"\n'
            f'in_reply_to: "{_yaml_str(in_reply_to)}"\n'
            f'references: {refs_yaml}\n'
            f'{att_fm_section}\n'
            f'tags: {tags_yaml}\n'
            f'source_pst: "{_yaml_str(pst_filename)}"\n'
            f'---'
        )

        content = f"{frontmatter}\n\n# {subject}\n\n{body}\n\n---\n\n관련: {related_line}\n"

        meta = {
            "msgid":         msgid,
            "date":          date_to_iso(dt),
            "from":          from_raw,
            "from_addr":     from_addr,
            "to":            to_list,
            "cc":            cc_list,
            "subject":       subject,
            "folder":        folder_path,
            "thread":        thread_id,
            "path":          str(filepath),
            "source_pst":    pst_filename,
            "n_attachments": len(attachment_metas),
        }

        if not dry_run:
            date_dir.mkdir(parents=True, exist_ok=True)
            filepath.write_text(content, encoding="utf-8")

        return meta

    except Exception as e:
        log.error("변환 실패: %s", e, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# 체크포인트
# ---------------------------------------------------------------------------

def load_state(out_root: Path) -> set[str]:
    """이전 변환에서 완료된 Message-ID 집합을 불러온다."""
    state_path = out_root / STATE_FILE
    if state_path.exists():
        data = json.loads(state_path.read_text(encoding="utf-8"))
        return set(data.get("done_msgids", []))
    return set()


def save_state(out_root: Path, done: set[str]) -> None:
    """완료된 Message-ID 집합을 체크포인트 파일에 저장한다."""
    state_path = out_root / STATE_FILE
    state_path.write_text(
        json.dumps({"done_msgids": list(done)}, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# PST 변환 메인 루프
# ---------------------------------------------------------------------------

def convert_pst(
    pst_path: Path,
    out_root: Path,
    config: dict,
    cutoff: Optional[datetime] = None,
    dry_run: bool = False,
    resume: bool = False,
    folder_filter: Optional[str] = None,
) -> dict:
    """PST 파일의 모든 메시지를 순회하며 Markdown 으로 변환한다."""
    pst_filename = pst_path.name
    done_ids: set[str] = load_state(out_root) if resume else set()
    folder_re = re.compile(folder_filter) if folder_filter else None

    stats = {
        "total": 0, "converted": 0,
        "skipped": 0, "error": 0, "attachments": 0,
    }
    index_rows: list[dict] = []

    if not dry_run:
        (out_root / ERRORS_DIR).mkdir(parents=True, exist_ok=True)

    # 컨텍스트 매니저로 감싸 예외 발생 시에도 backend.close() 가 반드시 호출됨
    with get_backend(config) as backend:
        backend.open(str(pst_path))

        log.info("PST 폴더 트리 스캔 중...")
        all_msgs = list(tqdm(
            backend.iter_messages(),
            unit="msg",
            desc="  스캔",
            dynamic_ncols=True,
            leave=False,
        ))
        log.info("총 %d개 메시지 발견", len(all_msgs))

        with tqdm(
            all_msgs,
            unit="msg",
            desc=pst_filename,
            dynamic_ncols=True,
        ) as pbar:
          for folder_path, msg in pbar:
            stats["total"] += 1

            if folder_re and not folder_re.search(folder_path):
                stats["skipped"] += 1
                pbar.set_postfix_str(
                    f"변환={stats['converted']} "
                    f"skip={stats['skipped']} "
                    f"오류={stats['error']} "
                    f"첨부={stats['attachments']}",
                    refresh=False,
                )
                continue

            raw_msgid = decode_mime_header(msg.message_identifier or "")

            # resume 체크: PST에 Message-ID가 없는 경우(캘린더·연락처 등)도
            # message_to_md()와 동일한 결정론적 생성 ID로 중복 검사
            if resume:
                if raw_msgid:
                    check_msgid = raw_msgid
                else:
                    _from_addr = normalize_address(
                        decode_mime_header(msg.sender_email_address or "")
                    )
                    _subject = (
                        decode_mime_header(msg.subject or "").strip() or "(제목 없음)"
                    )
                    _dt_iso = date_to_iso(
                        msg.client_submit_time
                        if isinstance(msg.client_submit_time, datetime)
                        else None
                    )
                    _seed = f"{_from_addr}{_subject}{_dt_iso}"
                    check_msgid = (
                        f"<generated-"
                        f"{hashlib.sha1(_seed.encode()).hexdigest()[:16]}"
                        f"@pst2md>"
                    )
                if check_msgid in done_ids:
                    stats["skipped"] += 1
                    pbar.set_postfix_str(
                        f"변환={stats['converted']} "
                        f"skip={stats['skipped']} "
                        f"오류={stats['error']} "
                        f"첨부={stats['attachments']}",
                        refresh=False,
                    )
                    continue

            if cutoff:
                date_val = msg.client_submit_time
                if isinstance(date_val, datetime):
                    msg_dt = (
                        date_val if date_val.tzinfo
                        else date_val.replace(tzinfo=timezone.utc)
                    )
                    if msg_dt >= cutoff:
                        stats["skipped"] += 1
                        pbar.set_postfix_str(
                            f"변환={stats['converted']} "
                            f"skip={stats['skipped']} "
                            f"오류={stats['error']} "
                            f"첨부={stats['attachments']}",
                            refresh=False,
                        )
                        continue

            meta = message_to_md(
                msg, folder_path, out_root, pst_filename, backend, dry_run
            )

            if meta is None:
                stats["error"] += 1
            else:
                stats["converted"]   += 1
                stats["attachments"] += meta.get("n_attachments", 0)
                index_rows.append(meta)
                # meta["msgid"]는 PST ID 또는 생성 ID로 항상 설정됨
                done_ids.add(meta["msgid"])

            pbar.set_postfix_str(
                f"변환={stats['converted']} "
                f"skip={stats['skipped']} "
                f"오류={stats['error']} "
                f"첨부={stats['attachments']}",
                refresh=False,
            )

    if not dry_run:
        save_state(out_root, done_ids)
        jsonl_path = out_root / "index_staging.jsonl"
        if resume and jsonl_path.exists():
            # 이미 기록된 msgid 읽어 중복 방지
            existing_msgids: set[str] = set()
            for line in jsonl_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    try:
                        existing_msgids.add(json.loads(line)["msgid"])
                    except (json.JSONDecodeError, KeyError):
                        pass
            with jsonl_path.open("a", encoding="utf-8") as f:
                for row in index_rows:
                    if row["msgid"] not in existing_msgids:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        else:
            with jsonl_path.open("w", encoding="utf-8") as f:
                for row in index_rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

    return stats


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def main() -> None:
    """명령행 인자를 파싱하고 convert_pst() 를 실행한다."""
    cfg = load_config()

    parser = argparse.ArgumentParser(description="PST → Markdown 변환기 (크로스플랫폼)")
    parser.add_argument("--pst",     required=True, help="PST 파일 경로")
    parser.add_argument("--out",     default=cfg["archive"]["root"], help="출력 루트")
    parser.add_argument("--cutoff",  help="이 날짜 이후 메일 제외 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="파일 미생성, 통계만")
    parser.add_argument("--resume",  action="store_true", help="체크포인트 이어 시작")
    parser.add_argument("--folder",  help="폴더 필터 정규식")
    parser.add_argument(
        "--backend",
        choices=["auto", "pypff", "readpst", "win32com"],
        help="PST 백엔드 강제 지정 (기본: config.toml 값)",
    )
    parser.add_argument(
        "--save-out",
        action="store_true",
        help="--out 경로를 ~/.pst2md/config.toml 에 영구 저장",
    )
    args = parser.parse_args()

    if args.backend:
        cfg["pst_backend"] = args.backend

    pst_path = Path(args.pst)
    if not pst_path.exists():
        sys.exit(f"PST 파일 없음: {pst_path}")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    if args.save_out:
        saved = save_archive_root(out_root)
        log.info("아웃풋 폴더를 config.toml 에 저장했습니다: %s → %s", out_root, saved)

    cutoff = None
    if args.cutoff:
        cutoff = datetime.fromisoformat(args.cutoff).replace(tzinfo=timezone.utc)

    log.info(
        "변환 시작: %s → %s (백엔드: %s)",
        pst_path, out_root, cfg.get("pst_backend", "auto"),
    )
    stats = convert_pst(
        pst_path=pst_path,
        out_root=out_root,
        config=cfg,
        cutoff=cutoff,
        dry_run=args.dry_run,
        resume=args.resume,
        folder_filter=args.folder,
    )

    print("\n=== 변환 결과 ===")
    for k, v in stats.items():
        print(f"  {k:12}: {v:,}")

    if args.dry_run:
        print("\n[dry-run] 파일은 생성되지 않았습니다.")


if __name__ == "__main__":
    main()
