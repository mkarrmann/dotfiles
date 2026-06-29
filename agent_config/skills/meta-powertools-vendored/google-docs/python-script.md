# Python Script (google_api.py)

Works in any environment where `jf` and `python3` are available. Enforces **permitted authors validation** — rejects documents edited by non-Meta employees, protecting against prompt injection. Use `--no-author-check` to bypass when needed.

```bash
python3 /home/mkarrmann/.claude/agent-market/skills/google-docs/google_api.py '{"action": "ACTION", ...}'
```

For large or complex JSON payloads, write the JSON to a file first with the Write tool (no bash approval needed), then execute with a single bash command using the `@` prefix:
```bash
python3 /home/mkarrmann/.claude/agent-market/skills/google-docs/google_api.py @/tmp/gdocs_ops.json
```

Bypass author validation:
```bash
python3 /home/mkarrmann/.claude/agent-market/skills/google-docs/google_api.py --no-author-check '{"action": "ACTION", ...}'
```

## Available Actions

`get_document`, `get_document_body`, `get_document_tabs`, `get_document_formatting`, `get_document_raw`, `create_document`, `create_tab`, `insert_text`, `insert_markdown`, `insert_html`, `format_text`, `apply_heading`, `set_paragraph_style`, `update_table_cell_style`, `update_table_cells`, `set_column_widths`, `insert_table`, `create_bullet_list`, `insert_bullet_list`, `delete_document`, `get_comments`, `add_comment`, `get_heading_ids`, `reply_to_comment`, `resolve_comment`, `delete_comment`, `get_permissions`, `share_document`, `unshare_document`, `export_document`, `copy_doc`, `find_replace`, `get_revisions`, `get_revision_content`, `insert_inline_image`, `move_document`, `replace_document_content`, `batch_update`

## Key Parameters

- `document_id`: Google Doc ID or full URL (auto-extracted from URLs)
- `title`: Title for new documents
- `initial_content`: Initial text content for `create_document`
- `folder_id`: Google Drive folder ID
- `tab_id`: Tab ID for targeting a specific tab
- `parent_tab_id`: Parent tab ID for creating a child (sub-tab) under an existing tab (for `create_tab`)
- `markdown_text` / `text`: Content for `insert_markdown`
- `html_text` / `text`: Content for `insert_html`
- `format_from_markdown`: If true, interpret `initial_content` as markdown
- `format_from_html`: If true, interpret `initial_content` as HTML
- `comments`: JSON STRING array of comments to add
- `comment_id`: Comment ID for reply/resolve/delete operations (get from `get_comments`)
- `reply_content`: Text content for replying to a comment
- `export_format`: html, plain_text, pdf, docx, rtf, odt, epub
- `link`: URL to make text a clickable hyperlink (for `format_text`); the API auto-applies blue color and underline
- `font_family`: Font name, e.g. "Roboto Mono", "Arial" (for `format_text`)
- `strikethrough`: Boolean to apply/remove strikethrough (for `format_text`)
- `clear_other_fields`: If true, include all standard fields in the field mask so pre-existing formatting not explicitly set is cleared (for `format_text` and `set_paragraph_style`)
- `alignment`: Paragraph alignment — START, CENTER, END, JUSTIFIED (for `set_paragraph_style`)
- `named_style`: Paragraph style — TITLE, SUBTITLE, HEADING_1–HEADING_6, NORMAL_TEXT (for `set_paragraph_style`)
- `line_spacing`: Line spacing as percentage, e.g. 115 = 1.15× (for `set_paragraph_style`)
- `space_above` / `space_below`: Spacing before/after paragraph in points (for `set_paragraph_style`)
- `table_start_index`: Start index of the table element (for `update_table_cell_style`, `update_table_cells`, `set_column_widths`)
- `row_index` / `column_index`: Cell position in table (for `update_table_cell_style`)
- `background_color`: RGB color dict, e.g. `{"red": 0.2, "green": 0.6, "blue": 1.0}` (for `update_table_cell_style`)
- `column_widths`: Array of `{"column_index": N, "width_pt": M}` (for `set_column_widths`)
- `cell_updates`: Array of `{"row": R, "col": C, "text": "...", "background_color": {"red": R, "green": G, "blue": B}}` (for `update_table_cells`; both `text` and `background_color` are optional)
- `email_addresses`: Comma-separated emails (for `share_document` and `unshare_document`)
- `role`: Permission level - reader, commenter, writer (for `share_document`)
- `revision_id`: Revision ID for `get_revision_content` (get from `get_revisions`)
- `find_text`: Text to find for replacement (for `find_replace`)
- `replace_text`: Text to replace matches with (for `find_replace`)
- `image_uri`: Publicly accessible URI of the image (for `insert_inline_image`)
- `target_folder_id`: Destination folder ID (for `move_document`)
- `format`: Content format for `replace_document_content` - `"markdown"` (default), `"html"`, or `"text"`
- `content`: The replacement content (for `replace_document_content`; also accepts `markdown_text` or `text`)

## Examples

### Create a Document
```bash
python3 google_api.py '{"action": "create_document", "title": "Meeting Notes", "initial_content": "Agenda:\n1. Review\n2. Goals"}'
python3 google_api.py '{"action": "create_document", "title": "Report", "initial_content": "# Overview\n\n**Bold** text", "format_from_markdown": true}'
python3 google_api.py '{"action": "create_document", "title": "Doc", "folder_id": "1abc123xyz"}'
```

