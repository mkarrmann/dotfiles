---
description: How to refresh expired CDN URLs in data party HTML files
---

# Refresh Reference

CDN URLs generated from Everstore/OIL handles expire (OIL: ~1.5 days, Everstore: up to 30 days). The refresh tool re-resolves handles to fresh CDN URLs without regenerating the entire data party.

## When to Use

Activate this workflow when the user says things like:
- "refresh my data party URLs"
- "the media in my data party is broken"
- "CDN URLs expired"
- "refresh pxl.cl/xxxxx"

## Workflow

### Step 1: Get the HTML file locally

PixelCloud HTML cannot be downloaded programmatically. Use `AskUserQuestion` to ask the user to:

1. Open the PixelCloud page in a browser (e.g. `https://pxl.cl/xxxxx`)
2. Click **"Download .html"** in the top right corner
3. Either:
   - **Copy the lookaside URL** from the new tab and paste it, then download with:
     ```bash
     curl -sL "<lookaside_url>" -o /tmp/data_party.html
     ```
   - **Save locally** with Ctrl+S / Cmd+S and provide the file path

If the user already has a local HTML file, skip this step.

### Step 2: Check for handle_metadata

The refresh tool needs to know which data columns contain handles and which contain the CDN URLs to refresh. There are two cases:

**Case A: HTML has `handle_metadata` in CONFIG** — The tool works automatically. Data parties generated with `handle_metadata` in `DataPartyConfig` embed the mapping in the HTML.

**Case B: HTML lacks `handle_metadata` (older data parties)** — Use `--handle-map` or `--video-id-map` to specify the mapping manually. To find the right mapping:

1. Look at the data columns in the HTML (identifiers and data keys)
2. Find columns that contain handles (Everstore hashes or OIL paths) or video IDs (numeric Facebook video IDs)
3. Find the corresponding CDN URL columns used by the media sources
4. Pass the mapping with the correct flag:
   - **Everstore/OIL handles** → `--handle-map url_key=handle_key`
   - **Facebook video IDs** (numeric) → `--video-id-map url_key=video_id_key`

**IMPORTANT:** Do NOT use `--handle-map` for columns that contain numeric Facebook video IDs. The `--handle-map` flag resolves values as Everstore handles, which produces "URL signature mismatch" errors when given video IDs. Use `--video-id-map` instead — it looks up the actual Everstore handles from the `dim_ad_videoid_handles` Hive table first, then resolves those to CDN URLs.

Example with Everstore/OIL handles:
```bash
--handle-map image_cdn_url=input_media_handle
```

Example with Facebook video IDs:
```bash
--video-id-map video_cdn_url=video_id
```

### Step 3: Run the refresh

```bash
# Basic refresh (overwrites in place)
buck run fbcode//tools/data_party_tool:refresh_urls -- /tmp/data_party.html

# With explicit handle mapping (Everstore/OIL handles)
buck run fbcode//tools/data_party_tool:refresh_urls -- /tmp/data_party.html \
  --handle-map image_cdn_url=input_media_handle

# With video ID mapping (numeric Facebook video IDs)
buck run fbcode//tools/data_party_tool:refresh_urls -- /tmp/data_party.html \
  --video-id-map video_cdn_url=video_id

# With a specific output path
buck run fbcode//tools/data_party_tool:refresh_urls -- /tmp/data_party.html \
  --output /tmp/refreshed.html
```

### Step 4: Re-upload to PixelCloud

Add `--reupload` to upload the refreshed file. This creates a **new** PixelCloud URL.

**IMPORTANT:** Do NOT include `--comment-on` in the reupload command. Always ask the user for explicit permission before posting any comment on the original post. Never post a comment automatically.

```bash
buck run fbcode//tools/data_party_tool:refresh_urls -- \
  /tmp/data_party.html \
  --handle-map image_cdn_url=input_media_handle \
  --reupload

# Or with video IDs:
buck run fbcode//tools/data_party_tool:refresh_urls -- \
  /tmp/data_party.html \
  --video-id-map video_cdn_url=video_id \
  --reupload
```

