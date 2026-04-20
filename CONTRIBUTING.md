# 기여 가이드

## 개발 환경 설정

의존성 관리는 **[uv](https://docs.astral.sh/uv)** 를 사용합니다.

### uv 설치

```bash
# Linux / WSL / macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 또는 winget
winget install astral-sh.uv
```

### 저장소 클론 및 의존성 설치

```bash
git clone https://github.com/RedDbear1229/psttomd.git
cd psttomd

# 기본 의존성 + 개발 도구 (pytest, ruff, mypy)
uv sync --group dev

# Linux/WSL: pypff 백엔드 포함
uv sync --group dev --extra linux

# Windows: pywin32 포함
uv sync --group dev --extra win32
```

`uv sync` 는 `pyproject.toml` 을 읽어 `.venv/` 를 자동 생성합니다.
별도로 `python -m venv` 를 실행하거나 pip 를 쓸 필요가 없습니다.

### 명령 실행

```bash
# venv 활성화 없이 바로 실행
uv run pst2md --pst /path/to/archive.pst --dry-run
uv run mailgrep "계약서"
uv run pytest tests/ -v

# 또는 venv 활성화 후 직접 실행
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pst2md --pst /path/to/archive.pst --dry-run
pytest tests/ -v
```

### 자주 쓰는 개발 명령

```bash
# 린트 (ruff)
uv run ruff check scripts/

# 자동 포맷
uv run ruff format scripts/

# 타입 체크 (mypy)
uv run mypy scripts/

# 테스트 + 커버리지
uv run pytest tests/ --cov=scripts --cov-report=term-missing
```

---

## 브랜치 전략

| 브랜치 | 용도 |
|---|---|
| `master` | 릴리스 안정 버전 |
| `dev` | 개발 통합 브랜치 |
| `feature/<이름>` | 기능 개발 |
| `fix/<이름>` | 버그 수정 |

PR 은 `dev` 브랜치로 보내주세요.

---

## 코딩 규칙

### 스타일

- **Google-style docstring**: 모든 public 함수/클래스에 Args / Returns / Raises 섹션 작성
- **타입 힌트**: 모든 함수 시그니처에 적용 (`from __future__ import annotations` 포함)
- 예외: `except Exception` 대신 구체적 예외 사용
  ```python
  # Bad
  except Exception:
      pass
  # Good
  except (OSError, ValueError):
      pass
  ```
- DB 연결은 반드시 `finally:` 에서 `conn.close()`

### 크로스플랫폼 필수 사항

- 경로 처리: `Path` 객체 사용, 문자열 변환 시 `replace("\\", "/")`
- 플랫폼 분기: `detect_platform()` 사용 (`"linux"` / `"wsl"` / `"windows"`)
- 새 백엔드 추가 시 `PSTBackend` ABC 구현 + `get_backend()` 팩토리 등록

---

## 커밋 메시지 형식

```
<type>: <한 줄 요약>

<본문 (선택)>
```

| type | 설명 |
|---|---|
| `feat` | 새 기능 |
| `fix` | 버그 수정 |
| `refactor` | 기능 변경 없는 코드 개선 |
| `docs` | 문서 변경 |
| `test` | 테스트 추가/수정 |
| `chore` | 빌드·설정 변경 |

예시:
```
feat: mailview Ctrl-A 첨부 파일 열기 기능 추가

- get_attachments_from_md(): frontmatter 파싱으로 첨부 목록 추출
- handle_open_attachments(): 단일/복수 첨부 처리, 중첩 fzf 피커
- open_file(): xdg-open / wslview / os.startfile 플랫폼 분기
```

---

## 새 PST 백엔드 추가

1. `scripts/lib/pst_backend.py` 에 `PSTBackend` 상속 클래스 작성
   - `open()`, `iter_messages()`, `get_attachment_data()`, `close()` 구현
   - 선택적 의존성 라이브러리는 `open()` 내부에서 import
2. `get_backend()` 팩토리에 분기 추가
3. `pyproject.toml` `[project.optional-dependencies]` 에 의존성 추가
4. `CLAUDE.md` 의존성 표 업데이트

### PypffBackend 특이 사항

- libpff C 레이어 예외는 `getattr(obj, attr, default)` 의 세 번째 인자로 잡히지 않음
  → `_safe_get(raw, attr, default)` 메서드로 모든 속성 접근을 `try/except Exception` 래핑
- 첨부 파일명: `attachment.name` → MAPI record_sets 탐색 (`_get_attachment_name_from_mapi`)
  순서: 0x3707 → 0x3704 → 0x3001

---

## 이슈 / PR

- 이슈: GitHub Issues 에 버그 리포트 또는 기능 요청
- PR: `dev` 브랜치 대상으로 생성, 변경 내용과 테스트 방법 기술
