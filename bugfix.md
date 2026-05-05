# 오류개선 (Bug Fixes)

## 목적

기존 코드의 결함, 플랫폼 호환성 문제, 테스트 인프라 결함을 추적한다.
새로운 기능 추가는 [features.md](features.md) 에서 별도 관리한다.

---

## ✅ 완료된 항목

### 1. WSL fzf 검색 워크플로우 (구 fixplan.md)

| # | 항목 | 결함 내용 |
|---|---|---|
| F1 | `Ctrl-B` 본문 검색 동작 | 첫 reload 후 쿼리 초기화 → `change:transform(...)` 으로 연속 쿼리 |
| F2 | 제목 검색 범위 | recent 100건만 → `Ctrl-S` DB 재로드 모드, `mailgrep --subject` 추가 |
| F3 | 한글 부분 검색 누락 | `unicode61` 단독 → `prefix='2 3 4'` + 자동 와일드카드 |
| F4 | FTS 쿼리 이스케이프 | `C++`, `2024-05` 등 punctuation 쿼리 실패 → 안전한 phrase 처리 + `--raw-fts` |
| F5 | 인덱스 복구 누락 | `cp -p`/`rsync -a` 시 mtime 보존 → 파일수↔DB row drift 보조 감지 |
| F6 | `--rebuild` 첨부 카운트 0 | 인라인 YAML 미인식 → 인라인/블록 모두 카운트 |
| F7 | `--all-archives` 조기 종료 | 기본 DB 부재 시 다른 아카이브 무시 → 가드 분기 이동 |
| F8 | WSL fzf 동작 검증 부재 | `scripts/wsl_smoke.sh` 7단계 자동화 |

### 2. 코드 품질 / 플랫폼 호환성 (구 project-improvement-roadmap.md)

| # | 항목 | 결함 내용 |
|---|---|---|
| R1 | `init_config_file` 생성 구조 | `pst_backend` 가 `[archive]` 섹션 안에 nested → 최상위 키로 이동 |
| R2 | 첨부 경로 백슬래시 | Windows 에서 `attachments\b9\..` → `.as_posix()` 정규화 |
| R3 | FTS 본문 인덱스 오염 | 첨부 테이블·LLM 블록까지 인덱싱 → `md_io.split().body` 만 인덱싱 |
| R13 | `chmod(0o555)` 테스트 실패 | Windows 권한 모델 차이 → `unittest.mock.patch` 기반 mock 으로 교체 |

### 3. 변환 파이프라인 (구 CHANGELOG.md `0.3.0`)

- `--resume` 중복 변환 — Calendar/Contact 등 Message-ID 없는 아이템 처리
- `address_display("Unknown")` 소문자 변환 버그
- `date:` 빈 값 YAML 모호성 → `date: null` 명시
- `Win32ComBackend.get_attachment_data` TOCTOU 경쟁 조건
- `ReadpstBackend._to_msgdata` 본문 추출 (mail-parser 4.x API 변경)
- YAML frontmatter 인젝션 취약점 (`"` → `'` 이스케이프)
- libpff C 레이어 예외 미처리 → `_safe_get()` 도입

---

## ❌ 미완료 항목

### B1. Windows 환경 테스트 실패 (총 10건)

| 파일 | 건수 | 원인 |
|---|---|---|
| `tests/test_config.py` | 2 | `Path("/from/toml")` 백슬래시 변환, `HOME` env var 미반영 |
| `tests/test_config_cli_generic.py` | 8 | Windows 경로 구분자, set/unset/legacy bridge 로직 |

**조치 방안**:
- `test_config.py::test_toml_file_applied`: `Path` 비교 시 `as_posix()` 사용 또는 `os.sep` 정규화
- `test_config.py::test_home_tilde_expanded`: `Path.home()` monkeypatch 로 변경 (Windows 는 `HOME` env var 무시)
- `test_config_cli_generic.py`: `_replace_in_section` 의 행 종결자(`\r\n` vs `\n`) 처리 점검 필요

### B2. 인라인 이미지 CID 매칭 결함 (구 inline-image-storage-review.md)

| # | 결함 | 영향 |
|---|---|---|
| I2 | CID 매칭이 filename 단순 비교 | `cid:part1.06090908.01060107@example` 형태 미매칭 → 본문 깨진 링크 |
| I3 | CID regex 협소 | `src='cid:..'`, `SRC="CID:.."`, 공백 형태 누락 |
| I4 | 인라인 이미지 중복 렌더 | 본문에 표시 + 첨부 섹션에 재표시 |
| I5 | frontmatter path ↔ 본문 링크 불일치 | 한쪽이 백슬래시면 `mailview` open/delete 실패 |

**조치 방안**:
1. 백엔드 메타데이터 확장 — `content_id`, `content_location`, `original_name`, `inline`
2. `_replace_cid_refs()` 정규식 확대:
   ```python
   re.compile(r'''src\s*=\s*["']cid:([^"']+)["']''', re.IGNORECASE)
   ```
3. `inline=true` 첨부는 `_build_attachment_section()` 에서 별도 분류

### B3. 메시지 ID 충돌 가능성 (구 roadmap P6)

**결함**: Message-ID 부재 시 `sender + subject + date` seed 만으로 generated msgid 생성.
동일 발신자가 같은 제목/시각으로 여러 메일 보내면 충돌 발생 (`.state.json`, `index_staging.jsonl`).

**조치 방안**:
- seed 에 폴더 경로 또는 PST-relative 메시지 인덱스 추가
- `tests/test_pst2md.py` 에 충돌 회귀 테스트 추가

---

## 우선순위

```
B1 (테스트 10건)  → 즉시 가능 / 회귀 검증 신뢰성 회복
B3 (메시지 ID)    → 데이터 무결성 / 재변환 시 데이터 손실 방지
B2 (인라인 이미지) → MD 출력 품질 / 기존 아카이브 재변환 유발
```
