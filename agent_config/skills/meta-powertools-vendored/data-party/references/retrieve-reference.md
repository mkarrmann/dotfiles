---
description: How to retrieve an existing data party HTML and regenerate a generation script from it
---

# Retrieve & Regenerate Reference

Recover a generation script from an existing data party HTML file. This enables iterating on a data party when the original script is unavailable or when customizations need to be preserved.

## When to Use

Activate this workflow when the user says things like:
- "I want to edit/update this data party" (and provides a PixelCloud URL or HTML file)
- "Regenerate the script for pxl.cl/xxxxx"
- "I have an old data party and want to make changes"
- "Retrieve my data party and update the schema"

## What's Recoverable

Data party HTML files embed data and configuration in two places:

### From JavaScript Variables (structured JSON)

| Variable | Recoverable Fields |
|----------|--------------------|
| `const DATA = [...]` | All data rows — column values, resolved URLs, identifiers, context values |
| `const CONFIG = {...}` | Schema (dimensions, options, variant groups, guidelines, dimension_layout), media config (groups, sources, layout, lazy_load), identifiers, context fields, export settings, assignments, handle_metadata |

**Important:** `CONFIG` only contains a **subset** of `UIConfig` fields: `primary_color`, `show_progress`, `display_mode`, `show_item_numbers`, `emphasize_notes_on_fail`. Most UI customizations must be recovered from the rendered HTML.

### From Rendered HTML (parse the DOM)

These fields are rendered directly into HTML by section classes and are NOT in CONFIG JSON:

| HTML Location | How to Extract | Python Field |
|---------------|----------------|--------------|
| `<title>` tag | Regex or parser | `UIConfig.title` |
| `<h1>` in `.header` | Parse header | `UIConfig.title` |
| `.subtitle` paragraph | Parse header | `UIConfig.description` |
| `.poc-info` span | Parse header | `UIConfig.poc_info` |
| `.instruction-steps li` elements | Parse list items | `UIConfig.instruction_steps` |
| `.instructions-content p` | Parse paragraph | `UIConfig.instructions` |
| `.context-section` blocks | Parse each section's title, icon, content | `UIConfig.context_sections` (list of `ContextSection`) |
| `.reviewer-sidebar` list items | Parse list | `UIConfig.reviewer_instructions` |
| CSS gradient values | Regex for `__GRAD_START__`/`__GRAD_END__` replacement values | `UIConfig.gradient_start`, `UIConfig.gradient_end` |
| Default range end `value=` | Parse `#range-end` input | `UIConfig.default_range_end` |

### Not Recoverable

These are lost when a script is not available:

- **Original Hive query** — the SQL and namespace used to load data
- **Custom Python data processing** — merges, joins, transformations, filtering logic
- **Sampling/filter config** — `DataSourceConfig.sample_size`, `filter_column`, `filter_values`
- **Data source namespace** — which Hive namespace the data came from
- **PixelCloud upload tags** — tags passed during PixelCloud upload
- **Output path config** — `DataPartyConfig.output_path`, `output_filename`

## Workflow

### Step 1: Find the Existing Script

Check if the original generation script exists:

```bash
ls fbcode/tools/data_party_tool/generation/*.py
```

Match the script to the data party by comparing:
- The `UIConfig.title` in the script against the PixelCloud post title (from `knowledge_load`)
- The `DataPartyConfig.id` against the PixelCloud URL's partyId

**If a script is found and the title matches**, present the script's configuration to the user immediately. Ask them whether the script is up to date, or if they've made manual changes since the last commit.

- **If the user confirms the script is current** — skip HTML download entirely and proceed directly to their requested edits. This avoids the slow and often-failing download process.
- **If the user says the HTML has been modified** — proceed to Step 2 to download and reconcile.
- **If no script is found** — proceed to Step 2 to download and recover.

### Step 2: Download the HTML

Use one of the following approaches:

#### Option A: Lookaside URL (preferred — works without authentication)

Ask the user to:
1. Open the PixelCloud page in a browser
2. click **"Download .html"** in the top right corner. Then either:

Option A. **Lookaside URL (preferred)**: Copy the URL from the new tab (`https://lookaside.fbsbx.com/pxlcld/framed/?...`) and paste it. Then download with:
   ```bash
   curl -sL "<lookaside_url>" -o /tmp/<script_name>.html
   ```
Option B. **Save locally**: Press **Ctrl+S** (or **Cmd+S** on Mac) to save the HTML file and provide the local file path.

#### Option C: `knowledge_load` MCP tool (metadata only)

