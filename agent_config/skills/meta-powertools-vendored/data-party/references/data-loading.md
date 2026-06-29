---
description: How to load data from various sources into the Data Party Tool
---

# Data Loading Patterns

How to load data from various sources into the Data Party Tool.

## From Hive

```python
ctx = pvc2.context(namespace="<namespace>", source="data_party_tool")
df = ctx.sql_to_pandas("<SQL query>")
data = df.to_dict("records")
```
Always use `pvc2.context()` — never `pvc2.pvc2()`.

## From JSON

```python
import json
with open("path/to/data.json") as f:
    data = json.load(f)
```

## From Google Spreadsheet

Use the `google-sheets` skill's `google_api.py` script to fetch structured row data. This is the preferred method — it returns a proper 2D array with correct row separation, handles quoted fields, and avoids the parsing issues of `knowledge_load`.

**IMPORTANT — Always use `google_api.py`, never `knowledge_load`, for spreadsheets.** `google_api.py` returns a structured 2D JSON array in ~5 seconds. `knowledge_load` returns raw HTML that is often 500K+ characters, too large to fit in context, and unreliable for structured data parsing.

### Step 1: Get spreadsheet metadata

```bash
python3 fbcode/claude-templates/components/skills/google-sheets/google_api.py \
  '{"action": "get_spreadsheet", "spreadsheet_id": "<SHEET_ID>"}' > /tmp/sheet_meta.json
```

This returns sheet names, dimensions (row/column counts), and sheet IDs. Use it to determine the correct range for `get_sheet_data`.

### Step 2: Fetch row data

**Shell escaping warning:** The `!` character in range notation (e.g., `Sheet1!A1:Z200`) causes bash escaping issues when passed as inline JSON. Always write the JSON request to a temp file first:

```bash
cat > /tmp/sheets_request.json << 'JSONEOF'
{"action": "get_sheet_data", "spreadsheet_id": "<SHEET_ID>", "range": "Sheet1!A1:Z200"}
JSONEOF
python3 fbcode/claude-templates/components/skills/google-sheets/google_api.py \
  "$(cat /tmp/sheets_request.json)" > /tmp/sheet_data.json
```

**Do NOT** pass the JSON directly as a bash argument — the `!` will be interpreted by bash history expansion or cause `Invalid \escape` JSON parsing errors.

Explore the first few rows to understand the structure before writing the parsing logic. The response has `data.values` — a 2D array where `values[0]` is the header row and `values[1:]` are data rows.

### Step 3: Extract and save as JSON

Save the JSON to `/tmp` so it is not committed to the repository. **Never save data JSON files inside `fbcode/tools/data_party_tool/generation/`** — that directory is checked into source control.

```python
import json

with open("/tmp/sheet_data.json") as f:
    d = json.load(f)

rows = d["data"]["values"]
headers = rows[0]
data_rows = rows[1:]

records = []
for row in data_rows:
    while len(row) < len(headers):
        row.append("")  # pad short rows
    records.append({
        "id": row[0],
        "field_a": row[1],
        # ... map columns by index
    })

records = [r for r in records if r["id"]]  # filter empty rows

output = "/tmp/my_data.json"
with open(output, "w") as f:
    json.dump(records, f, indent=2)
```

### Loading JSON in the data party script

Load the JSON from `/tmp` at runtime. Do not bundle it as a Buck resource.

```python
import json
import os

data_path = "/tmp/my_data.json"
if not os.path.exists(data_path):
    raise FileNotFoundError(
        f"{data_path} not found. Generate it first by running the data download step."
    )
with open(data_path) as f:
    data: list[dict[str, str]] = json.load(f)
```

### Why not `knowledge_load`?

`knowledge_load` concatenates CSV rows — newlines are replaced with spaces, making standard `csv.reader` parsing unreliable. Fields containing commas, quotes, or special characters cause row boundaries to shift, producing incorrect data. For the analyze workflow, `knowledge_load` on a Google Sheets URL returns raw HTML that is often 500K+ characters — too large to read in context. Always use `google_api.py` instead. Use `knowledge_load` only for PixelCloud URLs and wiki pages.

### Multi-Tab Batch Loading

When loading multiple tabs from the same spreadsheet (common in annotation analysis with one tab per model variant), use a single Python script to batch-read all tabs. This avoids multiple sequential shell commands and sidesteps the `!` escaping issue entirely.

```python
python3 << 'PYEOF'
import json, subprocess

SHEET_ID = "<SHEET_ID>"
TABS = ["tab_1", "tab_2", "tab_3"]
API_SCRIPT = "fbcode/claude-templates/components/skills/google-sheets/google_api.py"

all_data = {}
for tab in TABS:
    req = json.dumps({
        "action": "get_sheet_data",
        "spreadsheet_id": SHEET_ID,
        "range": f"{tab}!A1:ZZ"
    })
    result = subprocess.run(
        ["python3", API_SCRIPT, req],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    if data["success"]:
        rows = data["data"]["values"]
        headers = rows[0]
        records = []
        for row in rows[1:]:
            record = {}
            for i, h in enumerate(headers):
                record[h] = row[i] if i < len(row) else ""
            records.append(record)
        all_data[tab] = records
        print(f"{tab}: {len(records)} records, {len(headers)} columns")
    else:
        print(f"{tab}: FAILED - {data.get('error')}")

with open("/tmp/all_tabs_data.json", "w") as f:
    json.dump(all_data, f)

print(f"Total: {sum(len(v) for v in all_data.values())} records")
PYEOF
```

