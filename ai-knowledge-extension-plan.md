# AI Knowledge Extension Plan

## Goal

Extend this project from a PST-to-Markdown mail archive into an AI-assisted knowledge storage and management system. The goal is not to add a generic chatbot first. The better path is to help users find, connect, summarize, and reuse knowledge already present in their email archive.

The foundation is already suitable:

- emails are stored as Markdown;
- metadata exists in frontmatter;
- SQLite FTS5 search exists;
- `mailenrich` and `embed` already exist;
- Obsidian-compatible links can express knowledge relationships;
- Message-ID and thread IDs provide stable anchors.

## Guiding Principles

1. Preserve source material.
   - Original email body should remain immutable.
   - AI output must be stored separately from converted source text.

2. Prefer retrieval before generation.
   - Improve search and semantic retrieval before building answer-generation workflows.

3. Require provenance.
   - Any AI-generated summary, answer, entity, or relationship must point back to source messages.

4. Make AI output reproducible.
   - Store model, prompt version, input hash, generation time, and status.

5. Treat AI as assistive, not authoritative.
   - Entity extraction and related-link suggestions should include confidence or be marked as AI-generated.

## Recommended Feature Order

### Phase 1: Semantic Search

Add natural-language search on top of existing keyword search.

Current:

```text
keyword -> FTS5 -> result list
```

Target:

```text
natural language query -> embeddings -> related messages
keyword query -> FTS5 -> exact matches
combined ranking -> result list
```

Example queries:

- `작년에 A업체랑 단가 조정했던 내용`
- `김부장이 말한 납기 지연 건`
- `계약서 검토하면서 법무팀이 지적한 사항`

Required features:

- `semantic-search "question"`
- `mailview` semantic-search mode, for example `Ctrl-E`
- result labels: `[키워드]`, `[의미]`, `[둘다]`
- source message links for every result

Acceptance:

- A user can find conceptually related emails even when the exact query words do not appear in the message.

### Phase 2: Thread-Level Summaries

Summarizing individual emails is less valuable than summarizing conversations.

Generate thread summaries with:

- timeline;
- key decisions;
- open questions;
- action items;
- people involved;
- source message list.

Example output:

```markdown
# Thread: 프로젝트 A 견적 협의

## 요약
- 2024-03-10 최초 견적 요청
- 2024-03-12 단가 조정 요청
- 2024-03-15 납기 조건 변경
- 2024-03-18 최종 견적서 v3 송부

## 결정사항
- 단가: 12% 인하
- 납기: 4월 말
- 담당자: 홍길동

## 미결사항
- 유지보수 조건 확인 필요

## 근거 메일
- [[archive/2024/03/12/...]]
- [[archive/2024/03/18/...]]
```

Acceptance:

- A user can open a thread page and understand the conversation without reading every email.

### Phase 3: Entity Extraction

Extract structured knowledge from messages and threads.

Entity types:

- people;
- companies;
- projects;
- products;
- dates;
- amounts;
- contracts;
- incidents;
- decisions;
- action items.

Store AI-generated entities with confidence.

Example:

```yaml
ai_entities:
  people:
    - name: "홍길동"
      confidence: 0.92
  companies:
    - name: "A업체"
      confidence: 0.87
  projects:
    - name: "Project Alpha"
      confidence: 0.76
```

Acceptance:

- Users can browse messages by company, person, project, and decision-related entities.

### Phase 4: Related Mail Discovery

Suggest related messages beyond explicit threads.

Signals:

- embedding similarity;
- shared people;
- shared company/project;
- similar subject;
- nearby dates;
- quoted references;
- attachment names.

Output:

```markdown
## AI 추천 연결
- [[...]] confidence: 0.91 reason: "same project and similar contract terms"
- [[...]] confidence: 0.84 reason: "same customer and delivery issue"
```

Acceptance:

- Opening one email reveals other useful context even when they are not part of the same thread.

### Phase 5: Source-Grounded Q&A

Only add question answering after retrieval quality is reliable.

Requirements:

- every answer must cite source messages;
- answer generation must be allowed to say "not found";
- date ranges must be explicit when relevant;
- no source, no confident answer;
- results should open directly in `mailview`.

Example:

```text
질문: 작년에 A업체와 단가 조정한 내용 정리해줘

답변:
2024년 3월 A업체와 단가 12% 인하를 논의했습니다.
최종 견적서 v3 기준으로 납기는 4월 말입니다.

근거:
- 2024-03-12 홍길동, "견적서 수정 요청"
- 2024-03-18 김철수, "최종 견적서 v3 송부"
```

Acceptance:

- Users can verify every generated answer by opening cited source emails.

## Data Model Direction

The current message-level embedding table is a good start. For better retrieval, move toward chunk-level storage.

Suggested tables:

```sql
semantic_chunks (
    chunk_id     TEXT PRIMARY KEY,
    msgid        TEXT NOT NULL,
    thread       TEXT,
    path         TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    chunk_text   TEXT NOT NULL,
    body_hash    TEXT NOT NULL
);

chunk_embeddings (
    chunk_id    TEXT NOT NULL,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vector      BLOB NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (chunk_id, model)
);

entities (
    id               INTEGER PRIMARY KEY,
    type             TEXT NOT NULL,
    name             TEXT NOT NULL,
    normalized_name  TEXT NOT NULL
);

message_entities (
    msgid       TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    confidence  REAL,
    source      TEXT NOT NULL,
    model       TEXT,
    created_at  TEXT
);

thread_summaries (
    thread           TEXT PRIMARY KEY,
    summary          TEXT,
    decisions_json   TEXT,
    action_items_json TEXT,
    open_questions_json TEXT,
    input_hash       TEXT NOT NULL,
    model            TEXT NOT NULL,
    prompt_version   TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
```

