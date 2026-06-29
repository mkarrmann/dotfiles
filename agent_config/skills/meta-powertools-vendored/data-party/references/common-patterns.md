---
description: Reusable patterns and best practices for the Data Party Tool
---

# Common Patterns

Reusable patterns and best practices for the Data Party Tool.

## Template Script

Use this as the starting point for every new data party script:

```python
#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict
"""Data party: <TITLE>."""

import os

import pvc2

from tools.data_party_tool import (
    AnnotationSchema,
    ContextField,
    ContextSection,
    DataPartyConfig,
    DataPartyGenerator,
    DimensionConfig,
    DimensionType,
    ExportConfig,
    IdentifierField,
    MediaConfig,
    MediaGroup,
    MediaSource,
    MediaType,
    OptionConfig,
    StorageBackend,
    UIConfig,
    px_upload,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

def main() -> None:
    # 1. Load data
    ctx = pvc2.context(namespace="<NAMESPACE>", source="data_party_tool")
    df = ctx.sql_to_pandas("<QUERY>")
    data: list[dict[str, str]] = df.to_dict("records")

    # 2. Define schema, media, config
    schema = AnnotationSchema(...)
    media = MediaConfig(...)
    config = DataPartyConfig(
        id="<ID>",
        schema=schema,
        media=media,
        identifiers=[...],
        context_fields=[...],
        export=ExportConfig(...),
        ui=UIConfig(
            ...,
            poc_info=f"@{os.environ.get('USER', 'unknown')}",
        ),
    )

    # 3. Generate and save
    generator = DataPartyGenerator(config)
    result = generator.generate(data)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    local_path = os.path.join(OUTPUT_DIR, f"{config.id}.html")
    with open(local_path, "w", encoding="utf-8") as f:
        f.write(result.html_content)
    print(f"Saved: {local_path}")

    # 4. Upload to PixelCloud
    px_upload(local_path, title=config.ui.title or "Data Party")

if __name__ == "__main__":
    main()
```

## Dimension Definition Patterns

Put rubric definitions in **`OptionConfig.description`** (renders as button tooltips), NOT in the dimension `description` field. Keep dimension descriptions short.

```python
# CORRECT: Short description + rubric in option tooltips
DimensionConfig(
    key="audio_quality",
    name="Overall Audio Quality (Required)",
    description="Compared to the original, how is the final audio track?",
    type=DimensionType.OPTIONS,
    required=True,
    options=[
        OptionConfig(
            value="strong_pass",
            label="Strong Pass",
            description="The final audio track is clear, audible, with minimal distortion or noise.",
            color="#28a745",
        ),
        OptionConfig(
            value="weak_pass",
            label="Weak Pass",
            description="The final audio track is mostly clear and audible with some distortion or noise.",
            color="#ffc107",
        ),
        OptionConfig(
            value="fail",
            label="Fail",
            description="The final audio is significantly worse, posing high risk for business reputation.",
            color="#dc3545",
        ),
    ],
)

# WRONG: Long description with all rubric text inline
DimensionConfig(
    key="audio_quality",
    name="Overall Audio Quality",
    description="Compared to the original, how is the final audio track?\n\n"
        "STRONG PASS: The final audio track is clear...\n\n"
        "WEAK PASS: The final audio track is mostly clear...\n\n"
        "FAIL: The final audio is significantly worse...",
    ...
)
```

## Built-in Notes Field

`AnnotationSchema.allow_notes=True` (default) provides a general "Additional Notes" text area on every card. Only add custom `DimensionConfig(type=DimensionType.TEXT)` dimensions when the guidelines require per-dimension text input (e.g., "explain your reasoning for safety"). Do not add a generic "notes" or "comments" TEXT dimension — that duplicates the built-in one.

## Context Sections (product info at top of page)

Use `ContextSection` in `UIConfig.context_sections` to add a collapsible product context panel. Renders as a 2-column grid before Instructions. Always include context sections — they help annotators understand what they're evaluating.

