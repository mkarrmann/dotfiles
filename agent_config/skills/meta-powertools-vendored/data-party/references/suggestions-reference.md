---
description: Post-setup improvement suggestions to offer users after building a data party
---

# Post-Setup Suggestions

After completing Step 7 (reporting results), read this file and offer 2-3 relevant suggestions based on the data party just built. Pick suggestions that match the data party's characteristics — don't offer all of them.

## Suggestion Catalog

### Sticky Media (for complex annotations)

**When to suggest:** The data party has 5+ annotation dimensions, variant groups, or long annotation sections where the annotator might lose sight of the media while scrolling.

**What it does:** Keeps the media player stuck to the top of the viewport while the annotations continue scrolling below. Uses a two-column layout where media is on the left with `position: sticky; top: 10px`, so it stays visible as the annotator scrolls through dimensions on the right.

**How to enable:**
```python
ui=UIConfig(
    sticky_media=True,
    ...
)
```

**Talking point:** "Since you have [N] dimensions, annotators may lose sight of the media while scrolling through annotations. Sticky media keeps the video/image visible at all times."

---

### Team Assignments (Range Splitting)

**When to suggest:** The data party has 50+ items and the user mentions a team of annotators.

**What it does:** Pre-assigns item ranges to annotators. The generated page shows a "Team Assignments" table where each person can click "Load" to set their name and range.

**How to set up:** See [common-patterns.md](common-patterns.md) "Team Assignments" for the auto-split helper, team name sources (manual list, Google Sheet column, calendar meeting), and UI behavior.

**Talking point:** "I can pre-assign item ranges to your team. Each person clicks 'Load' next to their name to set their range automatically. Want me to split the work evenly? I can also pull names from a calendar meeting invite."

---

### Full Google Sheet Setup (Create + Share + Auto-Append)

**When to suggest:** The user wants Google Sheet export but doesn't have a sheet yet, or has a sheet but hasn't set up auto-append.

**What it does:** Creates a Google Sheet, shares it with all Meta employees (writer access), sets up Google Apps Script auto-append for one-click export, and configures the sheet URL to open directly to the Annotations tab. This is the full end-to-end setup.

**How to set up:** See [references/export-reference.md](export-reference.md) "Full Google Sheet Setup Workflow" for the complete step-by-step.

**Talking point:** "Want me to create a Google Sheet with one-click auto-append? I'll create the sheet, share it with Meta, and set up the Apps Script — you just need to deploy the script in the sheet."

---

### Google Apps Script Auto-Append (Sheet Already Exists)

**When to suggest:** The user already has a Google Sheet URL for export but hasn't set up auto-append yet (no `google_apps_script_url` in ExportConfig).

**What it does:** Instead of copy-paste, annotators click "Append to Google Sheet" to automatically push completed rows. Tracks which items have been appended to prevent duplicates.

**How to set up:** See [references/export-reference.md](export-reference.md) for the Apps Script setup guide.

**Talking point:** "Want to skip the copy-paste step? I can help set up Google Apps Script auto-append so annotators just click a button to push results."

---

### CDN URL Refresh Setup

**When to suggest:** The data party uses Everstore or OIL handles for media (resolved at generation time), and `handle_metadata` is not set on the config.

**What it does:** Maps URL columns to their source handle columns, enabling the `/data-party refresh` workflow when CDN URLs expire.

**How to enable:**
```python
config = DataPartyConfig(
    ...
    handle_metadata={
        "_resolved_video_url": "video_handle",   # url_key -> handle_key
        "_resolved_audio_url": "audio_handle",
    },
)
```

**Talking point:** "CDN URLs from Everstore/OIL expire after 1-30 days. Adding `handle_metadata` enables easy URL refresh without regenerating the whole page."

---

### Context Sections for Annotator Guidance

**When to suggest:** The data party has no `context_sections` in UIConfig, or the user provided a guidelines doc that isn't linked.

**What it does:** Adds a collapsible context panel at the top of the page with product info, goals, evaluation scope, and links to reference docs.

**Talking point:** "Adding context sections at the top helps annotators understand what they're evaluating without switching to a separate doc."

---

### Dropdown Rendering for Many Options

**When to suggest:** Any dimension has 5+ options, making the button row visually crowded.

**What it does:** Renders the dimension as a dropdown select instead of a row of buttons.

**How to enable:**
```python
DimensionConfig(
    ...
    render_as="dropdown",
)
```

**Talking point:** "The [dimension name] dimension has [N] options — switching to dropdown rendering would save space and reduce visual clutter."

---

### Multi-Select Dimensions

**When to suggest:** The annotation guidelines mention "select all that apply" or similar multi-choice requirements.

**What it does:** Allows annotators to select multiple options for a single dimension.

**How to enable:**
```python
DimensionConfig(
    ...
    multi_select=True,
)
```

**Talking point:** "If annotators need to select multiple [dimension] values (e.g., multiple issues), I can enable multi-select for that dimension."

---

## Selection Guidelines

Pick 2-3 suggestions that are **most relevant** to the data party just built. Prioritize:

1. **Suggestions that address clear gaps** (e.g., no handle_metadata when using resolved handles)
2. **Suggestions that improve the annotator experience** based on the complexity of the task
3. **Suggestions the user hasn't already configured**

Don't suggest features that are already enabled or irrelevant to the data type.
