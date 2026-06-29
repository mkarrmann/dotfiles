---
description: Detailed workflow for analyzing annotation results and generating reports with /visualize
---

# Analyze Results Reference

## Overview

After annotation is complete, users want a professional report summarizing results. This workflow orchestrates existing tools — Google Sheets reading, PixelCloud context loading, and the `/visualize` skill — to produce a comprehensive analysis.

## Invocation Patterns

Activate this workflow when the user says:
- "Analyze these results" + spreadsheet URL
- "Summarize annotation results from [source]"
- "Visualize results from [spreadsheet/pixelcloud]"
- "Generate a report from our data party annotations"
- "What were the results of [data party name]?"

## Auto-Detection: Is This Annotation Data?

When the data-party skill routes here, the data has already been classified as completed annotations. However, if you're loading data directly, check for these signals:

**Strong signals (any one is sufficient):**
- Columns named with scoring patterns: `*_score`, `*_passfail`, `*_rating`, `video_quality`, `critical_passfail`
- An `annotator_name` or `annotator` column with filled values
- Values in multiple columns that are pass/fail labels or numeric ratings (1-5)

**Weak signals (need 2+ together):**
- Multiple spreadsheet tabs named after model variants or experimental conditions
- Columns named `*_issues`, `*_notes` alongside score columns
- A `filename` column suggesting items were evaluated

If the data does NOT contain annotation columns, redirect to the main data-party workflow for creating an annotation page instead.

## Media Embedding Requirement

**Reports MUST include embedded media** (video, audio, images) whenever the source data party contains media. This is not optional — a report without media is incomplete. Media should appear in:
1. **Items Gallery** — a scrollable gallery of ALL items (or a representative sample for large datasets) with their media and annotation results side-by-side
2. **Failure Case Deep Dives** — detailed cards for 3-5 representative failure cases with embedded media players

Resolve ALL media URLs to embeddable CDN URLs early in the workflow (Step 2), not just for failure cases.

## Step-by-Step Workflow

### Step 1: Gather Sources (Parallel)

Collect all source references from the user. **Do NOT ask follow-up questions if the user has already provided both the annotation data and the data party context.** Only ask if critical information is missing.

Required and optional sources:

1. **Annotation data** (required) — Google Sheet URL containing completed annotations, or CSV file path
2. **Data party context** (optional) — PixelCloud URL (`pxl.cl/xxxxx`) of the original data party, for loading the schema, dimensions, and media configuration
3. **Original guidelines** (optional) — Google Doc URL or text describing the evaluation criteria
4. **Media source** (required when data party has media) — Where are the media files stored? Needed for embedding videos/images throughout the report. If the original data party has media, the report MUST include it — do not ask the user whether to include it. Only ask for the media *location* if it's not already available from the data party source or export data. Common patterns:
   - `manifold://{bucket}/path/to/{variant}/` — Manifold bucket with variant subdirectories. Convert to CDN: `https://interncache-all.fbcdn.net/manifold/{bucket}/path/to/{variant}/{filename}.mp4`
   - Everstore/OIL handles in a data column — resolve with `resolve_handles()` at runtime
   - CDN URLs already in the export data — use as-is
   - **If the data has a `filename` column but no URL columns**, proactively ask the user where the files are stored. Don't wait until the report is generated to discover media is missing.

### Step 2: Load Data (All in Parallel)

**Launch all data loading tasks in parallel** — do not wait for one to finish before starting the next:

| Source | Action | Tool |
|--------|--------|------|
| Annotation spreadsheet | Read annotation data (get metadata first for tab names, then read the target tab) | `google-sheets` skill's `google_api.py` |
| PixelCloud URL | Load metadata (title, description, tags) | `knowledge_load` MCP tool |
| Data party source | Find the generation script using the [retrieve workflow](retrieve-reference.md) | Follow Step 1 of retrieve-reference.md |
| Guidelines doc | Read evaluation criteria | `google-docs` skill or `knowledge_load` |

**IMPORTANT — Never use `knowledge_load` for spreadsheets.** It returns raw HTML that is often too large to read (500K+ characters) and requires a slow fallback to `google_api.py` anyway. Always go directly to `google_api.py` for spreadsheet data.

**Multi-tab loading:** When loading multiple tabs from the same spreadsheet, use a single Python script to batch-read all tabs in one invocation. See [data-loading.md](data-loading.md) "Multi-Tab Batch Loading" for the pattern. This avoids multiple sequential shell commands and shell-escaping issues with `!` in range notation.

