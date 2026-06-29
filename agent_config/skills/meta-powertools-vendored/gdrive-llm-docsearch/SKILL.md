---
name: gdrive-llm-docsearch
description: Discover and analyze Google Docs across your organization using natural language queries. Unlike the google-docs skill (which operates on a single known document), this skill FINDS documents you don't already have links to. Use when the user says "find gdocs about X", "search for documents where we discussed Y", "what docs exist about Z", or "find planning docs from the product team". Combines Metamate search with deep document analysis.
---

# Google Drive LLM Document Search

Discover Google Docs across your organization using natural language queries, then analyze their content in depth.

## How This Differs From Other Skills

| Skill | Purpose | When to Use |
|-------|---------|-------------|
| **gdrive-llm-docsearch** (this) | Find documents you don't have URLs for | "Find docs about quarterly planning" |
| **google-docs** | Read/write a specific document | "Read this doc: [URL]" |
| **summarize-doc-links** | Extract and summarize links FROM a doc | "Summarize the links in this doc" |
| **doc-collaboration** | Collaborative writing workflow | "Help me write this planning doc" |

---

## Available Actions

### Action 1: search_documents

**Tool:** `mcp__plugin_meta_mux__knowledge_filtered_search`

Search for Google Docs across the organization.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `keywords` | string | Yes | Plain keywords to search for |
| `natural_language_query` | string | Yes | Full question format for semantic search |
| `doc_types` | array | Yes | Set to `["GOOGLE_DOCUMENT"]` for Google Docs |
| `authors` | array | No | Employee IDs to filter by author |
| `start_creation_time` | string | No | Filter docs created after this date |
| `end_creation_time` | string | No | Filter docs created before this date |
| `start_update_time` | string | No | Filter docs updated after this date |
| `end_update_time` | string | No | Filter docs updated before this date |
| `workplace_group_ids` | array | No | Filter to specific Workplace groups |

**Example:**
```json
{
  "keywords": "quarterly planning roadmap",
  "natural_language_query": "What documents discuss quarterly planning for the product team?",
  "doc_types": ["GOOGLE_DOCUMENT"],
  "start_creation_time": "2024-01-01"
}
```

---

### Action 2: get_document_structure

**Tool:** `mcp__google_docs__google_docs`

Get the hierarchical structure of a discovered document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | Set to `"get_document_structure"` |
| `document_id` | string | Yes | Google Doc ID from search results |

**Returns:** Headings, tables, and section hierarchy.

---

### Action 3: get_document_body

**Tool:** `mcp__google_docs__google_docs`

Get the full text content of a document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | Set to `"get_document_body"` |
| `document_id` | string | Yes | Google Doc ID from search results |
| `include_formatting` | boolean | No | Include formatting info (default: false) |

**Returns:** All text segments with start/end indices.

---

### Action 4: find_text

**Tool:** `mcp__google_docs__google_docs`

Search for specific terms within a document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | Set to `"find_text"` |
| `document_id` | string | Yes | Google Doc ID |
| `search_text` | string | Yes | Text to search for |

**Returns:** All occurrences with positions.

---

### Action 5: get_permissions

**Tool:** `mcp__google_docs__google_docs`

Check who has access to a document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `action` | string | Yes | Set to `"get_permissions"` |
| `document_id` | string | Yes | Google Doc ID |

**Returns:** List of users with their access levels.

---

## Workflow

### Step 1: Parse Query → Extract Search Criteria

| User Query | Extracted Criteria |
|------------|-------------------|
| "find docs about X" | `keywords: "X"` |
| "documents from team Y about Z" | `keywords: "Z"`, `authors: [Y's IDs]` |
| "planning docs from Q4 2024" | `keywords: "planning"`, `start_creation_time: "2024-10-01"` |
| "compare docs about X and Y" | Two searches, then cross-analysis |

### Step 2: Search → Use Action 1

Call `search_documents` with extracted criteria. Returns document metadata and snippets.

### Step 3: Analyze → Use Actions 2-5

For each discovered document:
1. `get_document_structure` - Understand organization
2. `get_document_body` - Get full content
3. `find_text` - Locate specific terms
4. `get_permissions` - Check access (if needed)

### Step 4: Cross-Document Analysis

When multiple documents found:
- **Common themes** across documents
- **Contradictions** between documents
- **Timeline** of how thinking evolved
- **Gaps** in coverage

### Step 5: Return Results

```markdown
## Search Results for "quarterly planning"

Found 5 documents. Key insights:
- All docs agree on [theme]
- Gap: No coverage of [topic]

### 1. Q4 Product Roadmap
- **URL**: https://docs.google.com/document/d/...
- **Updated**: 2024-01-15
- **Owner**: alice @ meta . com (Alice Smith)
- **Summary**: Outlines 5 key initiatives...

### 2. Planning Meeting Notes
- **URL**: https://docs.google.com/document/d/...
- **Updated**: 2023-12-20
- **Summary**: December planning discussion...
```

---

## Limitations

| Can Do | Cannot Do |
|--------|-----------|
| Find docs indexed by Metamate | Search unindexed documents |
| Filter by author, date, group | Search by folder structure |
| Semantic/keyword search | Exact phrase matching |
| Analyze accessible docs | Access permission-denied docs |

**Tip:** For documents you already have URLs for, use the `google-docs` skill instead.
