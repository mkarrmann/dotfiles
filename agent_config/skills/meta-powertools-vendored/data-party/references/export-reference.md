---
description: Google Apps Script auto-append setup guide and ExportConfig reference
---

# Export Reference

## Overview

The data party tool supports three export methods:
1. **CSV download** — `exportToCSV()` downloads all items as a CSV file
2. **Copy to clipboard** — Copy tab-separated data for paste into Google Sheets
3. **Google Apps Script auto-append** — One-click append to Google Sheet with duplicate tracking

## How Auto-Append Works (CORS Bypass via Iframe)

Data party pages are hosted on PixelCloud (`lookaside.fbsbx.com`). A regular `fetch()` POST to Google Apps Script (`script.google.com`) is **blocked by CORS** because there is no `Access-Control-Allow-Origin` header on the response.

The solution uses a **hidden iframe form submission**, which is not subject to CORS restrictions:

1. A hidden `<iframe>` is created (or reused) in the DOM
2. A temporary `<form>` is created with `method="POST"`, `action` set to the Apps Script URL, and `target` set to the iframe's name
3. The JSON payload is placed in a hidden `<input name="payload">`
4. `form.submit()` sends the data — the browser POSTs it without CORS checks
5. The Apps Script response loads silently in the hidden iframe
6. The Google Sheet is also opened in a new tab via `window.open()` (called in the same user-gesture context to avoid popup blockers)

**This is why the Apps Script `Code.gs` must handle `e.parameter.payload`** (form params), not just `e.postData.contents` (raw body). See the Code.gs template below.

## Google Apps Script Auto-Append Setup

### Step 1: Create the Google Sheet

Create a new Google Sheet (or use an existing one). Note the Sheet URL.

### Step 2: Add the Apps Script

1. In the Google Sheet, go to **Extensions > Apps Script**
2. Replace the default `Code.gs` with the following (matches D92788518):

```javascript
function doPost(e) {
  try {
    var rawData;
    if (e.parameter && e.parameter.payload) {
      rawData = e.parameter.payload;
    } else if (e.postData && e.postData.contents) {
      rawData = e.postData.contents;
    }
    var data = JSON.parse(rawData);
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = null;
    if (data.sheet_gid) {
      var sheets = ss.getSheets();
      for (var i = 0; i < sheets.length; i++) {
        if (String(sheets[i].getSheetId()) === String(data.sheet_gid)) {
          sheet = sheets[i];
          break;
        }
      }
    }
    if (!sheet) {
      var sheetName = data.sheet_name || 'Sheet1';
      sheet = ss.getSheetByName(sheetName);
      if (!sheet) { sheet = ss.insertSheet(sheetName); }
    }
    if (sheet.getLastRow() === 0) { sheet.appendRow(data.headers); }
    data.rows.forEach(function(row) { sheet.appendRow(row); });
    return ContentService.createTextOutput(
      JSON.stringify({status: 'ok', rows: data.rows.length, sheet: sheet.getName()})
    ).setMimeType(ContentService.MimeType.JSON);
  } catch(err) {
    return ContentService.createTextOutput(
      JSON.stringify({status: 'error', message: err.toString()})
    ).setMimeType(ContentService.MimeType.JSON);
  }
}
```

**Key details:**
- The `e.parameter.payload` check must come first — this is how the iframe form submission sends data. The `e.postData.contents` fallback handles direct API calls.
- `sheet_gid` support: The client-side JS auto-extracts the GID from the Google Sheet URL (e.g., `#gid=1509993033`) and sends it in the payload. This ensures data appends to the correct tab even if the tab was renamed. Falls back to `sheet_name` if no GID is found.
- No lock needed — the script is fast enough that concurrent submissions are handled by Apps Script's built-in queueing.

### Step 3: Deploy as Web App

1. Click **Deploy > New deployment**
2. Select **Web app** as the type
3. Set:
   - **Execute as:** Me
   - **Who has access:** Anyone (within your organization)
4. Click **Deploy**
5. Copy the **Web app URL** — this is your `google_apps_script_url`

**After updating Code.gs:** You must create a **New deployment** (not just save). Each code change requires a new deployment to take effect. The URL changes with each new deployment — update `google_apps_script_url` in your data party script accordingly.

### Step 4: Configure ExportConfig

```python
export=ExportConfig(
    google_apps_script_url="https://script.google.com/macros/s/YOUR_SCRIPT_ID/exec",
    google_sheet_url="https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID/edit",
    sheet_name="Annotations",
),
```

## Full Google Sheet Setup Workflow

When the user wants a Google Sheet for export, do the entire setup in **one streamlined step** — create the sheet, share it, provide the Code.gs template, and prompt for the deployment URL all at once. This avoids multiple back-and-forth requests.

### Streamlined Flow

1. **Create + share the sheet** in a single message (run both commands):