**Finding the data party source:** Follow [retrieve-reference.md](retrieve-reference.md) Step 1 to locate the original generation script in `fbcode/tools/data_party_tool/generation/`. Match by title or data party ID against the PixelCloud metadata. If no script is found, follow retrieve-reference.md Step 2 to download the HTML and extract CONFIG/DATA JSON. From the source, extract:
- **Media configuration** — `MediaConfig`, `MediaSource` types, `StorageBackend`, layout
- **Media URL columns** — which data columns contain original/translated/variant URLs
- **CDN URL pattern** — how to convert source URLs (Manifold paths, Everstore handles) to CDN URLs for embedding

**Important:** The generation script (or recovered CONFIG JSON) is the source of truth for media structure. The annotation export spreadsheet contains the raw URLs (e.g. `manifold://...` paths) which must be converted to CDN URLs for embedding in the report.

**Front-load media discovery:** If the data contains filename-like columns (e.g., `filename`, `video_id`, `asset_name`) but NO URL columns (no `*_url`, no `https://` values), **ask the user immediately**:
1. Do they want media embedded in the report, or proceed without it?
2. If yes, where are the media files stored? Common patterns:
   - "Where are the video files stored? (e.g., `manifold://bucket/path/`)"
   - Provide the Manifold path pattern if the data party script has one

If the user wants media embedded, resolve locations before proceeding to Step 3. Missing media URLs are the #1 cause of reports needing regeneration. If they opt out, proceed without media and note in the report that media was omitted.

### Step 3: Analyze Data

#### Step 3a: Discover Value Taxonomy

**Before computing any statistics**, enumerate the unique values for each annotation column to understand the coding scheme. This prevents misclassifying values (e.g., missing "Strong Pass" when looking only for "Pass").

```python
from collections import Counter

for col in annotation_columns:
    values = Counter(r.get(col, "").strip() for r in records if r.get(col, "").strip())
    print(f"{col}: {dict(values.most_common())}")
```

**Why this matters:** Annotation schemas often use composite labels like "Fail: Translation Accuracy has 'Fail' with level 2 or 3 impact" or "Strong Pass" instead of simple "Pass"/"Fail". Computing statistics on the raw strings without first understanding the taxonomy leads to incorrect aggregations that require re-work.

#### Step 3b: Compute Per-Dimension Statistics

For each annotation dimension:

| Metric | Calculation |
|--------|-------------|
| **Sample size** | Count of non-empty values for this dimension |
| **Distribution** | Count and percentage of each value (pass/fail, scale ratings, option selections) |
| **Pass rate** | For pass/fail dimensions: percentage of ALL pass tiers combined (see below) |
| **Mean score** | For scale dimensions: average rating |
| **Agreement rate** | If multiple annotators rated the same items: percentage of matching ratings |

**Composite pass/fail tiers:** When a pass/fail dimension has multiple tiers (e.g., "Strong Pass", "Weak Pass", "Fail"), compute **overall pass rate = Strong Pass + Weak Pass** (i.e., everything that is not a Fail). Present both the tier breakdown AND the aggregate pass rate. The aggregate is the headline number; the breakdown shows quality distribution within passing items.

#### Failure Analysis

For dimensions with "fail" or low ratings:
- Count total failures
- Identify patterns in failure notes (common keywords, repeated issues)
- Select 3-5 representative failure cases for the deep dive gallery

#### Cross-Dimension Correlations

- Do items that fail on one dimension tend to fail on others?
- Are certain annotators more strict/lenient?
- Is there a pattern in which items (by index range) have more failures?

#### Media URL Resolution (for ALL items)

Resolve media URLs to embeddable CDN URLs for **all items** (not just failure cases). The report needs media for both the items gallery and the failure deep dives.

| Source URL Pattern | CDN URL Conversion |
|--------------------|--------------------|
| `manifold://{bucket}/{path}` | `https://interncache-all.fbcdn.net/manifold/{bucket}/{path}` |
| Everstore/OIL handles | Use `resolve_handles()` or check if CDN URLs are already in the export data |
| `https://interncache-...` (already CDN) | Use as-is |

If the dataset is very large (100+ items), resolve ALL URLs but only embed media for the gallery selection: all failures + a sample of weak passes (up to 5) + a sample of strong passes (up to 5).