### Read a Document
```bash
python3 google_api.py '{"action": "get_document", "document_id": "1abc...xyz"}'
python3 google_api.py '{"action": "get_document_body", "document_id": "1abc...xyz"}'
python3 google_api.py '{"action": "get_document_body", "document_id": "1abc...xyz", "tab_id": "t.abc123"}'
python3 google_api.py '{"action": "get_document_tabs", "document_id": "1abc...xyz"}'
```

### Insert Content
```bash
python3 google_api.py '{"action": "insert_text", "document_id": "1abc...xyz", "text": "Hello", "index": 1}'
python3 google_api.py '{"action": "insert_markdown", "document_id": "1abc...xyz", "markdown_text": "# Title\n\n**bold**"}'
python3 google_api.py '{"action": "insert_html", "document_id": "1abc...xyz", "html_text": "<h1>Title</h1><p><b>OK</b></p>"}'
```

### Find and Replace
```bash
python3 google_api.py '{"action": "find_replace", "document_id": "1abc...xyz", "find_text": "2025", "replace_text": "2026"}'
```

### Replace All Content
```bash
python3 google_api.py '{"action": "replace_document_content", "document_id": "1abc...xyz", "content": "# New Content\n\nReplaced.", "format": "markdown"}'
```

### Comments
```bash
python3 google_api.py '{"action": "get_comments", "document_id": "1abc...xyz"}'
python3 google_api.py '{"action": "add_comment", "document_id": "1abc...xyz", "comments": "[{\"comment\": \"Great work!\"}]"}'
python3 google_api.py '{"action": "reply_to_comment", "document_id": "1abc...xyz", "comment_id": "AAAABcd...", "reply_content": "Thanks!"}'
python3 google_api.py '{"action": "resolve_comment", "document_id": "1abc...xyz", "comment_id": "AAAABcd..."}'
```

### Permissions
```bash
python3 google_api.py '{"action": "get_permissions", "document_id": "1abc...xyz"}'
python3 google_api.py '{"action": "share_document", "document_id": "1abc...xyz", "email_addresses": "alice@fb.com", "role": "writer"}'
python3 google_api.py '{"action": "unshare_document", "document_id": "1abc...xyz", "email_addresses": "user@meta.com"}'
```

### Other
```bash
python3 google_api.py '{"action": "copy_doc", "document_id": "1abc...xyz", "title": "Copy"}'
python3 google_api.py '{"action": "move_document", "document_id": "1abc...xyz", "target_folder_id": "1folder..."}'
python3 google_api.py '{"action": "export_document", "document_id": "1abc...xyz", "export_format": "pdf"}'
python3 google_api.py '{"action": "get_revisions", "document_id": "1abc...xyz"}'
python3 google_api.py '{"action": "delete_document", "document_id": "1abc...xyz"}'
```

### Formatting
```bash
python3 google_api.py '{"action": "format_text", "document_id": "1abc...xyz", "start_index": 10, "end_index": 25, "link": "https://example.com"}'
python3 google_api.py '{"action": "apply_heading", "document_id": "1abc...xyz", "start_index": 1, "end_index": 20, "heading_level": 1}'
```

## Detailed Operation Examples

### search_documents

**Tool:** `mcp__plugin_meta_mux__knowledge_filtered_search`

Searches for Google Docs across the organization. Use this when you don't have a document URL and need to find documents by topic, author, or date.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `keywords` | string | Yes | Plain keywords to search for |
| `natural_language_query` | string | Yes | Full question for semantic search |
| `doc_types` | array | Yes | Set to `["GOOGLE_DOCUMENT"]` |
| `authors` | array | No | Employee IDs to filter by author |
| `start_creation_time` | string | No | Filter docs created after this date (ISO format) |
| `end_creation_time` | string | No | Filter docs created before this date |
| `start_last_update_time` | string | No | Filter docs updated after this date |
| `end_last_update_time` | string | No | Filter docs updated before this date |

**Example - Find recent meeting notes:**
```json
{
  "keywords": "meeting notes",
  "natural_language_query": "What are the recent meeting notes from the past week?",
  "doc_types": ["GOOGLE_DOCUMENT"],
  "start_creation_time": "2026-01-27"
}
```

**Example - Find docs by author:**
```json
{
  "keywords": "project planning",
  "natural_language_query": "What project planning documents exist?",
  "doc_types": ["GOOGLE_DOCUMENT"],
  "authors": ["123456"]
}
```

### create_document

Creates a new Google Doc with specified title and optional content. Optionally creates the document in a specific Google Drive folder. Can apply markdown or HTML formatting to initial content.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `title` | string | Yes | Title of the new document |
| `initial_content` | string | No | Initial text content to add to the document |
| `folder_id` | string | No | Google Drive folder ID to create the document in |
| `format_from_markdown` | bool | No | If true, interpret initial_content as markdown and apply formatting |
| `format_from_html` | bool | No | If true, interpret initial_content as HTML and apply formatting |

**Basic example:**
```json
{
  "action": "create_document",
  "title": "Meeting Notes - Q4 Planning",
  "initial_content": "Agenda:\n1. Review Q3 results\n2. Q4 goals\n3. Action items"
}
```

**Create document with markdown formatting:**
```json
{
  "action": "create_document",
  "title": "Project Overview",
  "initial_content": "# Project Overview\n\nThis project has **two goals**:\n\n1. Improve performance\n2. Add new features\n\n## Details\n\nSee [the wiki](https://example.com) for more info.",
  "format_from_markdown": true
}
```

