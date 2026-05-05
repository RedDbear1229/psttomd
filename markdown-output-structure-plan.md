# Markdown Output Structure Improvement Plan

## Purpose

This document proposes a more readable and consistent Markdown structure for emails converted from PST files. The goal is to keep machine-readable metadata in YAML frontmatter while making the visible Markdown body easy to scan in WSL/fzf, Obsidian, VS Code, and terminal viewers.

## Recommended Output Shape

```markdown
---
msgid: "<abc@example.com>"
date: 2024-03-15T09:30:00+09:00
from: "홍길동 <hong@example.com>"
to: ["kim@example.com", "lee@example.com"]
cc: []
subject: "견적서 전달드립니다"
folder: "Inbox/거래처"
thread: "t_a1b2c3d4"
attachments:
  - name: "견적서.pdf"
    size: 245760
    path: "attachments/ab/abc123.pdf"
tags: ["inbox", "georaecheo"]
source_pst: "archive.pst"
---

# 견적서 전달드립니다

> [!summary]
> **보낸 사람:** 홍길동 <hong@example.com>  
> **받는 사람:** kim@example.com, lee@example.com  
> **날짜:** 2024-03-15 09:30 +09:00  
> **폴더:** Inbox/거래처  
> **스레드:** [[t_a1b2c3d4]]

## 첨부 파일

| 파일 | 크기 | 위치 |
|---|---:|---|
| [견적서.pdf](../../attachments/ab/abc123.pdf) | 240 KB | `attachments/ab/abc123.pdf` |

---

## 본문

안녕하세요.

요청하신 견적서를 첨부드립니다.

---

## 관련

- 스레드: [[t_a1b2c3d4]]
- 발신자: [[hong@example.com|홍길동]]
- 태그: #inbox #georaecheo
```

## Structural Rules

- Keep YAML frontmatter for indexing, search, automation, and future AI enrichment.
- Use the first `#` heading only for the email subject.
- Add a summary callout immediately below the title for sender, recipients, date, folder, and thread.
- Put attachments before the original body so users can identify important files quickly.
- Put the original message under `## 본문` to clearly separate metadata from content.
- Put thread/person/tag links under `## 관련` for knowledge-management workflows.

## Attachment Rendering

Use a Markdown table for normal attachments:

```markdown
| 파일 | 크기 | 위치 |
|---|---:|---|
| [계약서.pdf](../../attachments/12/file.pdf) | 512 KB | `attachments/12/file.pdf` |
```

For image attachments, add a separate preview section after the table:

```markdown
### 이미지 미리보기

![image001.png](../../attachments/34/image.png)
```

Paths should always use `/`, even on Windows, so generated Markdown remains portable across WSL, Obsidian, and Git.

## Implementation Targets

- Update `scripts/pst2md.py::_build_header_block()` to generate the summary callout.
- Update `scripts/pst2md.py::_build_attachment_section()` to generate a table and optional image preview.
- Update `scripts/pst2md.py::message_to_md()` so the body is placed under `## 본문`.
- Keep frontmatter keys stable to avoid breaking `build-index`, `mailgrep`, `mailview`, and `mailenrich`.
- Add tests for generated Markdown structure, attachment tables, image preview, and slash-normalized paths.

## Compatibility Notes

- Obsidian renders `[!summary]` as a callout.
- VS Code and terminal viewers render it as a normal blockquote, which remains readable.
- fzf search still works because important visible fields remain plain text.
- The structure should preserve the existing `---` body separator expectations used by `md_io.split()`.