This pattern:
- Reads all tabs in a single invocation (no shell escaping issues)
- Converts each tab to a list of dicts keyed by header names
- Saves everything to one JSON file for downstream analysis
- Prints a summary so you can verify row counts before proceeding

## From Google Docs

Use the `google-docs` skill's `google_api.py` script to fetch document content. This is preferred over `knowledge_load` — it returns structured text with proper formatting and enforces permitted-authors validation.

```bash
python3 fbcode/claude-templates/components/skills/google-docs/google_api.py \
  '{"action": "get_document", "document_id": "<DOC_ID>"}' > /tmp/doc_content.json
```

Extract the `DOC_ID` from the Google Doc URL: `https://docs.google.com/document/d/<DOC_ID>/edit`.

## From Manifold (media files in a directory)

When media lives on manifold as individual files (e.g., `bucket/path/<item_id>/video.mp4`), construct interncache URLs directly in the data:
```python
MANIFOLD_BASE = "https://interncache-all.fbcdn.net/manifold"
BUCKET = "my_bucket"
PATH_PREFIX = "path/to/videos"

data = []
for item in source_data:
    item["video_url"] = f"{MANIFOLD_BASE}/{BUCKET}/{PATH_PREFIX}/{item['id']}/video.mp4"
    data.append(item)
```
Use `StorageBackend.EXTERNAL_URL` (not `MANIFOLD`) since the URLs are already resolved.

## From Manifold benchmark HTML

When the user provides a manifold path to a benchmark HTML with embedded data (often 100MB+ with base64 videos), download and extract only the metadata — do NOT parse the embedded media:
```bash
# Download the HTML (never cat large files)
manifold get "bucket/path/to/benchmark.html" /tmp/benchmark.html
```
Then extract structured data with regex (not an HTML parser):
```python
import re

with open("/tmp/benchmark.html", "r", errors="replace") as f:
    content = f.read()

# 1. Extract table headers
headers = re.findall(r'<th>(.*?)</th>', content)

# 2. Extract item IDs (e.g., ad_ids from table rows)
id_pairs = re.findall(r'<td>(\d+)</td>\s*<td>(\d{10,})</td>', content)

# 3. Extract metadata cells (skip base64 video cells)
# Adapt the pattern to match your HTML's metadata format
metadata_pattern = re.compile(r'<td>(your_metadata_marker.*?)</td>', re.DOTALL)
metadata_matches = metadata_pattern.findall(content)

# 4. Construct media URLs from the user-provided manifold path
MANIFOLD_BASE = "https://interncache-all.fbcdn.net/manifold"
for item in data:
    item["video_url"] = f"{MANIFOLD_BASE}/{bucket}/{path}/{item['id']}/video.mp4"
```
Save extracted data as JSON to `/tmp` for fast reloads:
```python
import json
with open("/tmp/my_data.json", "w") as f:
    json.dump(data, f)
```

## Table discovery

```bash
presto NAMESPACE --source='claude_skill:presto-cli' --execute 'SHOW TABLES'
presto NAMESPACE --source='claude_skill:presto-cli' --execute 'DESCRIBE table_name'
```

## Manifold Exploration

```bash
# List directory contents (always start here)
manifold ls "bucket/path/to/dir/" | head -30

# Check file sizes to avoid downloading huge files
manifold ls "bucket/path/to/dir/" | grep -v "^DIR"

# For small files (<10MB): pipe directly
manifold cat "bucket/path/to/file.json" | head -100

# For large files (>10MB): download first, then inspect locally
manifold get "bucket/path/to/file.html" /tmp/local_copy.html
```

**Key patterns:**
- Media files are typically organized as `bucket/path/<item_id>/video.mp4` — check a few subdirectories with `manifold ls` to discover the structure
- Interncache URL format: `https://interncache-all.fbcdn.net/manifold/<bucket>/<path>` — this is how manifold files become playable URLs in the browser
- **Never `manifold cat` files >10MB** — it will timeout. Use `manifold get` to download locally first
- Ask the user where the media files live — they often know the directory structure

## Benchmark HTML Parsing

For benchmark HTMLs with embedded media (base64-encoded videos/images):
- These files can be 100MB+ — **do NOT try to load the entire file into memory** to parse with an HTML parser
- The user will typically also provide a manifold path to the raw media files — use that for video URLs instead of the embedded base64 data
- **Extract structure first from a small sample**: read only the `<th>` headers and first 1-2 rows of non-video `<td>` cells to understand the table columns and metadata format
- **Always parse metadata before proposing schema** — metadata often contains context fields (ad text, transcripts, voice descriptions) and identifiers (voice_id, model_id) that should be shown to the user
- Use regex to extract structured data from the HTML — avoid HTML parsers that would try to parse huge base64 data cells
- If the HTML is very large (>50MB), ask the user if there is a smaller metadata source (JSON, Hive table, or spreadsheet) that contains the same item list

## Large Guidelines Documents (>50K characters)

- Ask the user which sections or dimensions to focus on, rather than reading the entire document
- Search for dimension names (e.g., "Compliance", "Accuracy") with regex to jump to relevant sections
- Ask if there is a shorter summary doc or a specific section/tab to reference
- Extract only the rubric definitions needed for `OptionConfig.description` tooltips
