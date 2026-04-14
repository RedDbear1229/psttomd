# CLAUDE.md — mailtomd 프로젝트 컨텍스트

이 파일은 Claude Code 가 이 저장소에서 작업할 때 참조하는 가이드입니다.

---

## 프로젝트 한 줄 요약

Outlook PST 파일을 YAML frontmatter Markdown 으로 변환하고,
SQLite FTS5 인덱스 + fzf/glow CLI + Obsidian 으로 검색·열람하는
크로스플랫폼(Windows / WSL / Linux) 이메일 아카이브 시스템.

---

## 디렉터리 구조

```
mailtomd/
├── scripts/
│   ├── pst2md.py            # PST → MD 변환 메인 (CID 교체, CAS, frontmatter)
│   ├── build_index.py       # SQLite FTS5 인덱스 빌더 (증분 / 전체 재구축)
│   ├── enrich.py            # Obsidian MOC 자동 생성 (people/threads/projects)
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
│       ├── config.py        # 설정 로더 + detect_platform() + archive_root()
│       ├── pst_backend.py   # PST 파서 추상화 (pypff/readpst/win32com)
│       ├── normalize.py     # RFC 2047 디코딩, 주소/날짜 정규화
│       └── attachments.py   # SHA-256 CAS 첨부 저장
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

---

## 자주 쓰는 커맨드

```bash
# 변환 테스트 (dry-run)
python scripts/pst2md.py --pst /path/to/test.pst --dry-run

# 인덱스 재구축
python scripts/build_index.py --archive ~/mail-archive --rebuild

# 무결성 검증 (샘플 200개)
python scripts/verify_integrity.py --archive ~/mail-archive

# 검색
python scripts/mailgrep.py "계약서" --from 홍길동 --after 2023-01-01

# 뷰어
python scripts/mailview.py "견적"

# 통계
python scripts/mailstat.py summary
```

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
   `tempfile.mktemp` + `os.unlink` 패턴, `finally` 에서 정리.

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
| `libpff-python` | pypff 백엔드 | Linux/WSL |
| `mail-parser` | readpst 백엔드 | 선택 |
| `pywin32` | win32com 백엔드 | Windows |
