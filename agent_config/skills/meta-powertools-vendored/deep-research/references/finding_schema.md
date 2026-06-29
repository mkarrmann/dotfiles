# Finding Schema

Every research subagent emits findings as **structured JSON inside `SendMessage` content**, not as free-text. The orchestrator collects these into `~/.claude/teams/<team>/findings.jsonl`.

## Schema (canonical)

```json
{
  "id": "F-3",
  "agent": "researcher-2",
  "claim": "Token storage uses Configerator at startup",
  "evidence": [
    {
      "type": "file_ref",
      "path": "fbcode/nest/auth/handler.py",
      "line_start": 85,
      "line_end": 92,
      "sha": "abc1234",
      "snippet": "def get_token():\n    cfg = configerator.get('auth.token.cfg')\n    ...",
      "symbol": "get_token",
      "symbol_kind": "function",
      "language": "python",
      "captured_at": "2026-04-21T18:32:11Z"
    },
    {
      "type": "diff_ref",
      "diff_id": "D101234",
      "context": "introduced the Configerator call in handler.py",
      "captured_at": "2026-04-21T18:32:11Z"
    },
    {
      "type": "tool_output",
      "tool": "fbgr",
      "query": "configerator.get.*auth",
      "hits": 14,
      "summary": "14 hits across fbcode/nest/auth/, all in handler.py and config.py",
      "captured_at": "2026-04-21T18:32:11Z"
    }
  ],
  "confidence": "high",
  "dependencies": ["F-1"],
  "needs_diff_landed": true,
  "source_kind": "native"
}
```

## Field reference

- `id`: `F-N` where N is monotonically increasing within the team.
- `agent`: emitting subagent name.
- `claim`: ≤ 200 chars, single-sentence factual statement.
- `evidence[]`: ≥ 1 item required. Types below.
- `confidence`: `"high" | "medium" | "low"` — agent's self-stated confidence (verifier may override).
- `dependencies`: list of other finding IDs this depends on (downstream verification can cascade).
- `needs_diff_landed`: true if claim asserts current production behavior; verifier will mark `[STALE]` if cited diff is `[DRAFT]` / `[ABANDONED]`.
- `source_kind`: `"native"` (agent emitted JSON directly) | `"extracted"` (Phase 1.5 retrofit from free-text).

## Evidence types

### file_ref
- `path`: repo-relative path
- `line_start`, `line_end`: 1-indexed inclusive
- `sha`: short commit hash captured at research time
- `snippet`: literal code (≥ 12 chars; ≤ 1000 chars)
- `symbol`: identifier name (function, class, type, const, variable)
- `symbol_kind`: `function | class | type | const | variable | unknown`
- `language`: `python | cpp | hpp | c | cc | h | tsx | ts | jsx | js | swift | kt | java | rs | go | thrift | proto | unknown`
- `captured_at`: ISO-8601 UTC timestamp

### diff_ref
- `diff_id`: `D` followed by 6-9 digits
- `context`: 1-line description of why the diff is cited
- `captured_at`: ISO-8601 UTC

### tool_output (informational; does NOT count toward verified_pct denominator)
- `tool`: name (`fbgr`, `fbgs`, `fbgf`, `buck2`, `jf`, `sl`, etc.)
- `query`: exact query string
- `hits`: integer count
- `summary`: ≤ 200 chars
- `captured_at`: ISO-8601 UTC

### knowledge_load (non-code evidence; counts toward verified_pct only if re-readable at gate time)
- `doc_id`: wiki page ID or URL
- `snippet`: quoted text (≥ 12 chars)
- `captured_at`: ISO-8601 UTC

### presto_query
- `query_id`: presto query ID
- `result_summary`: ≤ 200 chars

### external_url
- `url`: full URL
- `captured_at`: ISO-8601 UTC

## Helper script

See `~/.claude/skills/deep-research/references/scripts/finding_template.sh` for a JSON skeleton emitter.

## Where findings live

- In-flight: streamed via `SendMessage(type="finding", content=<json>)`
- Persisted: `~/.claude/teams/<team>/findings.jsonl` (one JSON object per line, append-only)
- Verified: `~/.claude/teams/<team>/verification.jsonl` (one verdict per finding)