## Markdown Storage Strategy

Do not mix AI-generated content with pristine email body.

Recommended block:

```markdown
<!-- AI-KNOWLEDGE:BEGIN -->
## 요약
...

## 결정사항
...

## 액션 아이템
...

## 관련 문서
- [[...]]
<!-- AI-KNOWLEDGE:END -->
```

Recommended frontmatter keys:

```yaml
ai_summary: "..."
ai_tags: ["계약", "견적", "납기"]
ai_entities: [...]
ai_hash: "body or thread input hash"
ai_model: "..."
ai_prompt_version: "..."
ai_updated_at: "..."
ai_confidence: 0.84
```

Rules:

- AI block may be regenerated.
- Source email body must not be modified.
- AI result must be skipped if `ai_hash` matches current input.
- Force regeneration should be explicit.

## Retrieval Architecture

Recommended search stack:

```text
User query
  -> normalize query
  -> keyword search via FTS5
  -> semantic search via embeddings
  -> optional metadata filters
  -> merge and rank
  -> show result list with source labels
```

Ranking signals:

- FTS exact match score;
- semantic similarity score;
- recency;
- same sender/person;
- thread match;
- attachment match;
- user favorites or recently opened messages.

Result labels:

- `[키워드]`
- `[의미]`
- `[둘다]`
- `[스레드]`
- `[첨부]`

## Security and Privacy

Email archives contain sensitive information. AI features must be designed with privacy controls.

Requirements:

- Support local model providers such as Ollama.
- Make external API use explicit.
- Allow folder exclusions, for example `Junk`, `HR`, `Legal`, `Private`.
- Mask or skip sensitive attachments by default.
- Store API tokens via environment variables where possible.
- Log what was sent to the model at the metadata level, not full sensitive payloads.

Recommended config:

```toml
[ai]
provider = "ollama"
endpoint = "http://localhost:11434"
model = "llama3.1:8b"
allow_external = false
skip_folders = ["Junk", "Spam", "Deleted Items", "Private"]
max_body_chars = 24000
```

## Cost and Reprocessing Controls

AI jobs must be idempotent.

Required metadata:

```yaml
ai_model:
ai_prompt_version:
ai_input_hash:
ai_created_at:
ai_status:
```

Controls:

- `--dry-run` for cost estimation;
- `--limit`;
- `--since` and `--until`;
- `--folder`;
- `--force`;
- budget limit;
- retry log;
- resumable job state.

## CLI Additions

Suggested commands:

```bash
semantic-search "A업체 단가 조정"
thread-summary --thread t_abc123
thread-summary --all --since 2024-01-01
entity-extract --limit 100
related-mails path/to/mail.md
ask "작년에 A업체와 단가 조정한 내용 정리해줘"
```

Suggested `mailview` bindings:

- `Ctrl-E`: semantic search mode
- `Ctrl-Y`: show related messages for selected email
- `Alt-S`: open thread summary
- `Alt-E`: show extracted entities

## Integration With Obsidian

AI-generated knowledge should become useful in Obsidian without making Obsidian mandatory.

Potential generated notes:

```text
people/<normalized-person>.md
companies/<normalized-company>.md
projects/<project>.md
threads/<thread-id>.md
decisions/<date>-<topic>.md
```

Each generated note should include:

- summary;
- linked source emails;
- confidence or source type;
- last generated timestamp;
- model and prompt version.

## Risks

Major risks:

- AI summaries may be wrong.
- Users may trust generated content as if it were source material.
- External API calls may leak sensitive information.
- Cost can grow quickly on large archives.
- AI output may become stale after re-conversion.
- Chunking can break context if done poorly.
- Search quality issues will reduce Q&A quality.

Mitigations:

- Always cite sources.
- Keep source and AI output separate.
- Prefer local models by default for sensitive archives.
- Use input hashes to detect staleness.
- Add dry-run and budget controls.
- Build semantic search before answer generation.

## Implementation Roadmap

### Step 1: Stabilize Base Search

- Fix fzf title/body search modes.
- Harden FTS escaping.
- Improve Korean keyword search.
- Improve index rebuild reliability.

### Step 2: Add Semantic Retrieval

- Move from message-level to chunk-level embeddings.
- Add `semantic-search`.
- Add hybrid ranking with FTS5.
- Show `[키워드]`, `[의미]`, `[둘다]` labels.

### Step 3: Add Thread Knowledge

- Generate thread summaries.
- Store decisions, action items, open questions.
- Create Obsidian-compatible thread notes.

### Step 4: Add Entity Graph

- Extract people, companies, projects, dates, amounts, and decisions.
- Store confidence.
- Generate people/company/project notes.

### Step 5: Add Source-Grounded Q&A

- Implement `ask`.
- Require citations.
- Allow "not found".
- Open cited messages directly in `mailview`.

## Success Criteria

The system should help a user answer work-memory questions such as:

- "작년에 A업체와 단가 조정한 내용이 뭐였지?"
- "이 프로젝트에서 아직 미결인 액션 아이템은?"
- "홍길동과 논의한 계약 조건 변경 내역은?"
- "이 장애 건과 비슷한 이전 사례가 있었나?"

Successful behavior:

1. The system retrieves relevant source emails.
2. It shows why each result is relevant.
3. It summarizes threads with source links.
4. It marks AI-generated content clearly.
5. It lets the user verify every claim by opening source emails.