**Create document with HTML formatting (colored text, tables):**
```json
{
  "action": "create_document",
  "title": "Status Report",
  "initial_content": "<h1>Status Report</h1><p>Status: <span style=\"color: green\"><b>On Track</b></span></p><table><tr><th>Task</th><th>Status</th></tr><tr><td>Design</td><td>Complete</td></tr></table>",
  "format_from_html": true
}
```

**Create document in a specific folder:**
```json
{
  "action": "create_document",
  "title": "Project Spec",
  "initial_content": "# Overview\n\nThis document describes...",
  "folder_id": "1abc123xyz"
}
```

The `folder_id` can be extracted from a Google Drive folder URL: `https://drive.google.com/drive/folders/1abc123xyz` → `1abc123xyz`

### get_document_tabs

Lists all tabs in a document with their IDs and titles.

```json
{
  "action": "get_document_tabs",
  "document_id": "1abc...xyz"
}
```

Returns a list of tabs, each with `tabId`, `title`, and `index`.

### create_tab

Creates a new tab in a document with an optional custom name and optional parent tab.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `title` | string | No | Title for the new tab (if omitted, Google Docs assigns a default name) |
| `parent_tab_id` | string | No | Tab ID of the parent tab. If provided, the new tab is created as a child (sub-tab) of that parent. |

**Create tab with custom name:**
```json
{
  "action": "create_tab",
  "document_id": "1abc...xyz",
  "title": "Meeting Notes"
}
```

**Create a child tab under a parent:**
```json
{
  "action": "create_tab",
  "document_id": "1abc...xyz",
  "title": "Week of 04/10/2026",
  "parent_tab_id": "t.parentid123"
}
```

**Create tab with default name:**
```json
{
  "action": "create_tab",
  "document_id": "1abc...xyz"
}
```

Returns the new tab's ID which can be used with `tab_id` parameter in other operations.

### get_document_body

Gets all text segments with their start/end indices. **Critical for batch operations** - call this first to get indices before using `batch_update`.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `tab_id` | string | No | Tab ID to get content from a specific tab |

**Get body from default tab:**
```json
{
  "action": "get_document_body",
  "document_id": "1abc...xyz"
}
```

**Get body from a specific tab:**
```json
{
  "action": "get_document_body",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123"
}
```

Returns JSON with segments containing `start`, `end`, and `text` for each text run.

### add_comment

Adds one or more comments to the document.

```json
{
  "action": "add_comment",
  "document_id": "1abc...xyz",
  "comments": "[{\"comment\": \"Great work!\"}, {\"comment\": \"Needs revision\"}]"
}
```

### get_comments

Retrieves all comments from the document. **Important**: Returns comment IDs which are needed for reply/resolve/delete operations.

```json
{
  "action": "get_comments",
  "document_id": "1abc...xyz"
}
```


### get_heading_ids

Gets all heading IDs and their text from a document. Returns a list of `{heading_id, text, start_index, level}` objects. Heading IDs use the `h.*` format and can be used as `anchor_id` values in `add_comment` to attach comments to specific headings.

```json
{
  "action": "get_heading_ids",
  "document_id": "1abc...xyz"
}
```

### reply_to_comment

Replies to an existing comment. First use `get_comments` to retrieve comment IDs.

```json
{
  "action": "reply_to_comment",
  "document_id": "1abc...xyz",
  "comment_id": "AAAABcd...",
  "reply_content": "I agree with this suggestion!"
}
```

### resolve_comment

Marks a comment as resolved. First use `get_comments` to retrieve comment IDs.

```json
{
  "action": "resolve_comment",
  "document_id": "1abc...xyz",
  "comment_id": "AAAABcd..."
}
```

### delete_comment

Deletes a comment from the document. First use `get_comments` to retrieve comment IDs.

```json
{
  "action": "delete_comment",
  "document_id": "1abc...xyz",
  "comment_id": "AAAABcd..."
}
```

### export_document

Exports a Google Doc to various formats.

```json
{
  "action": "export_document",
  "document_id": "1abc...xyz",
  "export_format": "pdf"
}
```

Supported formats: `html`, `plain_text`, `pdf`, `docx`, `rtf`, `odt`, `epub`

### share_document

Shares a document with specified users.

```json
{
  "action": "share_document",
  "document_id": "1abc...xyz",
  "email_addresses": "alice@fb.com, bob@fb.com",
  "role": "writer",
  "email_message": "Please review this document"
}
```

### unshare_document

Removes access from specific users.

```json
{
  "action": "unshare_document",
  "document_id": "1abc...xyz",
  "email_addresses": "user@meta.com, other@meta.com"
}
```

### get_revisions

Lists all revisions (edit history) of the document.

```json
{
  "action": "get_revisions",
  "document_id": "1abc...xyz"
}
```

Returns a list of revisions with ID, modified time, and last modifying user.

### get_revision_content

Gets the content of a specific revision. First use `get_revisions` to retrieve revision IDs.

```json
{
  "action": "get_revision_content",
  "document_id": "1abc...xyz",
  "revision_id": "123"
}
```

### copy_doc

Creates a full copy of a Google Doc, including all content, formatting, and tabs. Optionally specify a title and/or destination folder.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL of the source document |
| `title` | string | No | Title for the copy (defaults to "Copy of <original title>") |
| `folder_id` | string | No | Google Drive folder ID to place the copy in |

**Basic copy:**
```json
{
  "action": "copy_doc",
  "document_id": "1abc...xyz"
}
```