```bash
# Create the sheet
python3 .claude/skills/google-sheets/scripts/google_api.py \
  '{"action": "create_spreadsheet", "title": "[Data Party][YYYY-MM-DD] Title Here"}'

# Share with meta.com domain (writer access) — always do this for new sheets
gdrive permissions share <SHEET_ID> --type domain --domain meta.com --role writer --json
```

The create response contains `data.spreadsheetId` and `data.spreadsheetUrl`. Use the `[Data Party][Date] Title` naming convention. If the script path is not found, refer to `fbcode/claude-templates/components/skills/google-sheets/SKILL.md` for the correct path.

2. **Provide Code.gs + deployment instructions** — immediately after creating and sharing, present the Code.gs template (from the "Google Apps Script Auto-Append Setup" section above) and walk the user through deployment in a single message:
   - Tell the user to go to **Extensions > Apps Script** in the sheet
   - Give them the full `Code.gs` code to paste
   - Tell them to **Deploy > New deployment > Web app** with "Execute as: Me" and "Who has access: Anyone (within your organization)"
   - Ask them to paste the deployment URL back

3. **Wire up ExportConfig** — once the user provides the deployment URL, update `ExportConfig.google_apps_script_url` and `google_sheet_url` in the script. After the first export creates the Annotations tab, get its GID to update the URL:

```bash
python3 .claude/skills/google-sheets/scripts/google_api.py \
  '{"action": "get_spreadsheet", "spreadsheet_id": "SHEET_ID"}'
```

Look for the "Annotations" sheet in the response and note its `sheetId` (GID). Update `google_sheet_url` to include `#gid=GID` so the sheet opens directly to the Annotations tab.

### Handling Existing Sheets

**When the user provides an existing sheet:** Check permissions first with `gdrive permissions list <SHEET_ID> --json`. If the sheet lacks domain-wide meta.com writer access, offer to enable it: "The sheet doesn't have edit access for all Meta employees. Want me to enable it so annotators can use auto-append?"

### Proactive Suggestions

- When the user provides a Google Sheet URL for export but hasn't set up auto-append, proactively offer the full setup: "Want me to also set up one-click auto-append?"
- When you create a new Google Sheet, automatically enable meta.com domain-wide writer access — no need to ask.
- When the user provides an existing Google Sheet, check permissions with `gdrive permissions list` first. If the sheet lacks domain-wide meta.com writer access, offer to enable it: "Want me to enable edit access for everyone at Meta?"

## ExportConfig Field Reference

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `google_sheet_id` | `str` | `""` | Target sheet ID (informational) |
| `google_sheet_url` | `str` | `""` | Direct URL — enables "Open Google Sheet" button |
| `google_apps_script_url` | `str` | `""` | Deployed Apps Script URL — enables auto-append |
| `sheet_name` | `str` | `"Annotations"` | Target sheet/tab name |
| `append_mode` | `bool` | `True` | Append rows vs overwrite |
| `enable_csv_download` | `bool` | `True` | Enable CSV download button |
| `enable_clipboard_copy` | `bool` | `True` | Enable copy-to-clipboard |
| `include_timestamp` | `bool` | `True` | Include timestamp in export |
| `include_annotator` | `bool` | `True` | Include annotator name |
| `custom_export_fields` | `list[str]` | `[]` | Additional fields to export |

## Duplicate Tracking

The auto-append feature tracks which items have been appended using localStorage. This prevents duplicate rows when annotators click "Append" multiple times.

- **Button states:** Blue (ready) → Orange (partial — some already appended) → Green (all done)
- **Only new completions** are sent — items already appended are skipped
- **Tracking resets** when annotations are cleared via "Clear All"

## Troubleshooting

### Data Not Appearing in Sheet
- **Most common cause:** The `Code.gs` only handles `e.postData.contents` but not `e.parameter.payload`. The iframe form submission sends data as form parameters, not raw body. Ensure your Code.gs checks `e.parameter.payload` first (see template above).
- Verify the `sheet_name` matches the tab name in your Google Sheet
- Check that the Apps Script has permission to edit the sheet
- Check the Apps Script execution log: **Extensions > Apps Script > Executions**
- After updating Code.gs, you must **create a new deployment** — just saving the script is not enough

### Apps Script URL Expired or Invalid
- Apps Script deployment URLs change with each new deployment
- If the URL returns errors, re-deploy and update `google_apps_script_url` in your data party script, then rebuild and re-upload

### Duplicate Rows
- Duplicates are tracked per-browser via localStorage
- If annotators use different browsers or clear localStorage, duplicates may occur
- The sheet-side `Code.gs` can be extended with deduplication logic if needed

### Popup Blocked When Opening Sheet
- The Google Sheet opens via `window.open()` in the same click handler as the form submission
- If the browser blocks it, the data is still submitted — check the sheet directly
- Users can allow popups for the PixelCloud domain to fix this
