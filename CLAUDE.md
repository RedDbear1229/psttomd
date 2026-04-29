# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## 프로젝트 한 줄 요약 (pst2md)

Outlook PST 파일을 YAML frontmatter Markdown 으로 변환하고,
SQLite FTS5 인덱스 + fzf/glow·mdcat CLI + Obsidian 으로 검색·열람하는
크로스플랫폼(Linux / WSL / Windows Native) 이메일 아카이브 시스템.

> **지원 환경**: Linux · WSL · Windows Native 전용. Termux/Android 는 공식 지원하지 않습니다.

---

## 디렉터리 구조 (요약)

```
scripts/
├── pst2md.py            PST → MD 변환 (CID 교체, CAS 첨부, frontmatter)
├── build_index.py       SQLite FTS5 인덱스 빌더 (증분 / --rebuild)
├── enrich.py            Obsidian MOC (people/threads/projects)
├── mailenrich.py        LLM enrichment (요약/태그/백링크)
├── embed.py             OpenAI 호환 embedding 생성 (index.sqlite BLOB 저장, 중복 분석 방지)
├── mailgrep.py          FTS5 전문 검색 (click CLI)
├── mailview.py          fzf + glow/mdcat 인터랙티브 뷰어
├── mailstat.py          아카이브 통계
├── archive_monthly.py   월간 PST 배치
├── verify_integrity.py  MD · 인덱스 무결성 검증
├── config_cli.py        pst2md-config (범용 set/get/unset/path/edit)
├── mailenrich_config.py mailenrich-config (LLM 설정 thin wrapper)
├── {mailgrep,mailview,mailstat,archive_monthly.sh}  bash 하위호환 (수정 금지)
└── lib/
    ├── config.py          설정 로더 · detect_platform() · archive_root() · llm_config() · TOML 저장 헬퍼
    ├── config_schema.py   KNOWN_KEYS 레지스트리 (타입·기본값·민감 플래그) — pst2md-config 단일 진실원
    ├── md_io.py           MD split/write (본문 바이트 불변 + 원자 쓰기)
    ├── llm_client.py      LLM 어댑터 (OpenAI / Anthropic / Ollama, 강제 JSON)
    ├── embed_client.py    OpenAI 호환 /v1/embeddings 어댑터 (provider 분기 없음)
    ├── pst_backend.py     PST 파서 추상화 (pypff / readpst / win32com)
    ├── normalize.py       RFC 2047 디코딩, 주소/날짜 정규화
    └── attachments.py     SHA-256 CAS 첨부 저장 + magic bytes 확장자 추론

tests/                   pytest — 364 케이스 (config_cli_generic, mailview_doctor,
                         pst2md_autoindex 등 포함)
docs/{guide,runbook,hangul-input}.md
config.example.toml · install_{linux.sh,windows.ps1} · pyproject.toml
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

### 설정 스키마 레지스트리 (KNOWN_KEYS)

`scripts/lib/config_schema.py` 의 `KNOWN_KEYS: dict[str, KeySpec]` 가
`pst2md-config` CLI 의 **단일 진실 원천**이다. 각 항목은 dotted path
(`llm.scope.tag_max_count` 등), 타입(`str/int/bool/list/choice`), 기본값,
설명, 민감 플래그를 담는다.

- 새 설정을 추가할 때는 `DEFAULT_CONFIG` (config.py) 와 `KNOWN_KEYS`
  (config_schema.py) **양쪽 모두** 수정해야 한다. 1:1 대응이 깨지면 생성된
  config.toml 과 CLI 가 어긋난다.
- `pst2md-config set KEY VALUE` 는 `KNOWN_KEYS` 에 없는 키를 거부하고
  `difflib.get_close_matches` 로 근접 키를 제안한다.
- `sensitive=True` 키(예: `llm.token`) 는 `show`/`get` 시 마스킹되고
  `set` 시 stderr 경고를 출력한다.
- 값 쓰기는 `lib/config.py::save_setting()` / `unset_setting()` 이 처리한다.
  `_toml_key_line`, `_replace_in_section`, `_remove_in_section` 이 기존
  주석을 보존하며 in-place 로 수정한다. 섹션 사이 빈 줄은 저장 직후
  `re.sub(r"([^\n])\n\[", r"\1\n\n[", updated)` 로 정규화한다.

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

### CLI --help 규약

- **argparse 도구** (`pst2md`, `build-index`, `verify`, `enrich`, `archive-monthly`):
  `prog=`, 확장된 `description`, 예시가 포함된 `epilog`, `RawDescriptionHelpFormatter`,
  그리고 argument_group(입력/출력/동작) 으로 섹션 분리. 모든 argument 에 `metavar` 명시.
- **click 도구** (`mailgrep`, `mailview`, `mailstat`, `mailenrich`, `embed`,
  `mailenrich-config`, `pst2md-config`): epilog 블록 각 단락 앞에 `\b` 를 넣어
  click 이 공백을 reflow 하지 않도록 한다. 모든 `@click.option` 에 `metavar=` 명시.
- `help_option_names=["-h", "--help"]` 를 `context_settings` 에 추가해 `-h` 단축키를 제공한다.

---

## 개발 환경

### uv (Linux/WSL/Windows — 권장)

의존성 관리는 **uv** 를 사용합니다.

```bash
# 최초 설치 (개발 도구 + Linux 백엔드 포함)
# dev 그룹에 libpff-python(pypff) 이 sys_platform=='linux' 마커로 포함되어 있어
# Linux/WSL 에서는 --extra linux 없이도 자동 설치됨.
uv sync --group dev

