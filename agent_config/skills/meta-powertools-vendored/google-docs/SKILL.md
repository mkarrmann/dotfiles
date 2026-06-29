---
name: google-docs
description: Create, read, modify, and manage Google Docs documents. Use when the user asks to create a new Google Doc, read document content, edit documents, manage permissions, add or view comments, search for text, analyze document structure, or find/search for Google Docs by keywords, date, or author.
allowed-tools: Bash(meta google.docs get:*), Bash(meta google.docs revisions:*), Bash(meta google.docs revision-content:*), Bash(meta google.docs export:*), Bash(meta google.docs structure:*), Bash(meta google.docs find-index:*), Bash(meta google.docs diff:*), Bash(meta google.docs ghtml:*), Bash(meta google.docs metadata:*), Bash(meta google.docs list:*), Bash(meta google.docs apply:*), Bash(meta --local google.docs export:*), Bash(meta --local google.docs diff:*), Bash(meta --local google.docs apply:*), Bash(meta google.docs.tab list:*), Bash(meta google.docs.comment list:*), Bash(meta google.docs.comment metadata:*), Bash(meta google.docs.comment add:*), Bash(meta google.docs.comment reply:*), Bash(meta google.docs.comment reply-list:*), Bash(meta google.docs.comment reply-metadata:*), Bash(meta google.docs.comment resolve:*), Bash(meta google.docs.comment reopen:*), Bash(meta google.docs.comment update:*), Bash(meta google.docs.comment reply-update:*), Bash(meta google.docs.share list:*), Bash(mkdir /tmp/meta-ghtml*), Write(/tmp/meta-ghtml*), Read(/tmp/meta-ghtml*)
---

# Google Docs

Use `meta google.docs` for all Google Docs operations. **ghtml is the primary format** — it's a simplified HTML subset that round-trips losslessly through Google Docs, preserving formatting, comments, tabs, and metadata. Prefer ghtml for reading, writing, and editing docs. Run `meta google.docs ghtml` for the full tag reference.

Document arguments accept `--id=<DOC_ID>` or `--url=<URL>`. Prefer `--url=` when the user pasted a URL. Use `/tmp/meta-ghtml-*` for temp files. Run `meta google.docs <subcommand> --help` for help on any command.

**File locality:** `meta` runs remotely by default, so `export` writes files to a remote `/tmp/` you can't access. Use `meta --local` or capture stdout instead: `meta google.docs get --id=<DOC> --output=ghtml > /tmp/my-doc.html`. **`--local` needs a www checkout and a warm `hh` server** (run `hh` once first) — see Devserver gotchas below.

## Devserver gotchas

