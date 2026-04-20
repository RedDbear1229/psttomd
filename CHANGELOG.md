# Changelog

모든 주요 변경 사항을 이 파일에 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 를 따르고,
버전 관리는 [Semantic Versioning](https://semver.org/lang/ko/) 을 따릅니다.

---

## [0.3.0] - 2026-04-14

### Added
- **테스트 인프라** (`tests/data/test.pst`)
  - java-libpst 프로젝트의 공개 샘플 PST (265KB) 다운로드 및 포함
  - Calendar(1) / Contacts(2) / Freebusy(1) — 총 4개 메시지, 첨부 2개
- **magic bytes 기반 첨부 파일 확장자 추론** (`scripts/lib/attachments.py`)
  - `_guess_ext()`: PNG · JPG · PDF · ZIP · DOC(OLE2) · OOXML · GIF · BMP · MP4 · GZ · BZ2 자동 감지
  - 파일명에 확장자 없을 때 자동으로 폴백 적용
- **PypffBackend MAPI record_sets 첨부 파일명 추출** (`scripts/lib/pst_backend.py`)
  - `_get_attachment_name_from_mapi()`: `attachment.name = None` 일 때 record_sets 탐색
  - PR_ATTACH_LONG_FILENAME (0x3707) → PR_ATTACH_FILENAME (0x3704) → PR_DISPLAY_NAME (0x3001) 순서 폴백
  - 결과: `attachment_0` 대신 실제 파일명 또는 표시 이름 반영

### Fixed
- **`--resume` 중복 변환 및 state 저장 버그** (`scripts/pst2md.py`)
  - Calendar/Contact 등 Message-ID 없는 아이템은 `raw_msgid` 빈 문자열 → `done_ids.add()` 호출 안됨 → `.state.json` 항상 비어있던 문제 수정
  - resume 체크 시 `message_to_md()` 와 동일한 seed로 generated msgid 사전 계산
  - `done_ids` 추적을 `raw_msgid` → `meta["msgid"]` (항상 설정됨) 로 변경
  - `index_staging.jsonl` resume 모드에서 기존 msgid 중복 추가 방지
- **`address_display("Unknown")` → `"unknown"` 소문자 변환 버그** (`scripts/lib/normalize.py`)
  - 원인: `parseaddr("Unknown")` → `("", "Unknown")` 에서 `addr.lower()` 적용
  - 수정: `@` 없는 addr은 이메일 주소가 아닌 display name으로 처리 → 원문 보존
- **`normalize_address()` 비이메일 문자열 소문자 반환 버그** (`scripts/lib/normalize.py`)
  - 원인: `addr` 에 `@` 없어도 `addr.lower()` 로 반환
  - 수정: `@` 없으면 `""` 반환 (이메일이 아닌 값 제외)
- **`date:` (빈 값) YAML frontmatter 모호성** (`scripts/pst2md.py`)
  - 날짜 없는 메시지에서 `date: ` (trailing whitespace) 형태로 저장되던 문제
  - 수정: `date: null` 로 명시 (모든 YAML 파서에서 일관성 보장)
- **`Win32ComBackend.get_attachment_data` TOCTOU 경쟁 조건** (`scripts/lib/pst_backend.py`)
  - `tempfile.mktemp()` → `tempfile.mkstemp()` + `os.close(fd)` 로 안전한 임시 파일 생성
- **`ReadpstBackend._to_msgdata` 본문 추출 오류** (`scripts/lib/pst_backend.py`)
  - `mail.body` → `mail.text_html` / `mail.text_plain` 구분 (mail-parser 4.x API)
- **`Win32ComBackend.close()` AttributeError** (`scripts/lib/pst_backend.py`)
  - `self._ns` 없을 때 `close()` 호출 시 AttributeError 방지 (`hasattr` 가드 추가)
- **`convert_pst()` 예외 시 백엔드 리소스 누수** (`scripts/pst2md.py`)
  - `backend.close()` 를 `with get_backend(config) as backend:` 컨텍스트 매니저로 보장
- **YAML frontmatter 인젝션 취약점** (`scripts/pst2md.py`)
  - 발신자명·제목 등에 `"` 포함 시 frontmatter 파싱 깨지는 문제
  - `_yaml_str()` 헬퍼로 `"` → `'` 이스케이프 처리
- **PypffBackend libpff 저수준 예외 방어** (`scripts/lib/pst_backend.py`)
  - `getattr(raw, attr, default)` 는 `AttributeError` 만 잡음 — libpff C 레이어 예외 미처리
  - `_safe_get()` 메서드: 모든 속성 접근을 `try/except Exception` 으로 래핑
  - `number_of_attachments`, 첨부 객체 로드, 파일명/크기/데이터 읽기, 폴더 순회 전 구간 방어 처리
- **`subprocess` import 함수 내부 → 모듈 최상단 이동** (`scripts/lib/pst_backend.py`)

---

## [0.2.1] - 2026-04-14

### Changed
- **개발 환경 관리를 uv 로 일괄 전환**
  - `pyproject.toml`: `[dependency-groups]` 추가 (pytest, ruff, mypy), `[tool.uv]` 섹션 추가
  - `install_linux.sh`: `python3 -m venv` + `pip` → `uv sync --extra linux`
  - `install_windows.ps1`: `pip install` → `uv sync --extra win32`, winget 으로 uv 자동 설치
  - `CONTRIBUTING.md`: 개발 환경 설정 uv 명령어로 전면 교체
  - `CLAUDE.md`: 자주 쓰는 커맨드 `python scripts/*.py` → `uv run <entry-point>`
  - `docs/runbook.md`: 모든 실행 명령 `uv run` 기준으로 업데이트
  - `.gitignore`: `uv.lock` 커밋 대상 유지 (애플리케이션 재현 가능 빌드)

---

## [0.2.0] - 2026-04-14

### Added
- **크로스플랫폼 지원** (Windows Native / WSL / Linux)
  - `scripts/lib/config.py`: `detect_platform()`, `load_config()`, `~/.pst2md/config.toml`
  - `scripts/lib/pst_backend.py`: PST 파서 추상화 (`PypffBackend` / `ReadpstBackend` / `Win32ComBackend`)
- **Python CLI** (click 기반, 3종)
  - `scripts/mailgrep.py`: SQLite FTS5 전문 검색
  - `scripts/mailview.py`: fzf + glow 인터랙티브 뷰어
  - `scripts/mailstat.py`: 아카이브 통계 (summary / monthly / senders / folders / threads / attachments / range)
- **CID 인라인 이미지 처리** (`pst2md.py`)
  - `_replace_cid_refs()`: `src="cid:..."` → MD 파일 기준 상대 경로 교체
  - Obsidian / VS Code 에서 인라인 이미지 정상 표시
- **첨부 파일 Markdown 섹션 자동 삽입** (`pst2md.py`)
  - 이미지: `![name](rel_path)` — Obsidian 인라인 표시
  - 일반 파일: `[name](rel_path) (N KB)` — 클릭 시 OS 기본 앱으로 열기
- **mailview Ctrl-A 첨부 열기**
  - `get_attachments_from_md()`: frontmatter 파싱으로 첨부 목록 추출
  - `handle_open_attachments()`: 단일 → 즉시 열기 / 복수 → 중첩 fzf 피커
  - `open_file()`: `xdg-open` (Linux) / `wslview` (WSL) / `os.startfile` (Windows)
- **fzf execute() 크로스플랫폼 인용부호** (`mailview.py`)
  - Linux/WSL: 작은따옴표 `'...'` (sh)
  - Windows: 큰따옴표 `"..."` (cmd.exe)
- `scripts/archive_monthly.py`: `archive_monthly.sh` Python 크로스플랫폼 재작성
- `install_linux.sh`, `install_windows.ps1`: 플랫폼별 설치 스크립트
- `pyproject.toml`: click/tomli 의존성, optional-deps (linux/win32), entry points

### Changed
- `scripts/pst2md.py`: `pypff` 직접 import → `get_backend()` 추상화 사용
- 모든 스크립트: 설정 기본값을 환경변수(`$MAIL_ARCHIVE`)에서 `config.toml` 로 이전
- 전체 Python 파일 lint 및 리팩토링
  - Google-style docstring 전면 적용
  - `except Exception` → 구체적 예외 (`OSError`, `ValueError` 등)
  - `conn.close()` → `finally:` 블록 이동
  - 함수 내부 import → 모듈 최상단 이동 (선택적 의존성 제외)

### Fixed
- `mailview.py`: `stdin=open(tmp.name)` 파일 핸들 리소스 누수 → `with open(...)` 수정

---

## [0.1.0] - 2026-04-13

### Added
- 최초 구현 (Linux / WSL 전용)
- `scripts/pst2md.py`: pypff 기반 PST → Markdown 변환
  - YAML frontmatter (msgid / date / from / to / cc / subject / thread / attachments)
  - SHA-256 CAS 첨부 파일 저장 (`attachments/<sha2>/<sha256><ext>`)
  - 50MB+ 대용량 첨부 → `attachments_large/` 분리
  - Message-ID 기반 체크포인트 (`--resume`)
  - `--dry-run`, `--cutoff`, `--folder` 옵션
- `scripts/build_index.py`: SQLite FTS5 전문 검색 인덱스
  - 증분 처리 (`index_staging.jsonl`)
  - 전체 재구축 (`--rebuild`)
  - WAL 모드, contentless FTS5
- `scripts/enrich.py`: Obsidian MOC 자동 생성
  - `people/<email>.md`, `threads/<id>.md`, `projects/<tag>.md`
  - Dataview 쿼리 블록 삽입
- `scripts/verify_integrity.py`: 아카이브 무결성 검증
  - frontmatter 필수 필드, SHA-256 해시, UTF-8 인코딩, DB ↔ 파일 수 일치
- `scripts/lib/normalize.py`: RFC 2047 디코딩, CP949/EUC-KR 처리, 날짜 정규화
- `scripts/lib/attachments.py`: SHA-256 CAS 로직
- bash CLI 도구: `mailgrep`, `mailview`, `mailstat`, `archive_monthly.sh`
- `docs/guide.md`: 전체 사용 설명서
- `docs/runbook.md`: 운영 절차 (설치 / 변환 / 월간 배치 / 트러블슈팅)
