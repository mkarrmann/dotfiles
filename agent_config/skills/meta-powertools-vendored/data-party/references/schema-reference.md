---
description: Full API documentation for annotation schema classes in tools.data_party_tool
---

# Schema Reference

Full API documentation for annotation schema classes in `tools.data_party_tool`.

## Table of Contents

- [DimensionType](#dimensiontype)
- [OptionConfig](#optionconfig)
- [DimensionConfig](#dimensionconfig)
- [SeekConfig](#seekconfig)
- [VariantConfig](#variantconfig)
- [VariantGroup](#variantgroup)
- [AnnotationSchema](#annotationschema)

## DimensionType

Enum defining the type of annotation dimension.

| Value | Description | UI Element |
|-------|-------------|------------|
| `PASS_FAIL` | Pass/Fail buttons | Colored buttons (green/red) |
| `SCALE` | 1-N rating scale | Numbered buttons |
| `OPTIONS` | Multiple choice | Buttons or dropdown |
| `TEXT` | Free text input | Textarea |

## OptionConfig

Configuration for a single option in `PASS_FAIL` or `OPTIONS` dimensions.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `value` | `str` | required | Internal value (e.g., `"pass"`, `"fail"`) |
| `label` | `str` | required | Display label (e.g., `"Pass"`, `"Fail"`) |
| `description` | `str` | `""` | Tooltip/help text shown in guidelines |
| `color` | `str` | `""` | CSS color for highlight (e.g., `"#28a745"`) |

**Example:**
```python
OptionConfig(value="pass", label="Pass", color="#28a745")
OptionConfig(value="fail", label="Fail", color="#dc3545")
```

## DimensionConfig

Configuration for a single annotation dimension.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `key` | `str` | required | Unique identifier (e.g., `"safety"`) |
| `name` | `str` | required | Display name (e.g., `"Safety/Compliance"`) |
| `description` | `str` | `""` | Help text shown to annotators |
| `type` | `DimensionType` | `PASS_FAIL` | Dimension type |
| `required` | `bool` | `True` | Whether annotation is required |
| `options` | `list[OptionConfig] \| None` | `None` | For `PASS_FAIL` and `OPTIONS` types |
| `scale` | `int` | `5` | For `SCALE` type: range 1 to N |
| `rubric` | `list[str] \| None` | `None` | Scale point descriptions (length must match `scale`) |
| `render_as` | `str` | `"buttons"` | `"buttons"` or `"dropdown"` |
| `multi_select` | `bool` | `False` | Allow selecting multiple options (for `OPTIONS` type). Values stored as comma-separated string. |

**Validation rules:**
- `PASS_FAIL` and `OPTIONS` types require `options` to be set
- `SCALE` type requires `scale >= 2`
- If `rubric` is provided, its length must equal `scale`

**Examples:**
```python
# Pass/Fail
DimensionConfig(
    key="safety",
    name="Safety",
    type=DimensionType.PASS_FAIL,
    options=[
        OptionConfig(value="pass", label="Pass", color="#28a745"),
        OptionConfig(value="fail", label="Fail", color="#dc3545"),
    ],
)

# Scale 1-5
DimensionConfig(
    key="quality",
    name="Quality",
    type=DimensionType.SCALE,
    scale=5,
    rubric=["Very Poor", "Poor", "Acceptable", "Good", "Excellent"],
)

# Multiple choice with dropdown
DimensionConfig(
    key="best_variant",
    name="Best Variant",
    type=DimensionType.OPTIONS,
    options=[
        OptionConfig(value="v1", label="Variant 1"),
        OptionConfig(value="v2", label="Variant 2"),
        OptionConfig(value="none", label="None"),
    ],
    render_as="dropdown",
)

# Multi-select (select all that apply)
DimensionConfig(
    key="issues",
    name="Issues Found",
    description="Select all that apply",
    type=DimensionType.OPTIONS,
    multi_select=True,
    options=[
        OptionConfig(value="grammar", label="Grammar"),
        OptionConfig(value="factual", label="Factual Error"),
        OptionConfig(value="offensive", label="Offensive"),
        OptionConfig(value="irrelevant", label="Irrelevant"),
    ],
    required=False,
)

# Free text
DimensionConfig(
    key="comments",
    name="Comments",
    type=DimensionType.TEXT,
    required=False,
)
```

## SeekConfig

Configuration for a seek-to-timestamp button on a variant (for video).

| Field | Type | Description |
|-------|------|-------------|
| `key` | `str` | Data column containing the timestamp in seconds |
| `label` | `str` | Button label (e.g., `"cut_1"`) |

## VariantConfig

Configuration for a single variant in a variant group.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Unique variant identifier (e.g., `"1"`, `"2"`) |
| `label` | `str` | required | Display label (e.g., `"Variant 1"`) |
| `context_key` | `str` | `""` | Data column for variant-specific context |
| `seek_keys` | `list[SeekConfig]` | `[]` | Timestamp seek buttons (video only) |

## VariantGroup

A group of variants sharing the same annotation dimensions. Creates a grid layout with variants as columns and dimensions as rows.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Group identifier (e.g., `"highlights"`) |
| `label` | `str` | required | Display label (e.g., `"Highlights"`) |
| `variants` | `list[VariantConfig]` | `[]` | List of variants |
| `dimensions` | `list[DimensionConfig]` | `[]` | Dimensions applied to each variant |

Dimension keys are auto-generated as `{dim_key}_{variant_id}` (e.g., `quality_1`, `quality_2`).

**Example:**
```python
VariantGroup(
    id="images",
    label="Image Variants",
    variants=[
        VariantConfig(id="1", label="Variant 1"),
        VariantConfig(id="2", label="Variant 2"),
        VariantConfig(id="3", label="Variant 3"),
    ],
    dimensions=[
        DimensionConfig(
            key="relevance",
            name="Relevance",
            type=DimensionType.OPTIONS,
            options=[
                OptionConfig(value="yes", label="Yes", color="#28a745"),
                OptionConfig(value="no", label="No", color="#dc3545"),
            ],
        ),
        DimensionConfig(
            key="quality",
            name="Quality",
            type=DimensionType.SCALE,
            scale=5,
        ),
    ],
)
# Creates keys: relevance_1, relevance_2, relevance_3, quality_1, quality_2, quality_3
```

## AnnotationSchema

Complete schema defining annotation structure.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | `str` | required | Unique schema identifier |
| `name` | `str` | required | Display name |
| `version` | `str` | `"1.0"` | Schema version |
| `dimensions` | `list[DimensionConfig]` | `[]` | Top-level dimensions |
| `general_guidelines` | `list[str]` | `[]` | Guideline bullets shown to annotators |
| `variant_groups` | `list[VariantGroup]` | `[]` | Variant comparison groups |
| `dimension_layout` | `list[list[str]] \| None` | `None` | Layout rows of dimension keys |
| `require_all_dimensions` | `bool` | `True` | Require all dimensions before card is "complete" |
| `allow_notes` | `bool` | `True` | Show notes field |
| `show_context_after_annotation` | `bool` | `True` | Show context after card is complete |

**Methods:**
- `get_dimension(key)` — Get dimension by key (includes variant-expanded)
- `get_dimension_keys()` — All keys in order
- `get_all_expanded_dimensions()` — All dimensions with variant-expanded copies
- `validate()` — Returns list of error strings
