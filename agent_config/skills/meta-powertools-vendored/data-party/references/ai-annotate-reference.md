---
description: AI-native annotation workflow using Claude subagents to auto-annotate data party items
---

# AI Annotation Reference

## Overview

This workflow uses Claude Code's native capabilities (vision, reasoning, structured output) to automatically annotate data party items. Instead of calling an external API, Claude spawns subagent workers that read media directly via the `Read` tool (images) or analyze videos via the `understand_video` MCP tool, evaluate each item against the annotation dimensions, and return structured JSON annotations.

The primary output is an analysis report (via `/visualize`) showing per-dimension statistics, failure case galleries with embedded media, and cross-dimension patterns. Optionally, annotations can also be injected into the generated HTML as `PRE_ANNOTATIONS`, appearing with visual distinction (amber dashed borders, "AI" badges) for interactive human review.

## Invocation Patterns

Activate this workflow when the user says:
- "Auto-annotate these items"
- "Have Claude evaluate / judge the items"
- "AI annotate this data party"
- "Pre-annotate with AI"
- "Run Claude as annotator on [data party]"
- "AI annotate this PixelCloud URL: [url]" — extract data from HTML (Path B), annotate, and analyze
- "AI label [url]" — same as above, shorthand for the full workflow

## Step-by-Step Workflow

### Step 0: Establish Data Source

Determine the fastest path to get data for AI annotation.

