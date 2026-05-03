# UX Improvement Plan: WSL fzf Mail Search

## Goal

Make `mailview` feel like a fast, reliable WSL-native mail search tool. Users should be able to open `mailview`, choose a clear search mode, type Korean or English terms, inspect matching context in preview, and open the right email without guessing whether the current input is filtering a local list or querying the full archive.

## Target Workflow

```bash
mailview
```

Expected in fzf:

- `Ctrl-G`: search all indexed fields.
- `Ctrl-S`: search titles only.
- `Ctrl-B`: search body only.
- `Ctrl-F`: browse folders.
- `Ctrl-R`: reset to recent mail.
- `?`: show help.
- `Enter`: open the selected message.

Each search mode must update the prompt, for example `전체검색>`, `제목검색>`, `본문검색>`, or `최근메일>`.

## Search Mode Improvements

Current risk: typing in fzf can mean local filtering, not full archive search. This is confusing.

Required behavior:

- Use database-backed reloads for full, title, and body search.
- Use `change:reload(...)` so results update as the user types.
- Keep local fzf filtering secondary, not the primary search behavior.
- Add `mailgrep --subject QUERY` for title search instead of relying only on `--smart subject:...`.

Acceptance:

- A title/body match outside the initial recent list can still be found from inside fzf.

## Result Row Design

Rows should explain why each email matched.

Example:

```text
2024-03-12  [본문] [첨부]  홍길동 <hong@...>  프로젝트 견적 회신
2024-03-10  [제목]         김철수 <kim@...>   견적서 수정 요청
2024-03-08  [보낸이]       견적팀 <sales@...> Re: 계약 검토
```

Recommended fields:

- date
- match source: `[제목]`, `[본문]`, `[보낸이]`, `[받는이]`
- attachment marker
- sender
- subject

Keep each row one line. Put detailed metadata in preview.

## Preview Improvements

Preview should help users decide whether this is the right email, not simply render the whole document.

Recommended layout:

```text
제목: 프로젝트 견적 회신
날짜: 2024-03-12 09:31
보낸이: 홍길동 <hong@example.com>
받는이: me@example.com
폴더: Inbox/Project-A
첨부: 2개

────────────────────────

... 검색어 주변 본문 3~5줄 ...
이번 견적서는 3월 말까지 검토 부탁드립니다.
첨부한 견적서 v2 기준으로...
```

Behavior:

- If a search term exists, show matching lines with nearby context first.
- If no match context exists, show the metadata header plus the beginning of the body.
- Use `rg -n -C 3` or a Python context extractor.
- Keep full rendering on `Enter`.

## Empty Result Experience

Empty states should suggest the next action.

Example:

```text
결과 없음: "견적검토"

시도해볼 것:
- 더 짧은 단어로 검색: 견적
- 제목검색 모드: Ctrl-S
- 본문검색 모드: Ctrl-B
- 최근 변환 후라면: build-index --rebuild
- 한글 부분 검색이 필요하면 trigram 인덱스 활성화
```

This message should appear in fzf output or preview, not only as CLI stderr.

## Korean Search Quality

Korean search quality is a core UX requirement.

Problems with the current approach:

- FTS5 `unicode61` is weak for Korean partial matching.
- Terms like `견적` may not reliably match `견적서`.
- Compound words such as `계약검토` may be missed when searching `계약`.

Improvement options:

1. Prefer SQLite FTS5 `trigram` tokenizer if available in the WSL SQLite build.
2. If trigram is unavailable, add FTS5 prefix indexing such as `prefix='2 3 4'`.
3. Automatically apply prefix behavior for simple Korean tokens.
4. Keep an advanced/raw FTS mode for power users.

Test fixtures should include:

- `견적`, `견적서`, `견적요청`
- `계약`, `계약검토`
- `회의`, `회의록`
- Korean sender names
- email addresses and punctuation-heavy terms

## Index Trust and Recovery

Users need to know when search results may be stale.

