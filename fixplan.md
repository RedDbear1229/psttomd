# Fix Plan: WSL + fzf Search Workflow

## Context

Primary usage is expected to be inside WSL, with users searching email titles and bodies through `mailview`/`fzf`. The critical path is:

1. `pst2md` converts PST messages to Markdown.
2. `build-index` creates or updates `index.sqlite`.
3. `mailgrep` performs FTS5 queries.
4. `mailview` presents results through `fzf` with preview and open actions.

The project is already feature-rich, but the current search UX and index recovery path need tightening for this target workflow.

## Priority 1: Fix fzf Body Search Behavior

Current issue: `Ctrl-B` in `mailview` triggers one reload using the current `{q}`, then clears the query. New text typed after entering body-search mode does not continuously query the database.

Required change:

- Make body-search mode use `change:reload(...)` so every query update runs `mailview --fzf-input --body {q}`.
- Keep fzf local filtering separate from database-backed body search.
- Add tests around `_build_fzf_exec_commands()` or fzf command construction to verify body mode reloads on query changes.

Acceptance:

- Press `Ctrl-B`, type a body term, and see results from the full indexed archive, not only the already loaded list.

## Priority 2: Make Title Search Database-Backed

Current issue: launching `mailview` without a query loads only recent messages (`get_recent_paths()` default limit: 100). Typing in fzf filters only those visible rows, so it is not true archive-wide title search.

Required change:

- Add a formal title search mode, for example `Ctrl-S`, backed by DB reload.
- Add `mailgrep --subject QUERY` instead of relying only on `--smart subject:...`.
- Wire fzf title mode to `mailview --fzf-input --subject {q}` or equivalent.

Acceptance:

- A title outside the recent 100 messages can be found directly from fzf title-search mode.

## Priority 3: Improve Korean Search Quality

Current issue: FTS5 uses `tokenize='unicode61'`, which is weak for Korean partial matching. Searches like `견적` may not reliably match `견적서` depending on token boundaries.

Required change:

- Evaluate SQLite FTS5 `trigram` tokenizer availability in the target WSL SQLite version.
- If available, add a trigram FTS table for title/body search.
- If not, add FTS5 prefix indexing such as `prefix='2 3 4'` and automatically append `*` for simple Korean tokens.
- Add Korean search fixtures and integration tests.

Acceptance:

- Searches for common partial Korean terms such as `견적`, `계약`, `회의` return expected title/body matches.

## Priority 4: Harden FTS Query Escaping

Current issue: `_escape_fts5()` only handles a small subset of FTS syntax characters. Real email searches often include `C++`, `2024-05`, `foo/bar`, `a.b@example.com`, colons, and parentheses.

Required change:

- Make default search input safe by treating user terms as quoted literal phrases/tokens.
- Add an explicit advanced mode such as `--raw-fts` for users who want FTS5 operators.
- Add tests for punctuation-heavy queries.

Acceptance:

- Normal searches do not fail with `sqlite3.OperationalError` because of FTS syntax.

## Priority 5: Fix Index Refresh and Recovery

Current issue: `mailview` auto-index calls `build-index`, but `build-index` normally consumes only `index_staging.jsonl`. If Markdown files are copied, restored, or generated with `--no-index`, new files may not enter the DB.

Required change:

- Decide on one recovery model:
  - lightweight: if staging is missing but new MD files exist, show a clear message recommending `build-index --rebuild`;
  - stronger: add an incremental filesystem scan mode that indexes MD files not present in `messages.path`.
- Ensure `mailview --doctor` reports DB/file count mismatch.

Acceptance:

- Users can reliably recover search after copy/restore/manual changes without guessing the right command.

## Priority 6: Fix Rebuild Attachment Count

Current issue: generated attachment frontmatter uses inline YAML (`- {name: ...}`), but rebuild counting looks for block YAML (`- name:`). After `build-index --rebuild`, attachment counts can become zero.

Required change:

- Update `extract_frontmatter()` to count both inline and block attachment entries.
- Prefer replacing the loose parser with a proper YAML parser if adding a dependency is acceptable.

Acceptance:

- `has:attachment`, mailview attachment markers, and stats remain correct after `build-index --rebuild`.

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

1. Fix `build-index --rebuild` attachment counting.
2. Add `mailgrep --subject` and safer FTS escaping.
3. Rework `mailview` title/body fzf modes to use DB reload on input change.
4. Add Korean search fixtures and choose trigram or prefix indexing.
5. Improve auto-index recovery messaging or implement filesystem incremental scan.
6. Document WSL-first setup in README and `docs/guide.md`.
