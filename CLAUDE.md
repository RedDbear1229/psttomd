# CLAUDE.md — pst2md 프로젝트 컨텍스트

이 파일은 Claude Code 가 이 저장소에서 작업할 때 참조하는 가이드입니다.

---

## 프로젝트 한 줄 요약

Outlook PST 파일을 YAML frontmatter Markdown 으로 변환하고,
SQLite FTS5 인덱스 + fzf/glow CLI + Obsidian 으로 검색·열람하는
크로스플랫폼(Windows / WSL / Linux) 이메일 아카이브 시스템.

---

## 디렉터리 구조

```
pst2md/
├── scripts/
│   ├── pst2md.py            # PST → MD 변환 메인 (CID 교체, CAS, frontmatter)
│   ├── build_index.py       # SQLite FTS5 인덱스 빌더 (증분 / 전체 재구축)
│   ├── enrich.py            # Obsidian MOC 자동 생성 (people/threads/projects)
│   ├── mailenrich.py        # LLM enrichment CLI (요약 / 태그 / 백링크)
│   ├── mailenrich_config.py # mailenrich-config CLI (provider/endpoint/model/token)
│   ├── mailgrep.py          # click CLI: FTS5 전문 검색
│   ├── mailview.py          # click CLI: fzf + glow 인터랙티브 뷰어
│   ├── mailstat.py          # click CLI: 아카이브 통계
│   ├── archive_monthly.py   # 월간 PST 배치 (크로스플랫폼)
│   ├── verify_integrity.py  # MD 파일 무결성 검증
│   ├── mailgrep             # bash 버전 (Linux 하위호환, 수정 금지)
│   ├── mailview             # bash 버전 (Linux 하위호환, 수정 금지)
│   ├── mailstat             # bash 버전 (Linux 하위호환, 수정 금지)
│   ├── archive_monthly.sh   # bash 버전 (Linux 하위호환, 수정 금지)
│   └── lib/
│       ├── config.py        # 설정 로더 + detect_platform() + archive_root() + llm_config()
│       ├── md_io.py         # MD 파일 split/write (본문 바이트 불변 + 원자 쓰기)
│       ├── llm_client.py    # LLM 어댑터 (OpenAI / Anthropic / Ollama)
│       ├── pst_backend.py   # PST 파서 추상화 (pypff/readpst/win32com)
│       ├── normalize.py     # RFC 2047 디코딩, 주소/날짜 정규화
│       └── attachments.py   # SHA-256 CAS 첨부 저장 + magic bytes 확장자 추론
├── tests/
│   └── data/
│       └── test.pst         # 테스트용 샘플 PST (265KB, java-libpst 공개 데이터)
├── docs/
│   ├── guide.md             # 전체 사용 설명서
│   └── runbook.md           # 운영 절차
├── CLAUDE.md                # 이 파일
├── CHANGELOG.md
├── CONTRIBUTING.md
├── config.example.toml      # 설정 파일 예시
├── install_linux.sh
├── install_windows.ps1
└── pyproject.toml
```

---

## 핵심 아키텍처

### 플랫폼 감지

`scripts/lib/config.py` 의 `detect_platform()` 이 모든 분기의 기준:

```
"windows"  sys.platform == "win32"
"wsl"      /proc/version 에 "microsoft" 포함
"linux"    그 외
```

### PST 백엔드 추상화

`scripts/lib/pst_backend.py`:
- `MessageData` 데이터클래스: 모든 백엔드 공통 출력
- `PypffBackend`   — Linux/WSL, `libpff-python`
- `ReadpstBackend` — WSL/Linux, `readpst` CLI → EML → mail-parser
- `Win32ComBackend`— Windows, Outlook COM API (pywin32)
- `get_backend(config)` → 팩토리 함수

#### PypffBackend 방어 처리 세부 사항

libpff C 레이어 예외는 `getattr(obj, attr, default)` 의 세 번째 인자로 잡히지 않는다.
`_safe_get(raw, attr, default)` 메서드가 `try/except Exception` 으로 모든 속성 접근을 래핑한다.