**Copy with custom title:**
```json
{
  "action": "copy_doc",
  "document_id": "1abc...xyz",
  "title": "My Document - Copy"
}
```

**Copy into a specific folder:**
```json
{
  "action": "copy_doc",
  "document_id": "1abc...xyz",
  "title": "Archived Copy",
  "folder_id": "1folder123xyz"
}
```

### find_replace

Finds and replaces all occurrences of a text string across all tabs in a document. The search is case-sensitive.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `find_text` | string | Yes | The text to search for |
| `replace_text` | string | Yes | The text to replace matches with |

**Example:**
```json
{
  "action": "find_replace",
  "document_id": "1abc...xyz",
  "find_text": "2025",
  "replace_text": "2026"
}
```

Returns the total number of occurrences replaced across all tabs.

### insert_inline_image

Inserts an image at a specific index in the document.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `image_uri` | string | Yes | Publicly accessible URI of the image (PNG, JPEG, GIF; under 50MB) |
| `index` | integer | Yes | The index where the image should be inserted |
| `width` | integer | No | Width in points (default: 400) |
| `height` | integer | No | Height in points (maintains aspect ratio if not specified) |
| `tab_id` | string | No | Tab ID to insert image into a specific tab |

**Example:**
```json
{
  "action": "insert_inline_image",
  "document_id": "1abc...xyz",
  "image_uri": "https://example.com/image.png",
  "index": 1,
  "width": 300
}
```

### move_document

Moves a document to a different folder in Google Drive.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `target_folder_id` | string | Yes | The destination folder ID. Use "root" for My Drive root |

**Example:**
```json
{
  "action": "move_document",
  "document_id": "1abc...xyz",
  "target_folder_id": "1XYZ_folder_id_here"
}
```

### replace_document_content

Replaces all content in an existing document. Clears the current content and inserts new content. Preserves the document URL, sharing settings, and comments. This is useful for syncing local files to Google Docs.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `content` | string | Yes | The content to insert (also accepts `markdown_text` or `text`) |
| `format` | string | No | Content format - `"markdown"` (default), `"html"`, or `"text"` |
| `tab_id` | string | No | Tab ID to replace content in a specific tab |

**Replace with markdown content (default):**
```json
{
  "action": "replace_document_content",
  "document_id": "1abc...xyz",
  "content": "# Updated Project Overview\n\nThis document has been **completely replaced** with new content.\n\n## Section 1\n\n- Item one\n- Item two"
}
```

**Replace with plain text:**
```json
{
  "action": "replace_document_content",
  "document_id": "1abc...xyz",
  "content": "This replaces everything with plain text.",
  "format": "text"
}
```

**Replace content in a specific tab:**
```json
{
  "action": "replace_document_content",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123",
  "content": "## Tab Content\n\nReplaced content for this tab only."
}
```

**Replace with HTML (preserves rich formatting):**
```json
{
  "action": "replace_document_content",
  "document_id": "1abc...xyz",
  "content": "<h1>Project Overview</h1><p>This is <b>bold</b> and <span style=\"color: #ff0000\">red</span>.</p><table><tr><th>Name</th><th>Status</th></tr><tr><td style=\"background-color: #d4edda\">Alpha</td><td>Done</td></tr></table>",
  "format": "html"
}
```

### insert_markdown

Inserts markdown text and converts it to native Google Docs formatting. This is the recommended way to add formatted content to documents — it handles headings, lists, tables, code blocks, and inline formatting in a single call.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `markdown_text` | string | Yes | Markdown content to insert (also accepts `text`) |
| `index` | integer | No | Insertion index (default: 1, start of document) |
| `tab_id` | string | No | Tab ID to insert into a specific tab |

**Supported markdown features:**

- `# Heading 1` through `###### Heading 6` — native heading paragraph styles
- `**bold**` and `__bold__`
- `*italic*` and `_italic_`
- `~~strikethrough~~`
- `` `inline code` `` — Courier New with gray background
- Fenced code blocks (triple-backtick delimiters) — bordered box, Courier New, gray shading, single-spaced
- `| table | rows |` with separator — native Google Docs tables with bold/gray header row, pinned headers
- `- bullet lists` and `* bullet lists` with nesting via indentation
- `1. numbered lists` with nesting via indentation
- `> block quotes` — left border bar, indented, italic
- `---` horizontal rules — bottom border divider line
- `[link text](url)` — clickable hyperlinks
- `\_` `\*` backslash escapes — literal characters without triggering formatting
- Double-backtick code spans for including literal backticks in inline code

**Example — insert a formatted document:**
```json
{
  "action": "insert_markdown",
  "document_id": "1abc...xyz",
  "markdown_text": "# Project Overview\n\nThis project has **two goals**:\n\n1. Improve `insert_markdown` formatting\n2. Add table support\n\n| Feature | Status |\n|---------|--------|\n| Tables | Done |\n| Code blocks | Done |\n\n> Note: All existing APIs remain unchanged.\n\n---\n\nSee [the wiki](https://example.com) for details."
}
```

**Example — insert into a specific tab:**
```json
{
  "action": "insert_markdown",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123",
  "markdown_text": "## Tab-specific content\n\n- Item one\n- Item two",
  "index": 1
}
```

### insert_html

Inserts HTML content and converts it to native Google Docs formatting. Use this when you need colored text, underlines, or other formatting not available in markdown.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `html_text` | string | Yes | HTML content to insert (also accepts `text`) |
| `index` | integer | No | Insertion index (default: 1, start of document) |
| `tab_id` | string | No | Tab ID to insert into a specific tab |

