---
description: Full API documentation for configuration classes in tools.data_party_tool
---

# Config Reference

Full API documentation for configuration classes in `tools.data_party_tool`.

## Table of Contents

- [DataPartyConfig](#datapartyconfig)
- [DataSourceConfig](#datasourceconfig)
- [IdentifierField](#identifierfield)
- [ContextField](#contextfield)
- [ContextSection](#contextsection)
- [TeamAssignment](#teamassignment)
- [ExportConfig](#exportconfig)
- [UIConfig](#uiconfig)

## DataPartyConfig

Top-level configuration tying together all aspects of a data party.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Unique party identifier |
| `schema` | `AnnotationSchema` | required | Annotation schema |
| `media` | `MediaConfig` | required | Media configuration |
| `data_source` | `DataSourceConfig` | `DataSourceConfig()` | Data source config |
| `identifiers` | `list[IdentifierField]` | `[]` | Identifier fields (at least one required) |
| `context_fields` | `list[ContextField]` | `[]` | Additional context fields |
| `export` | `ExportConfig` | `ExportConfig()` | Export settings |
| `ui` | `UIConfig` | `UIConfig()` | UI settings |
| `debug_mode` | `bool` | `False` | Debug/display mode — removes annotation controls, shows data only |
| `output_path` | `str` | `""` | Manifold path for output |
| `output_filename` | `str` | `""` | Custom filename (default: `{id}_data_party.html`) |

**Methods:**
- `get_all_data_keys()` — All column keys needed from data source
- `validate()` — Returns list of error strings

## DataSourceConfig

Configuration for data source (primarily informational; data is loaded in the script).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `hive_namespace` | `str` | `""` | Hive namespace |
| `hive_table` | `str` | `""` | Hive table name |
| `hive_partitions` | `dict[str, str]` | `{}` | Partition filters |
| `sample_size` | `int` | `0` | Sample size (0 = all) |
| `random_seed` | `int \| None` | `None` | For reproducible sampling |
| `filter_column` | `str` | `""` | Column to filter on |
| `filter_values` | `list[str]` | `[]` | Values to include |

## IdentifierField

Configuration for a unique identifier field shown in annotation cards.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Column name in source data |
| `label` | `str` | required | Display label |
| `show_in_card` | `bool` | `True` | Show in annotation card |
| `show_in_export` | `bool` | `True` | Include in export |
| `link_template` | `str` | `""` | URL template with `{value}` placeholder |

**Example with deep link:**
```python
IdentifierField(
    key="ad_id",
    label="Ad ID",
    link_template="https://www.internalfb.com/idd/home?id={value}",
)
```

## ContextField

Configuration for additional context fields shown in annotation cards.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Column name in source data |
| `label` | `str` | required | Display label |
| `show_in_card` | `bool` | `True` | Show in card (collapsed by default) |
| `show_in_export` | `bool` | `True` | Include in export |
| `is_url` | `bool` | `False` | Render as clickable link |
| `truncate_at` | `int` | `0` | Truncate at N chars (0 = no truncate) |
| `show_after_complete` | `bool` | `False` | Only show after card is complete |

## ContextSection

Configuration for a context/info section displayed at the top of the page (above cards).

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | `str` | required | Section title |
| `content` | `str` | required | HTML content |
| `icon` | `str` | `""` | Icon/emoji prefix |

## TeamAssignment

A team member's annotation assignment with item range. Used in `UIConfig.team_assignments` to pre-assign ranges to annotators.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | `str` | required | Annotator name |
| `range_start` | `int` | required | 1-based inclusive start |
| `range_end` | `int` | required | 1-based inclusive end |

**Example:**
```python
from tools.data_party_tool import TeamAssignment

assignments = [
    TeamAssignment(name="Alice Smith", range_start=1, range_end=25),
    TeamAssignment(name="Bob Jones", range_start=26, range_end=50),
]
```

## ExportConfig

Configuration for annotation export.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `google_sheet_id` | `str` | `""` | Target sheet ID (empty = disabled) |
| `google_sheet_url` | `str` | `""` | Direct URL for paste workflow |
| `google_apps_script_url` | `str` | `""` | Apps Script URL for auto-append |
| `sheet_name` | `str` | `"Annotations"` | Target sheet/tab name |
| `append_mode` | `bool` | `True` | Append rows vs overwrite |
| `enable_csv_download` | `bool` | `True` | Enable CSV download button |
| `enable_clipboard_copy` | `bool` | `True` | Enable copy-to-clipboard |
| `include_timestamp` | `bool` | `True` | Include timestamp in export |
| `include_annotator` | `bool` | `True` | Include annotator name |
| `custom_export_fields` | `list[str]` | `[]` | Additional fields to export |

## UIConfig

Configuration for UI appearance and behavior.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | `str` | `"Data Party"` | Page title |
| `description` | `str` | `""` | Subtitle description |
| `instructions` | `str` | `""` | Plain text instructions |
| `instruction_steps` | `list[str]` | `[]` | Numbered instruction steps |
| `show_progress` | `bool` | `True` | Show progress bar |
| `show_card_numbers` | `bool` | `True` | Show card numbers |
| `enable_keyboard_nav` | `bool` | `True` | Arrow key navigation |
| `cards_per_page` | `int` | `1` | Cards per page (paginated mode) |
| `show_context_collapsed` | `bool` | `True` | Collapse context by default |
| `primary_color` | `str` | `"#1877f2"` | Primary theme color |
| `dark_mode` | `bool` | `False` | Dark mode |
| `display_mode` | `str` | `"scrollable"` | `"scrollable"` or `"paginated"` |
| `show_annotator_input` | `bool` | `True` | Show annotator name input |
| `show_range_selector` | `bool` | `True` | Show item range selector |
| `default_range_end` | `int` | `50` | Default end of range (0 = all) |
| `show_progress_grid` | `bool` | `True` | Show clickable progress grid |
| `show_guidelines` | `bool` | `True` | Show annotation guidelines section |
| `context_sections` | `list[ContextSection]` | `[]` | Top-of-page context sections |
| `show_config_section` | `bool` | `False` | Show config/about section |
| `config_items` | `dict[str, str]` | `{}` | Key-value config items to display |
| `poc_info` | `str` | `""` | Point of contact (e.g., `"@username"`) |
| `attribution_html` | `str` | `"Built with /data-party Claude skill"` | Header attribution text (supports HTML links) |
| `show_back_to_top` | `bool` | `True` | Show back-to-top button |
| `emphasize_notes_on_fail` | `bool` | `True` | Highlight notes when fail is selected |
| `show_item_numbers` | `bool` | `True` | Show item numbers in cards |
| `sticky_media` | `bool` | `False` | Media sticks to top of viewport while annotations scroll (two-column layout) |
| `gradient_start` | `str` | `""` | Header gradient start color |
| `gradient_end` | `str` | `""` | Header gradient end color |
| `team_assignments` | `list[TeamAssignment]` | `[]` | Pre-configured team assignments with name and item range |

## DataPartyGenerator

The generator that produces HTML from config + data.

```python
generator = DataPartyGenerator(
    config=config,
    party_id="my_party_abc123",
    sections=None,           # Custom section list (default: auto-selected)
    pre_annotations=None,    # AI pre-annotations dict (default: none)
)
result = generator.generate(data)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `config` | `DataPartyConfig` | required | Complete configuration |
| `party_id` | `str` | `""` | Unique ID for localStorage keys (auto-generated if empty) |
| `sections` | `list[Section] \| None` | `None` | Custom section list. If `None`, auto-selects `DEFAULT_SECTIONS` or `DEBUG_SECTIONS` based on `config.debug_mode` |
| `pre_annotations` | `dict[str, dict[str, Any]] \| None` | `None` | AI pre-annotation data. Keys are string item indices (1-based), values are dicts mapping dimension keys to annotation values. Pre-filled annotations appear with amber/dashed styling and "AI" badges in the UI. |