`meta --local` runs through `phps`, which needs a WWW checkout and a warm Hack (`hh`) server. On a box that has a www checkout it works fine once `hh` is warm — **run `hh` once first**; a cold server makes the first `--local` call hang or time out (it's not permanently broken). Only on a box with no www checkout at all is `--local` genuinely unusable, which cascades into how you must pass file paths.

**Gotcha — no WWW checkout → `--local` aborts.** `--local` attaches to `/home/<user>/fbsource/www`; if that's not set up, every `--local` command aborts:

```
Executing in local mode
Using www root: /home/<user>/fbsource/www (PWD is outside any www checkout)
WWW is not enabled on this devserver. Please use WWW On Demand...
Call to undefined function FlibSL\PHP\realpath()
```

This breaks the round-trip `export` → edit → `apply` workflow (which needs `--local` for the `.base` snapshot). Workaround: drop `--local` and use `get` + `replace`, `get` + `insert html`, or `get` + `batch-update`. Capture content via stdout:

```bash
meta google.docs get --id=<DOC> --tab-id=<TAB> --output=ghtml > /tmp/doc.html
```

**Gotcha — under server dispatch, `--file=` requires the `file://` prefix.** Bare paths only work with `--local`. Without it:

```
✗ --file must use the file:// prefix when running under server dispatch (e.g. --file=file:///home/<user>/tmp/doc.html, or --file=file://- for stdin). Bare paths are only supported with `meta --local`.
```

Always pass `--file=file:///absolute/path` or `--file=file://-` for stdin when not using `--local`.

## ghtml

```html
<html>
<head><title>Meeting Notes</title></head>
<body>
<h1>Meeting Notes</h1>
<p>Action items:</p>
<ul>
  <li><b>Alice</b>: update the <span style="color: #c00">dashboard</span></li>
  <li><b>Bob</b>: review <a href="https://example.com">the doc</a></li>
</ul>
<table>
  <tr>
    <td style="background-color: #f0f0f0"><b>Owner</b></td>
    <td style="background-color: #f0f0f0"><b>Status</b></td>
  </tr>
  <tr><td>Alice</td><td><span style="background-color: #b7e1cd"><b>Done</b></span></td></tr>
</table>
</body>
</html>
```

Markdown is also supported (`--markdown`, `--as-markdown`, `.insert markdown`) but is lossy — it cannot represent colors, highlights, font changes, or precise table formatting. Block fidelity also differs by command: `create --markdown` and `replace --markdown` use a separate converter that **flattens blockquotes (`>`) to plain paragraphs and drops `---` rules**. The ghtml-routed markdown commands (`insert markdown`, `append markdown`, `replace-all`/`bulk-find-replace --as=markdown`) render `---` as a horizontal rule but still emit `>` as literal text. For callouts/blockquotes, write `<aside>` in ghtml. Prefer ghtml for anything non-trivial.

### Callouts (`<aside>`) and horizontal rules (`<hr>`)

Google Docs has no native callout or horizontal-rule primitive, so the CLI stands them in with paragraph styling:

- `<aside>…</aside>` renders as a **shaded callout** — light amber fill with a left accent bar. Use it for blockquotes/callouts. (A bare `<aside>` is the callout; `<aside hidden …>` is the read-only comment construct from the ghtml reference, not a callout.)
- `<hr>` renders as a **horizontal line** — an empty paragraph carrying a bottom border (the Docs API has no `insertHorizontalRule`). `<hr data-type="page">` inserts a page break and `<hr data-type="section-…">` a section break instead.

**Fidelity caveat — these are write-only visual mappings.** They render correctly in the document but are not reconstructed on export: `get` / `export --output=ghtml` re-emit a written callout or rule as an ordinary `<p>`, so grepping the exported ghtml for `<aside>` / `<hr>` returns 0 **even though they render in the doc**. Verify them visually (open the doc, or `export -f pdf`), not by an export round-trip. By contrast, `data-col-widths` and cell `background-color` *do* round-trip — that is ghtml's durable advantage over markdown.

### Table column widths

Standard HTML width hints are **ignored**: `<colgroup>`/`<col>`, the `width="..."` attribute, and `style="width:..."` on `<td>`/`<th>` all get normalized to equal-width columns (Google Docs distributes width evenly by default). This is the usual cause of "all my columns came out the same width."

To control widths, put a **`data-col-widths`** attribute on the `<table>` tag — comma-separated **point** values, one per column. This is the only mechanism that works on `insert`/`replace`/`apply`, and it round-trips: `get`/`export` re-emits `data-col-widths` for fixed-width columns (an all-evenly-distributed table emits nothing).

```html
<table data-col-widths="55,170,32,60,120,290">
  <tr><th>SEV</th><th>Title</th><th>Lvl</th><th>Date</th><th>Status</th><th>Reason</th></tr>
  <tr><td>S123</td><td>...</td><td>3</td><td>May 12</td><td>Closed</td><td>...</td></tr>
</table>
```

- The number of values must match the column count.
- Values are absolute points and must fit the page content width. On a **paged** (default) doc the usable width is ~**468 pt** — keep the total ≤ ~466 pt or the rightmost column(s) get clipped/cut off. **Pageless** docs are wide, so a total around **700 pt** works well — give narrow columns (ID, level, date) small values and free-text columns (title, reason) large ones. After inserting a wide table, re-fetch and verify the right column isn't clipped.
- If the column count differs between tables in the same doc, set `data-col-widths` per table.

### Auto-linking

The CLI automatically preprocesses ghtml before sending to Google Docs:

- **Meta references**: D12345678, T12345678, S/SEV, P12345678, N12345678 → hyperlinked to internalfb.com.
- **Person chips**: `@username` → Google Docs person chip.
- **File paths**: `fbcode/`, `www/`, `xplat/`, `fbandroid/`, `fbobjc/`, `whatsapp/`, `arvr/` paths → hyperlinked to CodeHub. Paths must end with a recognized source file extension, optionally with `:LINE` suffix.

Auto-linking is skipped inside `<a>`, `<code>`, `<pre>`, and `<aside>` tags. Write file paths as plain text (not in `<code>` tags) so they auto-link to CodeHub. Avoid wrapping every code term in `<code>` in narrative prose — reserve it for standalone code blocks.

If ghtml has validation errors, `meta google.docs apply` reports them with line numbers and suggestions.

## Reading Documents

Always read with `--output=ghtml`. The default is markdown, which is lossy.

```bash
meta google.docs get --id=<DOC_ID> --output=ghtml
meta google.docs get --url=<URL> --output=ghtml --describe-images   # AI image descriptions
```

`--output=ghtml`/`html` returns a **standalone HTML document** — the doc title and file/revision metadata in `<head>`, with multi-tab docs split into `<article>` sections. Add `--include-stylesheet` to embed the doc's own named-style CSS, giving a faithful, self-contained render you can preview or hand to an HTML viewer (the `export -f ghtml` round-trip path still emits a bare fragment):

```bash
meta google.docs get --id=<DOC_ID> --output=html --include-stylesheet
```

Use `--tab-id=<ID>` for a specific tab.

**Gotcha — search-result titles can be stale.** `knowledge_filtered_search` returns the title from the last index update, not the live state — a recently renamed doc still shows its old name. Before quoting a doc title in user-visible output (Workplace posts, briefs, summaries), re-fetch via `meta google.docs get --id=<DOC_ID> --output=ghtml` and read the first `<h1>`. The Drive filename and body `<h1>` can drift independently.

## Round-Trip Editing

The primary workflow for non-trivial edits: export ghtml, edit locally, preview, apply.

```bash
meta --local google.docs export --id=<DOC> -f ghtml                  # Export (default: /tmp/meta-ghtml-{ID}.html, local)
# Edit the file with Write tool
meta --local google.docs diff --id=<DOC>                              # Preview changes
meta --local google.docs apply --id=<DOC>                             # Apply changes
```

`apply` requires the `.base` snapshot that `export -f ghtml` creates. Always export first. Use `--dest=<path>` / `--from=<path>` to override the default session file.

**Smart chips:** `get --output=ghtml` *does* export chips (read them fine): person → `<span data-person data-person-email=…>`, date → `<time datetime=…>`, file/doc link → `<a data-rich-link …>`, dropdown → `<span data-immutable="dropdown">value</span>`. (If a chip looks empty, cross-check with `meta google.docs structure` or `--output=raw-json`.) Creating chips from ghtml is partial:

| Chip | Create from ghtml? | How |
|------|--------------------|-----|
| Person | ✅ | `@username` auto-link, or `<span data-person data-person-email="x@meta.com" data-person-name="Name">Name</span>` |
| Date | ✅ | `<time datetime="2026-06-02T12:00:00Z" data-date-format="DATE_FORMAT_MONTH_DAY_YEAR_ABBREVIATED">Jun 2, 2026</time>` |
| File / doc rich-link | ✅ Workspace/Drive URLs only | `<a data-rich-link href="https://docs.google.com/...">Label</a>` creates a real chip on `insert`/`replace`/`apply` (emits `insertRichLink`; pass only the url — title/mimeType resolve server-side). **Only Google Workspace/Drive URLs are accepted** (Docs/Sheets/Slides/Forms/Drive); any other url (external sites, even YouTube, internalfb links) returns `400 insertRichLink: The URL is invalid` and **fails the entire `batchUpdate`** atomically — nothing is applied. For non-Workspace links use a plain `<a href>` (no `data-rich-link`), which stays an ordinary hyperlink. |
| Dropdown / status | ❌ | `data-immutable` — read-only, not creatable (no `insertDropdown` request exists) |

**Person chips via raw `batch-update` (when not using ghtml):** emit an `insertPerson` request — a real Docs API v1 capability that produces a genuine, clickable person chip (NOT a `mailto:`/profile hyperlink, which only yields styled text with no hovercard).

```json
[{"insertPerson":{"location":{"index":17,"tabId":"t.0"},"personProperties":{"email":"username@meta.com"}}}]
```

- **Pass `email` ONLY — never `name`.** The API resolves the display name from the email server-side. Including a name fails the whole batch: `Invalid requests[0].insertPerson: Insert person requests should not specify a name.` (The ghtml `data-person-name` attribute above is fine — the CLI strips it before emitting `insertPerson`.)
- For multi-tab docs, put `tabId` in `location`. To rebuild a chip line, `deleteContentRange` the old text, then in one ordered batch: insertText label → insertPerson → insertText separator → insertPerson (each `index` computed against the doc as prior requests apply).

For a single text replacement, skip the round-trip: `meta google.docs edit --id=<DOC> --find="old" --replace="new"`.

## Style & template doc

Before creating a new doc, check for a **Google Docs template / reference doc** in the user's CLAUDE.md — look for a "Google Docs" section naming a template URL, first in the project/repo CLAUDE.md, then in personal `~/.claude/CLAUDE.md`. If one is set, create the doc by **copying that template** instead of `create`-from-scratch:

```bash
meta google.docs copy    --id=<TEMPLATE_DOC_ID> --name="<New Title>"               # inherits named styles + pageless
meta google.docs replace --id=<NEW_ID> --tab-id=t.0 --file=file:///tmp/body.html   # fill the body
```

With a template configured, emit body ghtml as plain semantic markup (headings, paragraphs, lists) with no inline `<span>` fonts so the named styles format it. Add explicit styling only for what named styles can't carry — table column widths, exact image sizes, and code blocks.

## Creating Documents

For rich documents, create empty then push ghtml content:
```bash
meta google.docs create --title="My Doc"
meta --local google.docs apply --id=<NEW_ID> --from=report.html
```

For simple docs, `--body` works with plain text or markdown:
```bash
meta google.docs create --title="My Doc" --body="Hello world"
meta google.docs create --title="Report" --body="$(cat notes.md)" --markdown
```

## Inserting Content

Prefer `.insert html` — it accepts ghtml so you get formatting, links, tables, and colors. Use `.insert text` only for unformatted strings. All insert commands support `--tab-id=<ID>`, `--index=<N>`, `--end` for positioning.

```bash
meta google.docs.insert html  --id=<DOC> --html="<b>Bold</b> and <a href='...'>link</a>"   # Inline ghtml
meta google.docs.insert html  --id=<DOC> --file=file:///tmp/content.html --end             # ghtml from file (file:// required without --local)
cat fragment.html | meta google.docs.insert html --id=<DOC> --file=file://- --end          # ghtml from stdin
meta google.docs.insert text  --id=<DOC> --text="Plain string" --end                        # Plain text (no formatting)
meta google.docs.insert image --id=<DOC> --uri=<IMAGE_URI> --index=1                        # Image
```

## Find and Replace

```bash
meta google.docs replace-all     --id=<DOC> --find="old" --replace="new"                    # Plain text (all occurrences)
meta google.docs replace-all     --id=<DOC> --find="old" --replace="<b>new</b>" --as=html   # Rich HTML replacement
meta google.docs replace-all     --id=<DOC> --find="old" --replace="**new**" --as=markdown  # Rich Markdown replacement
meta google.docs bulk-find-replace --id=<DOC> --input=file:///tmp/replacements.json          # Multiple patterns
meta google.docs bulk-find-replace --id=<DOC> --input=file:///tmp/pairs.json --as=html       # Multiple rich HTML replacements
meta google.docs bulk-find-replace --id=<DOC> --input=file:///tmp/pairs.json --as=markdown   # Multiple rich Markdown replacements
```

**Gotcha — find/replace only matches visible text, not hyperlink targets.** Both `bulk-find-replace` and `replace-all` cannot update the URL behind a `[text](url)` link (verified: searching for a URL fragment that exists only as a link target returns `occurrencesChanged: 0`). To fix a wrong link target, regenerate the doc, use `apply` with a ghtml diff, or use `batch-update` with an `updateTextStyle` request that sets `link.url` over the link's index range.

**Gotcha — under server dispatch, `bulk-find-replace --input=` requires `file://` for local files.** Use `--input=file:///absolute/path/replacements.json` for files or `--input=file://-` for stdin. Bare paths only work with `--local`.

## Formatting

Prefer the ghtml round-trip (`export` → edit HTML → `apply`) — it's easier to apply bold, headings, colors directly in HTML. For targeted formatting without a round-trip, see `meta google.docs.format text --help`.

### Code blocks

`<pre>` (or `<pre><code>`) converts to a gray single-cell-table code block. The native Google Docs "Code block" building block (syntax-highlighted Roboto Mono) is **not** creatable via ghtml/API — use `<pre>`, or have the user insert the native block manually.

## Tabs

```bash
meta google.docs.tab list   --id=<DOC>                         # List all tabs
meta google.docs.tab add    --id=<DOC> --title="Notes"         # Create a tab
```

**Gotcha — per-tab ghtml/markdown export can bleed sibling-tab content.** When exporting a single tab, the rendered output may include content from other tabs as artifacts. If you see unexpected elements (e.g. dropdown chips, text blocks) that seem out of place, verify with `meta google.docs get --id=<DOC> --tab-id=<TAB> --output=raw-json` before reporting them — raw-json is authoritative per-tab. Note: raw-json output is a JSON array of structural elements, not a single object.

## Comments

**Reading:** prefer `meta google.docs get --output=ghtml` (comments are inline), or list them with `meta google.docs.comment list --id=<DOC>` (add `--output=json` to see `anchor_id` / `status` / `orphaned` / `ranges`; add `--orphaned` to list only orphaned "ghost" comments). Use `meta google.docs.comment metadata --id=<DOC> --comment-id=<CID>` for a single thread, and `reply-list` / `reply-metadata` for replies.

**Adding (anchored):** `meta google.docs.comment add` anchors the comment to document text via `--quoted-text` (case-sensitive). If the text appears once it anchors there; if it appears multiple times the CLI lists the matches with their ranges so you re-run with `--occurrence=N` (or pass explicit `--start-index`/`--end-index`). Use `--tab-id=t.N` for text in a non-first tab, and `--assignee-email=<email>` to file it as an action item.

**Comment formatting (`--as`).** Google Docs comments are plain-text threads that render only a tiny set of *inline* markers — `*bold*`, `_italics_`, `-strikethrough-` — and **not markdown**. To spare you that quirk, `--content` is parsed as **markdown by default** and converted to those markers: write `**bold**`, `*italic*`, `~~strike~~` (or pass `--as=html` and use `<b>`/`<i>`/`<s>`) and it renders correctly. There is **no support for lists, headings, tables, links, or code blocks** in a comment — they are flattened to plain text and the command prints a warning. Pass `--as=text` to send `--content` byte-for-byte (use this for code-heavy comments so `*`/`_`/`-` stay literal). This applies to `add`, `reply`, `update`, and `reply-update`.

```bash
# Anchor a comment to specific text
meta google.docs.comment add --id=<DOC> --quoted-text="the exact text" --content="Please review"
# Disambiguate when the text repeats
meta google.docs.comment add --id=<DOC> --quoted-text="TODO" --occurrence=2 --content="this one"
# Text in another tab, filed as an action item
meta google.docs.comment add --id=<DOC> --tab-id=t.0 --quoted-text="Q3 plan" --assignee-email=user@meta.com --content="please own this"

# Replies and thread state
meta google.docs.comment reply        --id=<DOC> --comment-id=<CID> --content="I agree"
meta google.docs.comment resolve      --id=<DOC> --comment-id=<CID> [--note="why it's resolved"]
meta google.docs.comment reopen       --id=<DOC> --comment-id=<CID>
meta google.docs.comment update       --id=<DOC> --comment-id=<CID> --content="edited body"
meta google.docs.comment reply-update --id=<DOC> --comment-id=<CID> --reply-id=<RID> --content="edited reply"

# Destructive (require --yes-i-am-sure --token=push)
meta google.docs.comment delete       --id=<DOC> --comment-id=<CID> --yes-i-am-sure --token=push
meta google.docs.comment reply-delete --id=<DOC> --comment-id=<CID> --reply-id=<RID> --yes-i-am-sure --token=push
```

**Reconciling orphaned ("ghost") comments.** When an edit deletes, fully replaces, or moves a comment's anchored text, the Docs API drops the anchor: the thread stays `status: OPEN` but resolves to empty `ranges`, so it disappears from the Docs UI while lingering unresolved in the API. The CLI reports these as `orphaned: true`. Don't leave them behind — they are invisible to humans yet still open in the data.

- **Before** deleting or replacing a span, list the comments and check whether any anchor overlaps the text you're about to remove, so you don't silently strand a thread.
- **After** an edit, find ghosts with `list --orphaned`, then for each one either re-anchor it (`comment add` against the text's new location) if the discussion still applies, or close it with context via `resolve --note`.

```bash
# Find comments whose anchored text was deleted (still OPEN, empty ranges)
meta google.docs.comment list --id=<DOC> --orphaned --output=json
# Re-anchor if the discussion still applies somewhere in the doc
meta google.docs.comment add --id=<DOC> --quoted-text="the text's new location" --content="<carry over the discussion>"
# Otherwise resolve with an explanatory note so the thread isn't a silent ghost
meta google.docs.comment resolve --id=<DOC> --comment-id=<CID> --note="Anchored text was removed during the rewrite"
```

**`quoted_text` can go stale.** The `quoted_text` (the Docs `plainTextQuote`) is captured when the comment is created and is never updated. After the anchored text is edited in place the anchor relocates correctly, but `quoted_text` keeps the OLD wording — treat it as the original quote, not the current text. To detect drift, compare `quoted_text` against the live text at the thread's `ranges` (read it via `meta google.docs get --output=ghtml`); if they diverge, the discussion may be stale, so surface it for review rather than trusting the quote. Don't try to "fix" it by editing the comment: the quote is immutable on the thread, and recreating the comment would lose its replies.

**Anchoring is body-scoped.** `--quoted-text` resolves only within the document body (including tables, the table of contents, and tabs). Text in headers, footers, or footnotes can't be anchored (the report's UC-060 / UC-061) — there is no header/footnote anchor path through the Docs comment API, and a quote that spans a paragraph boundary can't be anchored either. Comment on the nearest body text instead, or leave an unanchored comment (capability off).

## Document Structure

```bash
meta google.docs structure --id=<DOC>
# [0-1] SECTION_BREAK
# [1-66] TITLE: "Document Title"
# [234-242] HEADING_1: "Problem"
```

Use `[startIndex-endIndex]` ranges with `meta google.docs batch-update` to apply formatting to specific sections.

## Permissions

```bash
meta google.docs.share list   --id=<DOC>                                              # List access
meta google.docs.share grant  --id=<DOC> --emails=user@meta.com --role=writer          # Share
meta google.docs.share grant  --id=<DOC> --unixnames=alice,bob --role=writer           # By unixname
meta google.docs.share remove --id=<DOC> --email=user@meta.com --yes-i-am-sure         # Remove
```

**For a single text replacement, skip the round-trip entirely** and use `meta google.docs edit --id=<DOC> --find="old" --replace="new"` (faster than export + edit + apply for one-line changes).

**Important:** `apply` requires the `.base` snapshot that `export -f ghtml` creates. If you skip step 1 or modify the doc out-of-band between export and apply, `apply` will refuse — re-export to refresh the base snapshot.

**Warning — multi-tab table edits:** `gdocs apply` can misalign table cell insertions in multi-tab documents, placing text in the wrong cell. For table cell edits in multi-tab docs, use `gdocs batch-update` with the exact `startIndex` instead. See `python-script.md` for the reliable workflow.

### Export

```bash
meta --local google.docs export --id=<DOC> -f pdf --dest=report.pdf
meta --local google.docs export --id=<DOC> -f docx --dest=report.docx
meta google.docs revisions --id=<DOC>
meta google.docs revision-content --id=<DOC> --revision-id=<REV_ID>
```

## Images

Direct image commands upload local file paths automatically. In ghtml, local file paths in `<img>` tags are likewise uploaded automatically (on the `--local` `apply` path), which resolves local image references during the ghtml diff flow. Otherwise, use `meta google.docs upload-image --file=<path>` to get a public, Google-fetchable URL (30-day TTL).

- **`<img width>`/`height` are ignored on the `insert`/`replace` path** — the image lands at a fixed default (~the page content width, 468pt). For an **exact** size, insert via `batch-update` `insertInlineImage` with an explicit `objectSize` in points (e.g. **504pt = 7in**):

```bash
meta google.docs batch-update --id=<DOC> --requests='[{"insertInlineImage":{"uri":"<HTTPS_URL>","location":{"index":<N>},"objectSize":{"width":{"magnitude":504,"unit":"PT"},"height":{"magnitude":288,"unit":"PT"}}}}]'
```

- **The image URL must be fetchable by Google's servers** — internal URLs (e.g. `pxl.cl`) fail with "problem retrieving the image"; `upload-image` produces a usable `googleusercontent` URL.
- **`upload-image` reads the file on the remote dispatcher**, which can't see your local `/tmp` and has no stdin mode — so on a devserver without a www checkout (where `--local` is unavailable), uploading a locally-generated image is blocked. Use an already-public URL, or run `upload-image` where the file is reachable.
- Inline-object sizes aren't shown by `get`/`structure`; read them from `--output=raw-json` (`inlineObjects[*].inlineObjectProperties.embeddedObject.size`).

## Replace (full-body)

`meta google.docs replace --id=<DOC> --tab-id=t.0 --file=file:///tmp/file.html` replaces the entire tab body. Always pass `--tab-id` (use `.tab list` to find IDs). Person chips **in the replacement ghtml ARE created fine** — the `<span data-person>` form works on `replace`. The caveat is only about **pre-existing** chips already in the target tab: a full-body replace overwrites them, so any chip you want to keep must be present in your replacement ghtml (or re-inserted via `batch-update` afterward).

## Sensitive Mode (DSS-4)

Pass `--sensitive-mode` to any `meta google.docs` command when operating on DSS-4 documents. After creating a new DSS-4 doc, label it:

```bash
meta google.drive.label set --file-id=<FILE_ID> \
  --label-id=AQa4cse7HGPBfDwtVJ3IkUQDwsyQ57E5n6LRNNEbbFcb \
  --field-id=2E1C40F709 \
  --value=11B31F4625
```

## Searching for Documents

To find docs by topic, author, or date, use `mcp__plugin_meta_mux__knowledge_filtered_search` with `doc_types: ["GOOGLE_DOCUMENT"]`.

## Other Google Workspace CLIs

`meta google.sheets`, `meta google.slides`, `meta google.drive`, `meta google.chat`, `meta google.gmail`, `meta google.calendar`.