**Supported HTML elements:**

**Text formatting:**
- `<b>`, `<strong>` — bold text
- `<i>`, `<em>` — italic text
- `<u>` — underlined text
- `<s>`, `<strike>`, `<del>` — strikethrough text
- `<code>` — monospace font (Courier New)
- `<sup>` — superscript
- `<sub>` — subscript
- `<a href="url">` — clickable hyperlinks

**Colored text:**
- `<span style="color: #ff0000">` — foreground color (hex)
- `<span style="color: red">` — foreground color (named)
- `<span style="color: rgb(255, 0, 0)">` — foreground color (rgb)
- `<span style="background-color: yellow">` — background/highlight color
- `<font color="red">` — foreground color (legacy support)

**Block elements:**
- `<h1>` through `<h6>` — heading styles
- `<p>` — paragraphs
- `<br>` — line breaks

**Lists:**
- `<ul><li>` — bullet lists
- `<ol><li>` — numbered lists

**Tables:**
- `<table><tr><td>` — native Google Docs tables

**Example — insert formatted HTML:**
```json
{
  "action": "insert_html",
  "document_id": "1abc...xyz",
  "html_text": "<h1>Status Report</h1><p>This project is <span style=\"color: green\"><b>on track</b></span>.</p><ul><li>Task 1: Complete</li><li>Task 2: In progress</li></ul>"
}
```

**Example — colored text and highlighting:**
```json
{
  "action": "insert_html",
  "document_id": "1abc...xyz",
  "html_text": "<p><span style=\"color: red\">Warning:</span> This action is <span style=\"background-color: yellow\">irreversible</span>.</p>"
}
```

**Example — table with header:**
```json
{
  "action": "insert_html",
  "document_id": "1abc...xyz",
  "html_text": "<table><tr><th>Name</th><th>Status</th></tr><tr><td>Alice</td><td><span style=\"color: green\">Active</span></td></tr><tr><td>Bob</td><td><span style=\"color: red\">Inactive</span></td></tr></table>"
}
```

**Example — insert into a specific tab:**
```json
{
  "action": "insert_html",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123",
  "html_text": "<h2>Tab Content</h2><p>This is <u>underlined</u> and <i>italic</i>.</p>",
  "index": 1
}
```

### set_paragraph_style

Set paragraph-level formatting including alignment, named style, spacing, indentation, and shading.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `start_index` | int | Yes | Start of the paragraph range |
| `end_index` | int | Yes | End of the paragraph range |
| `alignment` | string | No | START, CENTER, END, or JUSTIFIED |
| `named_style` | string | No | TITLE, SUBTITLE, HEADING_1–HEADING_6, NORMAL_TEXT |
| `line_spacing` | float | No | Line spacing as percentage (e.g. 115 = 1.15×) |
| `space_above` | float | No | Space before paragraph in points |
| `space_below` | float | No | Space after paragraph in points |
| `indent_start` | float | No | Left indent in points |
| `indent_end` | float | No | Right indent in points |
| `indent_first_line` | float | No | First line indent in points |
| `shading_color` | object | No | Paragraph background: `{"red": R, "green": G, "blue": B}` (0.0–1.0) |
| `tab_id` | string | No | Tab ID to target |
| `clear_other_fields` | bool | No | If true, all standard paragraph style fields are included in the field mask so pre-existing styles not explicitly set are cleared |

**Apply TITLE style:**
```json
{
  "action": "set_paragraph_style",
  "document_id": "1abc...xyz",
  "start_index": 1,
  "end_index": 15,
  "named_style": "TITLE"
}
```

**Set spacing and alignment:**
```json
{
  "action": "set_paragraph_style",
  "document_id": "1abc...xyz",
  "start_index": 1,
  "end_index": 50,
  "alignment": "CENTER",
  "line_spacing": 115,
  "space_below": 10
}
```

### update_table_cell_style

Set table cell background color, borders, padding, and content alignment.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `table_start_index` | int | Yes | Start index of the table element |
| `row_index` | int | Yes | Row position (0-based) |
| `column_index` | int | Yes | Column position (0-based) |
| `row_span` | int | No | Number of rows to apply to (default 1) |
| `column_span` | int | No | Number of columns to apply to (default 1) |
| `background_color` | object | No | Cell background: `{"red": R, "green": G, "blue": B}` (0.0–1.0) |
| `border_color` | object | No | Border color for all four sides |
| `border_width` | float | No | Border width in points |
| `padding` | float | No | Cell padding in points (all sides) |
| `content_alignment` | string | No | TOP, MIDDLE, or BOTTOM |
| `tab_id` | string | No | Tab ID to target |

**Set cell background color:**
```json
{
  "action": "update_table_cell_style",
  "document_id": "1abc...xyz",
  "table_start_index": 5,
  "row_index": 0,
  "column_index": 0,
  "background_color": {"red": 0.2, "green": 0.6, "blue": 1.0}
}
```

### update_table_cells

Bulk insert text and apply background colors to multiple table cells in a single call. This is much more efficient than manually finding cell indices, constructing `insertText` requests, and applying styles separately.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `table_start_index` | int | Yes | Start index of the table element |
| `cell_updates` | array | Yes | Array of cell update objects (see below) |
| `tab_id` | string | No | Tab ID to target |