```python
@staticmethod
def _safe_get(raw, attr: str, default=None):
    try:
        val = getattr(raw, attr)
        return val if val is not None else default
    except Exception:
        return default
```

첨부 파일명은 `attachment.name` → MAPI record_sets 순서로 탐색한다:
1. `attachment.name` (pypff 기본 속성)
2. MAPI 0x3707 PR_ATTACH_LONG_FILENAME
3. MAPI 0x3704 PR_ATTACH_FILENAME
4. MAPI 0x3001 PR_DISPLAY_NAME
5. `f"attachment_{index}"` (최종 폴백)

OLE 임베디드 객체(`PR_ATTACH_METHOD = 5`)는 파일명이 없고 PR_DISPLAY_NAME("Untitled" 등)만 존재한다.

### pst2md.py 변환 흐름 (message_to_md)

```
헤더 파싱
  → date_dir 결정 (archive/YYYY/MM/DD) ← 반드시 첨부 처리 전에
  → 첨부 CAS 저장 (store_attachment) → attachment_metas
  → CID 교체: _replace_cid_refs(html_bytes, metas, date_dir, out_root)
  → 본문 변환: extract_body(msg, html_override=cid_replaced_html)
  → 첨부 섹션 삽입: _build_attachment_section(metas, date_dir, out_root)
  → YAML frontmatter + 본문 조립 → .md 저장
```

**중요**: `date_dir` 는 `_replace_cid_refs` / `_build_attachment_section` 호출 전에
반드시 결정되어야 한다 (MD 파일 기준 상대 경로 계산에 사용).

### --resume 체크포인트 동작

Message-ID 없는 아이템(Calendar, Contacts, 일부 내부 아이템)도 처리 가능하도록
`message_to_md()` 와 동일한 seed로 generated msgid를 `convert_pst()` 내부에서 사전 계산한다:

```python
seed = f"{from_addr}{subject}{date_to_iso(dt)}"
generated = f"<generated-{hashlib.sha1(seed.encode()).hexdigest()[:16]}@pst2md>"
```

`done_ids` 는 변환 완료 후 `meta["msgid"]` 로 추가하며 (항상 설정됨),
`index_staging.jsonl` 에는 기존 msgid 중복 체크 후 추가한다.

### 경로 관계

- MD 파일: `<out_root>/archive/YYYY/MM/DD/<filename>.md` (4단계 깊이)
- 첨부 파일: `<out_root>/attachments/<sha2>/<sha256><ext>`
- MD 내 첨부 링크: `os.path.relpath(abs_att, date_dir)` — MD 기준 상대 경로

### fzf 인용부호 전략 (mailview.py)

fzf `execute()` 안에서 경로에 공백이 있을 때:

| 플랫폼 | 셸 | 인용부호 |
|---|---|---|
| linux/wsl | `sh -c` | 작은따옴표 `'` |
| windows | `cmd.exe /c` | 큰따옴표 `"` |

Python 실행 경로(`sys.executable`), 스크립트 경로, `{2}` 플레이스홀더
모두 동일 규칙 적용.

---

## 코딩 규칙

### 공통

- **Google-style docstring** (Args/Returns/Raises/Example 섹션)
- 모든 `import` 는 모듈 최상단 — 함수 내부 import 금지 (단, 선택적 의존성 제외)
- `except Exception` 금지 → 구체적 예외 사용 (`OSError`, `ValueError`, `(json.JSONDecodeError, ValueError)` 등)
  - 예외: libpff C 레이어 방어 처리 (`_safe_get`) 는 `except Exception` 허용
- `conn.close()` 는 반드시 `finally:` 블록에서 호출
- 반환형 힌트: `dict` → `dict[str, Any]`, `Optional` → `Optional[X]`

### 선택적 의존성 import

백엔드별 라이브러리(`pypff`, `win32com`, `mailparser`)는
`open()` 메서드 내부에서 import — 설치되지 않은 환경에서 import 오류 방지.

### 크로스플랫폼

- 경로 구분자: `Path` 객체 사용, 문자열 변환 시 `.replace("\\", "/")`
- 디스크 크기: `_dir_size()` (mailstat.py) — `du -sh` 대체
- 날짜 계산: `dateutil.relativedelta` → `calendar` fallback
- 파일 열기: `open_file(path, plat)` (mailview.py) — `xdg-open` / `wslview` / `os.startfile`