**Note:** The `knowledge_load` MCP tool for PixelCloud URLs only returns **metadata** (title, tags, description) — **not the full HTML content**. Use it to confirm the title/description match with a script, but do not rely on it for CONFIG/DATA extraction.

```python
# This only returns metadata, NOT the HTML:
knowledge_load(url="https://pxl.cl/xxxxx")
# Returns: {"title": "...", "tags": "...", "description": "..."}
```

#### What does NOT work

- **`WebFetch` to `pxl.cl` or `pixelcloud.fb.com`** — domain verification fails
- **`curl` to `pxl.cl` or `internalfb.com/intern/px/p/...`** — returns the login page (requires browser session auth, not OAuth tokens)
- **`manifold get` with `botmate_pixelcloud_handle_content_mapping/`** — this bucket may not exist in all environments

### Step 3: Extract DATA and CONFIG JSON

Read the HTML file and extract the embedded JSON:

```python
import json
import re

with open(local_path, "r", encoding="utf-8") as f:
    html = f.read()

data_match = re.search(
    r"const DATA = (\[.*?\]);\s*\n\s*const CONFIG", html, re.DOTALL
)
config_match = re.search(
    r"const CONFIG = (\{.*?\});\s*\n\s*const TOTAL_ITEMS", html, re.DOTALL
)

data = json.loads(data_match.group(1))
config = json.loads(config_match.group(1))
```

### Step 4: Extract UI Customizations from HTML

Parse the rendered HTML to recover fields not in CONFIG JSON:

```python
# Title
title_match = re.search(r"<title>(.*?)</title>", html)
title = title_match.group(1) if title_match else "Data Party"

# Description (subtitle)
desc_match = re.search(r'<p class="subtitle">(.*?)</p>', html)
description = desc_match.group(1) if desc_match else ""

# POC info
poc_match = re.search(r'<span class="poc-info">(.*?)</span>', html)
poc_info = poc_match.group(1) if poc_match else ""

# Instruction steps
step_matches = re.findall(r"<li>(.*?)</li>",
    re.search(r'class="instruction-steps">(.*?)</ol>', html, re.DOTALL).group(1)
) if re.search(r'class="instruction-steps"', html) else []

# Context sections
context_sections = []
for match in re.finditer(
    r'<summary>(.+?)\s+(.*?)</summary>\s*<div class="context-content">(.*?)</div>',
    html, re.DOTALL
):
    context_sections.append({
        "icon": match.group(1).strip(),
        "title": match.group(2).strip(),
        "content": match.group(3).strip(),
    })

# Gradient colors (from inline CSS)
grad_start_match = re.search(r"linear-gradient\(135deg,\s*(#[0-9a-fA-F]+)", html)
gradient_start = grad_start_match.group(1) if grad_start_match else ""

# Default range end
range_end_match = re.search(r'id="range-end"[^>]*value="(\d+)"', html)
default_range_end = int(range_end_match.group(1)) if range_end_match else 50
```

### Step 5: Reconcile Script with HTML (if script exists)

If an existing script was found in Step 1, compare the extracted CONFIG/DATA against what the script defines. The PixelCloud HTML is the source of truth — the script may be an older version. Update the script to match the HTML:

- **Schema changes** — dimensions added/removed/renamed, option labels changed, guidelines updated
- **Media changes** — sources added, layout changed, storage backends changed
- **UI changes** — title, description, instructions, context sections, gradient colors
- **Data changes** — if the HTML has more/fewer items or different columns than the script's query would produce, save the HTML data as the new source

After reconciling, proceed to the user's requested edits.

### Step 5 (alt): Generate Script from Scratch (no existing script)

Save the extracted data to `/tmp/` and generate a new Python script:

```python
# Save data for reuse
# partyId is "{config_id}_{uuid}", e.g. "musicgen_video_ads_a1b2c3d4"
party_id = config.get("partyId", "")
# Strip trailing UUID to get the original config id
script_name = "_".join(party_id.split("_")[:-1]) if party_id else ""
# If extraction failed, derive a name from the page title
if not script_name:
    import re as _re
    title = _re.search(r"<title>(.*?)</title>", html)
    script_name = _re.sub(r"[^a-z0-9]+", "_", (title.group(1) if title else "data_party").lower()).strip("_")
data_path = f"/tmp/{script_name}_data.json"
with open(data_path, "w") as f:
    json.dump(data, f)
```

