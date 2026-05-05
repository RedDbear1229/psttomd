# Inline Image Storage Review

## Purpose

This document reviews the current handling of email body images during PST to Markdown conversion. The project stores extracted attachments under a separate content-addressed path and rewrites Markdown/HTML references to those files. That direction is sound, but several consistency and rendering issues should be fixed before relying on it for long-term archives.

## Current Flow

1. `pst2md` extracts attachments from each message.
2. `store_attachment()` saves bytes under:
   - `attachments/<sha256[:2]>/<sha256>.<ext>`
   - `attachments_large/<sha256[:2]>/<sha256>.<ext>` for large files.
3. HTML body `cid:` references are rewritten by `_replace_cid_refs()`.
4. `_build_attachment_section()` appends image links or file links to the Markdown body.
5. Frontmatter records attachment metadata under `attachments:`.

## Confirmed Strengths

- Content-addressed storage deduplicates repeated images and files.
- Markdown body links are generated relative to the `.md` file location.
- Images can render inline in Obsidian, VS Code, and `mdcat` when the link is valid.
- Keeping binary files outside the Markdown body keeps the text archive clean and Git-friendly.

## Issues Found

### 1. Platform-Specific Path Separators

`store_attachment()` currently stores `meta["path"]` using `str(Path)`. On Windows this can produce:

```text
attachments\b9\file.png
```

Expected portable form:

```text
attachments/b9/file.png
```

Impact:

- Frontmatter becomes platform-dependent.
- WSL, Obsidian, and Git workflows are less reliable.
- `mailview` attachment open/delete functions may fail if they assume archive-root-relative POSIX paths.

Recommended fix:

```python
rel_path = dest_path.relative_to(attachments_root.parent).as_posix()
```

### 2. CID Matching Depends on Filename

`_replace_cid_refs()` maps `cid:` references by comparing the CID prefix with `meta["name"]`.

This works for:

```html
<img src="cid:image001.png@abc">
```

but may fail for:

```html
<img src="cid:part1.06090908.01060107@example">
```

where the actual attachment filename is `image001.png`.

Impact:

- The image file is saved, but the body image remains unresolved.
- Markdown output may contain broken `cid:` links.

Recommended fix:

- Capture `Content-ID` or equivalent attachment metadata in backend extraction.
- Store `content_id`, `content_location`, `original_name`, and `is_inline` where available.
- Match `cid:` by normalized Content-ID first, then fallback to filename.

### 3. CID Regex Is Too Narrow

Current replacement focuses on `src="cid:..."`.

Likely missed forms:

```html
<img src='cid:image001.png'>
<img SRC="CID:image001.png">
<img src = "cid:image001.png">
```

Recommended pattern:

```python
r'''src\s*=\s*["']cid:([^"']+)["']'''
```

Keep `re.IGNORECASE`.

### 4. Inline Images Can Be Duplicated

If a CID image is successfully rewritten into the body, `_build_attachment_section()` may still append the same image again at the end:

```markdown
![image001.png](../../attachments/xx/hash.png)

## 첨부 파일

![image001.png](../../attachments/xx/hash.png)
```

Impact:

- Visual noise in long HTML emails.
- Users may confuse inline body images with user-attached files.

Recommended behavior:

- Mark CID body images as `inline: true`.
- Render inline images only where they appear in the original body.
- In the attachment table, either omit inline images or list them as `본문 이미지`.

### 5. Frontmatter and Body Links Can Diverge

The body link path is normalized with `/`, but frontmatter `attachments[].path` may not be. `mailview` parses frontmatter to open/delete attachments, so mismatched path formatting can create inconsistent behavior.

Recommended fix:

- Normalize paths at storage time.
- Add tests that verify:
  - frontmatter path uses `/`;
  - body image link uses `/`;
  - `mailview.get_attachments_from_md()` can open the same file.

## Recommended Metadata Shape

```yaml
attachments:
  - name: "image001.png"
    original_name: "image001.png"
    content_id: "image001.png@abc"
    sha256: "abc123..."
    size: 12345
    path: "attachments/ab/abc123.png"
    inline: true
    large: false
```

For normal user attachments:

```yaml
attachments:
  - name: "견적서.pdf"
    sha256: "def456..."
    size: 245760
    path: "attachments/de/def456.pdf"
    inline: false
    large: false
```

## Implementation Checklist

- [ ] Change `store_attachment()` to return POSIX-style relative paths.
- [ ] Extend backend attachment metadata to include Content-ID when available.
- [ ] Match CID references by Content-ID first, filename second.
- [ ] Broaden CID `src=` regex to support single quotes, spacing, and uppercase.
- [ ] Add `inline` flag to attachment metadata.
- [ ] Avoid rendering inline images twice in the final Markdown.
- [ ] Add tests for Windows path normalization.
- [ ] Add tests for CID values that do not match filenames.
- [ ] Add an end-to-end fixture with HTML body image plus normal attachment.

## Priority

1. Fix path normalization first. This is simple and prevents cross-platform breakage.
2. Improve CID matching next. This directly affects whether body images render.
3. Add inline-vs-attachment rendering separation after metadata support exists.