```python
from tools.data_party_tool import ContextSection

ui=UIConfig(
    title="My Data Party",
    context_sections=[
        ContextSection(
            title="Description",
            icon="📋",
            content="What the product is and what it does.",
        ),
        ContextSection(
            title="How it works",
            icon="⚙️",
            content="Pipeline details. <strong>HTML</strong> is supported.",
        ),
        ContextSection(
            title="Goals",
            icon="🎯",
            content="What this evaluation aims to measure.",
        ),
        ContextSection(
            title="Evaluation Scope",
            icon="📊",
            content="200 items, 4 dimensions, P0 required / P1 optional.",
        ),
    ],
    ...
)
```

## Variant Grid (A/B comparison)

Use `VariantGroup` with `VariantConfig` list and shared `DimensionConfig` list. Creates a grid with variants as columns and dimensions as rows. Dimension keys are auto-expanded as `{dim_key}_{variant_id}` (e.g., `accuracy_normalized`, `accuracy_abstractive`).

```python
from tools.data_party_tool import VariantConfig, VariantGroup

schema = AnnotationSchema(
    dimensions=[
        # Standalone dimensions (not per-variant)
        DimensionConfig(key="overall", name="Overall", ...),
    ],
    variant_groups=[
        VariantGroup(
            id="models",
            label="Model Comparison",
            variants=[
                VariantConfig(id="model_a", label="Model A"),
                VariantConfig(id="model_b", label="Model B"),
            ],
            dimensions=[
                # These are rated once per variant
                DimensionConfig(key="quality", name="Quality", ...),
                DimensionConfig(key="relevance", name="Relevance", ...),
            ],
        ),
    ],
)
```

## Side-by-side Media Comparison

Two or more `MediaSource` entries in one `MediaGroup` with `layout="side_by_side"`. This supports 2-wide, 3-wide, or more columns.

## Pass/Fail Dimensions

`DimensionType.PASS_FAIL` **requires explicit `options`** with `OptionConfig` values. Without them, the script will crash at runtime with `ValueError: Dimension '...' of type pass_fail requires options`.

```python
# CORRECT: PASS_FAIL with explicit options
DimensionConfig(
    key="compliance",
    name="Compliance",
    description="Does the content comply with guidelines?",
    type=DimensionType.PASS_FAIL,
    required=True,
    options=[
        OptionConfig(
            value="pass",
            label="Pass",
            description="Content complies with all guidelines.",
            color="#28a745",
        ),
        OptionConfig(
            value="fail",
            label="Fail",
            description="Content violates one or more guidelines.",
            color="#dc3545",
        ),
    ],
)

# WRONG: PASS_FAIL without options — will crash!
DimensionConfig(key="compliance", name="Compliance", type=DimensionType.PASS_FAIL)
```

Set `UIConfig.emphasize_notes_on_fail=True` to highlight the built-in notes field when "fail" is selected.

## Scale Ratings

Use `DimensionType.SCALE` with `scale=5` and optional `rubric` list describing each point. `scale` must be >= 2, and if `rubric` is provided, its length must match `scale`.

## Debug/Display Mode Template

Use this template when the user wants to visualize data without annotation:

```python
config = DataPartyConfig(
    id="my_debug_party",
    schema=AnnotationSchema(
        id="debug",
        name="Debug View",
        version="1.0",
        dimensions=[],
        allow_notes=False,
    ),
    media=MediaConfig(
        groups=[
            MediaGroup(
                id="media",
                label="Media",
                sources=[MediaSource(key="video_url", label="Video", type=MediaType.VIDEO)],
            ),
        ],
    ),
    identifiers=[IdentifierField(key="id", label="ID")],
    context_fields=[
        ContextField(key="col1", label="Column 1"),
        ContextField(key="col2", label="Column 2"),
    ],
    debug_mode=True,
    ui=UIConfig(
        title="Debug: My Data",
        description="Data visualization",
        default_range_end=0,  # Show all items
        poc_info=f"@{os.environ.get('USER', 'unknown')}",
    ),
)
```

## Local File Debug Display

Use `StorageBackend.LOCAL_FILE` when the user has local files (e.g., model outputs in `/tmp/`) and wants to quickly visualize them without uploading to a CDN. Files are base64-encoded directly into the HTML. Requires `debug_mode=True`.

```python
# Use StorageBackend.LOCAL_FILE in MediaSource
MediaSource(
    key="video_path", label="Video",
    type=MediaType.VIDEO,
    backend=StorageBackend.LOCAL_FILE,
)

# Data items contain local file paths as values
data = [{"id": "clip_001", "video_path": "/tmp/outputs/clip_001.mp4"}, ...]

# Must set debug_mode=True on DataPartyConfig
config = DataPartyConfig(..., debug_mode=True)
```

