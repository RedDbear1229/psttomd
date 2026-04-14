# pst2md

Outlook PST 파일을 Markdown으로 변환해 CLI와 Obsidian으로 관리하는 아카이브 시스템.

```
PST (90GB)  →  Markdown + SQLite FTS5  →  glow/fzf CLI  +  Obsidian Vault
```

**지원 환경**: Linux · WSL · Windows Native

---

## 빠른 시작

### Linux / WSL

```bash
./install_linux.sh

pst2md --pst "/mnt/c/Users/YOU/Documents/Outlook Files/archive.pst"
build-index
mailgrep "견적서" --from 홍길동
mailview
mailstat summary
```

### Windows (PowerShell)

```powershell
.\install_windows.ps1

pst2md --pst "C:\Users\YOU\Documents\Outlook Files\archive.pst"
build-index
mailgrep "견적서" --from 홍길동
mailview
mailstat summary
```

---

## CLI 도구

| 명령 | 설명 |
|---|---|
| `pst2md --pst <파일>` | PST → Markdown 변환 |
| `build-index` | SQLite FTS5 인덱스 구축 |
| `mailgrep <키워드> [옵션]` | 전문검색 |
| `mailview [키워드]` | fzf 선택 → glow 렌더링 |
| `mailstat <서브커맨드>` | 통계 (summary/monthly/senders 등) |
| `enrich` | Obsidian people/threads/projects MOC 생성 |
| `verify` | 아카이브 무결성 검증 |
| `archive-monthly --pst <파일>` | 월간 배치 (12개월+ 메일 아카이브) |

---

## PST 백엔드

| 플랫폼 | 백엔드 | 설정값 |
|---|---|---|
| Linux / WSL | libpff-python | `pypff` |
| WSL (대안) | readpst CLI | `readpst` |
| Windows Native | Outlook COM API | `win32com` |

`~/.pst2md/config.toml`에서 변경:
```toml
pst_backend = "auto"   # 플랫폼에 따라 자동 선택
```

---

## 파일 구조

```
pst2md/
├── scripts/
│   ├── pst2md.py            # PST → MD 변환기
│   ├── build_index.py       # SQLite FTS5 인덱스
│   ├── enrich.py            # Obsidian MOC 생성
│   ├── mailgrep.py          # 검색 CLI (Python/click)
│   ├── mailview.py          # fzf + glow 뷰어
│   ├── mailstat.py          # 통계 CLI
│   ├── archive_monthly.py   # 월간 배치
│   ├── verify_integrity.py  # 무결성 검증
│   ├── mailgrep             # 검색 CLI (bash, Linux 전용)
│   ├── mailview             # 뷰어 (bash, Linux 전용)
│   ├── mailstat             # 통계 (bash, Linux 전용)
│   ├── archive_monthly.sh   # 월간 배치 (bash, Linux 전용)
│   └── lib/
│       ├── config.py        # 설정 로더 + 플랫폼 감지
│       ├── pst_backend.py   # PST 파서 추상화
│       ├── normalize.py     # 주소/날짜/인코딩 정규화
│       └── attachments.py   # SHA-256 CAS
├── docs/
│   ├── guide.md             # 상세 사용 설명서
│   └── runbook.md           # 운영 절차
├── install_linux.sh         # Linux/WSL 설치 스크립트
├── install_windows.ps1      # Windows 설치 스크립트
└── pyproject.toml
```

---

## 상세 문서

**[docs/guide.md](docs/guide.md)** — 설치부터 운영까지 전체 사용 설명서

목차:
- 시스템 요구사항
- Linux/WSL 설치
- Windows 설치
- config.toml 설정 옵션
- 첫 번째 변환 (PoC)
- 전체 PST 배치 변환
- CLI 도구 전체 옵션 상세
- Obsidian 연동 및 Dataview 쿼리
- 아카이브 구조 및 Markdown 스키마
- 월간 운영 절차 및 체크리스트
- 트러블슈팅
- FAQ