Add startup or doctor checks:

- archive root
- `index.sqlite` existence
- DB row count
- Markdown file count
- `index_staging.jsonl` existence
- last indexed timestamp
- FTS5 tokenizer support

Warn only when there is a problem.

Example:

```text
경고: Markdown 파일 12,420개, 인덱스 11,980개
권장: build-index --rebuild
```

## WSL-Specific UX

Recommended defaults:

- PST input may live under `/mnt/c/...`.
- Archive output should live inside WSL ext4, for example `~/mail-archive`.

Doctor warnings:

- Warn if `archive.root` is under `/mnt/c`.
- Check UTF-8 locale.
- Check `fzf`, `sqlite3`, `rg`, `awk`, `glow`, and `mdcat`.
- Suggest exact install commands.

Example:

```text
성능 경고: archive.root가 /mnt/c 아래에 있습니다.
권장:
  pst2md-config set archive.root ~/mail-archive
```

## Key Binding Simplification

Default header should show only the common actions:

```text
Enter 열람 | Ctrl-G 전체 | Ctrl-S 제목 | Ctrl-B 본문 | Ctrl-F 폴더 | Ctrl-R 초기화 | ? 도움말
```

Move advanced actions to help:

- `Ctrl-A`: open attachments
- `Ctrl-U`: open URL
- `Ctrl-P`: raw Markdown
- `Ctrl-O`: editor
- `Ctrl-D`: delete
- `Alt-I`: stats
- `Alt-T`: tags

Avoid placing destructive actions in the primary header.

## Reading Experience

Recommended behavior:

- `Enter`: full message rendering.
- `Ctrl-P`: raw Markdown including frontmatter.
- `Ctrl-M`: metadata-focused view.
- Future option: open directly near the best search match.

For WSL, `glow` may be the most stable default reader. `mdcat` is useful when inline images matter, but it depends more on terminal graphics support.

## Search Presets

Add shortcuts for common mail-search patterns:

- `Alt-1`: today
- `Alt-2`: this week
- `Alt-3`: this month
- `Alt-A`: has attachments
- `Alt-P`: person/sender browser
- `Alt-R`: recently opened

These should reload the DB-backed result list, not only filter the current fzf rows.

## Recently Opened and Favorites

Repeated lookup is common in email workflows.

Suggested files:

```text
~/mail-archive/.mailview-history.jsonl
~/mail-archive/.mailview-favorites.json
```

Suggested actions:

- Record successful `Enter` opens.
- `Alt-R`: show recently opened messages.
- `Ctrl-Y`: toggle favorite.
- `Alt-Y`: show favorites.

## Implementation Roadmap

### Phase 1: Search Reliability

- Add `mailgrep --subject`.
- Harden FTS escaping.
- Rework `mailview` full/title/body modes to use DB reload on input change.
- Improve empty-result messaging.

### Phase 2: fzf Usability

- Show explicit prompt per mode.
- Simplify visible key bindings.
- Add match source labels.
- Change preview to metadata plus match context.

### Phase 3: WSL Confidence

- Strengthen `mailview --doctor`.
- Warn on `/mnt/c` archive location.
- Compare DB row count and Markdown file count.
- Report stale staging/index state.

### Phase 4: Korean Search

- Evaluate trigram tokenizer.
- Add prefix/trigram index strategy.
- Add Korean search fixtures and integration tests.

### Phase 5: Repeated Workflows

- Add date presets.
- Add recently opened history.
- Add favorites.
- Improve person and folder browsers.

## Success Criteria

The target experience is:

1. User runs `mailview`.
2. User presses `Ctrl-S` and types `견적`.
3. Results include titles such as `견적서`, `견적요청`, and `견적검토` across the full archive.
4. User presses `Ctrl-B` and types `계약검토`.
5. Results include body matches across the full archive.
6. Preview immediately shows matching context.
7. If results are stale or missing, the tool explains how to rebuild the index.
