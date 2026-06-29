---
description: Full API documentation for media configuration classes in tools.data_party_tool
---

# Media Reference

Full API documentation for media configuration classes in `tools.data_party_tool`.

## Table of Contents

- [MediaType](#mediatype)
- [StorageBackend](#storagebackend)
- [MediaSource](#mediasource)
- [MediaGroup](#mediagroup)
- [MediaConfig](#mediaconfig)
- [resolve_media_url](#resolve_media_url)

## MediaType

Enum defining the type of media content.

| Value | Description | HTML Element |
|-------|-------------|--------------|
| `VIDEO` | Video content | `<video>` tag |
| `AUDIO` | Audio content | `<audio>` tag |
| `IMAGE` | Image content | `<img>` tag |
| `TEXT` | Text content | `<div>` with text |
| `PDF` | PDF document | Embedded viewer |

## StorageBackend

Enum defining the storage backend for media files.

| Value | URL Format | Resolution |
|-------|------------|------------|
| `MANIFOLD` | `manifold://bucket/path` | Converted to interncache URL |
| `OIL` | OIL handle | Requires `resolve_oil_handles()` |
| `EVERSTORE` | Everstore handle | Requires `resolve_everstore_handles()` |
| `EXTERNAL_URL` | `https://...` | Used as-is |
| `BLOBSTORE` | `blobstore://...` | Pass-through |
| `LOCAL_FILE` | `/path/to/file.mp4` | Base64-encoded into `data:` URI at generation time |

### LOCAL_FILE Backend

`StorageBackend.LOCAL_FILE` reads local files and base64-encodes them directly into the HTML as `data:` URIs. This is **debug-mode only** — the generator will reject it if `debug_mode=False`.

Data items should contain local file paths as values (e.g., `/tmp/outputs/clip.mp4`).

**Important:** For `EVERSTORE` and `OIL` backends, you must resolve handles to CDN URLs
before passing data to the generator. Use the utilities in `utils.py`:
- `resolve_everstore_handles(data, handle_key, url_key)` for Everstore
- `resolve_oil_handles(data, handle_key, url_key)` for OIL
- `resolve_handles(data, handle_key, url_key)` for auto-detection

Then set the `MediaSource.key` to the `url_key` and use `StorageBackend.EXTERNAL_URL`.

## MediaSource

Configuration for a single media source (one column in the data).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Column name in source data |
| `label` | `str` | required | Display label |
| `type` | `MediaType` | required | Type of media |
| `backend` | `StorageBackend` | `EXTERNAL_URL` | Storage backend |
| `show_controls` | `bool` | `True` | Show playback controls (video/audio) |
| `autoplay` | `bool` | `False` | Auto-play media |
| `loop` | `bool` | `False` | Loop playback |
| `muted` | `bool` | `True` | Mute by default (needed for autoplay) |

## MediaGroup

Group of related media for comparison or display.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Unique group identifier |
| `label` | `str` | required | Display label (empty string for no label) |
| `sources` | `list[MediaSource]` | `[]` | Media sources in this group |
| `layout` | `str` | `"side_by_side"` | Layout mode |

**Layout options:**
- `"side_by_side"` — Sources displayed next to each other horizontally
- `"stacked"` — Sources stacked vertically
- `"tabs"` — Tabbed interface, one source visible at a time
- `"carousel"` — Swipeable carousel
- `"single"` — Single source (use when group has one source)

## MediaConfig

Complete media configuration for a data party.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `groups` | `list[MediaGroup]` | `[]` | Media groups |
| `lazy_load` | `bool` | `True` | Use `preload="none"` for videos |
| `preload_next` | `bool` | `True` | Preload next card media |

**Methods:**
- `get_all_sources()` — All sources across all groups
- `get_source_keys()` — All source column keys

## resolve_media_url

```python
def resolve_media_url(path: str, backend: StorageBackend, media_type: MediaType | None = None) -> str
```

Resolve a media path to a playable URL based on its storage backend.

- `MANIFOLD`: Converts `manifold://bucket/path` to `https://interncache-all.fbcdn.net/manifold/bucket/path`
- `EXTERNAL_URL`: Returns the URL as-is
- `OIL` / `EVERSTORE`: Pass-through (must be pre-resolved with utility functions)
- `LOCAL_FILE`: Reads the file and returns a `data:` URI (requires `media_type` for MIME detection)

## embed_local_file

```python
def embed_local_file(path: str, media_type: MediaType) -> str
```

Read a local file, base64-encode it, and return a `data:` URI string. Called automatically by `resolve_media_url` for `LOCAL_FILE` backend but can also be used directly for advanced use cases.

- Raises `FileNotFoundError` if file doesn't exist
- Detects MIME type from file extension, falling back to `mimetypes.guess_type()`