## Team Assignments

### Sources for Team Names

1. **Manual list** — user provides names directly (e.g., "split between Alice, Bob, and Carol")
2. **Google Sheet column** — read names from a spreadsheet column using the `google-sheets` skill's `google_api.py`
3. **Calendar meeting invitees** — use the `/calendar` skill to fetch meetings and extract attendee names. Meeting share URLs (`fburl.com/meeting/...`) cannot be loaded directly. Instead, search the user's calendar +/- 4 weeks from today:
   ```bash
   .claude/skills/calendar/scripts/get-meetings.py "-4 weeks" "+4 weeks" --full
   ```
   Scan the output for the meeting title, then extract the attendee list. If the script path is not found, refer to `fbcode/claude-templates/components/skills/calendar/SKILL.md` for the correct path and usage.

### Auto-Split Helper

Use this helper to split items evenly across team members, distributing the remainder one item at a time:

```python
from tools.data_party_tool import TeamAssignment

TEAM_NAMES: list[str] = ["Alice", "Bob", "Carol", ...]
TOTAL_ITEMS: int = 200

def _build_team_assignments() -> list[TeamAssignment]:
    """Split items evenly across team members."""
    if not TEAM_NAMES:
        return []
    n = len(TEAM_NAMES)
    per_person = TOTAL_ITEMS // n
    remainder = TOTAL_ITEMS % n
    assignments: list[TeamAssignment] = []
    start = 1
    for i, name in enumerate(TEAM_NAMES):
        size = per_person + (1 if i < remainder else 0)
        assignments.append(
            TeamAssignment(name=name, range_start=start, range_end=start + size - 1)
        )
        start += size
    return assignments
```

Use in UIConfig: `team_assignments=_build_team_assignments()`.

### UI Behavior

The Team Assignments section **only appears when `team_assignments` is non-empty** in `UIConfig`. When present, it shows a compact chip grid where each chip displays the annotator name and item range. Clicking a chip sets the annotator name input and range selector, then filters visible cards. The current user's chip is highlighted with a colored border based on the annotator name input. Annotators can also add assignments dynamically (saved in localStorage). Config-baked assignments are read-only and cannot be removed.

## Google Sheet Creation and Sharing

To create a new Google Sheet and share it with all Meta employees:

```bash
# Create the sheet
python3 .claude/skills/google-sheets/scripts/google_api.py \
  '{"action": "create_spreadsheet", "title": "[Data Party][2026-02-28] My Data Party"}'

# Share with meta.com domain (writer access)
gdrive permissions share <SHEET_ID> --type domain --domain meta.com --role writer --json
```

If the script path is not found, refer to `fbcode/claude-templates/components/skills/google-sheets/SKILL.md` for the correct path.

When creating a new sheet, automatically enable meta.com domain-wide writer access — new sheets are private by default. When the user provides an existing sheet, check permissions with `gdrive permissions list` first. If the sheet lacks domain-wide meta.com writer access, offer to enable it: "Want me to enable edit access for everyone at Meta?"

See [export-reference.md](export-reference.md) "Full Google Sheet Setup Workflow" for the complete end-to-end setup including Apps Script auto-append.

## Customizations by Data Source

- **Everstore handles:** Add `resolve_everstore_handles(data, handle_key=..., url_key=...)` after loading data
- **OIL handles:** Add `resolve_oil_handles(data, handle_key=..., url_key=...)` after loading data
- **Video IDs:** Add `resolve_video_ids_to_cdn_urls(data, video_id_key=..., url_key=...)` after loading data
- **Manifold paths:** Use `StorageBackend.MANIFOLD` in `MediaSource`
- **Google Sheets export:** Add `google_sheet_url` to `ExportConfig` for open-sheet button
- **Custom sections:** Use `context_sections` in `UIConfig` for top-of-page context panels
- **Variant grids:** Use `VariantGroup` in `AnnotationSchema` for A/B comparisons
- **Color-coded options:** Add `color` to `OptionConfig` for visual differentiation
- **Dropdown rendering:** Set `render_as="dropdown"` on `DimensionConfig` for compact UI
