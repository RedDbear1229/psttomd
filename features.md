# 기능개선 (Feature Improvements)

## 목적

새로운 기능 추가 및 기존 기능 강화 항목을 추적한다.
기존 코드의 결함 수정은 [bugfix.md](bugfix.md) 에서 별도 관리한다.

---

## 단기 — 검색·UX 강화

### S1. mailgrep 쿼리 파싱 강화 (구 roadmap P7)

**현재**: smart 쿼리 파싱이 whitespace split 기반 → 공백 포함 값 깨짐.
날짜 필터가 timezone 포함 ISO 문자열 비교 → day boundary 부정확.

**목표**:
- `shlex.split()` 기반 쿼리 토크나이저
- `folder:"Inbox/계약 문서"`, `subject:"월간 보고"` 형태 지원
- 메시지 날짜 UTC 정규화 또는 date-only 컬럼 추가
- `tests/test_mailgrep.py` 에 quoted filter, timezone boundary 테스트 추가

### S2. 인덱스 복구 신뢰성 강화 (구 roadmap P8)

**현재**: `mailview --doctor` 가 DB/파일 카운트 drift 만 보고. mtime 의존 일부 잔존.

**목표**:
- DB 경로 coverage (path 컬럼 ↔ 실제 파일 교집합/차집합)
- orphan DB rows 보고 (DB 에는 있고 파일이 없는 항목)
- staging 파일 상태 (마지막 indexed timestamp)
- 선택적 manifest hash (전체 아카이브 무결성)

```text
경고: Markdown 12,420개 / 인덱스 11,980개 / orphan rows 3개
권장: build-index --rebuild
```

### S3. fzf UX 개선 (구 ux-improvement-plan Phase 2-3)

**모드 프롬프트**: `최근메일>`, `전체검색>`, `제목검색>`, `본문검색>` 명시

**결과 행 매치 라벨**:
```text
2024-03-12  [본문] [첨부]  홍길동 <hong@...>  프로젝트 견적 회신
2024-03-10  [제목]         김철수 <kim@...>   견적서 수정 요청
```

**미리보기 개선**: 매칭 컨텍스트 우선 표시 (`rg -n -C 3` 또는 Python 컨텍스트 추출)

**빈 결과 안내**: 다음 행동 제안 (짧은 단어 / 모드 전환 / `build-index --rebuild`)

**키 바인딩 정리**:
- 헤더에는 공통: `Enter 열람 | Ctrl-G 전체 | Ctrl-S 제목 | Ctrl-B 본문 | Ctrl-F 폴더 | Ctrl-R 초기화 | ? 도움말`
- 파괴적 액션은 헤더 밖으로

**WSL doctor 강화**:
- `archive.root` 가 `/mnt/c` 아래면 ext4 이전 권장 메시지
- UTF-8 locale 체크 / `fzf`, `sqlite3`, `rg`, `glow`, `mdcat` 설치 안내

### S4. 반복 워크플로우 (구 ux-improvement-plan Phase 5)

- 날짜 프리셋: `Alt-1` 오늘 / `Alt-2` 이번 주 / `Alt-3` 이번 달
- 첨부 필터: `Alt-A` has attachments
- 최근 열람 기록: `~/mail-archive/.mailview-history.jsonl` + `Alt-R`
- 즐겨찾기: `~/mail-archive/.mailview-favorites.json` + `Ctrl-Y`/`Alt-Y`

---

## 중기 — 출력 품질·구조

### M1. MD 출력 구조 정비 (구 markdown-output-structure-plan + roadmap P4)

**목표 구조**:

```markdown
---
msgid: "<abc@example.com>"
date: 2024-03-15T09:30:00+09:00
from: "홍길동 <hong@example.com>"
to: ["kim@example.com"]
subject: "견적서 전달드립니다"
folder: "Inbox/거래처"
thread: "t_a1b2c3d4"
attachments:
  - name: "견적서.pdf"
    size: 245760
    path: "attachments/ab/abc123.pdf"
    inline: false
---

# 견적서 전달드립니다

> [!summary]
> **보낸 사람:** 홍길동 <hong@example.com>
> **받는 사람:** kim@example.com
> **날짜:** 2024-03-15 09:30 +09:00
> **폴더:** Inbox/거래처

## 첨부 파일

| 파일 | 크기 | 위치 |
|---|---:|---|
| [견적서.pdf](../../attachments/ab/abc123.pdf) | 240 KB | `attachments/ab/abc123.pdf` |

---

## 본문

원문 메일 본문

---

## 관련

- 스레드: [[t_a1b2c3d4]]
- 발신자: [[hong@example.com|홍길동]]
- 태그: #inbox
```