Ensure `px` is installed with `feature install --persist px`.

### Step 5: Report results and ask about comment

After uploading, report results clearly labeling the **original** vs **new** URLs. Then **ask the user for explicit permission** before posting any comment on the original. **Never post a comment without the user's confirmation.** Also offer instructions for in-place replacement:

> Refreshed X/Y video CDN URLs
> - **Original PixelCloud URL:** https://pxl.cl/ORIGINAL
> - **New PixelCloud URL (refreshed):** https://pxl.cl/NEW_CODE
>
> Would you like me to post a comment on the original (pxl.cl/ORIGINAL) linking to the refreshed version?
>
> If you'd like to replace the original HTML in-place (keeping the same URL), follow these steps:
> 1. Open **pxl.cl/NEW_CODE** (new, refreshed) → click **"Download .html"** → Cmd+S / Ctrl+S → save locally
> 2. Open **pxl.cl/ORIGINAL** (original) → click the **⋮ menu** (top right) → **Edit Post** → **Browse** → select the saved HTML → **Save**

### Step 6: Post comment (only after user confirms)

**Only** if the user explicitly agrees, post the comment using `--comment-on` or programmatically:

```bash
buck run fbcode//tools/data_party_tool:refresh_urls -- \
  /tmp/data_party.html \
  --comment-on "pxl.cl/ORIGINAL"
```

Or programmatically:

```python
from tools.data_party_tool import comment_on_pixelcloud_post
comment_on_pixelcloud_post("pxl.cl/ORIGINAL", "[Sent by claude via /data-party] Refreshed version with updated CDN URLs: https://pxl.cl/NEW_CODE")
```

The comment text must always be: `[Sent by claude via /data-party] Refreshed version with updated CDN URLs: <new_url>`

## CLI Reference

```bash
buck run fbcode//tools/data_party_tool:refresh_urls -- <input> [options]
```

| Argument | Description |
|----------|-------------|
| `<input>` | Local HTML file path |
| `--output` | Output path (default: overwrite input) |
| `--handle-map` | Explicit `url_key=handle_key` mapping (repeatable). Use for Everstore/OIL handles in HTML without `handle_metadata`. |
| `--video-id-map` | Explicit `url_key=video_id_key` mapping (repeatable). Use for numeric Facebook video IDs. Resolves via `dim_ad_videoid_handles` table. |
| `--reupload` / `--no-reupload` | Re-upload to PixelCloud as a new post (default: False) |
| `--comment-on PXL_URL` | After `--reupload`, comment on this PixelCloud post with a link to the new URL. Accepts `pxl.cl/XXXXX` or a short code. |

## Ensuring Refresh Works for New Data Parties

When generating a new data party that uses CDN handle resolution or video ID resolution, **always** populate `handle_metadata` in `DataPartyConfig`:

```python
config = DataPartyConfig(
    ...,
    handle_metadata={
        "video_cdn_url": "video_handle",     # url_key -> handle_key (Everstore/OIL)
        "audio_cdn_url": "audio_handle",
        "ad_video_cdn_url": "video_id",      # url_key -> video_id_key (video ID lookup)
    },
)
```

This enables the refresh tool to re-resolve the handles automatically without needing `--handle-map`. The generated HTML will also show a notice banner when a majority of media elements fail to load, directing users to the refresh command.

## Python API

```python
from tools.data_party_tool import refresh_data_party_urls

# Refresh with handle_metadata already in the HTML
refresh_data_party_urls("/tmp/my_data_party.html")

# Refresh an older HTML without handle_metadata (Everstore/OIL handles)
refresh_data_party_urls(
    "/tmp/my_data_party.html",
    handle_metadata_override={"image_cdn_url": "input_media_handle"},
)

# Refresh an older HTML with video IDs (numeric Facebook video IDs)
refresh_data_party_urls(
    "/tmp/my_data_party.html",
    video_id_metadata_override={"video_cdn_url": "video_id"},
)
```

See [upload-reference.md](upload-reference.md) for full API signature of `refresh_data_party_urls`.