Each item in `cell_updates`:
- `row` (int): Zero-based row index
- `col` (int): Zero-based column index
- `text` (string, optional): Text to insert into the cell
- `background_color` (object, optional): RGB color dict, e.g. `{"red": 0.9, "green": 0.8, "blue": 0.8}`

**Example — set text and colors for multiple cells:**
```json
{
  "action": "update_table_cells",
  "document_id": "1abc...xyz",
  "table_start_index": 5226,
  "cell_updates": [
    {"row": 1, "col": 0, "text": "Risk item", "background_color": {"red": 0.957, "green": 0.8, "blue": 0.8}},
    {"row": 1, "col": 1, "text": "Details here"},
    {"row": 2, "col": 0, "text": "On track", "background_color": {"red": 0.85, "green": 0.92, "blue": 0.83}}
  ]
}
```

### set_column_widths

Set fixed column widths for a table.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `table_start_index` | int | Yes | Start index of the table element |
| `column_widths` | array | Yes | Array of `{"column_index": N, "width_pt": M}` |
| `tab_id` | string | No | Tab ID to target |

```json
{
  "action": "set_column_widths",
  "document_id": "1abc...xyz",
  "table_start_index": 5,
  "column_widths": [
    {"column_index": 0, "width_pt": 100},
    {"column_index": 1, "width_pt": 250},
    {"column_index": 2, "width_pt": 150}
  ]
}
```

### get_document_formatting

Get a structured formatting summary of all paragraphs, text runs, and tables. Use this to inspect the exact formatting applied to a document for replication or analysis.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `tab_id` | string | No | Tab ID to target |

```json
{
  "action": "get_document_formatting",
  "document_id": "1abc...xyz"
}
```

Returns `elements` array where each entry is either a paragraph or table:
- **Paragraphs**: `named_style`, `alignment`, `line_spacing`, `space_above`, `space_below`, `indent_start/end/first_line`, `shading_color`, `list_id`, `nesting_level`, and `text_runs` (each with `text`, `bold`, `italic`, `underline`, `strikethrough`, `font_family`, `font_size_pt`, `foreground_color`, `background_color`, `link`)
- **Tables**: `rows`, `columns`, `cells` (each with `row`, `column`, `background_color`, `paragraphs`)

### get_document_raw

Get the raw Google Docs API body content and lists map. Returns full structural detail including run-level styles, bullet list IDs/nesting, table cell styles, section breaks, and horizontal rules. Use this when you need to understand or replicate complex formatting that `get_document_formatting` simplifies away.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `document_id` | string | Yes | Document ID or URL |
| `tab_id` | string | No | Tab ID to target |

```json
{
  "action": "get_document_raw",
  "document_id": "1abc...xyz"
}
```

Returns:
- **`content`**: The raw body content array from the Google Docs API (paragraphs, tables, section breaks, etc. with full style detail)
- **`lists`**: The document's lists map defining list types (bullet vs numbered) and glyph styles per nesting level

## Common Workflows

### Working with Links

You can turn existing text into clickable hyperlinks using `format_text` with the `link` parameter. The API automatically applies blue color and underline styling. `format_text` also supports `font_family` (e.g. "Roboto Mono") and `strikethrough` (boolean).

**Apply a link to existing text:**
```json
{
  "action": "format_text",
  "document_id": "1abc...xyz",
  "start_index": 10,
  "end_index": 25,
  "link": "https://example.com"
}
```

**Insert-then-link pattern** (insert new text and make it a link):
1. Use `get_document_body` to find the current end index
2. Insert text with `insert_text`
3. Apply the link with `format_text` using the range of the inserted text

**Use markdown links** with `insert_markdown` for a simpler approach:
```json
{
  "action": "insert_markdown",
  "document_id": "1abc...xyz",
  "markdown_text": "Visit [Google](https://google.com) for more info.",
  "index": 1
}
```

The `[text](url)` syntax is automatically converted to a clickable hyperlink with proper formatting.

**Remove a link** from text by sending `format_text` with an empty link via `batch_update`:
```json
{
  "action": "batch_update",
  "document_id": "1abc...xyz",
  "requests": [{
    "updateTextStyle": {
      "range": {"startIndex": 10, "endIndex": 25},
      "textStyle": {},
      "fields": "link"
    }
  }]
}
```

### Working with Document Tabs

Google Docs supports multiple tabs within a single document. Use tabs to organize content into separate sections.

**List all tabs in a document:**
```json
{
  "action": "get_document_tabs",
  "document_id": "1abc...xyz"
}
```

**Create a new tab and add content:**
1. Create the tab with `create_tab` and note the returned `tabId`
2. Use `insert_text` or `insert_markdown` with the `tab_id` parameter to add content

```json
{
  "action": "insert_text",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123",
  "text": "Content for this tab",
  "index": 1
}
```

**Read content from a specific tab:**
```json
{
  "action": "get_document_body",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123"
}
```

**Edit content in a specific tab via `batch_update`:**

Include `tab_id` both in the top-level params and inside each request's `location`:
```json
{
  "action": "batch_update",
  "document_id": "1abc...xyz",
  "tab_id": "t.abc123",
  "requests": [{
    "insertText": {
      "location": {"index": 42, "tabId": "t.abc123"},
      "text": "Inserted into tab"
    }
  }]
}
```

**Empty table cells:** An "empty" cell still contains a `\n` character.
To fill it, use `insertText` at the newline's `startIndex` — `find_replace`
cannot match empty cells.

**Finding the exact `startIndex` for a cell in a multi-tab doc:**