### Step 4: Generate Report with /visualize

Invoke the `/visualize` skill to create a professional HTML report. Use the `experiment-report` archetype as a starting point.

**Report structure to request:**

```
Executive Summary
- 3-5 high-level key points:
  - Overall pass rate across all dimensions
  - Worst-performing dimension (name + pass rate)
  - Number of critical failures (items failing on 2+ dimensions)
  - Most common failure pattern (e.g., "distortions in 70% of failures")
  - Notable outliers or surprising results
- Sample size and annotator count

Per-Dimension Results
- Table: Dimension | Sample Size | Pass Rate | Distribution
- Distribution bars/charts for each dimension (pure CSS — no Chart.js/D3)
- Use conic-gradient donut for overall distribution, CSS bar charts per dimension

Tabbed Gallery (with embedded media — REQUIRED)
- Organized by result tier using interactive tabs:
  - "Failures" tab — show ALL failure items (never sample failures)
  - "Weak Passes" tab — show a representative sample (up to 10 items)
  - "Strong Passes" tab — show a representative sample (up to 5 items)
- For each item in the gallery:
  - Embedded media players (video/audio/image) matching the data party layout
  - Item identifier and key context fields
  - Annotation results summary (pass/fail badges, scores)
  - Color-coded status (green=pass, red=fail, yellow=mixed)
  - Annotator notes (if available)

Cross-Dimension Pattern Analysis
- Co-occurrence table: which dimensions tend to fail together
- Items failing on 3+ dimensions (critical failures) — list and highlight
- Common weakness patterns across dimensions
- Correlation between dimension ratings (e.g., "items that fail safety also tend to score low on quality")

Annotator Agreement (if multiple annotators)
- Inter-annotator agreement metrics
- Annotator-level breakdown

Methodology
- Link to original data party (PixelCloud URL)
- Link to annotation guidelines (Google Doc or data party instructions)
- Data collection period
- Total items evaluated
- AI model used (if AI-annotated)
```

#### Embedded Media (Required When Present)

**Always embed media throughout the report** — in both the Items Gallery and the Failure Case Deep Dives — when the original data party contains media (video, audio, or images). Do not ask the user — if the data party has media, the report must include it. A report without embedded media is incomplete and will need to be regenerated.

**Match the original data party's media sizing and layout:**
- Read the video/image CSS from `fbcode/tools/data_party_tool/templates/styles.css` for the canonical sizing
- Videos: `max-height: 280px`, `aspect-ratio: 16 / 9`, `object-fit: contain`, `width: 100%`, `background: #000`, `border-radius: 8px`
- Images: `max-height: 300px`, `object-fit: contain`, `width: 100%`, `border-radius: 8px`
- Side-by-side layout: use `grid-template-columns: repeat(auto-fit, minmax(280px, 1fr))` with `gap: 15px`
- Include labels above each media item (e.g. "Original (EN)", "Translated (ES)") matching the `MediaSource.label` from the generation script

```html
<!-- Side-by-side video pair (matching data party sizing) -->
<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:15px">
  <div>
    <h4 style="font-size:0.9rem; margin-bottom:8px; color:#495057">Original (EN)</h4>
    <video controls preload="metadata" style="width:100%; max-height:280px; aspect-ratio:16/9; object-fit:contain; border-radius:8px; background:#000">
      <source src="https://interncache-all.fbcdn.net/manifold/..." type="video/mp4">
    </video>
  </div>
  <div>
    <h4 style="font-size:0.9rem; margin-bottom:8px; color:#495057">Translated (ES)</h4>
    <video controls preload="metadata" style="width:100%; max-height:280px; aspect-ratio:16/9; object-fit:contain; border-radius:8px; background:#000">
      <source src="https://interncache-all.fbcdn.net/manifold/..." type="video/mp4">
    </video>
  </div>
</div>

<!-- Audio -->
<audio controls style="width:100%">
  <source src="https://interncache-..." type="audio/mpeg">
</audio>

<!-- Image -->
<img src="https://interncache-..." style="width:100%; max-height:300px; object-fit:contain; border-radius:8px" />
```

### Step 5: Report Results

Share the `/visualize` output URL. Ask if the user wants:
- Different failure cases highlighted
- Additional dimensions analyzed
- Different visualization style
- Export of the analysis data as a separate spreadsheet

## Example Analysis Prompt for /visualize

When invoking `/visualize`, provide a prompt like:

