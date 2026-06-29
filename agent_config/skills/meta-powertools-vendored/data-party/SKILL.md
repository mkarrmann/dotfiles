---
name: data-party
description: Create annotation pages, AI-judge evaluations, and analysis reports for media content (video, image, audio, text). Use when the user wants to create a data party, annotate, auto-annotate with AI, analyze annotation results, debug/display media data.
allowed-tools: Bash(buck:*), Bash(buck2:*), Bash(presto:*), Bash(ls:*), Bash(mkdir:*), Bash(manifold:*), Read, Write, Edit, Glob, Grep
---

# Data Party Tool

End-to-end platform for media evaluation: create shareable annotation pages, run AI-judge evaluations (via Claude or Gemini), and generate professional analysis reports with embedded media. Supports video, image, audio, and text with flexible annotation schemas (pass/fail, scale, multiple choice, free text), variant comparison grids, and export to CSV/clipboard/Google Sheets.

## When to Use

Activate this skill when the user wants to:
- Create a "data party" or annotation HTML page
- Build a human evaluation tool for media content
- Compare media variants (A/B testing, model outputs)
- Generate an annotation UI from a Hive table or spreadsheet
- Set up a quality assessment workflow
- **Auto-annotate with AI** — use Claude (images/text) or Gemini (video/audio) as an AI judge to pre-annotate or fully annotate items
- **Debug/display/browse data** — lightweight view without annotation controls (debug mode)
- **Analyze/visualize annotation results** — generate a professional report with embedded media from completed annotations (also triggered by "visualize" when data contains completed annotations)
- **Retrieve and iterate** on an existing data party from a PixelCloud URL or HTML file
- **Refresh expired CDN URLs** in an existing data party HTML file or PixelCloud link

## Library Location

- **Buck target:** `fbcode//tools/data_party_tool:data_party_tool`
- **Import:** `from tools.data_party_tool import ...`
- **Key classes:** `DataPartyConfig`, `DataPartyGenerator`, `AnnotationSchema`, `MediaConfig`
- **Examples directory:** `fbcode/tools/data_party_tool/generation/`

Scripts go in `fbcode/tools/data_party_tool/generation/` with targets in `generation/BUCK`.

## References

| Reference | What's Inside |
|-----------|---------------|
| [config-reference.md](references/config-reference.md) | `DataPartyConfig`, `UIConfig`, `ExportConfig` — all top-level configuration options |
| [schema-reference.md](references/schema-reference.md) | `AnnotationSchema`, `DimensionConfig`, `VariantGroup`, `VariantConfig` — annotation dimensions and variant comparison grids |
| [media-reference.md](references/media-reference.md) | `MediaConfig`, `MediaSource` — video/image/audio/text sources, storage backends, layouts |
| [common-patterns.md](references/common-patterns.md) | Template script, `ContextSection`, team assignments, customization patterns |
| [data-loading.md](references/data-loading.md) | Hive queries, Google Sheets, JSON, Manifold, benchmark HTML parsing |
| [upload-reference.md](references/upload-reference.md) | PixelCloud upload, Manifold upload, Everstore/OIL handle resolution, video ID resolution, CDN URL refresh |
| [export-reference.md](references/export-reference.md) | Google Apps Script auto-append, CSV/clipboard export, Google Sheets setup |
| [ai-annotate-reference.md](references/ai-annotate-reference.md) | AI-judge workflow — Claude (images/text) and Gemini (video/audio) auto-annotation, batch sizing, Plugboard API |
| [analyze-reference.md](references/analyze-reference.md) | Analysis reports — statistics, embedded media gallery, `/visualize` integration |
| [retrieve-reference.md](references/retrieve-reference.md) | Recover scripts from PixelCloud URLs, HTML extraction, CONFIG/DATA JSON parsing |
| [refresh-reference.md](references/refresh-reference.md) | Refresh expired CDN URLs, `--handle-map` / `--video-id-map`, re-upload workflow |
| [suggestions-reference.md](references/suggestions-reference.md) | Post-upload improvement suggestions to offer users |
| `fbcode/tools/data_party_tool/generation/` | Existing data party scripts as working examples |

## Workflow Routing