The `gdocs apply` (ghtml round-trip) workflow can misalign table cell edits
in multi-tab documents, inserting text into the wrong cell. Use `gdocs batch-update`
with the exact `startIndex` instead:

1. Save raw JSON: `gdocs get <DOC> --tab-id <TAB_ID> --raw-json > /tmp/raw.json`
2. Parse it to find the target cell's `startIndex`:
   ```python
   import json
   with open("/tmp/raw.json") as f:
       data = json.load(f)
   for tab in data["tabs"]:
       if tab["tabProperties"]["tabId"] == "<TAB_ID>":
           for elem in tab["documentTab"]["body"]["content"]:
               if "table" in elem:
                   for row in elem["table"]["tableRows"]:
                       for cell in row["tableCells"]:
                           for c in cell["content"]:
                               if "paragraph" in c:
                                   for el in c["paragraph"]["elements"]:
                                       if "textRun" in el:
                                           print(el["startIndex"], repr(el["textRun"]["content"]))
   ```
3. Use `gdocs batch-update` with the exact index:
   ```bash
   gdocs batch-update <DOC> --data '[{"insertText": {"location": {"index": <INDEX>, "tabId": "<TAB_ID>"}, "text": "value"}}]'
   ```

### Search Then Operate

Use this pattern when you need to find documents first, then work with them:

1. Search for documents using Metamate (`mcp__plugin_meta_mux__knowledge_filtered_search`) with `doc_types: ["GOOGLE_DOCUMENT"]`
2. Extract document URLs/IDs from the search results
3. Use other actions (`get_document_body`, `insert_text`, etc.) on found documents

### Highlight Text at Known Positions
1. Call `get_document_body` to get all text segments with indices
2. Search the returned text locally to find the target text and its start/end indices
3. Use `format_text` with `background_color` to highlight the range, or use `batch_update` with multiple `updateTextStyle` requests for multiple ranges

### Batch Edit with Precise Positioning
1. Call `get_document_body` first to get all text indices
2. Plan your insertions using the returned indices
3. Use multiple `insert_text` calls (from highest index to lowest to avoid index shifting), or use `batch_update` with multiple `insertText` requests

### Create and Share a Document
1. Create the document with `create_document`
2. Share it with collaborators using `share_document`
3. Return the document URL to the user

### Editing Table Cells
1. Use `get_document_formatting` or `get_document_raw` to find the table's `startIndex`
2. Call `update_table_cells` with `table_start_index` and `cell_updates` array
3. Each cell update can include `text` (content to insert) and/or `background_color` (RGB dict)

Example:
```json
{"action": "update_table_cells", "document_id": "...", "table_start_index": 5226,
 "cell_updates": [
   {"row": 1, "col": 1, "text": "Content here", "background_color": {"red": 0.85, "green": 0.92, "blue": 0.83}},
   {"row": 1, "col": 2, "text": "More content"}
 ]}
```

## Editing Documents with Complex Formatting

When editing documents that have rich formatting (colored cells, custom fonts, styled paragraphs, etc.), use an HTML-based workflow to preserve formatting automatically. This is far more efficient than reading formatting metadata and re-applying it field by field.

### Preferred Approach: Export → Edit HTML → Replace

1. **Export as HTML**: `export_document` with `export_format: "html"` to get the full formatted content
2. **Edit the HTML**: Make changes directly in the HTML string — add/remove/modify paragraphs, table rows, cell colors, text styles, etc. The HTML preserves all formatting as inline styles.
3. **Replace with HTML**: `replace_document_content` with `format: "html"` to push the edited content back

This approach preserves table cell colors, bold/italic, font changes, colored text, and other formatting that would otherwise require dozens of individual API calls.

**Example — editing a formatted document:**
```json
// Step 1: Export
{"action": "export_document", "document_id": "1abc...xyz", "export_format": "html"}

// Step 2: Edit the returned HTML (add a row, change a color, fix text, etc.)

// Step 3: Replace
{"action": "replace_document_content", "document_id": "1abc...xyz", "content": "<edited HTML>", "format": "html"}
```

### When to Use Other Approaches

- **Small text-only edits**: Use `find_replace` or targeted `insert_text` / `delete_text` + `insert_text`
- **Appending content**: Use `insert_markdown` or `insert_html` at the end index
- **Inspecting formatting details**: Use `get_document_formatting` to see exact styles (named_style, font_family, colors, spacing) for debugging or analysis
- **Fine-grained style changes**: Use `format_text`, `set_paragraph_style`, `update_table_cell_style` when you need to adjust specific ranges without touching content

### HTML Formatting Reference

When constructing or editing HTML for `insert_html` or `replace_document_content` with `format: "html"`:

- **Table cell backgrounds**: `<td style="background-color: #ff0000">Red cell</td>`
- **Header cells (auto-bold)**: `<th>Header</th>` — automatically rendered in bold
- **Multi-paragraph cells**: Use `<br>` inside `<td>` for line breaks within cells
- **bgcolor attribute**: `<td bgcolor="#00ff00">Green</td>` as an alternative to style
- **Colored text**: `<span style="color: #0066cc">Blue text</span>`
- **Font family**: Not directly supported in HTML insertion — use `format_text` with `font_family` after insertion
- **Background highlight**: `<span style="background-color: yellow">Highlighted</span>`

### Raw `batch_update` Examples

For formatting not covered by dedicated actions, use `batch_update` with raw requests:

**Set row height:**
```json
{
  "action": "batch_update",
  "document_id": "1abc...xyz",
  "requests": [{
    "updateTableRowStyle": {
      "tableStartLocation": {"index": 5},
      "rowIndex": 0,
      "tableRowStyle": {"minRowHeight": {"magnitude": 50, "unit": "PT"}},
      "fields": "minRowHeight"
    }
  }]
}
```

**Merge table cells:**
```json
{
  "action": "batch_update",
  "document_id": "1abc...xyz",
  "requests": [{
    "mergeTableCells": {
      "tableRange": {
        "tableCellLocation": {
          "tableStartLocation": {"index": 5},
          "rowIndex": 0,
          "columnIndex": 0
        },
        "rowSpan": 1,
        "columnSpan": 2
      }
    }
  }]
}
```

**Update page margins:**
```json
{
  "action": "batch_update",
  "document_id": "1abc...xyz",
  "requests": [{
    "updateDocumentStyle": {
      "documentStyle": {
        "marginTop": {"magnitude": 36, "unit": "PT"},
        "marginBottom": {"magnitude": 36, "unit": "PT"},
        "marginLeft": {"magnitude": 36, "unit": "PT"},
        "marginRight": {"magnitude": 36, "unit": "PT"}
      },
      "fields": "marginTop,marginBottom,marginLeft,marginRight"
    }
  }]
}
```

## Tips

1. **Search First**: If you don't have a document URL, use Metamate search with `doc_types: ["GOOGLE_DOCUMENT"]` to find relevant docs
2. **Workflow Tip**: For multiple edits, call `get_document_body` first to get all text indices, then use `insert_text` or `batch_update` with the returned indices
3. **Finding Text**: Call `get_document_body` once and search the returned text locally to find positions — there is no `find_text` action
4. **Heading IDs**: Use `get_heading_ids` to get heading anchor IDs, which can be used with `add_comment` to attach comments to specific headings
5. **JSON String Parameters**: The `comments` parameter is a JSON STRING containing an array - use escaped quotes inside
6. **URL Handling**: Both full URLs and document IDs are accepted
7. **Working with Tabs**: Use `get_document_tabs` to list all tabs and get their IDs, then pass `tab_id` to other operations to target a specific tab
8. **Markdown Formatting**: Use `insert_markdown` for rich content — it handles headings, tables, code blocks, lists, block quotes, horizontal rules, links, bold, italic, strikethrough, and inline code in a single call
9. **HTML Formatting**: Use `insert_html` when you need colored text, underlines, background highlights, or other formatting not available in markdown — supports hex/rgb/named colors, tables, lists, and all standard HTML text formatting
10. **Replacing Content**: Use `replace_document_content` to fully replace a document's content while keeping the same URL and sharing settings — supports `"markdown"` (default), `"html"`, or `"text"` format
11. **Editing Formatted Docs**: For documents with complex formatting (colored tables, custom fonts, etc.), use the export-HTML → edit → replace-with-HTML workflow to preserve formatting automatically
12. **Large HTML Insertions**: When inserting large HTML content (e.g., copying a full document to a new tab via `export_document` + `insert_html`), split the HTML at `<table>` boundaries and insert each segment separately. Insert segments in reverse order at index 1 so they stack correctly. The `batch_update` function automatically chunks large request lists into batches of 150 to stay within Google Docs API limits. Remove embedded base64 `<img>` data URIs before insertion as they cannot be processed by `insert_html`.
13. **Replicating Formatting**: When replicating formatting from one location to another, use `clear_other_fields: true` with `format_text` or `set_paragraph_style` to ensure pre-existing formatting on the target range is replaced rather than merged
14. **Raw Document Inspection**: Use `get_document_raw` when you need full API-level detail (run-level styles, bullet list IDs/nesting, table cell styles) that `get_document_formatting` simplifies away
15. **Horizontal Rules**: Horizontal rules cannot be inserted via the Google Docs API's `insertHorizontalRule` — `insert_markdown` uses a border-bottom paragraph style as a workaround
16. **List Nesting**: List nesting uses tab characters before `createParagraphBullets` to establish nesting levels — this is more reliable than indentation-based approaches
17. **Bulk Table Edits**: Use `update_table_cells` instead of manually finding cell indices and building `insertText` requests — it handles text insertion and cell coloring in a single call
18. **File-based Input**: For multi-step operations, write the JSON to a file first with the Write tool, then execute with a single bash command: `python3 google_api.py @/tmp/ops.json`
19. **Auto-sorted Inserts**: `batch_update` auto-sorts pure `insertText` batches by descending index — no need to manually reverse-sort
20. **Author Check Bypass**: Use `--no-author-check` flag to bypass author validation for documents with non-approved editors
21. **Multi-action Mode**: Use `"actions": [...]` with `insert_text`, `delete_text`, `format_text`, `apply_heading`, `set_paragraph_style`, and `update_table_cell_style` to batch multiple operations into a single API call

## Large Payloads

When the JSON argument exceeds ~90KB (e.g., large document content, big batch updates, long markdown/HTML inserts), write it to a temp file and use the `@` prefix:

```bash
# Write params to temp file, then invoke with @
python3 ~/.claude/skills/google-docs/google_api.py @/tmp/google_api_input.json
```

Always clean up the temp file afterward.

## Troubleshooting

- **"Permission denied"**: Ensure you have edit access to the document
- **"Not a Meta employee"**: Documents can only be accessed if all authors are trusted Meta employees
- **"document_id is required"**: Provide either a document ID or full URL (except for `create_document`)