**Path selection rule — prefer Path B whenever possible:**
- If the user provides a **PixelCloud URL or HTML file** (even if a generation script also exists), go directly to **Path B**. Extracting data from the HTML is dramatically faster than re-running a script (no Hive query, no `buck2 build`, no handle resolution — seconds vs. 5-15 minutes). The HTML already contains all the resolved data and config needed.
- Only use **Path A** if: (a) no HTML/PixelCloud URL exists yet (the script hasn't been run), OR (b) the user explicitly asks to re-run the generation script with fresh data.
- Do NOT search for or modify a generation script unless the user explicitly asks to use one.

#### Path A: Generation Script Exists

**Before any AI annotation, the generation script must cache its resolved data.** This prevents a common pitfall where `ORDER BY RANDOM()` or other non-deterministic queries produce different item orderings across runs, causing position-based pre-annotations to mismatch displayed items.

Check the generation script for data caching. If it doesn't already cache, add the cache-first pattern:

```python
def main() -> None:
    data_cache_path = "/tmp/<script_name>_data.json"
    if os.path.exists(data_cache_path):
        with open(data_cache_path) as f:
            data: list[dict[str, Any]] = json.load(f)
        print(f"Loaded {len(data)} items from cache ({data_cache_path})")
    else:
        # All data loading, handle resolution, URL resolution goes here
        ctx = pvc2.context(...)
        df = ctx.sql_to_pandas(...)
        # ... extract fields, resolve handles ...
        data = df.to_dict("records")

        # Save cache AFTER all resolution is complete
        with open(data_cache_path, "w") as f:
            json.dump(data, f, default=str)
        print(f"Saved {len(data)} items to {data_cache_path}")

    # Schema, config, generation code continues here (outside the if/else)
    # ...
```

**Key rules for the cache pattern:**
- The entire data loading block (Hive query, handle resolution, URL resolution) must be inside the `else` branch
- Cache is saved only after ALL resolution is complete (resolved URLs, not raw handles)
- Subsequent runs load from cache and skip the query entirely
- Pre-annotations are loaded AFTER data loading (whether from cache or fresh)
- To force a fresh query, delete the cache file: `rm /tmp/<script_name>_data.json`

**If the script already caches data correctly,** run it once to establish the cache before proceeding to Step 1.

#### Path B: HTML-Only (No Generation Script)

When the user provides a PixelCloud URL or HTML file without a generation script, extract data and config directly from the HTML. Follow the [Retrieve & Regenerate Reference](retrieve-reference.md) Steps 2–3 to download and extract:

1. **Download the HTML** via lookaside URL or local file save (see retrieve-reference.md Step 2). **Important:** Lookaside URLs expire quickly (minutes, not hours). Download once and work with the local copy for all subsequent steps. Do NOT re-download later — the URL will likely return the Facebook login page instead of the HTML.
2. **Extract DATA and CONFIG** from the embedded JavaScript:

```python
python3 << 'PYEOF'
import json, re

with open('/tmp/<name>.html', 'r', encoding='utf-8') as f:
    html = f.read()

data_match = re.search(r"const DATA = (\[.*?\]);\s*\n\s*const CONFIG", html, re.DOTALL)
config_match = re.search(r"const CONFIG = (\{.*?\});\s*\n\s*const TOTAL_ITEMS", html, re.DOTALL)

data = json.loads(data_match.group(1))
config = json.loads(config_match.group(1))

with open('/tmp/<name>_data.json', 'w') as f:
    json.dump(data, f)
with open('/tmp/<name>_config.json', 'w') as f:
    json.dump(config, f, indent=2)

print(f"Extracted {len(data)} items, config partyId={config.get('partyId', 'N/A')}")
PYEOF
```

3. **Identify media URL columns** from `config["media"]["groups"][*]["sources"][*]["key"]` — these are the data column names containing image/video URLs
4. **Identify handle columns** if present — check `config.get("handle_metadata", {})` for `url_key -> handle_key` mappings, or look for columns ending in `_handle` in the data

The extracted `config["schema"]["dimensions"]` provides all dimension definitions, options, and layout needed for the evaluation prompt. No generation script is needed — Steps 1–5 work identically using the extracted data and config.

**Data determinism:** Unlike Path A, HTML-only data is already fixed (no random ordering). The extracted DATA is the source of truth and the item ordering will never change, so position-based pre-annotations are inherently safe.

### Step 1: Read the Config

**Path A (script):** Read the existing data party generation script from `generation/` to extract:

1. **Items** — the data list (or data loading logic)
2. **Dimensions** — from `AnnotationSchema.dimensions` and `variant_groups`
3. **Guidelines** — from dimension descriptions, option descriptions, rubric text, and any `general_guidelines`
4. **Media sources** — which columns contain image/video/audio URLs
5. **Identifiers** — for referencing items in the output

**Path B (HTML-only):** Read from the extracted config JSON (`/tmp/<name>_config.json`):

1. **Items** — from `/tmp/<name>_data.json`
2. **Dimensions** — from `config["schema"]["dimensions"]` (each has `key`, `name`, `description`, `type`, `options`)
3. **Guidelines** — from `config["schema"].get("general_guidelines", [])` and individual dimension/option descriptions
4. **Media sources** — from `config["media"]["groups"][*]["sources"]` (each has `key`, `label`, `type`)
5. **Identifiers** — from `config["identifiers"]`
6. **Dimension layout** — from `config["schema"].get("dimension_layout", [])` (shows how dimensions are grouped in the UI)

### Step 2: Prepare Media

Claude subagents can read images via the `Read` tool and analyze videos via the `understand_video` MCP tool, but both need local file paths. **Always download media in parallel** using `ThreadPoolExecutor` for reliability and speed (handles errors, reports sizes, removes corrupt files, downloads concurrently):

```python
python3 -c "
import json, subprocess, os
from concurrent.futures import ThreadPoolExecutor, as_completed

with open('/tmp/<script_name>_data.json') as f:
    data = json.load(f)

os.makedirs('/tmp/<script_name>_media', exist_ok=True)

def download_one(args):
    idx, key, suffix, url = args
    if not url:
        return f'Item {idx} {suffix}: NO URL'
    out_path = f'/tmp/<script_name>_media/item{idx}_{suffix}.jpg'
    try:
        result = subprocess.run(
            ['curl', '-sL', '-o', out_path, '-w', '%{http_code}', url],
            capture_output=True, text=True, timeout=30
        )
        http_code = result.stdout.strip()
        size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
        if size < 500:
            os.remove(out_path)
            return f'Item {idx} {suffix}: HTTP {http_code}, {size} bytes — REMOVED (too small)'
        return f'Item {idx} {suffix}: HTTP {http_code}, {size} bytes'
    except Exception as e:
        return f'Item {idx} {suffix}: FAILED ({e})'

# Build download tasks
tasks = []
for i in range(len(data)):
    item = data[i]
    idx = i + 1
    for key, suffix in [('image_url', 'image'), ('poster_url', 'poster')]:
        tasks.append((idx, key, suffix, item.get(key, '')))

# Download in parallel (20 workers)
with ThreadPoolExecutor(max_workers=20) as pool:
    futures = {pool.submit(download_one, t): t for t in tasks}
    for f in as_completed(futures):
        print(f.result())
"
```

**Why parallel Python over sequential loops:** Parallel downloads with `ThreadPoolExecutor(max_workers=20)` is dramatically faster for datasets with 50+ items and multiple media files per item. Sequential downloads of 150 files can take 10+ minutes; parallel downloads complete in under a minute. Python also handles HTTP errors, checks file sizes, and removes corrupt downloads (< 500 bytes) in a single pass.

**Always read from the data cache** (`/tmp/<script_name>_data.json`) — never re-query the data source. This ensures the downloaded media matches the exact items that will appear in the generated HTML.

**Supported media:**

| Type | Supported | Method |
|------|-----------|--------|
| Images (URL) | Yes | Download to `/tmp/`, read via `Read` tool |
| Images (local) | Yes | Read directly via `Read` tool |
| Video (URL) | Yes | Download to `/tmp/`, analyze via `understand_video` MCP tool or direct Plugboard API |
| Video (local) | Yes | Analyze directly via `understand_video` MCP tool or direct Plugboard API |
| Video with audio | Yes | Gemini understands both visual and audio tracks. Keep videos under 10 MB to preserve audio (videos >10 MB are auto-compressed with audio stripped). |
| Audio (URL) | Yes | Download to `/tmp/`, evaluate via direct Plugboard API with `audio/mpeg`, `audio/wav`, `audio/ogg`, or `audio/flac` MIME type |
| Audio (local) | Yes | Evaluate directly via Plugboard API. See [Multi-Media Items](#multi-media-items-multiple-videosaudio-per-item) for the pattern. |
| Text | Yes | Inline in prompt |

**Audio note:** The `understand_video` MCP tool does not support standalone audio files. For audio-only items (MP3, WAV, OGG, FLAC), use the **direct Plugboard API** pattern documented in [Multi-Media Items](#multi-media-items-multiple-videosaudio-per-item). Gemini natively understands audio content — pass the audio file as `inlineData` with the appropriate MIME type.

**Video prerequisites:** The `video-analyzer` MCP must be installed (`claude-templates mcp video-analyzer install`). Videos up to ~2 minutes work well. **Skip AI annotation for videos over 10 MB** — compression strips audio, degrading evaluation quality. Flag these items as "requires human" in the results. See [Video Annotation](#video-annotation-via-video-mcp) for details.

**CRITICAL — MCP/Plugboard availability:** Before dispatching any video or audio annotation batches, run the Plugboard connectivity check (see [SSL Workaround](#ssl-workaround-for-od--linux-environments)). **If Plugboard is unreachable, STOP immediately.** Do NOT attempt text-only fallback evaluation for video/audio items — the results will be unreliable and misleading. Report the connectivity failure to the user and ask them to resolve the environment issue before proceeding.

### Step 3: Build Evaluation Prompt

For each batch of items, construct a prompt that includes:

1. **Role and task description**
2. **Annotation guidelines** — extracted from the schema
3. **Dimension definitions** — type, options, rubric
4. **Items to evaluate** — with media file paths and context
5. **Output format** — structured JSON

Include all available context (product name, category, description, prompts) from the cached data. This helps the AI make informed judgments even when some media is missing.

**Save the prompt for review:** Before dispatching subagents, save the fully rendered prompt (with actual dimensions, items, and file paths filled in) to `/tmp/<script_name>_ai_judge_prompt.txt` so the user can review what the AI judge will see. This is especially useful for debugging mismatches or refining guidelines. **Share the file path with the user** — tell them the prompt was saved and where, so they can inspect it: "Saved the AI judge prompt to `/tmp/<script_name>_ai_judge_prompt.txt` — you can review what the AI evaluator will see."

#### Prompt Template (Image Items)

```
You are a STRICT and CRITICAL annotation judge evaluating media items for a data party.
Your task is to evaluate each item against the provided dimensions
and return structured JSON annotations.

## CRITICAL CALIBRATION INSTRUCTIONS

You MUST be a tough, discerning judge. Do NOT default to the highest rating — that should be reserved for truly excellent results. Apply these calibration rules:

- The HIGHEST rating (e.g., "strong_pass", 5/5) = Genuinely impressive, professional quality, no noticeable issues whatsoever.
- The MIDDLE rating (e.g., "weak_pass", 3/5) = Acceptable but not great. Minor issues exist. This should be your DEFAULT for "okay" results.
- The LOWEST rating (e.g., "fail", 1/5) = Clear problems that are immediately noticeable.

MOST AI-generated content will have SOME imperfections. If you notice ANY issues — even minor ones — use the middle rating, not the top rating. Reserve the top rating for results that are genuinely indistinguishable from professional human-created content.

## Annotation Guidelines

{general_guidelines from schema, if any}

## Dimensions

{For each dimension, include:}

### {dimension.name} ({dimension.type})
{dimension.description}

{For PASS_FAIL/OPTIONS:}
Options:
{For each option:}
- **{option.label}** ({option.value}): {option.description}

{For SCALE:}
Scale: 1 to {dimension.scale}
{For each rubric entry:}
- {i}: {rubric_text}

{For TEXT:}
Provide a brief free-text response.

## Items to Evaluate

{For each item in the batch:}

### Item {item_index}
- Context: {key context fields from the data, e.g. name, category, description}
- Media files: {list only files that were successfully downloaded; note any missing}

## Instructions
1. Use the Read tool to view each image file
2. Evaluate each dimension based on what you see
3. Write detailed, specific rationales with concrete observations

## Output Format

Think carefully and critically through each dimension. For EACH rationale, provide SPECIFIC observations — reference what you actually saw/heard (e.g., "the lighting in the top-left is overexposed", "the transition at 0:05 is jarring", "the text overlay is partially obscured"). Do NOT write generic rationales like "looks good" or "acceptable quality".

Return ONLY a JSON object (no markdown code fences, no explanation outside the JSON):

{
  "{item_index}": {
    "{dimension_key}": "{value}",
    "{dimension_key}_rationale": "Detailed, specific observation. What exactly did you see? Reference specific elements, timestamps, or locations. Why this rating and not a higher/lower one?",
    "notes": "[{Dim1 Label}: {rating}] {rationale} | [{Dim2 Label}: {rating}] {rationale} | ..."
  },
  ...
}

Each dimension MUST have a corresponding `{dimension_key}_rationale` field with detailed, specific chain-of-thought reasoning.

The `notes` field MUST aggregate individual rationales in the format: `[{Dimension Label}: {rating}] {rationale} | [...]`. This structured format preserves per-dimension reasoning in a single field. Do NOT write a generic summary — concatenate the individual rationales with dimension labels and ratings.

Values must exactly match the option values defined above.
For PASS_FAIL dimensions, use the exact option value strings (e.g., "pass", "fail", "strong_pass").
For SCALE dimensions, use integers within the defined range.
For OPTIONS dimensions, use the exact option value strings.
For TEXT dimensions, provide a brief text response.

```

#### Prompt Template (Video Items)

For video items, each subagent uses the `understand_video` MCP tool instead of reading images directly. The prompt to `understand_video` must include the same strict calibration instructions:

```
For each video item, call the understand_video MCP tool with:
- video_path: /tmp/dp_ai_videos/item_{index}.mp4
- prompt: (see below)

understand_video prompt:
---
You are a STRICT and CRITICAL annotation judge evaluating this video against specific dimensions.

## CRITICAL CALIBRATION INSTRUCTIONS
You MUST be a tough, discerning judge. The HIGHEST rating is reserved for genuinely impressive, professional quality with no noticeable issues. The MIDDLE rating should be your DEFAULT for "acceptable but not great" results. The LOWEST rating is for clear, immediately noticeable problems.

## Annotation Guidelines
{general_guidelines}

## Dimensions
{dimension definitions as above}

## Context
- Identifiers: {key=value pairs}
- Context: {context field values}

## Output Format
Think carefully and critically through each dimension. For EACH rationale, provide SPECIFIC observations — reference what you actually saw/heard (e.g., timestamps, visual elements, audio details). Do NOT write generic rationales.

Return ONLY a JSON object (no markdown code fences, no explanation outside the JSON):
{
  "{dimension_key}": "{value}",
  "{dimension_key}_rationale": "Detailed, specific observation with timestamps or element references.",
  "notes": "[{Dim1 Label}: {rating}] {rationale} | [{Dim2 Label}: {rating}] {rationale} | ..."
}

The `notes` field MUST aggregate individual rationales in the structured format above. Remember: the middle rating is your DEFAULT.
---

After receiving the understand_video response, parse the JSON from
Gemini's free-text output and add it to the results dict keyed by
the item index (as a string).
```

**Key difference from image workflow:** For images, the subagent reads the image via the `Read` tool and evaluates it directly using Claude's vision. For videos, the subagent delegates to the `understand_video` MCP tool which uses Gemini's native video understanding, then parses Gemini's text response to extract the structured annotation.

**Size check:** Before calling `understand_video`, check the file size. Skip videos over 10 MB — report them as "skipped (file too large, requires human)" in the results. This ensures audio is preserved for all AI-annotated videos.

### Step 3.5: Select Sample Size

**Before dispatching, ask the user how many items they want AI to label.** Use `AskUserQuestion` with 3-4 options based on the total item count, each showing the estimated completion time:

```
Proposed options (adapt to actual item count N):
- "10 items (~2 min)" — quick calibration check
- "50 items (~5 min)" — representative sample
- "All {N} items (~{T} min)" — full evaluation
```

**Time estimation heuristic (parallel dispatch with ~10 workers):**

| Items | Image-only | Video (parallel Plugboard) | Mixed |
|-------|-----------|---------------------------|-------|
| 10    | ~1-2 min  | ~2-3 min                  | ~2-3 min |
| 50    | ~3-5 min  | ~5-8 min                  | ~5-8 min |
| 100   | ~5-8 min  | ~8-12 min                 | ~8-15 min |
| 500   | ~15-20 min| ~25-40 min                | ~25-40 min |

These estimates assume parallel dispatch using `ThreadPoolExecutor` with 10 workers. Actual times depend on media file size, network speed, and Plugboard/Claude response times. Video items take longer due to base64 encoding overhead and Gemini processing time.

**After the user selects a sample size**, slice the data: take the first N items from the cached data (preserving deterministic order) and proceed to Step 4 with only those items.

### Step 4: Batch and Dispatch

**CRITICAL — Subagent MCP Limitation:** Task tool subagents (launched via `Task` with `subagent_type="general-purpose"`) are **denied permission to use MCP tools** like `understand_video`, `watch_video`, and often `Bash`. This means subagents CANNOT directly evaluate video or audio content. For video/audio annotation, use the **direct Plugboard API batch runner** described below instead of Task subagents.

#### Step 4a: Plugboard Connectivity Check (Video/Audio Only)

**Before dispatching any video or audio annotation**, verify Plugboard connectivity. Run this check ONCE at the start — do not skip it:

```bash
python3 -c "
import ssl, urllib.request
ctx = ssl.create_default_context(cafile='/etc/pki/tls/certs/fb_certs.pem')
ctx.load_cert_chain('/var/facebook/tupperware/tls/x509_identities/client.pem')
handler = urllib.request.HTTPSHandler(context=ctx)
opener = urllib.request.build_opener(handler)
req = urllib.request.Request('https://plugboard.x2p.facebook.net/', headers={'Content-Type': 'application/json'})
try:
    opener.open(req, timeout=10)
except urllib.error.HTTPError as e:
    print(f'Connected (HTTP {e.code}) — Plugboard is reachable')
except Exception as e:
    print(f'FAILED: {e}')
    import sys; sys.exit(1)
"
```

A `Connected (HTTP 404)` response confirms Plugboard is reachable and mTLS is working. **If this fails, STOP immediately** — do NOT fall back to text-only evaluation. Report the error to the user.

#### Step 4b: Choose Dispatch Method

| Media Type | Method | Why |
|-----------|--------|-----|
| **Image-only** | Task subagents (Claude vision via `Read` tool) | Subagents can read images natively — no MCP needed |
| **Video, audio, or multi-media** | Direct Plugboard API batch runner script | Subagents cannot use MCP tools; direct API with mTLS is the reliable path |
| **Mixed (some images, some videos)** | Separate: subagents for images, batch runner for videos | Use the right tool for each media type |

#### Method 1: Task Subagents (Image-Only Items)

Split items into batches and use the `Task` tool with `subagent_type="general-purpose"`.

```
Batch sizing guidelines:
- 5 items per batch: when each item has 1-2 images and few dimensions
- 2-3 items per batch: when each item has multiple images (3+) or many dimensions (5+)
- 10 items per batch: when items are text-only or single image with 1-2 dimensions
```

Launch **all batches in parallel** using concurrent `Task` tool calls in a single message:

```python
# Pseudocode — ALL calls in a single message for parallel execution
Task(subagent_type="general-purpose", prompt=build_prompt(items[0:5], dims), description="AI annotate items 1-5")
Task(subagent_type="general-purpose", prompt=build_prompt(items[5:10], dims), description="AI annotate items 6-10")
# ... etc
```

Each subagent reads images via the `Read` tool (supports PNG, JPG, etc.), evaluates each item, and returns a JSON object.

#### Method 2: Direct Plugboard API Batch Runner (Video/Audio Items — Primary Method)

For video/audio annotation, create a two-script pattern: a **per-item evaluator** and a **parallel batch runner**. This is the primary and most reliable method for video/audio on OD/Linux environments.

**Per-item evaluator script** — save to `/tmp/<name>_ai_eval.py`:

```python
#!/usr/bin/env python3
"""Evaluate a single item via Plugboard/Gemini API."""
import ssl, urllib.request, json, base64, sys, os, re
from pathlib import Path

item_idx = int(sys.argv[1])  # 1-based index

with open('/tmp/{DATA_PATH}') as f:
    data = json.load(f)
item = data[item_idx - 1]

# Build media parts — adapt file paths and MIME types to your data
parts = []
media_files = [
    # (local_path, mime_type, label)
    (f'/tmp/{MEDIA_DIR}/item{item_idx:06d}_image.png', 'image/png', 'Conditioning Image'),
    (f'/tmp/{MEDIA_DIR}/item{item_idx:06d}_video.mp4', 'video/mp4', 'Generated Video'),
]

for fpath, mime, label in media_files:
    if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
        b64 = base64.b64encode(Path(fpath).read_bytes()).decode("utf-8")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        parts.append({"text": f"[Above: {label}]"})

# Add evaluation prompt — fill in {PROMPT} with the actual prompt from Step 3
parts.append({"text": """{PROMPT}"""})

# Call Gemini via Plugboard with mTLS
model = os.environ.get("GEMINI_MODEL", "gemini-3-flash")
url = f"https://plugboard.x2p.facebook.net/v1beta/models/{model}:generateContent"
body = {"contents": [{"role": "user", "parts": parts}]}

ctx = ssl.create_default_context(cafile="/etc/pki/tls/certs/fb_certs.pem")
ctx.load_cert_chain("/var/facebook/tupperware/tls/x509_identities/client.pem")
handler = urllib.request.HTTPSHandler(context=ctx)
opener = urllib.request.build_opener(handler)
headers = {"Content-Type": "application/json", "x-goog-api-key": "sk-plugboard-dummy-1234567890"}
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")

with opener.open(req, timeout=180) as resp:
    result = json.loads(resp.read().decode())

# Parse JSON from Gemini response — strip markdown fences and handle nested objects
response_parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
response_text = "\n".join(p["text"] for p in response_parts if "text" in p)
response_text = re.sub(r'```json\s*', '', response_text)
response_text = re.sub(r'```\s*$', '', response_text)
json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
if json_match:
    print(json.dumps({str(item_idx): json.loads(json_match.group())}))
else:
    print(f"ERROR: Could not parse JSON from response for item {item_idx}", file=sys.stderr)
    sys.exit(1)
```

**Parallel batch runner** — save to `/tmp/<name>_batch_runner.py`:

```python
#!/usr/bin/env python3
"""Run AI evaluation in parallel using ThreadPoolExecutor."""
import subprocess, json, sys, os
from concurrent.futures import ThreadPoolExecutor, as_completed

EVAL_SCRIPT = "/tmp/{NAME}_ai_eval.py"
TOTAL_ITEMS = {N}  # Set to selected sample size from Step 3.5
MAX_WORKERS = 10

def evaluate_item(idx):
    """Run the per-item eval script as a subprocess."""
    result = subprocess.run(
        ["python3", EVAL_SCRIPT, str(idx)],
        capture_output=True, text=True, timeout=300
    )
    if result.returncode != 0:
        return idx, None, result.stderr.strip()
    try:
        return idx, json.loads(result.stdout), None
    except json.JSONDecodeError:
        return idx, None, f"JSON parse error: {result.stdout[:200]}"

# Dispatch all items in parallel
all_results = {}
errors = []

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
    futures = {pool.submit(evaluate_item, i): i for i in range(1, TOTAL_ITEMS + 1)}
    done_count = 0
    for future in as_completed(futures):
        idx, result, error = future.result()
        done_count += 1
        if error:
            errors.append(f"Item {idx}: {error}")
            print(f"  [{done_count}/{TOTAL_ITEMS}] Item {idx}: FAILED — {error}", file=sys.stderr)
        else:
            all_results.update(result)
            print(f"  [{done_count}/{TOTAL_ITEMS}] Item {idx}: OK")

# Save results
with open('/tmp/{NAME}_raw_results.json', 'w') as f:
    json.dump(all_results, f, indent=2)
print(f"\nCompleted: {len(all_results)}/{TOTAL_ITEMS} items")
if errors:
    print(f"Errors: {len(errors)}")
    for e in errors:
        print(f"  {e}")
```

**Run the batch runner:**
```bash
python3 /tmp/<name>_batch_runner.py
```

**ETA reporting:** Before running, tell the user: "Dispatching {N} items with {workers} parallel workers. Estimated completion: ~{T} minutes." Use the time estimates from Step 3.5.

### Step 5: Collect, Condense, and Save

After all subagents return, collect their JSON results. Each annotation includes `*_rationale` fields with chain-of-thought reasoning and a `notes` field that aggregates the rationales. **Strip the rationale fields** before saving, but **build structured notes** from them — the UI only displays dimension values and `notes`:

```python
# Pseudocode for the orchestrator
pre_annotations = {}
dim_labels = {
    "dim_key_1": "Dimension 1 Label",
    "dim_key_2": "Dimension 2 Label",
    # ... map each dimension key to its display label
}

for batch_result in subagent_results:
    batch_anns = json.loads(batch_result)
    for key, ann in batch_anns.items():
        # Build structured notes from individual rationales
        rationale_parts = []
        for dim_key, dim_label in dim_labels.items():
            rating = ann.get(dim_key, "N/A")
            rationale = ann.get(f"{dim_key}_rationale", "")
            if rationale:
                rationale_parts.append(f"[{dim_label}: {rating}] {rationale}")
        aggregated_notes = " | ".join(rationale_parts)

        # Strip rationale fields — UI only shows dimension values + notes
        cleaned = {k: v for k, v in ann.items() if not k.endswith("_rationale")}
        cleaned["notes"] = aggregated_notes
        pre_annotations[key] = cleaned
```

Save the merged pre-annotations to `/tmp/<script_name>_pre_annotations.json`:

```bash
cat > /tmp/<script_name>_pre_annotations.json << 'ENDJSON'
{
  "1": {"dim1": "good", "dim2": "acceptable", "notes": "[Dim 1: good] Specific observation... | [Dim 2: acceptable] Specific observation..."},
  "2": {"dim1": "good", "dim2": "good", "notes": "[Dim 1: good] Specific observation... | [Dim 2: good] Specific observation..."}
}
ENDJSON
```

**Note on structured notes format:** The `notes` field uses the format `[{Dimension Label}: {rating}] {rationale} | [...]` to preserve per-dimension reasoning in a single field. This is far more useful than a generic summary because annotators can see exactly why the AI chose each rating. The `*_rationale` fields are stripped (they improve AI reasoning quality but are not displayed in the UI).

The keys must be **string representations of 1-based item indices** (matching `cardIndex` in the UI).

### Step 5b: Structured Validation (Calibration Check)

**After collecting all results, automatically validate the distribution before proceeding.** This catches miscalibrated prompts early — before the results are injected into the data party.

```python
# Compute per-dimension distribution
from collections import Counter

dims = [d["key"] for d in schema_dimensions]
total = len(pre_annotations)

print(f"\n=== AI Annotation Validation ({total} items) ===")
miscalibrated = False

for dim_key in dims:
    counts = Counter(ann.get(dim_key, "N/A") for ann in pre_annotations.values())
    print(f"\n{dim_key}:")
    for value, count in counts.most_common():
        pct = count / total * 100
        marker = " ⚠️" if pct > 70 else ""
        print(f"  {value}: {count}/{total} ({pct:.0f}%){marker}")
        if pct > 70 and value not in ("pass",):  # "pass" on compliance dims is expected
            miscalibrated = True

if miscalibrated:
    print("\n⚠️  WARNING: One or more dimensions have >70% of items rated the same value.")
    print("   This may indicate a miscalibrated prompt (too lenient or too strict).")
    print("   Consider re-running with adjusted calibration instructions.")
```

**Calibration red flags (auto-detected):**
- Any non-compliance dimension with >70% of items rated the highest value → likely too lenient
- Any dimension with >70% of items rated the lowest value → likely too strict
- Zero variance (100% same rating) on any dimension → prompt issue or dimension too broad

**If miscalibration is detected:** Report the distribution to the user and ask whether to proceed or re-run with adjusted calibration. Do NOT silently proceed — miscalibrated annotations are worse than no annotations because they mislead human reviewers.

### Step 6: Generate Analysis Report

**After validation passes (or the user confirms to proceed despite warnings), automatically generate an analysis report.** Do not ask the user whether to analyze — proceed directly. The analysis report is the primary output of the AI annotation workflow: it works regardless of whether the HTML supports `PRE_ANNOTATIONS` injection, and it provides the most actionable view of the AI judge's calibration and findings.

Follow the [Analyze Results Reference](analyze-reference.md) workflow:

1. **Compute per-dimension statistics** — pass rates, distribution of each rating tier, failure counts
2. **Identify failure cases** — which items failed on which dimensions, with rationales from the structured notes
3. **Cross-dimension correlation** — items with 3+ non-top-tier dimensions, common weakness patterns
4. **Generate report** — invoke `/visualize` with the `experiment-report` archetype, including:
   - Executive summary with 3-5 high-level key points (e.g., overall pass rate, worst-performing dimension, number of critical failures, most common failure pattern, notable outliers)
   - Per-dimension results table with distribution bars/charts
   - **Tabbed gallery** organized by result tier — use tabs for "Failures" (show ALL), "Weak Passes" (show a representative sample), and "Strong Passes" (show a representative sample), each with embedded media (using CDN URLs — see below)
   - Cross-dimension pattern analysis
   - Methodology section linking to the data party PixelCloud URL and annotation guidelines
5. **Upload to PixelCloud** and report the URL alongside the data party URL

**PixelCloud CSP Restriction:** PixelCloud renders HTML inside an iframe with a Content Security Policy that **blocks CDN-loaded scripts** (e.g., Chart.js, D3.js, Mermaid). If you use `<script src="https://cdn.jsdelivr.net/...">`, the script will be silently blocked and charts will render as blank canvases. **Always use pure CSS/SVG charts** in analysis reports uploaded to PixelCloud:
- **Donut charts**: Use CSS `conic-gradient` on a circular div
- **Bar charts**: Use CSS `width` percentages on `div` elements inside a track
- **Distribution bars**: Use inline `style="width: {pct}%"` with colored div fills
- **Never use `<canvas>` elements** — they require JavaScript charting libraries

**The analysis report should reference the data party URL** so readers can navigate between the report and the interactive annotation UI.

#### Preparing Media URLs for the Report

The analysis report is uploaded to PixelCloud — it needs **original CDN URLs** from the extracted data, NOT the downloaded local file paths from Step 2.

**Build a gallery items list** by joining pre-annotations with the extracted DATA:

```python
# Load the extracted data and pre-annotations
with open('/tmp/<name>_data.json') as f:
    data = json.load(f)
with open('/tmp/<name>_pre_annotations.json') as f:
    pre_annotations = json.load(f)

# Build gallery items with CDN URLs from the original data
gallery_items = []
for idx_str, ann in pre_annotations.items():
    idx = int(idx_str) - 1  # Convert 1-based to 0-based
    item = data[idx]
    gallery_items.append({
        "index": idx_str,
        "risk_level": ann.get("risk_level", ""),
        "notes": ann.get("notes", ""),
        # Use the ACTUAL URLs from the data — these are the CDN URLs
        "image_url": item.get("image_url", ""),  # e.g. https://interncache-.../_cond.png
        "video_url": item.get("video_url", ""),  # e.g. https://interncache-.../.mp4
        # Include context fields for display
        "context": {k: item.get(k, "") for k in context_field_keys},
    })
```

**CRITICAL — Always use extracted URLs, never construct manually:** Read the actual URL values from `data[idx]` — do NOT construct URLs by guessing filename patterns. The path structure varies across data parties (e.g., `ad_id/bundle_video.mp4` vs `bundle_video/ad_id.mp4`), and filenames in the data (e.g., `000000_cond.png`) may differ from what you'd guess (e.g., `000000_image.jpg`). Pull URLs directly from the resolved fields in the extracted DATA JSON.

**Verification step:** Before uploading the report, test that at least one media URL loads correctly. Use `curl -sI <url> | head -1` to check for HTTP 200. If URLs return 403/404, the CDN URLs may have expired — suggest running `/data-party refresh` first.

#### Embedding Media in the Report

When invoking `/visualize`, embed media from the `gallery_items` structure above using the CDN URLs:

- **Images**: Use `<img src="{item['image_url']}" />` with the CDN URL from the data
- **Videos**: Use `<video src="{item['video_url']}" controls />` with the CDN URL from the data
- **Never use local file paths** (e.g., `/tmp/.../item1_image.jpg`) — these won't resolve in PixelCloud

Organize gallery items into **tabs by result tier**: a "Failures" tab (show ALL failures), a "Weak Passes" tab (representative sample), and a "Strong Passes" tab (representative sample). This lets reviewers focus on the tier they care about without scrolling past items they've already triaged.

**Data source for analysis:** Use the pre-annotations JSON (`/tmp/<name>_pre_annotations.json`) and cached data (`/tmp/<name>_data.json`) directly — no need to load from a spreadsheet since all results are already in memory.

### Step 7: Report Results

Report to the user:
- **Analysis report URL** (primary output) — the PixelCloud URL from Step 6
- Total items annotated by AI
- Items skipped (missing media, corrupted files)
- Per-dimension distribution table from Step 5b validation (with calibration warnings if any)
- Pre-annotations JSON saved at `/tmp/<name>_pre_annotations.json`
- AI judge prompt saved at `/tmp/<name>_ai_judge_prompt.txt` — for reviewing what the evaluator saw
- Reminder that human review is recommended
- **Path A:** Note that cached data is at `/tmp/<script_name>_data.json` — delete to force fresh query
- **Path B:** Note the original data party URL for reference

### Step 8 (Optional): Inject into Data Party HTML

**This step is optional.** After presenting the analysis report, ask the user:

> "Would you also like to inject these annotations into the data party HTML for interactive review? This adds AI pre-annotations with visual badges that human annotators can review and override."

**Only proceed with injection if the user confirms.** Some data party HTMLs predate `PRE_ANNOTATIONS` support, in which case injection will fail or produce a broken page. The analysis report from Step 6 is the universally useful output.

If the user confirms, proceed with the appropriate path:

#### Path A: Generation Script Exists

The generation script should already have pre-annotation loading code behind a `--pre-annotate` flag. If not, add an `argparse` flag and guarded loading after the data loading section:

```python
import argparse

parser = argparse.ArgumentParser()
parser.add_argument(
    "--pre-annotate",
    action="store_true",
    help="Load AI pre-annotations from cache if available",
)
args = parser.parse_args()

# Load AI pre-annotations only when --pre-annotate flag is specified
pre_annotations_path = "/tmp/<script_name>_pre_annotations.json"
pre_annotations = None
if args.pre_annotate and os.path.exists(pre_annotations_path):
    with open(pre_annotations_path) as f:
        pre_annotations = json.load(f)
    print(f"Loaded {len(pre_annotations)} AI pre-annotations")

# Generate HTML
generator = DataPartyGenerator(config, pre_annotations=pre_annotations)
result = generator.generate(data)
```

Then rebuild and run with the flag:

```bash
buck2 run fbcode//tools/data_party_tool/generation:<script_name> -- --pre-annotate
```

**Important:** Without `--pre-annotate`, the script generates a clean data party even if the pre-annotations file exists. This prevents accidentally shipping AI annotations when only a fresh human-annotation page is needed.

The generated HTML will have AI annotations pre-filled with visual distinction. Because the data loads from cache, the item ordering is guaranteed to match the pre-annotations.

#### Path B: HTML-Only (Replace PRE_ANNOTATIONS in Original HTML)

When no generation script exists, replace the empty `PRE_ANNOTATIONS` in the original HTML with actual data. The data party template **already includes** full PRE_ANNOTATIONS support (`const PRE_ANNOTATIONS`, `_aiAnnotated`, `_loadPreAnnotations()`, AI badge rendering). You only need to swap the empty object with your data.

**CRITICAL: Do NOT inject duplicate JavaScript.** The template already contains `const PRE_ANNOTATIONS = {};`, `let _aiAnnotated = {};`, `function _loadPreAnnotations()`, and all AI badge rendering code. Injecting these again causes duplicate `const` declarations → JavaScript crash → blank page.

```python
python3 << 'PYEOF'
import json

with open('/tmp/<name>.html', 'r') as f:
    html = f.read()
with open('/tmp/<name>_pre_annotations.json', 'r') as f:
    pre_annotations = json.load(f)

pre_ann_json = json.dumps(pre_annotations)

# Replace the empty PRE_ANNOTATIONS with actual data
old = 'const PRE_ANNOTATIONS = {};'
new = 'const PRE_ANNOTATIONS = ' + pre_ann_json + ';'

count = html.count(old)
if count != 1:
    print(f"WARNING: Expected 1 occurrence of empty PRE_ANNOTATIONS, found {count}")
    # If 0, the HTML may predate PRE_ANNOTATIONS support or use a different format — check manually
    # If >1, something is already wrong with the HTML
else:
    html = html.replace(old, new)
    with open('/tmp/<name>.html', 'w') as f:
        f.write(html)
    print(f"Replaced PRE_ANNOTATIONS with {len(pre_annotations)} items ({len(html):,} bytes)")
PYEOF
```

This single replacement is all that's needed. The template's existing `_loadPreAnnotations()` function automatically processes the data on page load, populates annotation controls, and applies AI styling (amber dashed borders, "AI" badges).

**After replacement, re-upload to PixelCloud:**

Use the refresh CLI with `--reupload`. If CDN URLs are expired (Everstore/OIL handles), the tool will also refresh them. For Manifold interncache URLs, no refresh is needed but the `--reupload` flag still works:

```bash
buck2 run fbcode//tools/data_party_tool:refresh_urls -- \
  /tmp/<name>.html --reupload
```

See [refresh-reference.md](refresh-reference.md) for full details on handle mapping and video ID resolution.

**After uploading, report the new PixelCloud URL.** For Path B, offer to post a comment on the original data party linking to the new annotated version (see [refresh-reference.md](refresh-reference.md) Steps 5–6).

## Data Determinism (Cache-First Pattern)

**This is the most important pattern for AI annotation with generation scripts (Path A).** Pre-annotations are keyed by position index ("1", "2", ...), so the data ordering must be identical between the AI evaluation run and every subsequent HTML generation run.

**For HTML-only annotation (Path B), determinism is inherent** — the DATA is extracted from a fixed HTML file and never changes, so pre-annotations always match.

### The Problem (Path A Only)

Many data party scripts use `ORDER BY RANDOM()` or other non-deterministic queries. Each `buck2 run` produces items in a different order, but the pre-annotations JSON still maps index "1" to the first item. If the first item changes between runs, the annotation for "woman in pink saree" might appear on a "phone case" item.

### The Solution

Always use the cache-first pattern (see [Step 0](#step-0-establish-data-source)):

```
Path A (script):
  First run (no cache):  Hive query → resolve handles → save cache → generate HTML
  AI annotation:         Read from cache → download media → evaluate → save pre_annotations
  Subsequent runs:       Load from cache → load pre_annotations → generate HTML (deterministic)

Path B (HTML-only):
  Extract:               Download HTML → extract DATA + CONFIG → save to /tmp/
  AI annotation:         Read from extracted data → download media → evaluate → save pre_annotations
  Inject:                Inject pre_annotations into original HTML → refresh CDN URLs → re-upload
```

### Re-running with Fresh Data

**Path A:** To get fresh data (new random sample, updated date range):
1. Delete the cache: `rm /tmp/<script_name>_data.json`
2. Delete stale pre-annotations: `rm /tmp/<script_name>_pre_annotations.json`
3. Run the script — it will query fresh data and save a new cache
4. Re-run AI annotation if needed

**Path B:** To annotate a different version of the data party, re-download the updated HTML and re-extract DATA and CONFIG. Delete the old pre-annotations before re-running AI annotation.

**Never delete just the cache without also deleting pre-annotations** — this guarantees a mismatch.

## Pre-Annotation Data Format

The `pre_annotations` dict maps string item indices (1-based) to dimension values:

```python
pre_annotations = {
    "1": {
        "safety": "pass",
        "quality": 4,
        "category": "nature",
        "notes": "Clear, high-quality image with no safety concerns"
    },
    "2": {
        "safety": "fail",
        "quality": 2,
        "category": "urban",
        "notes": "Contains inappropriate content in background"
    }
}
```

**Key rules:**
- Keys are **strings** of 1-based indices (matching `cardIndex`)
- Dimension values must match the exact option values from the schema
- Scale values must be integers
- `notes` key is optional — maps to the "Additional Notes" field. Should aggregate the chain-of-thought rationales into a concise summary
- `*_rationale` fields should be **stripped** before saving — they improve AI reasoning quality but are not displayed in the UI
- Variant group dimensions use expanded keys: `"{dim_key}_{variant_id}"`

## UI Treatment

When `PRE_ANNOTATIONS` are present in the HTML:

| Element | AI Styling | Human Override |
|---------|-----------|----------------|
| Option buttons | Dashed border + "AI" micro-badge | Click any button — AI styling removed |
| Rating buttons | Dashed border + "AI" micro-badge | Click any button — AI styling removed |
| Dropdowns | Dashed amber border | Change selection — AI styling removed |
| Text inputs | Dashed amber border + amber background | Type anything — AI styling removed |
| Notes field | Amber gradient background + "AI" badge | Edit text — AI styling removed |
| Section header | "AI Pre-annotated" badge | N/A |

- **Annotator name** should reflect the AI model used. When Gemini is the evaluator (video/audio via Plugboard), set the annotator name to `gemini-{version}` (e.g., `gemini-3-flash`). When Claude evaluates directly (images via Read tool), use `claude-ai`. The annotator name is set in the HTML via the pre-annotations — to override the default, the generation script or HTML injection should set the annotator name input field's default value to match the model used
- **Clear All** reloads pre-annotations as defaults (since they're baked into the HTML)
- **localStorage** takes priority — if a human has already annotated an item, AI values don't overwrite

## Edge Cases

### Items without images
Include all available text context (identifiers, context fields) in the prompt. Claude can still evaluate text-based dimensions.

### Corrupted or missing media
Some media URLs return 404 or tiny error pages. The Python download script removes files < 500 bytes. Subagents should note "base media unavailable" and evaluate only the available images. This is common with manifold URLs that have expired or Everstore handles for deleted content.

### Large datasets (100+ items)
- Use larger batch sizes (10 items)
- Launch batches in waves to avoid overwhelming context
- Consider annotating a representative sample first (e.g., first 20 items)

### Ambiguous dimensions
If a dimension is subjective or lacks clear rubric, instruct Claude to be conservative and note uncertainty in the `notes` field.

### Variant groups
For variant groups, the subagent prompt should include all variant media side by side and use expanded dimension keys:

```json
{
  "1": {
    "quality_original": 4,
    "quality_generated": 3,
    "preference_original": "preferred",
    "preference_generated": "not_preferred"
  }
}
```

## Video/Audio Annotation via Plugboard API (Primary Method)

Video and audio annotation uses Gemini via the Plugboard API with mTLS authentication. This is the **primary and most reliable method** for video/audio evaluation on OD/Linux environments.

**Why Plugboard API over the `understand_video` MCP tool:**
- The `understand_video` MCP tool fails on OD/Linux with SSL errors (Meta's internal CAs are not in the default SSL context)
- Task subagents are denied permission to use MCP tools (`understand_video`, `watch_video`, `Bash`), making subagent-based video annotation impossible via MCP
- The direct Plugboard API with explicit mTLS context works reliably and supports multi-media items (video + image + audio in a single request)

**How it works:**
1. **Download media** to `/tmp/<name>_media/` (parallel download via `ThreadPoolExecutor` — see Step 2)
2. **Create per-item evaluator script** that base64-encodes media and calls Plugboard directly (see Step 4, Method 2)
3. **Run parallel batch runner** with `ThreadPoolExecutor(max_workers=10)` to evaluate all items concurrently
4. **Parse JSON** from Gemini's response and collect into pre-annotations dict

### Plugboard API Details

The Plugboard API requires mTLS with:
1. **CA bundle** at `/etc/pki/tls/certs/fb_certs.pem` (Meta's internal CA certificates)
2. **Client certificate** at `/var/facebook/tupperware/tls/x509_identities/client.pem` (Tupperware service identity)

```python
# Core Plugboard call pattern
ctx = ssl.create_default_context(cafile="/etc/pki/tls/certs/fb_certs.pem")
ctx.load_cert_chain("/var/facebook/tupperware/tls/x509_identities/client.pem")
handler = urllib.request.HTTPSHandler(context=ctx)
opener = urllib.request.build_opener(handler)

model = "gemini-3-flash"
url = f"https://plugboard.x2p.facebook.net/v1beta/models/{model}:generateContent"
body = {"contents": [{"role": "user", "parts": parts}]}
headers = {"Content-Type": "application/json", "x-goog-api-key": "sk-plugboard-dummy-1234567890"}
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")

with opener.open(req, timeout=180) as resp:
    result = json.loads(resp.read().decode())
```

### Connectivity Check

**Always run before dispatching video/audio batches** (see Step 4a). A `Connected (HTTP 404)` confirms Plugboard is reachable. If it fails, STOP — do not fall back to text-only evaluation.

### Video Constraints

| Constraint | Value | Notes |
|-----------|-------|-------|
| Max file size | **10 MB** | Skip AI annotation for larger videos — flag as "requires human" |
| Audio preservation | <10 MB | Videos under 10 MB are sent as-is — audio track preserved, Gemini understands both |
| Recommended duration | Up to ~2 min | Most <2 min videos at reasonable quality stay under 10 MB |
| Supported formats | MP4, MOV, AVI, WebM, MKV, M4V | Standard video formats |
| Processing time | ~10-30s per video | Depends on duration and model |

### Batch Sizing for Video

Video annotation is slower than image annotation. Adjust batch sizes:

| Scenario | Batch Size | Rationale |
|----------|-----------|-----------|
| Short videos (<30s), few dimensions | 5 items | Moderate per-item cost |
| Long videos (>1 min), many dimensions | 2-3 items | High per-item cost |
| Video + audio per item (multi-modal) | 3-5 items | Large base64 payloads; use Plugboard API directly |
| Audio-only items | 5 items | Smaller payloads than video; use Plugboard API |
| Mixed image + video | Separate batches | Different evaluation methods |

### Why the `understand_video` MCP Tool Fails on OD/Linux

The `understand_video` MCP tool fails on OnDemand (OD) instances and most Linux devservers with:

```
Error: Cannot connect to Plugboard: <urlopen error EOF occurred in violation of protocol (_ssl.c:2427)>
```

**Root cause:** The video-analyzer MCP server's Linux code path creates a plain `urllib` opener without configuring an SSL context. Plugboard requires mTLS with Meta's internal CA bundle (`/etc/pki/tls/certs/fb_certs.pem`) and client certificate (`/var/facebook/tupperware/tls/x509_identities/client.pem`). Python's default SSL context uses `/etc/pki/tls/cert.pem` which doesn't include Meta's internal CAs, causing the handshake to fail.

Additionally, **Task subagents are denied permission to use MCP tools** like `understand_video` and `Bash`, making subagent-based video annotation via MCP impossible even if the SSL issue were fixed.

**Solution:** Use the direct Plugboard API with explicit mTLS context (see Step 4, Method 2). The per-item evaluator + batch runner pattern is the primary and recommended approach for all video/audio annotation.

### Model Selection

**Always use `gemini-3-flash` for Gemini-based annotation.** This model is reliable, fast, and produces well-calibrated ratings with detailed rationales. Preview models (e.g., `gemini-3-pro-preview`) are unstable and frequently return HTTP 500 errors even at low concurrency — avoid them unless the user explicitly insists, and warn about instability. If >30% of items fail with a model, suggest falling back to `gemini-3-flash`.

**Default to a single model — prefer Gemini when video/audio is involved.** When any dimension requires video or audio analysis, use Gemini for ALL dimensions in a single API call per item. Do NOT split evaluation across Claude (text dimensions) and Gemini (video dimensions) by default. Using a single model is simpler, avoids result-merging complexity, and gives the model full context across all dimensions. Only split models if the user explicitly requests it or there's a clear cost optimization reason.

To override the model, set the `GEMINI_MODEL` environment variable:

```bash
GEMINI_MODEL=gemini-3-flash claude  # default
```

### Multi-Media Items (Multiple Videos/Audio per Item)

When each item has **multiple media files** (e.g., original video + mixed video + audio track), the `understand_video` MCP tool is insufficient because it only handles one file per call. Instead, use the **direct Plugboard API** which supports multiple `inlineData` parts in a single request.

**Pattern:** Write a reusable Python script to `/tmp/` that handles one item at a time:

```python
# /tmp/<script_name>_ai_eval_item.py
import ssl, urllib.request, json, base64, sys, os
from pathlib import Path

item_idx = int(sys.argv[1])  # 1-based

with open('/tmp/<script_name>_data.json') as f:
    data = json.load(f)
item = data[item_idx - 1]

# Build media parts — add ALL media files for this item
parts = []
media_files = [
    (f'/tmp/<script_name>_media/item{item_idx}_original.mp4', 'video/mp4', 'Original Video'),
    (f'/tmp/<script_name>_media/item{item_idx}_mixed.mp4', 'video/mp4', 'Mixed Video'),
    (f'/tmp/<script_name>_media/item{item_idx}_music.wav', 'audio/wav', 'Music Track'),
]

for fpath, mime, label in media_files:
    if os.path.exists(fpath) and os.path.getsize(fpath) > 500:
        b64 = base64.b64encode(Path(fpath).read_bytes()).decode("utf-8")
        parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        parts.append({"text": f"[Above: {label}]"})

parts.append({"text": "<evaluation prompt with dimensions, context, output format>"})

# Call Gemini via Plugboard
model = os.environ.get("GEMINI_MODEL", "gemini-3-flash")
url = f"https://plugboard.x2p.facebook.net/v1beta/models/{model}:generateContent"
body = {"contents": [{"role": "user", "parts": parts}]}

ctx = ssl.create_default_context(cafile="/etc/pki/tls/certs/fb_certs.pem")
ctx.load_cert_chain("/var/facebook/tupperware/tls/x509_identities/client.pem")
handler = urllib.request.HTTPSHandler(context=ctx)
opener = urllib.request.build_opener(handler)
headers = {"Content-Type": "application/json", "x-goog-api-key": "sk-plugboard-dummy-1234567890"}
req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")

with opener.open(req, timeout=180) as resp:
    result = json.loads(resp.read().decode())

# Parse and output JSON — strip markdown fences and handle nested objects
import re
response_parts = result.get("candidates", [{}])[0].get("content", {}).get("parts", [])
response_text = "\n".join(p["text"] for p in response_parts if "text" in p)
response_text = re.sub(r'```json\s*', '', response_text)
response_text = re.sub(r'```\s*$', '', response_text)
json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
if json_match:
    print(json.dumps({str(item_idx): json.loads(json_match.group())}))
else:
    print(f"ERROR: Could not parse JSON from response", file=sys.stderr)
    sys.exit(1)
```

Then dispatch **one subagent per item** (all in parallel), each running `python3 /tmp/<script_name>_ai_eval_item.py <item_index>`. This gives Gemini all media context in a single call, producing much better evaluations than separate per-file calls.

**Supported MIME types for Plugboard/Gemini:**
- Video: `video/mp4`, `video/quicktime`, `video/webm`, `video/x-msvideo`
- Audio: `audio/wav`, `audio/mpeg`, `audio/ogg`, `audio/flac`
- Image: `image/png`, `image/jpeg`, `image/gif`, `image/webp`

### Mixed Media Data Parties

When a data party has both images and videos:

1. **Separate items by media type** during batching
2. **Image batches** → subagents use `Read` tool (Claude vision)
3. **Video batches** → subagents use `understand_video` MCP tool (Gemini vision)
4. **Multi-media items** → use the direct Plugboard API (see above)
5. **Merge all results** into a single `pre_annotations` dict

The orchestrator should detect media type from the `MediaConfig` sources (check `MediaType.VIDEO` vs `MediaType.IMAGE`) and route each item to the appropriate evaluation method.

## Notes

- **No external API needed for images** — uses Claude Code's native vision and reasoning
- **Video uses Gemini** — via the `video-analyzer` MCP server (must be installed separately) or direct Plugboard API
- **Audio uses Gemini via Plugboard** — the `understand_video` MCP tool does not support standalone audio. Use the direct Plugboard API with audio MIME types (`audio/mpeg`, `audio/wav`, `audio/ogg`, `audio/flac`). See [Multi-Media Items](#multi-media-items-multiple-videosaudio-per-item).
- **Hard stop on MCP/Plugboard failure** — if Plugboard is unreachable, do NOT attempt text-only fallback for video/audio items. Stop and report the error to the user.
- **Always use `gemini-3-flash`** — reliable, fast, and produces well-calibrated ratings with detailed rationales. Preview models (e.g., `gemini-3-pro-preview`) are unstable and may return frequent HTTP 500 errors.
- **Parallelism everywhere** — media downloads use `ThreadPoolExecutor(max_workers=20)`, subagent dispatch uses concurrent `Task` tool calls, Plugboard API calls run in parallel via the batch runner pattern. When using Task subagents (image-only), each subagent should also parallelize its per-item API calls internally using `ThreadPoolExecutor(max_workers=5)` — this cuts per-batch time from ~4 min to ~1 min since each item's evaluation is independent. Collect results via `as_completed()` and return the merged batch JSON.
- **Structured notes format** — notes use `[{Dim Label}: {rating}] {rationale} | [...]` to preserve per-dimension reasoning. Do NOT allow generic summary notes.
- **Annotator name reflects the model** — use `gemini-{version}` (e.g., `gemini-3-flash`) when Gemini evaluates, `claude-ai` when Claude evaluates directly
- **Auto-validate calibration** — after collecting results, always check per-dimension distribution. Flag any dimension with >70% same rating as potentially miscalibrated.
- **Analysis report is the primary output** — after a full AI annotation run, automatically generate an analysis report via `/visualize` following Step 6. The report MUST embed media (video/audio/images) using CDN URLs from the extracted data — do NOT use local file paths or fabricate filenames. Use tabbed gallery: "Failures" (ALL), "Weak Passes" (sample), "Strong Passes" (sample). HTML injection (Step 8) is optional and requires user confirmation.
- **Human review recommended** — AI annotations are suggestions, not ground truth
- **Image format** — Claude's `Read` tool supports PNG, JPG, GIF, WebP
- **Video format** — `understand_video` supports MP4, MOV, AVI, WebM, MKV, M4V (max 50 MB raw upload, but keep under 10 MB to preserve audio — see [Video Constraints](#video-constraints))
- **Audio format** — Plugboard API supports MP3 (`audio/mpeg`), WAV (`audio/wav`), OGG (`audio/ogg`), FLAC (`audio/flac`)
- **Re-annotation** — delete `/tmp/<script_name>_pre_annotations.json` and re-run the AI workflow. Do NOT delete the data cache unless you want fresh data.
- **Cache and pre-annotations are coupled** — always delete both together when starting fresh, never just one
