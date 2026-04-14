# 기여 가이드

## 개발 환경 설정

```bash
git clone https://github.com/RedDbear1229/psttomd.git
cd psttomd

# 가상환경 생성 및 의존성 설치
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 기본 의존성
pip install -e ".[dev]"

# Linux/WSL 추가 (pypff 백엔드)
pip install -e ".[linux]"

# Windows 추가 (win32com 백엔드)
pip install -e ".[win32]"
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

### 테스트

```bash
# 단위 테스트 실행
pytest tests/ -v

# 커버리지 확인
pytest tests/ --cov=scripts --cov-report=term-missing
```

실제 PST 파일이 없어도 `--dry-run` 과 샘플 MD 파일로 대부분 테스트 가능합니다.

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
2. `get_backend()` 팩토리에 분기 추가
3. `pyproject.toml` `[project.optional-dependencies]` 에 의존성 추가
4. `CLAUDE.md` 의존성 표 업데이트

---

## 이슈 / PR

- 이슈: GitHub Issues 에 버그 리포트 또는 기능 요청
- PR: `dev` 브랜치 대상으로 생성, 변경 내용과 테스트 방법 기술
