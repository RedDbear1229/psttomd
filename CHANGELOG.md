# Changelog

모든 주요 변경 사항을 이 파일에 기록합니다.
형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/) 를 따르고,
버전 관리는 [Semantic Versioning](https://semver.org/lang/ko/) 을 따릅니다.

---

## [0.2.0] - 2026-04-14

### Added
- **크로스플랫폼 지원** (Windows Native / WSL / Linux)
  - `scripts/lib/config.py`: `detect_platform()`, `load_config()`, `~/.mailtomd/config.toml`
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
