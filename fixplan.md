# Fix Plan: WSL + fzf Search Workflow

## Context

Primary usage is expected to be inside WSL, with users searching email titles and bodies through `mailview`/`fzf`. The critical path is:

1. `pst2md` converts PST messages to Markdown.
2. `build-index` creates or updates `index.sqlite`.
3. `mailgrep` performs FTS5 queries.
4. `mailview` presents results through `fzf` with preview and open actions.

The project is already feature-rich. The original search UX fixes are mostly complete; the remaining work is focused on edge-case recovery and WSL smoke verification.

## Review Status

Reviewed after the Claude Code improvement pass.

Validation:

- `tests/test_mailgrep.py`, `tests/test_build_index.py`, `tests/test_mailview.py`, and `tests/test_mailview_doctor.py` passed with `178 passed, 6 skipped`.
- Test command used:
  - `.venv\Scripts\python.exe -m pytest tests\test_mailgrep.py tests\test_build_index.py tests\test_mailview.py tests\test_mailview_doctor.py --basetemp=.tmp_pytest_review_elevated`
- Direct SQLite FTS5 smoke check confirmed `"견적"*` matches `견적서` with `unicode61` and `prefix='2 3 4'`.

## Priority 1: Fix fzf Body Search Behavior

Status: Completed.

Current issue: `Ctrl-B` in `mailview` triggers one reload using the current `{q}`, then clears the query. New text typed after entering body-search mode does not continuously query the database.

Required change:

- Make body-search mode use `change:reload(...)` so every query update runs `mailview --fzf-input --body {q}`.
- Keep fzf local filtering separate from database-backed body search.
- Add tests around `_build_fzf_exec_commands()` or fzf command construction to verify body mode reloads on query changes.

Acceptance:

- Press `Ctrl-B`, type a body term, and see results from the full indexed archive, not only the already loaded list.

Completion notes:

- `mailview` now builds `--fzf-input --body {q}` reload commands.
- Linux/WSL uses `change:transform(...)` to trigger DB reload as the query changes.
- Unit tests cover the generated body reload command.

## Priority 2: Make Title Search Database-Backed

Status: Completed.

Current issue: launching `mailview` without a query loads only recent messages (`get_recent_paths()` default limit: 100). Typing in fzf filters only those visible rows, so it is not true archive-wide title search.

Required change:

- Add a formal title search mode, for example `Ctrl-S`, backed by DB reload.
- Add `mailgrep --subject QUERY` instead of relying only on `--smart subject:...`.
- Wire fzf title mode to `mailview --fzf-input --subject {q}` or equivalent.

Acceptance:

- A title outside the recent 100 messages can be found directly from fzf title-search mode.

Completion notes:

- `mailgrep --subject QUERY` is implemented.
- `mailview` exposes `Ctrl-S` title search backed by DB reload.
- Unit tests cover subject reload command construction.

## Priority 3: Improve Korean Search Quality

Status: Completed.

Current issue: FTS5 uses `tokenize='unicode61'`, which is weak for Korean partial matching. Searches like `견적` may not reliably match `견적서` depending on token boundaries.

Required change:

- Evaluate SQLite FTS5 `trigram` tokenizer availability in the target WSL SQLite version.
- If available, add a trigram FTS table for title/body search.
- If not, add FTS5 prefix indexing such as `prefix='2 3 4'` and automatically append `*` for simple Korean tokens.
- Add Korean search fixtures and integration tests.

Acceptance:

- Searches for common partial Korean terms such as `견적`, `계약`, `회의` return expected title/body matches.

Completion notes:

- `messages_fts` now uses `prefix='2 3 4'`.
- Safe search appends prefix wildcard expressions such as `"견적"*`.
- Korean partial-match fixtures cover `견적`, `계약`, and `회의`.

## Priority 4: Harden FTS Query Escaping

Status: Completed.

Current issue: `_escape_fts5()` only handles a small subset of FTS syntax characters. Real email searches often include `C++`, `2024-05`, `foo/bar`, `a.b@example.com`, colons, and parentheses.

Required change:

- Make default search input safe by treating user terms as quoted literal phrases/tokens.
- Add an explicit advanced mode such as `--raw-fts` for users who want FTS5 operators.
- Add tests for punctuation-heavy queries.

Acceptance:

- Normal searches do not fail with `sqlite3.OperationalError` because of FTS syntax.

Completion notes:

- Default search now quotes user tokens as safe FTS5 phrases.
- `--raw-fts` is available for advanced FTS5 operators.
- Punctuation-heavy query tests cover values such as `C++`, `2024-05`, `foo/bar`, and email addresses.