### 주소 정규화 규칙 (normalize.py)

- `normalize_address()`: `@` 없는 문자열은 이메일 주소가 아닌 것으로 판단 → `""` 반환
  - pypff 가 반환하는 `"Unknown"` 같은 플레이스홀더를 이메일로 처리하지 않기 위함
- `address_display()`: `@` 없는 addr 은 display name 으로 처리 → `name or raw` 반환

---

## 개발 환경

### uv (Linux/WSL/Windows — 권장)

의존성 관리는 **uv** 를 사용합니다.

```bash
# 최초 설치 (개발 도구 + Linux 백엔드 포함)
uv sync --group dev --extra linux

# mailenrich (LLM enrichment) 도 함께 설치
uv sync --group dev --extra linux --extra mailenrich

# 의존성 추가 시 (pyproject.toml 수정 후)
uv sync

# 린트 / 포맷 / 타입 체크
uv run ruff check scripts/
uv run ruff format scripts/
uv run mypy scripts/

# 테스트
uv run pytest tests/ -v
```

### pip (Android/Termux — uv 미지원 환경)

uv 는 `aarch64-linux-android` 를 지원하지 않습니다. Termux 에서는 pip 를 직접 사용합니다:

```bash
# 의존성 설치
pip install click tomli tqdm html2text beautifulsoup4 \
    python-slugify chardet python-dateutil mail-parser

# pypff 는 소스 빌드 필요 (ld 심링크 사전 준비)
ln -sf $(which lld) $(dirname $(which lld))/ld
pip install libpff-python

# 패키지 editable 설치 (CLI 커맨드를 PATH 에 등록)
pip install -e .

# mailenrich (LLM enrichment) 사용 시
pip install httpx
# 또는: pip install -e '.[mailenrich]'

# 이후 python scripts/... 없이 바로 실행 가능
pst2md --pst tests/data/test.pst --dry-run
```

---

## 자주 쓰는 커맨드

`pip install -e .` 로 설치한 뒤에는 `python scripts/...` 없이 바로 실행한다.

```bash
# 변환 테스트 (dry-run) — 샘플 PST
pst2md --pst tests/data/test.pst --out ~/mail-archive --dry-run

# 변환 + 재실행 (resume 테스트)
pst2md --pst tests/data/test.pst --out ~/mail-archive
pst2md --pst tests/data/test.pst --out ~/mail-archive --resume
# 결과: skipped: 4 (중복 변환 없음)

# 출력 경로 config.toml 에 영구 저장
pst2md --pst /path/to/archive.pst --save-out

# 설정 확인 / 변경
pst2md-config show
pst2md-config set-output ~/mail-archive
pst2md-config init --force

# 인덱스 재구축
build-index --archive ~/mail-archive --rebuild

# 무결성 검증 (샘플 200개)
verify --archive ~/mail-archive

# 검색
mailgrep "계약서" --from 홍길동 --after 2023-01-01
mailgrep "invoice" --body "payment" --json

# 뷰어
mailview "견적"

# 통계
mailstat summary

# LLM enrichment (mailenrich)
mailenrich-config show                  # 현재 LLM 설정 확인
mailenrich-config set-provider ollama   # 로컬 무료 테스트
mailenrich-config set-endpoint http://localhost:11434
mailenrich-config set-model llama3.1:8b
export LLM_TOKEN=sk-xxxxx              # 토큰 설정 (권장)
mailenrich --dry-run                    # 예상 토큰/비용 확인
mailenrich --limit 10                   # 10개 처리
mailenrich --force --concurrency 8      # 강제 재실행

# uv 환경(Linux/WSL/Windows)에서도 동일
uv run pst2md --pst /path/to/archive.pst --dry-run
```

---

## mailenrich 아키텍처

### MD 파일 구조 (4 개의 `---` 구분자)

