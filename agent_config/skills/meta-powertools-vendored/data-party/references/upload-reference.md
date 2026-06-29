---
description: Full API documentation for upload and URL resolution utilities in tools.data_party_tool
---

# Upload Reference

Full API documentation for upload and URL resolution utilities in `tools.data_party_tool`.

## Table of Contents

- [PixelCloud Upload](#pixelcloud-upload)
- [PixelCloud Comment](#pixelcloud-comment)
- [Manifold Upload](#manifold-upload)
- [Everstore Handle Resolution](#everstore-handle-resolution)
- [OIL Handle Resolution](#oil-handle-resolution)
- [Auto-detect Handle Resolution](#auto-detect-handle-resolution)
- [Video ID Resolution](#video-id-resolution)

## PixelCloud Upload

### px_upload

```python
def px_upload(local_path: str, title: str | None = None) -> None
```

Upload a file to PixelCloud via the `px` CLI. This is the **recommended** method — no `PIXELCLOUD_OAUTH_TOKEN` env var required.

**Requires:** `px` CLI installed via `feature install --persist px`.

| Param | Description |
|-------|-------------|
| `local_path` | Path to the file to upload |
| `title` | Optional title for the PixelCloud post |

**Usage:**
```python
from tools.data_party_tool import px_upload

px_upload(local_path, title=config.ui.title or "Data Party")
```

> **Deprecated:** `upload_to_pixelcloud()` and `save_and_upload_to_pixelcloud()` require `PIXELCLOUD_OAUTH_TOKEN` and should not be used in new scripts. Use `px_upload()` instead.

## PixelCloud Comment

### comment_on_pixelcloud_post

```python
def comment_on_pixelcloud_post(
    pxl_url: str,
    comment: str,
) -> str
```

Post a comment on an existing PixelCloud post. Returns the comment node ID.

**Requires:** `px` CLI installed via `feature install --persist px`.

| Param | Description |
|-------|-------------|
| `pxl_url` | PixelCloud URL (`pxl.cl/XXXXX`, `https://pxl.cl/XXXXX`, full internalfb URL, or bare short code) |
| `comment` | Plain-text comment body |

**Usage:**
```python
from tools.data_party_tool import comment_on_pixelcloud_post

comment_on_pixelcloud_post(
    "pxl.cl/8WLMs",
    "Refreshed version with updated CDN URLs: https://pxl.cl/8WMfm",
)
```

## Manifold Upload

### upload_to_manifold

```python
def upload_to_manifold(
    local_path: str,
    bucket: str,
    remote_dir: str,
    filename: str | None = None,
) -> str
```

Upload a local file to Manifold. Returns the full manifold path (`manifold://bucket/dir/file`).

### manifold_path_to_url

```python
def manifold_path_to_url(manifold_path: str) -> str
```

Convert a manifold path to an interncache URL for viewing.

`manifold://bucket/path/file` becomes `https://interncache-all.fbcdn.net/manifold/bucket/path/file`

### save_and_upload_html

```python
def save_and_upload_html(
    html_content: str,
    bucket: str,
    remote_dir: str,
    filename_prefix: str = "data_party",
) -> tuple[str, str]
```

Save HTML locally and upload to Manifold. Returns `(manifold_path, viewable_url)`.

## Everstore Handle Resolution

### resolve_everstore_handles

```python
def resolve_everstore_handles(
    data: list[dict[str, Any]],
    handle_key: str,
    url_key: str | None = None,
    callsite: str = "data_party_tool",
    batch_size: int = 20,
) -> list[dict[str, Any]]
```

Batch-convert Everstore handles to CDN URLs in data rows.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `data` | `list[dict]` | required | Data rows (modified in place) |
| `handle_key` | `str` | required | Column containing Everstore handles |
| `url_key` | `str \| None` | `{handle_key}_cdn_url` | Column for generated CDN URLs |
| `callsite` | `str` | `"data_party_tool"` | UrlGen callsite identifier |
| `batch_size` | `int` | `20` | Concurrent requests per batch |

**Example:**
```python
resolve_everstore_handles(data, handle_key="image_handle", url_key="image_url")
# Each row now has data["image_url"] with a CDN URL
```

## OIL Handle Resolution

### resolve_oil_handles

```python
def resolve_oil_handles(
    data: list[dict[str, Any]],
    handle_key: str,
    url_key: str | None = None,
    extension: str = "mp4",
    callsite: str = "data_party_tool",
    batch_size: int = 20,
) -> list[dict[str, Any]]
```

Batch-convert OIL handles to CDN URLs. Same interface as `resolve_everstore_handles` with an additional `extension` parameter for the file type (default: `"mp4"`).

## Auto-detect Handle Resolution

### resolve_handles

```python
def resolve_handles(
    data: list[dict[str, Any]],
    handle_key: str,
    url_key: str | None = None,
    extension: str = "mp4",
    callsite: str = "data_party_tool",
    batch_size: int = 20,
) -> list[dict[str, Any]]
```

Auto-detect handle type and resolve to CDN URLs. Handles containing `/` are treated as OIL; all others as Everstore. Results are merged into `url_key`.

## Video ID Resolution

### resolve_video_ids_to_cdn_urls

```python
def resolve_video_ids_to_cdn_urls(
    data: list[dict[str, Any]],
    video_id_key: str = "video_id",
    url_key: str = "video_cdn_url",
    callsite: str = "data_party_tool",
    batch_size: int = 20,
) -> list[dict[str, Any]]
```

Look up Everstore handles for video IDs via the `dim_ad_videoid_handles` table in the `ad_delivery` namespace, then convert to CDN URLs.

**Note:** This function requires `pvc2` to query the handle lookup table. It handles chunking large ID lists automatically.

**Example:**
```python
resolve_video_ids_to_cdn_urls(data, video_id_key="video_id", url_key="video_cdn_url")
```

## CDN URL Refresh

### handle_metadata

When resolving Everstore/OIL handles or video IDs, store the mapping in `DataPartyConfig.handle_metadata` so URLs can be refreshed later without rerunning the full script:

```python
config = DataPartyConfig(
    ...,
    handle_metadata={
        "video_cdn_url": "video_handle",      # url_key -> handle_key (Everstore/OIL)
        "audio_cdn_url": "audio_handle",
        "ad_video_cdn_url": "video_id",       # url_key -> video_id_key (video ID lookup)
    },
)
```

### refresh_data_party_urls

```python
def refresh_data_party_urls(
    html_path: str,
    output_path: str | None = None,
    callsite: str = "data_party_tool",
    batch_size: int = 20,
    handle_metadata_override: dict[str, str] | None = None,
) -> str
```

Re-resolve expired CDN URLs in an existing data party HTML file. Reads the embedded data and `handle_metadata` config, re-resolves all handle columns to fresh CDN URLs, and writes the updated HTML.

**Important:** CDN URLs from Everstore/OIL expire (OIL: ~1.5 days, Everstore: up to 30 days). Always populate `handle_metadata` when resolving handles to enable easy URL refresh.

For the full refresh workflow (download, CLI usage, `--handle-map`, re-upload, and updating the original post), see [refresh-reference.md](refresh-reference.md).