Then generate a script that:
1. **Loads data from the saved JSON** (not from Hive, since the query is lost)
2. **Reconstructs `DataPartyConfig`** from CONFIG JSON + extracted HTML fields
3. **Preserves `handle_metadata`** if present
4. **Preserves all context sections** with their HTML content
5. **Sets `poc_info`** to auto-detect the current user
6. **Ask the user** if they know the original Hive query and want to re-query instead of using saved data
7. **Re-resolve CDN URLs from handles** — CDN URLs extracted from the HTML are likely expired. Identify the URL type and re-resolve:
   - **Everstore/OIL CDN URLs** (pattern: `https://interncache-*.fbcdn.net/v/t*.*-*/*...?...&oh=...&oe=...`, e.g. `interncache-ldc.fbcdn.net/v/t45.1600-4/...n.png?...` or `interncache-nha.fbcdn.net/v/t42.1790-2/...n.mp4?...`) — these expire. The data must also contain the source handle column (check `handle_metadata` or look for columns with Everstore hashes or OIL paths). Call `resolve_handles()` (or `resolve_video_ids_to_cdn_urls()`) to produce fresh CDN URLs.
   - **Manifold interncache URLs** (e.g. `https://interncache-all.fbcdn.net/manifold/genads_models/tree/audio/.../file.mp4`) — these don't expire but should still use `manifold_path_to_url()` for consistency. Convert back to Manifold path (`manifold://genads_models/tree/audio/.../file.mp4`) first.

   Do **not** hardcode expired CDN URLs from the extracted data — always re-resolve from the source handles or Manifold paths.

### Step 6: Iterate

The user can now modify the regenerated script and re-run it following the standard Steps 5–7 from the main workflow.

## CONFIG JSON → Python Mapping

| Config JSON Path | Python Class | Constructor Field |
|-----------------|-------------|-------------------|
| `schema.id` | `AnnotationSchema` | `id` |
| `schema.name` | `AnnotationSchema` | `name` |
| `schema.version` | `AnnotationSchema` | `version` |
| `schema.dimensions[]` | `DimensionConfig` | `dimensions` |
| `schema.dimensions[].type` | `DimensionType` | `dim_type` (e.g. `"pass_fail"` → `DimensionType.PASS_FAIL`) |
| `schema.dimensions[].options[]` | `OptionConfig` | `options` |
| `schema.dimensions[].dimension_layout` | `AnnotationSchema` | `dimension_layout` |
| `schema.variant_groups[]` | `VariantGroup` | `variant_groups` |
| `schema.variant_groups[].variants[]` | `VariantConfig` | `variants` |
| `schema.general_guidelines[]` | `AnnotationSchema` | `general_guidelines` |
| `media.groups[]` | `MediaGroup` | `groups` |
| `media.groups[].sources[]` | `MediaSource` | `sources` |
| `media.groups[].sources[].type` | `MediaType` | `media_type` |
| `media.groups[].sources[].storage_backend` | `StorageBackend` | `storage_backend` |
| `media.layout` | `MediaConfig` | `layout` |
| `identifiers[]` | `IdentifierField` | `identifiers` |
| `context_fields[]` | `ContextField` | `context_fields` |
| `export.google_sheet_url` | `ExportConfig` | `google_sheet_url` |
| `export.enable_csv_download` | `ExportConfig` | `enable_csv_download` |
| `export.enable_clipboard_copy` | `ExportConfig` | `enable_clipboard_copy` |
| `assignments` | `AssignmentConfig` | `assignments` |
| `handle_metadata` | `DataPartyConfig` | `handle_metadata` |

### Fields from HTML (not in CONFIG JSON)

| HTML Source | Python Field |
|-------------|-------------|
| `<title>` / `<h1>` | `UIConfig.title` |
| `.subtitle` | `UIConfig.description` |
| `.poc-info` | `UIConfig.poc_info` |
| `.instruction-steps li` | `UIConfig.instruction_steps` |
| `.context-section` blocks | `UIConfig.context_sections` |
| `.reviewer-sidebar li` | `UIConfig.reviewer_instructions` |
| CSS gradient | `UIConfig.gradient_start`, `UIConfig.gradient_end` |
| `#range-end` value | `UIConfig.default_range_end` |
| `CONFIG.ui.primary_color` | `UIConfig.primary_color` |
| `CONFIG.ui.display_mode` | `UIConfig.display_mode` |
| `CONFIG.ui.show_progress` | `UIConfig.show_progress` |
| `CONFIG.ui.emphasize_notes_on_fail` | `UIConfig.emphasize_notes_on_fail` |