# mailenrich (LLM enrichment) 도 함께 설치
uv sync --group dev --extra mailenrich

# Windows 네이티브(Outlook COM) 백엔드가 필요하면
uv sync --group dev --extra win32

# 의존성 추가 시 (pyproject.toml 수정 후)
uv sync

# 린트 / 포맷 / 타입 체크
uv run ruff check scripts/
uv run ruff format scripts/
uv run mypy scripts/

# 테스트 (현재 364 케이스)
uv run pytest tests/ -v

# 단일 파일 / 단일 테스트만
uv run pytest tests/test_config_cli_generic.py -v
uv run pytest tests/test_md_io.py::TestWrite::test_body_bytes_preserved -v

# 키워드 필터
uv run pytest -k "doctor or autoindex"
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
# 변환 완료 후 자동으로 build-index 가 증분 모드로 실행된다.
# 건너뛰려면 --no-index (대량 재변환 후 한 번에 --rebuild 할 때 권장)
pst2md --pst tests/data/test.pst --out ~/mail-archive --no-index

# 출력 경로 config.toml 에 영구 저장
pst2md --pst /path/to/archive.pst --save-out

# 설정 확인 / 변경 (모든 설정은 ~/.pst2md/config.toml 에 통합 — 분산 없음)
pst2md-config show                    # 전체 출력 (민감값 마스킹)
pst2md-config show llm                # 섹션만
pst2md-config get archive.root        # 단일 키 (스크립트용)
pst2md-config set archive.root ~/mail-archive          # 범용 set
pst2md-config set pst_backend pypff
pst2md-config set mailview.auto_index false
pst2md-config set mailview.preview_viewer mdcat        # glow ↔ mdcat
pst2md-config set llm.provider ollama
pst2md-config set llm.concurrency 8
pst2md-config unset mailview.glow_style                # 기본값 복원
pst2md-config path                    # 설정 파일 절대 경로
pst2md-config edit                    # $EDITOR 로 열기
pst2md-config set-output ~/mail-archive     # alias = set archive.root ...
pst2md-config set-viewer mdcat              # alias = set mailview.preview_viewer ...
pst2md-config init --force
# 구형 `set glow|mdcat` 은 deprecated — set-viewer 사용 권장 (stderr 경고).

# 인덱스 재구축
build-index --archive ~/mail-archive --rebuild

# 무결성 검증 (샘플 200개)
verify --archive ~/mail-archive

# 검색
mailgrep "계약서" --from 홍길동 --after 2023-01-01
mailgrep "invoice" --body "payment" --json

# 뷰어
mailview "견적"
mailview --doctor           # 플랫폼/locale/fzf/glow/mdcat/bat/awk 진단 출력 후 종료
# fzf 내부: Esc = 쿼리+필터 초기화 (Ctrl-R 동일), ':q'+Enter = 종료 (Linux/WSL)
# 한글 입력 문제는 docs/hangul-input.md 참고
# 인라인 이미지 (기본: Kitty/WezTerm/iTerm2/Windows Terminal 1.22+ sixel):
#   preview + Enter 전체 열람 양쪽 모두 mdcat(기본)이 sixel/그래픽 프로토콜로 인라인 렌더.
#   mdcat 미설치 시 자동으로 glow 폴백 (이미지는 텍스트 링크로만 표시).
#   sixel 미지원 구형 터미널이면 `pst2md-config set-viewer glow` 로 전환.
#   설치: cargo install mdcat-ng  (sixel 기본 활성화 fork, 바이너리는 'mdcat' 으로 설치됨)
#   Enter 렌더링은 pager 미사용(less 경유 시 그래픽 깨짐) — 터미널 스크롤로 이동.

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

# Embedding 생성 (embed) — 본문을 float 벡터화 → index.sqlite 저장
# OpenAI 호환 /v1/embeddings 면 어떤 서버든 동작 (OpenAI · Ollama · LM Studio).
pst2md-config set embedding.endpoint http://localhost:11434/v1   # Ollama 예시
pst2md-config set embedding.model    nomic-embed-text
export EMBEDDING_TOKEN=sk-xxxx                                  # OpenAI 사용 시
embed --dry-run                         # 후보 수 / 예상 토큰
embed --limit 200                       # 최대 200개
embed --force                           # body_hash 무시 재실행
# 중복 분석 방지: msgid 별 (body_hash, model) 쌍을 저장 — 본문·모델 동일 시 skip.

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

8. **pst2md 재변환 후 mailenrich 재호출**: `_clean_md_body()` 가 NBSP/zero-width 문자를
   정규화하므로 기존 아카이브를 다시 변환하면 body 바이트가 바뀐다 → `llm_hash` 불일치로
   `mailenrich` 가 LLM 재호출을 요청한다. 예상 비용은 `mailenrich --dry-run` 으로 확인하고,
   재처리가 필요하면 `mailenrich --force` 로 일괄 재실행한다.

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