```
---
<frontmatter>          ← YAML (기존 키 + LLM 키: summary/llm_tags/related/llm_hash/...)
---

# 제목
**보낸사람:** ...

---

<pristine body>        ← PST 추출 본문 (바이트 불변 — SHA-256 llm_hash 로 감시)

---

<!-- LLM-ENRICH:BEGIN -->
## 요약 (LLM)
...
## 관련 문서 (LLM)
- [[t_xxx]] — 이유
<!-- LLM-ENRICH:END -->

## 첨부 파일 (기존, 위치 보존)
관련: [[t_xxx]]  (기존 footer, 위치 보존)
```

### lib/md_io.py — 핵심 불변식

- `split(path)` → `MdParts(frontmatter, frontmatter_raw, head, body, llm_block, tail)`
  - `rfind` 로 body 종료 구분자 탐색 → body 안의 `---` 수평선에 강건
  - LLM 블록은 tail 에서 정규식 추출, split 시 제거
- `write(path, fm_updates, llm_sections, original)` — 원자 쓰기
  - tmp 파일 → `split(tmp)` 로 재파싱 → `assert body_before == body_after`
  - `tmp.replace(path)` (atomic rename)
  - 예외 발생 시 `tmp.unlink(missing_ok=True)`

### lib/llm_client.py — 어댑터 패턴

| Provider   | 강제 JSON 방식                        |
|------------|--------------------------------------|
| openai     | `response_format={"type":"json_object"}` |
| anthropic  | tool-use (`tool_choice={"type":"tool","name":"enrich_mail"}`) |
| ollama     | `format: "json"` 네이티브 파라미터    |

토큰 우선순위: **env `LLM_TOKEN` > config.toml `[llm].token`**

### 멱등성 / delta 감지

`body_hash = sha256(parts.body.encode("utf-8")).hexdigest()`
- frontmatter / LLM 블록 변경은 해시에 영향 없음
- `parts.frontmatter.get("llm_hash") == body_hash` 이면 skip

---

## 주의 사항

1. **bash 스크립트 4종** (`mailgrep`, `mailview`, `mailstat`, `archive_monthly.sh`) 은
   Linux 하위호환용이므로 수정하지 않는다. Python 버전만 개선한다.

2. **dry_run=True** 일 때 `attachment_metas` 에는 `"path"` 키가 없다.
   `_replace_cid_refs` 와 `_build_attachment_section` 은 `path` 없는 항목을 자동 skip.

3. **CID 교체**는 `not dry_run` 일 때만 수행한다 (파일이 실제로 저장된 경우만).

4. **SQLite FTS5** 인덱스는 contentless — `body` 컬럼 원문은 저장하지 않는다.
   `rebuild` 시 MD 파일을 다시 읽어 재인덱싱한다.

5. **Win32ComBackend** `get_attachment_data` 는 임시 파일 경유 (`SaveAsFile`) —
   `tempfile.mkstemp()` + `os.close(fd)` 로 파일 생성 후 `SaveAsFile` 호출, `finally` 에서 정리.

6. **YAML frontmatter** 문자열 필드의 `"` 는 `_yaml_str()` 로 `'` 로 이스케이프한다.
   msgid, from, subject, folder, thread, in_reply_to, source_pst 에 적용.

7. **날짜 없는 메시지** (Freebusy 등) 는 `date: null` 로 저장하고
   `archive/undated/` 에 `00000000-0000__<slug>__<hash>.md` 로 저장된다.

---

## 의존성 요약

| 패키지 | 용도 | 필수 |
|---|---|---|
| `html2text` | HTML → Markdown | ✓ |
| `beautifulsoup4` | HTML 파싱 | ✓ |
| `python-slugify` | 파일명 슬러그 | ✓ |
| `tqdm` | 진행 바 | ✓ |
| `chardet` | 인코딩 탐지 | ✓ |
| `click` | CLI 프레임워크 | ✓ |
| `python-dateutil` | 날짜 계산 | ✓ |
| `tomli` | TOML 파싱 (Python < 3.11) | ✓ |
| `mail-parser` | readpst 백엔드 EML 파싱 | 선택 |
| `libpff-python` | pypff 백엔드 | Linux/WSL |
| `pywin32` | win32com 백엔드 | Windows |