Before starting the 7-step annotation workflow, determine which workflow applies. **Inspect the data first** — completed annotation data should trigger the analyze workflow, not the annotation or debug workflow.

### Auto-Detection: Annotation Data vs. Raw Data

When the user provides a spreadsheet or data source, sample the columns and values to classify the data:

| Signal | Indicates |
|--------|-----------|
| Columns named `*_score`, `*_passfail`, `*_rating`, `pass_fail`, `quality_score` | **Completed annotations** → analyze workflow |
| Values like "pass", "fail", "strong_pass", "weak_pass", numeric scores (1-5) in multiple columns | **Completed annotations** → analyze workflow |
| Column named `annotator_name`, `annotator`, `rater` with filled values | **Completed annotations** → analyze workflow |
| Multiple tabs/sheets named after model variants or conditions | **Completed annotations** → analyze workflow |
| Raw media URLs/handles without any rating columns | **Raw data** → annotation or debug workflow |
| User says "visualize", "analyze", "summarize", "results" with a spreadsheet URL | **Completed annotations** → analyze workflow |

**When annotation data is detected:** Follow the [Analyze Results](#analyze-results) workflow. Read [references/analyze-reference.md](references/analyze-reference.md) immediately for the detailed step-by-step workflow.

**When "visualize" is ambiguous:** If the user says "visualize" with a data source that contains completed annotations, treat it as "analyze these annotation results", NOT "display this data in debug mode". Only use debug mode when the data has no annotation columns or when the user explicitly says "debug", "browse", or "display mode".

## Workflow

Follow this 7-step interactive flow:

### Step 1: Gather Inputs

Ask the user for these upfront in a single question with a free-form text response:

1. **Data source** — Hive table — **must include namespace** (e.g., `mgenai.my_table`). If the user provides only a table name without namespace, ask for it immediately before proceeding. Also accepts: Google Spreadsheet URL, JSON file, Manifold paths, or CSV
2. **Annotation guidelines** — free-form description, Google Doc URL, or existing schema from a spreadsheet. If the user just wants to browse/inspect data without annotating, suggest [debug/display mode](#debugdisplay-mode) instead — it skips annotation controls for a lightweight view.
3. **Export destination** — CSV download only, or Google Sheet URL. If the user wants a Google Sheet but doesn't have one yet, offer to create one (see [references/export-reference.md](references/export-reference.md) "Full Google Sheet Setup Workflow").
4. **Context document** (optional) — Google Doc, wiki page, or "none"
5. **Existing data party** (optional) — PixelCloud URL (`pxl.cl/xxxxx`), HTML file, or previous script name from `generation/` (e.g., `my_data_party.py`) to use as a starting point
6. **Media source** (optional) — If the data references media files (video, audio, images) by filename but not by URL, ask where the media is stored (Manifold path pattern, Everstore handles, or CDN URLs). This avoids back-and-forth later when media needs to be embedded in the data party or analysis report.
7. **AI annotation** (optional) — AI-judge auto-annotation (see [ai-annotate-reference.md](references/ai-annotate-reference.md)), AI pre-annotate then human review, or human-only (default). If AI annotation is requested, an analysis report with embedded media is auto-generated after the run (see [analyze-reference.md](references/analyze-reference.md)).

### Step 2: Discover Schema

Inspect data source and guidelines. **Fetch all sources in parallel.**

**Existing data party as reference:** If the user provided a PixelCloud URL or HTML file in Step 1, follow the [retrieve workflow](references/retrieve-reference.md). First check for a matching script in `generation/` — if found and confirmed current by the user, use it directly. Only download the HTML if the script is outdated or missing — see [references/retrieve-reference.md](references/retrieve-reference.md) for download options. Use the recovered schema, media layout, and context sections as the baseline instead of building from scratch.

**Hive tables:** Use the `presto-query` skill or run directly: `presto NAMESPACE --source='claude_skill:presto-cli' --execute 'DESCRIBE table_name'`, then sample rows. If the user provided the hive table, but not the namespace, ask for it directly in a follow up question.

**Google Spreadsheets:** Always use the `google-sheets` skill's `google_api.py` script directly via Bash — **never** use `knowledge_load` for spreadsheets. `google_api.py` returns a proper 2D JSON array in ~5 seconds, while `knowledge_load` returns raw HTML that is often 500K+ characters and unreliable for structured data. See [references/data-loading.md](references/data-loading.md) for the full workflow. Quick reference:

```bash
# 1. Get metadata (tab names, row counts)
python3 /home/mkarrmann/.claude/agent-market/skills/google-sheets/google_api.py \
  '{"action": "get_spreadsheet", "spreadsheet_id": "<SHEET_ID>"}'

# 2. Fetch data (write JSON to temp file to avoid ! escaping issues)
cat > /tmp/sheets_request.json << 'JSONEOF'
{"action": "get_sheet_data", "spreadsheet_id": "<SHEET_ID>", "range": "Sheet1!A1:Z500"}
JSONEOF
python3 /home/mkarrmann/.claude/agent-market/skills/google-sheets/google_api.py \
  "$(cat /tmp/sheets_request.json)" > /tmp/sheet_data.json
```

The response contains `data.values` — a 2D array where `values[0]` is the header row and `values[1:]` are data rows. For multi-tab loading, see the batch pattern in [references/data-loading.md](references/data-loading.md).

**Google Docs:** Use the `google-docs` skill's `google_api.py` script (preferred) or `knowledge_load` MCP tool as fallback.

**Manifold paths and benchmark HTMLs:** See [references/data-loading.md](references/data-loading.md) for exploration patterns, benchmark HTML parsing, and large document handling.

**Never use `phps` or `WebFetch` for table/doc discovery.**

### Step 3: Propose Schema and Layout

Based on discovered data, propose a complete configuration covering:

1. **Annotation dimensions** — `PASS_FAIL`, `SCALE`, `OPTIONS`, or `TEXT`. The tool has a built-in notes field (`allow_notes=True`) for overall notes— don't duplicate it with a custom TEXT dimension.
2. **Media layout** — `side_by_side`, `single`, or `stacked`
3. **Variant groups** — if comparing variants, use `VariantGroup`
4. **Context fields** — data columns shown per-card
5. **Identifiers** — ID columns (ad_id, video_id) with optional `link_template`
6. **Export configuration** — CSV, clipboard, Google Sheet URL

**Export column order:** `annotator | timestamp | item_index | identifiers | media URLs | dimensions | variant ratings | notes`

Present the proposal with **a visual layout sketch** and **ask for confirmation**:

```
Proposed layout:

┌─ Page ─────────────────────────────────────────────────────────────┐
│  ╔═ Header ══════════════════════════════════════════════════════╗ │
│  ║  Title — Data Party                                           ║ │
│  ║  Description text                                             ║ │
│  ╚═══════════════════════════════════════════════════════════════╝ │
│  ┌─ Controls ────────────────────────────────────────────────────┐ │
│  │ Annotator: [________]   Range: [1] to [50]  [Export Results]  │ │
│  └───────────────────────────────────────────────────────────────┘ │
│  ▶ Context (collapsible — product description, goals, etc.)      │
│  ▶ Instructions (collapsible)                                      │
│  ▶ Guidelines (collapsible)                                        │
│  Progress: ████████░░░░░░░░ 12/50                                  │
│  ┌─ Card #1 ─────────────────────────────────────────────────────┐ │
│  │  Ad ID: 12345 | Video ID: 67890                               │ │
│  │  ┌──────────────┐  ┌──────────────┐                           │ │
│  │  │ Original     │  │ Generated    │                           │ │
│  │  │   ▶          │  │   ▶          │                           │ │
│  │  └──────────────┘  └──────────────┘                           │ │
│  │  Context: Summary | Description                                │ │
│  │  ┌─ Annotations ─────────────────────────────────────────────┐ │ │
│  │  │ Safety [Pass][Fail]  │ Quality [Strong][Weak][Fail]        │ │ │
│  │  │ Additional Notes: [________________________]               │ │ │
│  │  └────────────────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────────────┘ │
│  ┌─ Card #2 ─── ... ────────────────────────────────────────────┐ │
│  └───────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

**Key rules:** Show full page layout, at least one complete card, media arrangement, annotation buttons, context fields

### Step 4: Generate the Script

Create the script at `fbcode/tools/data_party_tool/generation/<script_name>.py`. Add `# pyre-strict` at the top of the file.

For the template script, dimension definition patterns, and customization options, see [references/common-patterns.md](references/common-patterns.md).

**Link reference docs in context sections** — If the user provided annotation guidelines (Google Doc URL), a context document, or any other reference material, include a clickable HTML link to it in the appropriate `ContextSection` content. Use `<a href="URL" target="_blank">Link Text</a>` within the content string so annotators can easily access the source documents.

**Keep the subtitle short** — `UIConfig.description` appears in the header as a one-line subtitle. Keep it to one short sentence. Move longer details into `context_sections` instead.

**Always include POC** — Every generated script must set `poc_info` in `UIConfig` to auto-detect the current user's unix name at runtime:

```python
poc_info=f"@{os.environ.get('USER', 'unknown')}",
```

The `/data-party` attribution is included in the header by default — no need to set `attribution_html` unless overriding it.


### Step 5: Add Buck Target

Add a `python_binary` to `fbcode/tools/data_party_tool/generation/BUCK`:

```python
python_binary(
    name = "<script_name>",
    srcs = ["<script_name>.py"],
    main_function = "tools.data_party_tool.generation.<script_name>.main",
    deps = [
        "//tools/data_party_tool:data_party_tool",
        "//pvc2:pvc2",
    ],
)
```

Only include `//pvc2:pvc2` if the script queries Hive tables at runtime; omit it for scripts that only load from local JSON.

### Step 6: Build and Run

```bash
buck2 build fbcode//tools/data_party_tool/generation:<script_name>
buck2 run fbcode//tools/data_party_tool/generation:<script_name>
```

If PixelCloud upload fails, ensure `px` is installed (`feature install --persist px`) and try again. Use `px_upload(local_path, title=...)` from the library.

### Step 7: Report Results

Report: local file path, item count, HTML size, buck target for future runs, PixelCloud URL (if uploaded).

After reporting, **always ask the user if it looks good or if they'd like edits**. Present the PixelCloud URL and then ask in your response text.

**Proactive suggestions (REQUIRED after every upload):** After every PixelCloud upload — initial or re-upload — read [references/suggestions-reference.md](references/suggestions-reference.md) and proactively offer 2-3 relevant suggestions. This applies to the first build AND all subsequent re-uploads after edits. For example:

> Does the data party look good? Let me know if you'd like any changes, for example:
> - Add a "Prompt Repeatability" dimension
> - Enable sticky media so the video stays visible while scrolling through annotations
> - Set up Google Apps Script auto-append for one-click export

Pick suggestions relevant to the current data party — don't use generic ones. Consider what dimensions might be missing, whether the layout could be improved, or if context fields could be added/removed. After re-uploads, tailor suggestions to what hasn't been configured yet — don't re-suggest features already enabled.

### Iterating on edits

When the user requests changes:
1. Make the edit to the Python script
2. **Do NOT re-upload to PixelCloud automatically** — ask the user if they have more edits or if they're ready to regenerate and upload
3. Only run `buck2 run` when the user confirms they're done editing
4. **After every re-upload, proactively offer suggestions** — read [references/suggestions-reference.md](references/suggestions-reference.md) and suggest 2-3 improvements that haven't been configured yet. This ensures users discover useful features they may not know about.

**Batch edits to minimize rebuilds:** When making multiple small changes (e.g., sticky media, team assignments, export URL), apply all edits to the script first, then do a single rebuild + re-upload. Proactively ask: "Any other changes before I rebuild and re-upload?"

## Debug/Display Mode

When the user wants to quickly **browse**, **debug**, or **prototype** their data without annotation overhead, use debug mode. Activate when the user says things like "debug my data", "display mode", "browse this data", "prototype a data party", or "just show me the data".

**Important:** Do NOT activate debug mode when the data contains completed annotation columns (scores, pass/fail, ratings). If the user says "visualize" with annotation data, route to the [Analyze Results](#analyze-results) workflow instead. See [Workflow Routing](#workflow-routing) above.

**Streamlined 4-step workflow:**

1. **Gather** — Ask for data source and any context (same as Step 1, but skip annotation guidelines and export destination)
2. **Discover** — Inspect data source, identify media columns and context fields (same as Step 2)
3. **Generate debug script** — Create the script with `debug_mode=True` on `DataPartyConfig`. Use a minimal `AnnotationSchema` (empty dimensions list, `allow_notes=False`). Focus on identifiers, context fields, and media display.
4. **Build and run** — Same as Steps 5-6, then report results

**Key difference from annotation mode:** Set `debug_mode=True` on `DataPartyConfig`. This automatically:
- Removes: annotator input, export buttons, upload modal, instructions, guidelines, progress grid, annotations per card, notes per card, status badges
- Adds: **"Copy All to CSV"** button — copies all data fields to clipboard as CSV (excludes internal `_resolved_*` fields, converts interncache URLs back to `manifold://` paths)
- Keeps: header, all context sections (expanded by default), media display, identifiers, search + range (merged into one compact bar), config section
- Context fields are expanded by default for easy data inspection

```python
config = DataPartyConfig(
    id="my_debug_party",
    schema=AnnotationSchema(
        id="debug", name="Debug", version="1.0",
        dimensions=[], allow_notes=False,
    ),
    media=media,
    identifiers=[...],
    context_fields=[...],
    debug_mode=True,
    ui=UIConfig(title="Debug: My Data", ...),
)
```

## Analyze Results

When the user wants to **analyze**, **summarize**, or **visualize results** from completed annotations, follow the analyze workflow. Activate when the user says things like "analyze these results", "summarize annotation results", "visualize results from", "visualize [spreadsheet URL]", or provides a spreadsheet URL that contains completed annotation data (see [Workflow Routing](#workflow-routing) for auto-detection signals).

**Always read [references/analyze-reference.md](references/analyze-reference.md)** for the detailed step-by-step workflow, including source gathering, data analysis patterns, media embedding, and `/visualize` integration.

**Quick overview:**
1. **Gather sources** — annotation spreadsheet (Google Sheet URL), PixelCloud URL for context, original guidelines, **and media source** (Manifold paths, Everstore handles, or CDN URLs for embedding in the report)
2. **Load data** — read annotation data directly via `google_api.py` (NOT `knowledge_load` — see Step 2 for the exact commands). Load PixelCloud context and resolve media URLs in parallel.
3. **Analyze** — compute pass rates per dimension, identify failure patterns, pull sample failures
4. **Visualize** — invoke `/visualize` to create a professional report with distribution charts, stats tables, and failure case gallery **with embedded media**

## AI Annotation (Auto-Annotate with Claude)

When the user wants to **auto-annotate**, **AI judge**, or have **Claude evaluate** data party items, use the AI-native annotation workflow.

**Activation triggers:** "auto-annotate", "AI annotate", "Claude judge", "AI evaluate", "pre-annotate"

**MANDATORY — Read the reference doc FIRST:** Before taking ANY action (no searching for scripts, no downloading HTML, no exploring the codebase), read [references/ai-annotate-reference.md](references/ai-annotate-reference.md) in full. This reference contains critical information about environment setup, Plugboard connectivity, subagent limitations, and the correct dispatch method for video/audio annotation. Skipping this step leads to wasted time debugging environment issues that are already documented.

The reference covers the full step-by-step workflow, cache-first data pattern, prompt template, media download scripts, batch sizing guidance, sample count selection, and edge cases.

## Team Assignments (Range Splitting)

When the user wants to **split work**, **assign ranges**, **team annotation**, or **divide among team**, add pre-configured team assignments so each annotator has a named range.

**Activation triggers:** "split work", "assign ranges", "team annotation", "divide among team", "assign to team"

For team name sources (manual list, Google Sheet column, calendar meeting invitees), the auto-split helper pattern, and UI behavior details, see [references/common-patterns.md](references/common-patterns.md) "Team Assignments" section.

## Retrieving & Iterating on Existing Data Parties

When a user provides a PixelCloud URL or HTML file and wants to edit or iterate on an existing data party:

1. **Find the script first** — check `fbcode/tools/data_party_tool/generation/` for the original script. Use `knowledge_load` on the PixelCloud URL to get the title, then match it against scripts. If a matching script is found, **present it to the user immediately** and ask if it's up to date. If confirmed, skip the HTML download and start editing directly.
2. **Download HTML only if needed** — only download the PixelCloud HTML if (a) no script exists, or (b) the user says the script is outdated. See [references/retrieve-reference.md](references/retrieve-reference.md) for download options and what does NOT work.
3. **If no script exists, recover from HTML** — download the HTML, extract embedded DATA + CONFIG JSON, parse the rendered HTML for UI customizations (title, instructions, context sections, gradient colors, etc.), and regenerate a script from scratch.

CONFIG JSON contains schema, media, identifiers, context fields, export, and handle_metadata. Most `UIConfig` fields (title, description, instructions, context sections, poc_info, gradient colors) are rendered into HTML and must be parsed from the DOM. See [references/retrieve-reference.md](references/retrieve-reference.md) for the full extraction workflow with code snippets, what's recoverable vs not, and the CONFIG-to-Python mapping.

## Refreshing Expired CDN URLs

When a user asks to refresh a data party (e.g. "refresh my data party URLs", "media is broken", "refresh pxl.cl/xxxxx"), use the standalone refresh CLI instead of regenerating the entire page. See [references/refresh-reference.md](references/refresh-reference.md) for the full step-by-step workflow, CLI arguments, and how to ensure new data parties support refresh via `handle_metadata`.

**IMPORTANT:** After reuploading, **never** post a comment on the original PixelCloud post without first asking the user for explicit permission. Always report results and ask before commenting.

## Notes

- **Save data JSON to `/tmp`, never inside `generation/`** — the `generation/` directory is checked into source control. Always save extracted data (from spreadsheets, Hive, Manifold, etc.) to `/tmp/<script_name>_data.json` and load it at runtime.
- **Ask the user about data structure before exploring** — users know their data layout
- **Never `manifold cat` files >50MB** — use `manifold get` to download locally first
- **Before downloading large HTML files from Manifold** — run `manifold ls` to check file size first. If the file is very large (>50MB), it likely contains base64-encoded media (videos/images embedded directly in the HTML). Ask the user if the data is available somewhere else (e.g., a Hive table, separate video URLs, or a JSON manifest) before downloading. Parsing base64-encoded HTML is slow and memory-intensive.
- **Always use Buck imports** — `from tools.data_party_tool import ...`, never `sys.path.insert`
- **Never run scripts directly** — always `buck2 run`, never `python3 script.py`
- **Never use WebFetch for Google Docs/Sheets** — use the `google-docs` skill's `google_api.py` for Google Docs; use the `google-sheets` skill's `google_api.py` for Google Spreadsheets (returns proper 2D arrays). See [references/data-loading.md](references/data-loading.md).
- **Never use phps** for table discovery — use `presto` CLI
- **Never use pvc2.pvc2()** — always `pvc2.context()`
- **Always save locally first** before attempting uploads
- **Use `# pyre-strict`** at the top of all new Python files
- **Add scripts to `tools/data_party_tool/generation/`** — do not scaffold a separate user directory
- **Add targets to `tools/data_party_tool/generation/BUCK`** — each script gets its own `python_binary`
- **Do not duplicate the built-in notes field** — `allow_notes=True` (default) provides general notes
- **Put rubric definitions in `OptionConfig.description`** — not in the dimension `description` field
- **Google Sheet export setup** — when the user wants Google Sheet export, offer the full setup: create sheet, share with meta.com, set up Apps Script auto-append, and configure the Annotations tab GID. If the user provides an existing sheet with restricted access, ask if they want to enable edit access for Meta. See [references/export-reference.md](references/export-reference.md) for the Code.gs template, full setup workflow, and domain sharing pattern.
- **Prefer `resolve_handles` over `resolve_everstore_handles`** — `resolve_handles` auto-detects handle type (OIL vs Everstore) and produces more reliable CDN URLs. Using `resolve_everstore_handles` on OIL handles causes "URL signature mismatch" errors.
- **PixelCloud HTML cannot be downloaded via `WebFetch`, `curl`, or `knowledge_load`** — see [references/retrieve-reference.md](references/retrieve-reference.md) for download options and what does NOT work.
- **`knowledge_load` for PixelCloud returns metadata only** — use it to confirm the title/description match with a script, not for full HTML extraction.