```
Create an experiment report for a data party annotation analysis.

Title: [Data Party Name] — Annotation Results
Archetype: experiment-report

Data:
- [N] items evaluated by [M] annotators
- Dimensions: [list dimensions with types]

Results:
[Include computed statistics, distribution tables, failure cases with media URLs]

Media (REQUIRED — embed throughout the report):
- The original data party has [video/audio/image] media in [side_by_side/single/stacked] layout
- CDN URLs for ALL items (or representative sample):
  [List item index, original URL, translated URL for each item]
- Use the data party's canonical video sizing: max-height 280px, aspect-ratio 16/9, object-fit contain
- Items gallery: show ALL failures + sample of weak passes (up to 5) + sample of strong passes (up to 5) with embedded media
- Failure deep dives: 3-5 cases with side-by-side media players

Include:
1. Executive summary with overall pass rate
2. Per-dimension results table with pass rates and distributions
3. Items gallery with embedded media for all items (failures first, color-coded)
4. Failure case deep dives with embedded side-by-side media players
5. Methodology section linking to pxl.cl/xxxxx

Header small subtitle attribution:
- Use "Generated by /data-party + /visualize"
- /data-party should link to https://www.internalfb.com/claude-templates/skills/data-party
- /visualize should link to https://www.internalfb.com/claude-templates/skills/visualize
```

## Notes

- **Anonymize annotator names by default** — do not include real annotator names anywhere in the generated report. Use anonymous labels (e.g., "Rater A", "Rater B") for the annotator analysis table and omit annotator attributions from failure case cards. Only use real names if the user explicitly asks to display them.
- **No new Python library code needed** — this workflow orchestrates existing tools
- **Always embed media throughout the report** — if the original data party has media, the report must include embedded media in BOTH the items gallery AND the failure deep dives. A report without media is incomplete. Do not ask the user; this is the default behavior. Resolve ALL media URLs early (Step 2), not just for failure cases.
- **Always use extracted URLs, never construct manually** — pull media URLs directly from the resolved fields in the extracted DATA JSON or annotation export. Do NOT guess or reconstruct URL paths from Manifold paths — the path structure varies (e.g., `ad_id/bundle_video.mp4` vs `bundle_video/ad_id.mp4`). Before uploading the report, verify at least one media URL loads: `curl -sI <url> | head -1` should return HTTP 200. If URLs return 403/404, suggest running `/data-party refresh` first.
- **Match data party sizing** — read the video/image CSS from `fbcode/tools/data_party_tool/templates/styles.css` for canonical sizing values. Never hardcode arbitrary sizes.
- **PixelCloud CSP blocks CDN scripts** — PixelCloud renders HTML inside an iframe that blocks CDN-loaded scripts (Chart.js, D3.js, Mermaid, etc.). Always use **pure CSS/SVG charts** in reports: conic-gradient donuts, CSS width-percentage bars, inline styled divs. Never use `<canvas>` elements or external charting libraries.
- **Maximize parallelism** — load the spreadsheet metadata, PixelCloud context, and generation script concurrently in Step 2. Do not serialize these calls.
- **Never use `knowledge_load` for spreadsheets** — it returns raw HTML that is often too large (500K+ chars) and unreliable for structured data. Always use `google_api.py` directly. Use `knowledge_load` only for PixelCloud URLs and wiki pages.
- **Always discover value taxonomy first** — before computing statistics, enumerate unique values per annotation column (Step 3a). This prevents misclassifying composite labels like "Strong Pass" or "Fail: Translation Accuracy has..." and avoids costly re-analysis.
- **Pass rate = all pass tiers combined** — when pass/fail has tiers (Strong Pass, Weak Pass), overall pass rate = Strong Pass + Weak Pass. Present both the aggregate headline and the tier breakdown.
- **CDN URLs in export data may have expired** — if media doesn't load in the report, suggest running `/data-party refresh` first
- **Manifold interncache URLs don't expire** — unlike Everstore/OIL CDN URLs, Manifold interncache URLs are stable and do not require refreshing
- **Large annotation datasets** — for 500+ rows, compute summary statistics rather than listing every item
- **Multiple annotators** — if the same items were rated by multiple people, compute inter-annotator agreement before averaging
- **Multi-tab spreadsheets** — when loading multiple tabs, use the batch loading pattern from [data-loading.md](data-loading.md) to read all tabs in a single Python script. Do not make separate shell commands per tab.