## Priority 5: Fix Index Refresh and Recovery

Status: Partially completed.

Current issue: `mailview` auto-index calls `build-index`, but `build-index` normally consumes only `index_staging.jsonl`. If Markdown files are copied, restored, or generated with `--no-index`, new files may not enter the DB.

Required change:

- Decide on one recovery model:
  - lightweight: if staging is missing but new MD files exist, show a clear message recommending `build-index --rebuild`;
  - stronger: add an incremental filesystem scan mode that indexes MD files not present in `messages.path`.
- Ensure `mailview --doctor` reports DB/file count mismatch.

Acceptance:

- Users can reliably recover search after copy/restore/manual changes without guessing the right command.

Completion notes:

- `mailview --doctor` reports DB/file count mismatch and missing FTS prefix index.
- `mailview` warns when new MD files are detected but `index_staging.jsonl` is missing.

Remaining risk:

- The auto-index check depends on `md_file.mtime > index.sqlite.mtime`. Files restored with preserved older mtimes, such as `cp -p`, backup restore, or `rsync -a`, can still be missing from DB without triggering the warning.
- Improve detection by comparing DB row count/path set with `archive/**/*.md`, or at least by checking file count mismatch when staging is absent.

## Priority 6: Fix Rebuild Attachment Count

Status: Completed.

Current issue: generated attachment frontmatter uses inline YAML (`- {name: ...}`), but rebuild counting looks for block YAML (`- name:`). After `build-index --rebuild`, attachment counts can become zero.

Required change:

- Update `extract_frontmatter()` to count both inline and block attachment entries.
- Prefer replacing the loose parser with a proper YAML parser if adding a dependency is acceptable.

Acceptance:

- `has:attachment`, mailview attachment markers, and stats remain correct after `build-index --rebuild`.

Completion notes:

- `extract_frontmatter()` counts both inline YAML entries and block YAML entries.
- Attachment count tests cover inline, block, mixed, empty, and terminated attachment sections.

## Newly Found Issues

### Priority 7: Fix `mailgrep --all-archives` Default DB Precheck

Status: New.

Current issue:

- `mailgrep` checks the default archive `index.sqlite` before branching into `--all-archives`.
- If the default archive DB is missing but another configured archive root has a valid DB, `mailgrep QUERY --all-archives` exits early instead of searching the available archives.

Required change:

- Move the default `db.exists()` check into the non-`--all-archives` branch.
- In `--all-archives` mode, build the DB list from `archive.roots` first and fail only if no configured archive DB exists.
- Add a test where the default DB is absent but another archive DB exists.

Acceptance:

- `mailgrep "term" --all-archives` searches all existing configured archive DBs without requiring the default DB to exist.

### Priority 8: Add WSL fzf Smoke Test

Status: New.

Current issue:

- Unit tests verify fzf command construction, but not actual WSL terminal behavior with fzf 0.47+, `change:transform(...)`, and Korean input.

Required change:

- Add a WSL smoke test or manual runbook that creates a tiny archive, rebuilds the index, launches `mailview`, and verifies `Ctrl-B`/`Ctrl-S` DB-backed search behavior.
- Keep this separate from normal CI if interactive terminal automation is too brittle.

Acceptance:

- A maintainer can verify the target WSL + fzf workflow with a single documented command or short runbook.

## WSL Operational Recommendations

- Keep the archive in WSL ext4, for example `~/mail-archive`, not under `/mnt/c`, for faster SQLite and file traversal.
- Keep PST source files on `/mnt/c` only as input; convert into WSL-local storage.
- Use `pypff` as the default WSL backend where possible, with `readpst` as fallback.
- Add a WSL-specific smoke test script:
  - convert `tests/data/test.pst`;
  - rebuild index;
  - run `mailgrep`;
  - run `mailview --doctor`;
  - verify expected DB row count.

## Suggested Implementation Order

1. Completed: fix `build-index --rebuild` attachment counting.
2. Completed: add `mailgrep --subject` and safer FTS escaping.
3. Completed: rework `mailview` title/body fzf modes to use DB reload on input change.
4. Completed: add Korean search fixtures and prefix indexing.
5. Remaining: strengthen auto-index recovery beyond mtime-only detection.
6. Remaining: fix `mailgrep --all-archives` default DB precheck.
7. Remaining: add WSL-specific fzf smoke verification.
8. Completed: document WSL-first setup in README and `docs/guide.md`.