**구현 대상**:
- `pst2md.py::_build_header_block()` — `> [!summary]` callout 생성
- `pst2md.py::_build_attachment_section()` — Markdown 테이블 + 이미지 미리보기
- `pst2md.py::message_to_md()` — 본문을 `## 본문` 섹션으로 이동
- `lib/md_io.py::split()` — 새 구조 호환 (현 `---` 구분자 유지)
- 테스트 — 생성된 구조, 첨부 테이블, slash-normalized 경로

**호환성 주의**:
- frontmatter 키 안정성 유지 (`build-index`, `mailgrep`, `mailview`, `mailenrich` 영향 없음)
- 기존 아카이브 재변환 비용 발생 — `mailenrich --dry-run` 으로 비용 산정 후 진행

### M2. 인라인 이미지 메타데이터 강화 (구 inline-image-storage-review)

bugfix.md `B2` 의 결함 수정과 더불어 다음 메타데이터 도입:

```yaml
attachments:
  - name: "image001.png"
    original_name: "image001.png"
    content_id: "image001.png@abc"
    sha256: "abc..."
    size: 12345
    path: "attachments/ab/abc123.png"
    inline: true
    large: false
```

**효과**: 인라인 이미지는 본문에만 렌더, 첨부 테이블에서는 별도 분류.

---

## 장기 — AI 지식 관리 (구 ai-knowledge-extension-plan)

### L1. 시맨틱 검색 (Phase 1)

**현재**: `embed.py` 메시지 레벨 임베딩만 존재.

**목표**:
- chunk-level 임베딩 (`semantic_chunks`, `chunk_embeddings` 테이블)
- `semantic-search "question"` 명령어
- `mailview` 시맨틱 모드 (`Ctrl-E`)
- 결과 라벨: `[키워드]`, `[의미]`, `[둘다]`
- FTS5 + 임베딩 하이브리드 랭킹

### L2. 스레드 요약 (Phase 2)

```markdown
# Thread: 프로젝트 A 견적 협의

## 타임라인
- 2024-03-10 최초 견적 요청
- 2024-03-12 단가 조정 요청
- 2024-03-18 최종 견적서 v3 송부

## 결정사항
- 단가: 12% 인하
- 납기: 4월 말

## 미결사항
- 유지보수 조건 확인 필요

## 근거 메일
- [[archive/2024/03/12/...]]
```

스키마: `thread_summaries(thread, summary, decisions_json, action_items_json, open_questions_json, input_hash, model, prompt_version, updated_at)`

### L3. 엔터티 추출 (Phase 3)

people / companies / projects / products / dates / amounts / contracts / decisions / action_items

```yaml
ai_entities:
  people:
    - name: "홍길동"
      confidence: 0.92
  companies:
    - name: "A업체"
      confidence: 0.87
```

### L4. 관련 메일 발견 (Phase 4)

신호: 임베딩 유사도, 공통 인물, 공통 회사/프로젝트, 유사 제목, 인접 날짜, 인용 참조, 첨부 이름

```markdown
## AI 추천 연결
- [[...]] confidence: 0.91 reason: "same project and similar contract terms"
```

### L5. 소스 기반 Q&A (Phase 5)

**원칙**: 모든 답변은 출처 메일 인용. "찾을 수 없음" 명시 가능. 검색 품질 안정 후 도입.

```text
질문: 작년에 A업체와 단가 조정한 내용 정리해줘

답변:
2024년 3월 A업체와 단가 12% 인하를 논의했습니다.
최종 견적서 v3 기준으로 납기는 4월 말입니다.

근거:
- 2024-03-12 홍길동, "견적서 수정 요청"
- 2024-03-18 김철수, "최종 견적서 v3 송부"
```

---

## AI 비용·프라이버시 가드레일 (구 roadmap P12)

장기 AI 기능 도입 전에 다음 가드레일이 필요하다:

```toml
[ai]
provider = "ollama"
endpoint = "http://localhost:11434"
model = "llama3.1:8b"
allow_external = false
skip_folders = ["Junk", "Spam", "Deleted Items", "Private", "HR", "Legal"]
max_body_chars = 24000
```

- 배치 제출 전 budget 사전 검사
- 모델 응답 강건한 JSON 추출 (code fence, 설명 텍스트 제거)
- 토큰은 환경변수 우선
- 메타데이터만 로깅 (full payload 금지)
- `--dry-run`, `--limit`, `--since/--until`, `--folder`, `--force` 표준화

---

## 우선순위

```
S1 (mailgrep shlex)        → 작음 / 즉시 가치
S2 (인덱스 doctor 강화)    → 중간 / 운영 안정성
S3 (fzf UX)                → 중간 / 일상 사용성
M1 (MD 출력 구조)          → 큼 / 기존 아카이브 재변환 필요
M2 (인라인 이미지 메타)    → bugfix B2 와 함께
L1-L5 (AI)                 → 매우 큼 / S1~M2 안정화 후 착수
```
