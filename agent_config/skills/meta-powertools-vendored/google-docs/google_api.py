#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
"""
Google Docs API script using Jellyfish GraphQL.

Usage:
    python3 google_api.py '{"action": "get_document", "document_id": "..."}'
    python3 google_api.py '{"action": "copy_doc", "document_id": "...", "title": "Copy Title"}'
    python3 google_api.py '{"action": "find_replace", "document_id": "...", "find_text": "old", "replace_text": "new"}'
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

# Global sandbox host for ondemand environments
# Can be set via SANDBOX_HOST environment variable or --sandbox-host CLI param
_SANDBOX_HOST: str | None = None


def set_sandbox_host(host: str | None) -> None:
    """Set the sandbox host for ondemand environments."""
    global _SANDBOX_HOST
    _SANDBOX_HOST = host


def get_sandbox_host() -> str | None:
    """Get the sandbox host from global var or environment."""
    return _SANDBOX_HOST or os.environ.get("SANDBOX_HOST")


def get_dcat() -> str:
    """Get a DCAT token for Google API Proxy authentication."""
    clicat_cmd = "corp_clicat" if shutil.which("corp_clicat") else "clicat"
    result = subprocess.run(
        [
            clicat_cmd,
            "create",
            "--verifier_type",
            "OTHER",
            "--verifier_id",
            "google_api_proxy",
            "--token_timeout_seconds",
            "60",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to get dcat: {result.stderr}")
    return result.stdout.strip()


def extract_document_id(url_or_id: str) -> str:
    """Extract document ID from a Google Docs URL or return as-is if already an ID."""
    patterns = [
        r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)",
        r"drive\.google\.com/file/d/([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url_or_id)
        if match:
            return match.group(1)
    return url_or_id


def _utf16_len(text: str) -> int:
    """Return the length of text in UTF-16 code units (Google Docs index units)."""
    return len(text.encode("utf-16-le")) // 2


def _utf16_offset(text: str, codepoint_offset: int) -> int:
    """Convert a code-point offset within text to a UTF-16 offset."""
    return _utf16_len(text[:codepoint_offset])


_MAX_INLINE_VARIABLES_BYTES = 100_000  # ~98KB, margin below 128KB MAX_ARG_STRLEN


def _run_jf_graphql_cmd(
    query: str,
    variables: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    """Run jf graphql, using a temp file for large variable payloads."""
    cmd = ["jf"]
    sandbox_host = get_sandbox_host()
    if sandbox_host:
        cmd.extend(["--sandbox-host", sandbox_host])

    variables_json = json.dumps(variables)
    tmpfile_path = None
    try:
        if len(variables_json.encode("utf-8")) >= _MAX_INLINE_VARIABLES_BYTES:
            fd, tmpfile_path = tempfile.mkstemp(suffix=".json", prefix="jf_vars_")
            with os.fdopen(fd, "w") as f:
                f.write(variables_json)
            cmd.extend(["graphql", "--query", query, "--variables-file", tmpfile_path])
        else:
            cmd.extend(["graphql", "--query", query, "--variables", variables_json])
        # Add timeout to prevent hanging (60 seconds should be sufficient for Google API calls)
        return subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", timeout=60
        )
    finally:
        if tmpfile_path is not None:
            try:
                os.unlink(tmpfile_path)
            except OSError:
                pass


def call_google_api(
    method: str,
    endpoint: str,
    payload: dict[str, Any] | None = None,
    auth_token_class: str = "GoogleDriveAuthTokenAsSelf",
) -> dict[str, Any]:
    """Call Google API via Jellyfish GraphQL."""
    dcat = get_dcat()

    query = """mutation GoogleApiProxy($dcat: SensitiveString, $endpoint: String!, $method: String!, $payload: String, $authTokenClass: String!) {
        xfb_google_api_proxy(input: {
            auth_token_class: $authTokenClass,
            method: $method,
            endpoint: $endpoint,
            payload: $payload,
            enforce_permitted_authors: true,
            follow_redirects: true,
            max_response_size: 104857600,
            dcat: $dcat
        }) {
            success
            http_status
            content_type
            response_body
            error_message
        }
    }"""

    variables = {
        "dcat": dcat,
        "endpoint": endpoint,
        "method": method,
        "payload": json.dumps(payload) if payload else None,
        "authTokenClass": auth_token_class,
    }

    try:
        result = _run_jf_graphql_cmd(query, variables)
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Google API request timed out after 60 seconds. The service may be slow or unavailable.",
        }

    if result.returncode != 0:
        return {"success": False, "error": result.stderr}

    try:
        response = json.loads(result.stdout)
        # Handle both wrapped and unwrapped responses
        data = response.get("xfb_google_api_proxy") or response.get("data", {}).get(
            "xfb_google_api_proxy", {}
        )
        if data.get("success"):
            body = data.get("response_body")
            if body:
                try:
                    return {"success": True, "data": json.loads(body)}
                except json.JSONDecodeError:
                    return {"success": True, "data": body}
            return {"success": True, "data": None}
        return {
            "success": False,
            "error": data.get("error_message") or f"API call failed: {result.stdout}",
            "http_status": data.get("http_status"),
        }
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Failed to parse response: {e}"}


def get_document(document_id: str, tab_id: str | None = None) -> dict[str, Any]:
    """Get document content.

    Args:
        document_id: The document ID or URL.
        tab_id: Optional tab ID to get content from a specific tab.
            If not provided, returns the first (default) tab's content.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    if tab_id:
        endpoint += "?includeTabsContent=true"
    return call_google_api("GET", endpoint)


def get_document_tabs(document_id: str) -> dict[str, Any]:
    """Get list of all tabs in a document.

    Args:
        document_id: The document ID or URL.

    Returns:
        A dict with success status and list of tabs with their IDs and titles.
    """
    doc_id = extract_document_id(document_id)
    endpoint = (
        f"https://docs.googleapis.com/v1/documents/{doc_id}?includeTabsContent=true"
    )
    result = call_google_api("GET", endpoint)

    if not result.get("success"):
        return result

    doc = result.get("data", {})
    tabs = doc.get("tabs", [])

    tab_list = []

    def _collect_tabs(tabs):
        for tab in tabs:
            tab_props = tab.get("tabProperties", {})
            tab_list.append(
                {
                    "tabId": tab_props.get("tabId"),
                    "title": tab_props.get("title", ""),
                    "index": tab_props.get("index", 0),
                }
            )
            _collect_tabs(tab.get("childTabs", []))

    _collect_tabs(tabs)

    return {
        "success": True,
        "data": {
            "documentId": doc_id,
            "title": doc.get("title", ""),
            "tabs": tab_list,
        },
    }


def create_tab(
    document_id: str,
    title: str | None = None,
    parent_tab_id: str | None = None,
) -> dict[str, Any]:
    """Create a new tab in a document.

    Args:
        document_id: The document ID or URL.
        title: Optional title for the new tab. If not provided, Google Docs
            will assign a default name.
        parent_tab_id: Optional tab ID of the parent tab. If provided, the new
            tab is created as a child (sub-tab) of that parent.

    Returns:
        A dict with success status and the new tab's ID.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"

    tab_properties: dict[str, Any] = {}
    if title:
        tab_properties["title"] = title
    if parent_tab_id:
        tab_properties["parentTabId"] = parent_tab_id

    request: dict[str, Any] = {"addDocumentTab": {}}
    if tab_properties:
        request["addDocumentTab"]["tabProperties"] = tab_properties

    payload = {"requests": [request]}
    result = call_google_api("POST", endpoint, payload)

    if not result.get("success"):
        return result

    # Extract the new tab ID from the response
    # The tabId is nested under tabProperties in the response
    replies = result.get("data", {}).get("replies", [])
    if replies and "addDocumentTab" in replies[0]:
        tab_props = replies[0]["addDocumentTab"].get("tabProperties", {})
        new_tab_id = tab_props.get("tabId")
        return {
            "success": True,
            "data": {
                "documentId": doc_id,
                "tabId": new_tab_id,
                "title": title,
            },
        }

    return result


def _get_element_text(element: dict) -> str:
    """Extract display text from a paragraph element (textRun, person chip, etc.)."""
    text_run = element.get("textRun")
    if text_run:
        return text_run.get("content", "")
    person = element.get("person")
    if person:
        name = person.get("personProperties", {}).get("name", "")
        return name if name else person.get("personId", "")
    date_element = element.get("dateElement")
    if date_element:
        return date_element.get("dateElementProperties", {}).get("displayText", "")
    rich_link = element.get("richLink")
    if rich_link:
        props = rich_link.get("richLinkProperties", {})
        return props.get("title", props.get("uri", ""))
    return ""


def get_document_body(document_id: str, tab_id: str | None = None) -> dict[str, Any]:
    """Get document body with text segments and indices.

    Args:
        document_id: The document ID or URL.
        tab_id: Optional tab ID to get content from a specific tab.
            If not provided, returns the first (default) tab's content.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    if tab_id:
        endpoint += "?includeTabsContent=true"

    result = call_google_api("GET", endpoint)
    if not result.get("success"):
        return result

    doc = result.get("data", {})

    # If tab_id is specified, find the specific tab's content (recurse into child tabs)
    if tab_id:

        def _find_tab_body(tabs):
            for tab in tabs:
                if tab.get("tabProperties", {}).get("tabId") == tab_id:
                    return tab.get("documentTab", {}).get("body", {})
                child_result = _find_tab_body(tab.get("childTabs", []))
                if child_result is not None:
                    return child_result
            return None

        body = _find_tab_body(doc.get("tabs", []))
        if body is None:
            return {"success": False, "error": f"Tab with ID '{tab_id}' not found"}
    else:
        # Default behavior: get body from first tab or root body
        tabs = doc.get("tabs", [])
        if tabs:
            body = tabs[0].get("documentTab", {}).get("body", {})
        else:
            body = doc.get("body", {})

    content = body.get("content", [])

    def _extract_segments(elements):
        """Extract text segments from content elements, including tables."""
        result = []
        for element in elements:
            if "paragraph" in element:
                para = element["paragraph"]
                for text_element in para.get("elements", []):
                    text = _get_element_text(text_element)
                    if text:
                        result.append(
                            {
                                "start": text_element.get("startIndex", 0),
                                "end": text_element.get("endIndex", 0),
                                "text": text,
                            }
                        )
            elif "table" in element:
                table = element["table"]
                for row in table.get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        result.extend(_extract_segments(cell.get("content", [])))
        return result

    segments = _extract_segments(content)

    return {
        "success": True,
        "data": {"title": doc.get("title", ""), "segments": segments},
    }


def get_document_formatting(
    document_id: str,
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Get structured formatting summary of a document.

    Returns paragraph styles, text run formatting, table cell styles,
    and list info for every element in the document body.

    Args:
        document_id: The document ID or URL.
        tab_id: Optional tab ID.
    """
    doc_result = get_document(document_id, tab_id)
    if not doc_result.get("success"):
        return doc_result

    doc = doc_result["data"]
    body = _get_body_from_doc(doc, tab_id)
    content = body.get("content", [])
    title = doc.get("title", "")
    lists_map = _get_lists_from_doc(doc, tab_id)

    elements_out: list[dict[str, Any]] = []

    for elem in content:
        if "paragraph" in elem:
            para = elem["paragraph"]
            para_style = para.get("paragraphStyle", {})
            para_info: dict[str, Any] = {
                "type": "paragraph",
                "start_index": elem.get("startIndex", 0),
                "end_index": elem.get("endIndex", 0),
            }

            # Paragraph style fields
            if para_style.get("namedStyleType"):
                para_info["named_style"] = para_style["namedStyleType"]
            if para_style.get("alignment"):
                para_info["alignment"] = para_style["alignment"]
            spacing = para_style.get("lineSpacing")
            if spacing is not None:
                para_info["line_spacing"] = spacing
            space_above = para_style.get("spaceAbove", {}).get("magnitude")
            if space_above is not None:
                para_info["space_above"] = space_above
            space_below = para_style.get("spaceBelow", {}).get("magnitude")
            if space_below is not None:
                para_info["space_below"] = space_below
            indent_start = para_style.get("indentStart", {}).get("magnitude")
            if indent_start is not None:
                para_info["indent_start"] = indent_start
            indent_end = para_style.get("indentEnd", {}).get("magnitude")
            if indent_end is not None:
                para_info["indent_end"] = indent_end
            indent_first = para_style.get("indentFirstLine", {}).get("magnitude")
            if indent_first is not None:
                para_info["indent_first_line"] = indent_first
            shading_bg = (
                para_style.get("shading", {})
                .get("backgroundColor", {})
                .get("color", {})
                .get("rgbColor")
            )
            if shading_bg:
                para_info["shading_color"] = shading_bg

            # Bullet/list info
            bullet = para.get("bullet")
            if bullet:
                para_info["list_id"] = bullet.get("listId", "")
                para_info["nesting_level"] = bullet.get("nestingLevel", 0)

            # Text runs
            text_runs: list[dict[str, Any]] = []
            for pe in para.get("elements", []):
                tr = pe.get("textRun")
                if tr:
                    run_info: dict[str, Any] = {"text": tr.get("content", "")}
                    ts = tr.get("textStyle", {})
                    if ts.get("bold"):
                        run_info["bold"] = True
                    if ts.get("italic"):
                        run_info["italic"] = True
                    if ts.get("underline"):
                        run_info["underline"] = True
                    if ts.get("strikethrough"):
                        run_info["strikethrough"] = True
                    wff = ts.get("weightedFontFamily", {})
                    if wff.get("fontFamily"):
                        run_info["font_family"] = wff["fontFamily"]
                    fs = ts.get("fontSize", {}).get("magnitude")
                    if fs is not None:
                        run_info["font_size_pt"] = fs
                    fg = ts.get("foregroundColor", {}).get("color", {}).get("rgbColor")
                    if fg:
                        run_info["foreground_color"] = fg
                    bg = ts.get("backgroundColor", {}).get("color", {}).get("rgbColor")
                    if bg:
                        run_info["background_color"] = bg
                    link = ts.get("link", {}).get("url")
                    if link:
                        run_info["link"] = link
                    text_runs.append(run_info)
                else:
                    # Handle non-textRun elements (person chips, rich links, etc.)
                    chip_text = _get_element_text(pe)
                    if chip_text:
                        text_runs.append({"text": chip_text})

            para_info["text_runs"] = text_runs
            elements_out.append(para_info)

        elif "table" in elem:
            table = elem["table"]
            table_info: dict[str, Any] = {
                "type": "table",
                "start_index": elem.get("startIndex", 0),
                "rows": table.get("rows", 0),
                "columns": table.get("columns", 0),
                "cells": [],
            }
            for row_idx, tr in enumerate(table.get("tableRows", [])):
                for col_idx, tc in enumerate(tr.get("tableCells", [])):
                    cell_info: dict[str, Any] = {
                        "row": row_idx,
                        "column": col_idx,
                    }
                    # Cell background
                    tc_style = tc.get("tableCellStyle", {})
                    cell_bg = (
                        tc_style.get("backgroundColor", {})
                        .get("color", {})
                        .get("rgbColor")
                    )
                    if cell_bg:
                        cell_info["background_color"] = cell_bg
                    # Cell content as paragraph summaries
                    cell_paras: list[dict[str, Any]] = []
                    for cell_elem in tc.get("content", []):
                        if "paragraph" not in cell_elem:
                            continue
                        cp = cell_elem["paragraph"]
                        cp_text = ""
                        for cpe in cp.get("elements", []):
                            cp_text += _get_element_text(cpe)
                        cell_paras.append({"text": cp_text.rstrip("\n")})
                    cell_info["paragraphs"] = cell_paras
                    table_info["cells"].append(cell_info)
            elements_out.append(table_info)

    return {
        "success": True,
        "data": {"title": title, "elements": elements_out},
    }


def get_document_raw(document_id: str, tab_id: str | None = None) -> dict[str, Any]:
    """Get the raw Google Docs API body content and lists map.

    Returns the full structural detail from the API including run-level styles,
    bullet list IDs/nesting, table cell styles, section breaks, and horizontal
    rules. Use this when you need to understand or replicate complex formatting.

    Args:
        document_id: The document ID or URL.
        tab_id: Optional tab ID.
    """
    doc_result = get_document(document_id, tab_id)
    if not doc_result.get("success"):
        return doc_result

    doc = doc_result["data"]
    body = _get_body_from_doc(doc, tab_id)
    lists_map = _get_lists_from_doc(doc, tab_id)

    return {
        "success": True,
        "data": {
            "title": doc.get("title", ""),
            "content": body.get("content", []),
            "lists": lists_map,
        },
    }


def create_document(
    title: str,
    initial_content: str | None = None,
    folder_id: str | None = None,
    format_from_markdown: bool = False,
    format_from_html: bool = False,
) -> dict[str, Any]:
    """Create a new Google Doc.

    Args:
        title: The title of the document.
        initial_content: Optional initial text content to insert into the document.
        folder_id: Optional folder ID to create the document in. If not provided,
            the document will be created in the user's root Drive folder.
        format_from_markdown: If True, interpret initial_content as markdown and
            convert to Google Docs formatting (bold, italic, headings, lists, etc.).
        format_from_html: If True, interpret initial_content as HTML and convert
            to Google Docs formatting (colored text, tables, underlines, etc.).
    """
    endpoint = "https://www.googleapis.com/drive/v3/files"
    payload: dict[str, Any] = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        payload["parents"] = [folder_id]

    result = call_google_api("POST", endpoint, payload)
    if not result.get("success"):
        return result

    doc_id = result.get("data", {}).get("id")
    if not doc_id:
        return result

    # If no initial content, return with URL added
    if not initial_content:
        return {
            "success": True,
            "data": {
                "documentId": doc_id,
                "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            },
        }

    # Use markdown formatting if requested
    if format_from_markdown:
        md_result = insert_markdown(doc_id, initial_content, index=1)
        if md_result.get("success"):
            return {
                "success": True,
                "data": {
                    "documentId": doc_id,
                    "url": f"https://docs.google.com/document/d/{doc_id}/edit",
                },
            }
        return md_result

    # Use HTML formatting if requested
    if format_from_html:
        html_result = insert_html(doc_id, initial_content, index=1)
        if html_result.get("success"):
            return {
                "success": True,
                "data": {
                    "documentId": doc_id,
                    "url": f"https://docs.google.com/document/d/{doc_id}/edit",
                },
            }
        return html_result

    # Plain text insertion
    update_endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"
    update_payload = {
        "requests": [
            {"insertText": {"location": {"index": 1}, "text": initial_content}}
        ]
    }
    update_result = call_google_api("POST", update_endpoint, update_payload)

    if update_result.get("success"):
        return {
            "success": True,
            "data": {
                "documentId": doc_id,
                "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            },
        }
    return update_result


def copy_doc(
    document_id: str, title: str | None = None, folder_id: str | None = None
) -> dict[str, Any]:
    """Create a full copy of a Google Doc.

    Args:
        document_id: The document ID or URL of the source document.
        title: Optional title for the copy. If not provided, Google Drive
            will name it "Copy of <original title>".
        folder_id: Optional folder ID to place the copy in. If not provided,
            the copy is placed in the user's root Drive folder.

    Returns:
        A dict with success status and the new document's ID and URL.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/copy"
    payload: dict[str, Any] = {}
    if title:
        payload["name"] = title
    if folder_id:
        payload["parents"] = [folder_id]

    result = call_google_api("POST", endpoint, payload if payload else None)
    if not result.get("success"):
        return result

    new_doc_id = result.get("data", {}).get("id")
    if not new_doc_id:
        return result

    return {
        "success": True,
        "data": {
            "documentId": new_doc_id,
            "url": f"https://docs.google.com/document/d/{new_doc_id}/edit",
        },
    }


def find_replace(document_id: str, find_text: str, replace_text: str) -> dict[str, Any]:
    """Find and replace text across all tabs in a document.

    All occurrences of find_text in every tab will be replaced with replace_text.

    Args:
        document_id: The document ID or URL.
        find_text: The text to search for.
        replace_text: The text to replace it with.

    Returns:
        A dict with success status and the number of occurrences replaced.
    """
    doc_id = extract_document_id(document_id)

    # Get all tabs in the document
    tabs_result = get_document_tabs(doc_id)
    if not tabs_result.get("success"):
        return tabs_result

    tabs = tabs_result["data"].get("tabs", [])
    if not tabs:
        return {"success": False, "error": "No tabs found in the document"}

    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"

    # Build a replaceAllText request for each tab
    requests: list[dict[str, Any]] = []
    for tab in tabs:
        tab_id = tab.get("tabId")
        requests.append(
            {
                "replaceAllText": {
                    "containsText": {
                        "text": find_text,
                        "matchCase": True,
                    },
                    "replaceText": replace_text,
                    "tabsCriteria": {"tabIds": [tab_id]},
                }
            }
        )

    result = call_google_api("POST", endpoint, {"requests": requests})
    if not result.get("success"):
        return result

    # Sum up the total occurrences changed across all tabs
    total_replaced = 0
    replies = result.get("data", {}).get("replies", [])
    for reply in replies:
        replace_reply = reply.get("replaceAllText", {})
        total_replaced += replace_reply.get("occurrencesChanged", 0)

    return {
        "success": True,
        "data": {
            "occurrencesChanged": total_replaced,
        },
    }


def get_comments(document_id: str) -> dict[str, Any]:
    """Get comments from a document."""
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments?fields=*"
    return call_google_api("GET", endpoint)


def add_comment(
    document_id: str, comment: str, anchor: str = "", anchor_id: str = ""
) -> dict[str, Any]:
    """Add a comment to a document.

    Args:
        document_id: The document ID or URL.
        comment: The text content of the comment.
        anchor: Text in the document to anchor the comment to (used as
            quotedFileContent). Must exactly match text in the document.
        anchor_id: A kix.* or h.* element ID to use as the comment anchor.
            Required for comments to be visible in Google Docs UI.
            Use get_heading_ids to find heading IDs, or reuse kix.* IDs
            from existing comments.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments?fields=id,content,author,quotedFileContent,anchor,deleted,resolved"
    payload: dict[str, Any] = {"content": comment}
    if anchor_id:
        payload["anchor"] = anchor_id
    if anchor:
        payload["quotedFileContent"] = {"mimeType": "text/plain", "value": anchor}
    return call_google_api("POST", endpoint, payload)


def get_heading_ids(document_id: str) -> dict[str, Any]:
    """Get all heading IDs and their text from a document.

    Returns a list of {heading_id, text, start_index, level} objects
    that can be used as anchor_id values in add_comment.
    Heading IDs use the h.* format and are preserved when copying documents.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    result = call_google_api("GET", endpoint)
    if not result.get("success"):
        return result

    doc = result["data"]
    body = doc.get("body", {})
    content = body.get("content", [])

    headings: list[dict[str, Any]] = []
    for elem in content:
        if "paragraph" in elem:
            p = elem["paragraph"]
            style = p.get("paragraphStyle", {})
            heading_id = style.get("headingId", "")
            named_style = style.get("namedStyleType", "")
            if heading_id and named_style.startswith("HEADING"):
                text = ""
                for e in p.get("elements", []):
                    text += _get_element_text(e)
                text = text.strip()
                level = named_style.replace("HEADING_", "")
                headings.append(
                    {
                        "heading_id": heading_id,
                        "text": text,
                        "start_index": elem.get("startIndex", 0),
                        "level": level,
                    }
                )

    return {"success": True, "data": {"headings": headings}}


def reply_to_comment(
    document_id: str, comment_id: str, reply_content: str
) -> dict[str, Any]:
    """Reply to an existing comment on a document.

    Args:
        document_id: The document ID or URL.
        comment_id: The ID of the comment to reply to (from get_comments).
        reply_content: The text content of the reply.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments/{comment_id}/replies?fields=id,content,author"
    payload = {"content": reply_content}
    return call_google_api("POST", endpoint, payload)


def resolve_comment(document_id: str, comment_id: str) -> dict[str, Any]:
    """Mark a comment as resolved by creating a resolve reply.

    Google Drive API v3 treats the comment ``resolved`` field as read-only.
    To resolve a comment, you must POST a reply with ``action: "resolve"``
    to the replies endpoint.

    Args:
        document_id: The document ID or URL.
        comment_id: The ID of the comment to resolve (from get_comments).
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments/{comment_id}/replies?fields=id,action"
    payload = {"content": "Resolved", "action": "resolve"}
    return call_google_api("POST", endpoint, payload)


def delete_comment(document_id: str, comment_id: str) -> dict[str, Any]:
    """Delete a comment from a document.

    Args:
        document_id: The document ID or URL.
        comment_id: The ID of the comment to delete (from get_comments).
    """
    doc_id = extract_document_id(document_id)
    endpoint = (
        f"https://www.googleapis.com/drive/v3/files/{doc_id}/comments/{comment_id}"
    )
    return call_google_api("DELETE", endpoint)


def get_revisions(document_id: str) -> dict[str, Any]:
    """Get the revision history of a document.

    Args:
        document_id: The document ID or URL.

    Returns:
        A list of revisions with ID, modified time, and last modifying user.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/revisions?fields=revisions(id,modifiedTime,lastModifyingUser)"
    return call_google_api("GET", endpoint)


def get_revision_content(document_id: str, revision_id: str) -> dict[str, Any]:
    """Get the content of a specific revision.

    Args:
        document_id: The document ID or URL.
        revision_id: The revision ID (from get_revisions).

    Returns:
        The document content at that revision.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/revisions/{revision_id}?alt=media"
    return call_google_api("GET", endpoint)


def unshare_document(document_id: str, email_addresses: str) -> dict[str, Any]:
    """Remove access from specific users.

    Args:
        document_id: The document ID or URL.
        email_addresses: Comma-separated list of email addresses to remove.

    Returns:
        Success status and list of removed permissions.
    """
    doc_id = extract_document_id(document_id)
    emails = [e.strip() for e in email_addresses.split(",") if e.strip()]

    if not emails:
        return {"success": False, "error": "No email addresses provided"}

    # First get all permissions
    perms_endpoint = (
        f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions?fields=*"
    )
    perms_result = call_google_api("GET", perms_endpoint)
    if not perms_result.get("success"):
        return perms_result

    permissions = perms_result.get("data", {}).get("permissions", [])
    removed = []
    errors = []

    for email in emails:
        # Find permission ID for this email
        perm_id = None
        for perm in permissions:
            if perm.get("emailAddress", "").lower() == email.lower():
                perm_id = perm.get("id")
                break

        if not perm_id:
            errors.append(f"No permission found for {email}")
            continue

        # Delete the permission
        delete_endpoint = (
            f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions/{perm_id}"
        )
        delete_result = call_google_api("DELETE", delete_endpoint)
        if delete_result.get("success"):
            removed.append(email)
        else:
            errors.append(f"Failed to remove {email}: {delete_result.get('error')}")

    return {
        "success": len(removed) > 0,
        "data": {
            "removed": removed,
            "errors": errors if errors else None,
        },
    }


def insert_inline_image(
    document_id: str,
    image_uri: str,
    index: int,
    width: int | None = None,
    height: int | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Insert an inline image into a document.

    Args:
        document_id: The document ID or URL.
        image_uri: The publicly accessible URI of the image (PNG, JPEG, GIF; under 50MB).
        index: The index where the image should be inserted.
        width: Optional width in points (default: 400).
        height: Optional height in points (maintains aspect ratio if not specified).
        tab_id: Optional tab ID to insert image into a specific tab.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"

    insert_request: dict[str, Any] = {
        "insertInlineImage": {
            "uri": image_uri,
            "location": {"index": index},
        }
    }

    # Add optional size
    object_size: dict[str, Any] = {}
    if width:
        object_size["width"] = {"magnitude": width, "unit": "PT"}
    if height:
        object_size["height"] = {"magnitude": height, "unit": "PT"}
    if object_size:
        insert_request["insertInlineImage"]["objectSize"] = object_size

    # Add tab ID if specified
    if tab_id:
        insert_request["insertInlineImage"]["location"]["tabId"] = tab_id

    result = call_google_api("POST", endpoint, {"requests": [insert_request]})
    return result


def move_document(document_id: str, target_folder_id: str) -> dict[str, Any]:
    """Move a document to a different folder in Google Drive.

    Args:
        document_id: The document ID or URL.
        target_folder_id: The destination folder ID. Use "root" for My Drive root.
    """
    doc_id = extract_document_id(document_id)

    # Get current parents
    get_endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}?fields=parents&supportsAllDrives=true"
    get_result = call_google_api("GET", get_endpoint)
    if not get_result.get("success"):
        return get_result

    current_parents = get_result.get("data", {}).get("parents", [])
    remove_parents = ",".join(current_parents) if current_parents else ""

    # Move to new folder
    move_endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}?addParents={target_folder_id}&supportsAllDrives=true"
    if remove_parents:
        move_endpoint += f"&removeParents={remove_parents}"

    return call_google_api("PATCH", move_endpoint, {})


def get_permissions(document_id: str) -> dict[str, Any]:
    """Get document permissions."""
    doc_id = extract_document_id(document_id)
    endpoint = (
        f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions?fields=*"
    )
    return call_google_api("GET", endpoint)


def share_document(
    document_id: str, email: str, role: str = "reader"
) -> dict[str, Any]:
    """Share a document with a user."""
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/permissions"
    payload = {"type": "user", "role": role, "emailAddress": email}
    return call_google_api("POST", endpoint, payload)


def export_document(document_id: str, mime_type: str) -> dict[str, Any]:
    """Export a document to a specified format."""
    doc_id = extract_document_id(document_id)
    mime_types = {
        "html": "text/html",
        "plain_text": "text/plain",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "rtf": "application/rtf",
        "odt": "application/vnd.oasis.opendocument.text",
        "epub": "application/epub+zip",
    }
    actual_mime = mime_types.get(mime_type, mime_type)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}/export?mimeType={actual_mime}"
    return call_google_api("GET", endpoint)


def batch_update(
    document_id: str, requests: list[dict[str, Any]], tab_id: str | None = None
) -> dict[str, Any]:
    """Execute batch update requests on a document.

    Automatically chunks large request lists to stay within Google Docs API
    limits (~200 requests per batch call). Chunks are sent sequentially and
    the result of the last successful chunk is returned.

    Args:
        document_id: The document ID or URL.
        requests: List of update request objects.
        tab_id: Optional tab ID to apply updates to a specific tab.
            If not provided, updates are applied to the first (default) tab.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate"

    # If tab_id is specified, add tabId to each location in the requests
    if tab_id:
        for request in requests:
            _add_tab_id_to_request(request, tab_id)

    # Auto-sort pure insertText batches by descending index to prevent index shift.
    # Mixed-type batches (insert+format) are left in caller order since format
    # requests reference post-insert indices.
    if requests and all("insertText" in r for r in requests):
        requests = sorted(
            requests,
            key=lambda r: r["insertText"].get("location", {}).get("index", 0),
            reverse=True,
        )

    # Chunk large request lists to stay within API limits
    max_batch_size = 150
    if len(requests) <= max_batch_size:
        payload = {"requests": requests}
        return call_google_api("POST", endpoint, payload)

    result: dict[str, Any] = {"success": True}
    for i in range(0, len(requests), max_batch_size):
        chunk = requests[i : i + max_batch_size]
        payload = {"requests": chunk}
        result = call_google_api("POST", endpoint, payload)
        if not result.get("success"):
            return result
    return result


def replace_document_content(
    document_id: str,
    content: str,
    format: str = "markdown",
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Replace all content in a document.

    Clears existing content and inserts new content.
    Preserves the document URL, sharing settings, and comments.

    Args:
        document_id: The document ID or URL.
        content: The content to insert.
        format: Content format - "markdown" (default), "html", or "text".
        tab_id: Optional tab ID to replace content in a specific tab.
    """
    doc_id = extract_document_id(document_id)

    doc_result = get_document(doc_id, tab_id)
    if not doc_result.get("success"):
        return doc_result

    body = _get_body_from_doc(doc_result["data"], tab_id)
    body_content = body.get("content", [])
    if not body_content:
        return {"success": False, "error": "Document has no content elements"}

    end_index = body_content[-1].get("endIndex", 1)

    if end_index > 2:
        clear_result = batch_update(
            doc_id,
            [
                {
                    "deleteContentRange": {
                        "range": {"startIndex": 1, "endIndex": end_index - 1}
                    }
                }
            ],
            tab_id,
        )
        if not clear_result.get("success"):
            return clear_result

    if format == "markdown":
        return insert_markdown(doc_id, content, 1, tab_id)
    elif format == "html":
        return insert_html(doc_id, content, 1, tab_id)
    else:
        return insert_text(doc_id, content, 1, tab_id)


def _add_tab_id_to_request(request: dict[str, Any], tab_id: str) -> None:
    """Helper to add tabId to location objects within a request."""
    # Handle different request types that have location or range objects
    if "insertText" in request:
        location = request["insertText"].get("location", {})
        location["tabId"] = tab_id
        request["insertText"]["location"] = location
    elif "deleteContentRange" in request:
        range_obj = request["deleteContentRange"].get("range", {})
        range_obj["tabId"] = tab_id
        request["deleteContentRange"]["range"] = range_obj
    elif "updateTextStyle" in request:
        range_obj = request["updateTextStyle"].get("range", {})
        range_obj["tabId"] = tab_id
        request["updateTextStyle"]["range"] = range_obj
    elif "updateParagraphStyle" in request:
        range_obj = request["updateParagraphStyle"].get("range", {})
        range_obj["tabId"] = tab_id
        request["updateParagraphStyle"]["range"] = range_obj
    elif "createParagraphBullets" in request:
        range_obj = request["createParagraphBullets"].get("range", {})
        range_obj["tabId"] = tab_id
        request["createParagraphBullets"]["range"] = range_obj
    elif "deleteParagraphBullets" in request:
        range_obj = request["deleteParagraphBullets"].get("range", {})
        range_obj["tabId"] = tab_id
        request["deleteParagraphBullets"]["range"] = range_obj
    elif "insertTable" in request:
        location = request["insertTable"].get("location")
        if location:
            location["tabId"] = tab_id
        end_loc = request["insertTable"].get("endOfSegmentLocation")
        if end_loc:
            end_loc["tabId"] = tab_id
    elif "updateTableCellStyle" in request:
        table_range = request["updateTableCellStyle"].get("tableRange", {})
        cell_loc = table_range.get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "updateTableColumnProperties" in request:
        start_loc = request["updateTableColumnProperties"].get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "pinTableHeaderRows" in request:
        start_loc = request["pinTableHeaderRows"].get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "mergeTableCells" in request:
        table_range = request["mergeTableCells"].get("tableRange", {})
        cell_loc = table_range.get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "unmergeTableCells" in request:
        table_range = request["unmergeTableCells"].get("tableRange", {})
        cell_loc = table_range.get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "updateTableRowStyle" in request:
        start_loc = request["updateTableRowStyle"].get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "insertInlineImage" in request:
        location = request["insertInlineImage"].get("location", {})
        location["tabId"] = tab_id
    elif "insertPageBreak" in request:
        location = request["insertPageBreak"].get("location", {})
        location["tabId"] = tab_id
    elif "deleteTableRow" in request:
        cell_loc = request["deleteTableRow"].get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "deleteTableColumn" in request:
        cell_loc = request["deleteTableColumn"].get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "insertTableRow" in request:
        cell_loc = request["insertTableRow"].get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id
    elif "insertTableColumn" in request:
        cell_loc = request["insertTableColumn"].get("tableCellLocation", {})
        start_loc = cell_loc.get("tableStartLocation", {})
        start_loc["tabId"] = tab_id


def insert_text(
    document_id: str, text: str, index: int = 1, tab_id: str | None = None
) -> dict[str, Any]:
    """Insert text at a specific index in the document.

    Args:
        document_id: The document ID or URL.
        text: The text to insert.
        index: The index at which to insert the text (default: 1).
        tab_id: Optional tab ID to insert text into a specific tab.
            If not provided, text is inserted into the first (default) tab.
    """
    requests = [
        {"insertText": {"location": {"index": index}, "text": text}},
        # Reset character formatting so inserted text doesn't inherit
        # bold/italic/etc. from the text at the insertion point.
        {
            "updateTextStyle": {
                "range": {"startIndex": index, "endIndex": index + len(text)},
                "textStyle": {},
                "fields": "bold,italic,underline,strikethrough",
            }
        },
    ]
    return batch_update(document_id, requests, tab_id)


def format_text(
    document_id: str,
    start_index: int,
    end_index: int,
    bold: bool | None = None,
    italic: bool | None = None,
    underline: bool | None = None,
    font_size: int | None = None,
    foreground_color: dict[str, float] | None = None,
    background_color: dict[str, float] | None = None,
    link: str | None = None,
    font_family: str | None = None,
    strikethrough: bool | None = None,
    tab_id: str | None = None,
    clear_other_fields: bool = False,
) -> dict[str, Any]:
    """Format text in a range with various styles.

    Args:
        document_id: The document ID or URL.
        start_index: The start index of the range to format.
        end_index: The end index of the range to format.
        bold: Whether to make text bold.
        italic: Whether to make text italic.
        underline: Whether to underline text.
        font_size: Font size in points.
        foreground_color: Text color as RGB dict.
        background_color: Background/highlight color as RGB dict.
        link: URL to make the text a clickable hyperlink. The API automatically
            applies link styling (blue color, underline).
        font_family: Font family name (e.g., "Arial", "Roboto Mono").
        strikethrough: Whether to apply strikethrough formatting.
        tab_id: Optional tab ID to format text in a specific tab.
        clear_other_fields: If True, include all standard text style fields in the
            field mask so pre-existing formatting not explicitly set is cleared.
    """
    text_style: dict[str, Any] = {}
    fields = []

    if bold is not None:
        text_style["bold"] = bold
        fields.append("bold")
    if italic is not None:
        text_style["italic"] = italic
        fields.append("italic")
    if underline is not None:
        text_style["underline"] = underline
        fields.append("underline")
    if font_size is not None:
        text_style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
        fields.append("fontSize")
    if foreground_color is not None:
        text_style["foregroundColor"] = {"color": {"rgbColor": foreground_color}}
        fields.append("foregroundColor")
    if background_color is not None:
        text_style["backgroundColor"] = {"color": {"rgbColor": background_color}}
        fields.append("backgroundColor")
    if link is not None:
        text_style["link"] = {"url": link}
        fields.append("link")
    if font_family is not None:
        text_style["weightedFontFamily"] = {"fontFamily": font_family, "weight": 400}
        fields.append("weightedFontFamily")
    if strikethrough is not None:
        text_style["strikethrough"] = strikethrough
        fields.append("strikethrough")

    if clear_other_fields:
        # Include all standard fields so unset ones are cleared to defaults
        fields = [
            "bold",
            "italic",
            "underline",
            "strikethrough",
            "smallCaps",
            "foregroundColor",
            "backgroundColor",
            "fontSize",
            "weightedFontFamily",
            "baselineOffset",
            "link",
        ]

    requests = [
        {
            "updateTextStyle": {
                "range": {"startIndex": start_index, "endIndex": end_index},
                "textStyle": text_style,
                "fields": ",".join(fields),
            }
        }
    ]
    return batch_update(document_id, requests, tab_id)


def apply_heading(
    document_id: str,
    start_index: int,
    end_index: int,
    heading_level: int,
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Apply a heading style to a paragraph.

    Args:
        document_id: The document ID or URL.
        start_index: The start index of the range to format.
        end_index: The end index of the range to format.
        heading_level: Heading level (1-6).
        tab_id: Optional tab ID to apply heading in a specific tab.
    """
    heading_map = {
        1: "HEADING_1",
        2: "HEADING_2",
        3: "HEADING_3",
        4: "HEADING_4",
        5: "HEADING_5",
        6: "HEADING_6",
    }
    named_style = heading_map.get(heading_level, "NORMAL_TEXT")
    requests = [
        {
            "updateParagraphStyle": {
                "range": {"startIndex": start_index, "endIndex": end_index},
                "paragraphStyle": {"namedStyleType": named_style},
                "fields": "namedStyleType",
            }
        }
    ]
    return batch_update(document_id, requests, tab_id)


def set_paragraph_style(
    document_id: str,
    start_index: int,
    end_index: int,
    alignment: str | None = None,
    named_style: str | None = None,
    line_spacing: float | None = None,
    space_above: float | None = None,
    space_below: float | None = None,
    indent_start: float | None = None,
    indent_end: float | None = None,
    indent_first_line: float | None = None,
    shading_color: dict[str, float] | None = None,
    tab_id: str | None = None,
    clear_other_fields: bool = False,
) -> dict[str, Any]:
    """Set paragraph style properties for a range.

    Args:
        document_id: The document ID or URL.
        start_index: The start index of the range.
        end_index: The end index of the range.
        alignment: Paragraph alignment: "START", "CENTER", "END", or "JUSTIFIED".
        named_style: Named style type: "TITLE", "SUBTITLE", "HEADING_1"-"HEADING_6", "NORMAL_TEXT".
        line_spacing: Line spacing as percentage (e.g., 115 = 1.15x).
        space_above: Space above paragraph in points.
        space_below: Space below paragraph in points.
        indent_start: Start indent in points.
        indent_end: End indent in points.
        indent_first_line: First line indent in points.
        shading_color: Paragraph background shading as RGB dict.
        tab_id: Optional tab ID.
        clear_other_fields: If True, include all standard paragraph style fields in
            the field mask so pre-existing styles not explicitly set are cleared.
    """
    doc_id = extract_document_id(document_id)
    paragraph_style: dict[str, Any] = {}
    fields: list[str] = []

    if alignment is not None:
        paragraph_style["alignment"] = alignment
        fields.append("alignment")
    if named_style is not None:
        paragraph_style["namedStyleType"] = named_style
        fields.append("namedStyleType")
    if line_spacing is not None:
        paragraph_style["lineSpacing"] = line_spacing
        fields.append("lineSpacing")
    if space_above is not None:
        paragraph_style["spaceAbove"] = {"magnitude": space_above, "unit": "PT"}
        fields.append("spaceAbove")
    if space_below is not None:
        paragraph_style["spaceBelow"] = {"magnitude": space_below, "unit": "PT"}
        fields.append("spaceBelow")
    if indent_start is not None:
        paragraph_style["indentStart"] = {"magnitude": indent_start, "unit": "PT"}
        fields.append("indentStart")
    if indent_end is not None:
        paragraph_style["indentEnd"] = {"magnitude": indent_end, "unit": "PT"}
        fields.append("indentEnd")
    if indent_first_line is not None:
        paragraph_style["indentFirstLine"] = {
            "magnitude": indent_first_line,
            "unit": "PT",
        }
        fields.append("indentFirstLine")
    if shading_color is not None:
        paragraph_style["shading"] = {
            "backgroundColor": {"color": {"rgbColor": shading_color}}
        }
        fields.append("shading")

    if clear_other_fields:
        # Include all standard fields so unset ones are cleared to defaults
        fields = [
            "namedStyleType",
            "alignment",
            "direction",
            "lineSpacing",
            "keepLinesTogether",
            "keepWithNext",
            "avoidWidowAndOrphan",
            "spacingMode",
            "shading",
            "indentFirstLine",
            "indentStart",
            "indentEnd",
            "spaceAbove",
            "spaceBelow",
        ]

    requests = [
        {
            "updateParagraphStyle": {
                "range": {"startIndex": start_index, "endIndex": end_index},
                "paragraphStyle": paragraph_style,
                "fields": ",".join(fields),
            }
        }
    ]
    return batch_update(doc_id, requests, tab_id)


def delete_document(document_id: str) -> dict[str, Any]:
    """Delete a document (move to trash)."""
    doc_id = extract_document_id(document_id)
    endpoint = f"https://www.googleapis.com/drive/v3/files/{doc_id}"
    return call_google_api("DELETE", endpoint)


def insert_table(
    document_id: str,
    rows: int,
    columns: int,
    index: int = 1,
    data: list[list[str]] | None = None,
) -> dict[str, Any]:
    """Insert a table at the specified index.

    Args:
        document_id: The document ID or URL
        rows: Number of rows
        columns: Number of columns
        index: Where to insert the table
        data: Optional 2D list of cell contents

    Returns:
        Result of the batch update
    """
    requests: list[dict[str, Any]] = []

    # Insert the table structure
    requests.append(
        {
            "insertTable": {
                "location": {"index": index},
                "rows": rows,
                "columns": columns,
            }
        }
    )

    result = batch_update(document_id, requests)

    if not result.get("success"):
        return result

    # Clean up the extra empty paragraph that insertTable creates
    _remove_extra_table_paragraph(document_id, index)

    # If data was provided, populate cells
    if data:
        # Need to re-read document to get table cell indices
        doc_result = get_document(document_id)
        if not doc_result.get("success"):
            return doc_result

        doc = doc_result.get("data", {})
        body = doc.get("body", {})
        content = body.get("content", [])

        # Find the table we just inserted
        table_element = None
        for elem in content:
            if "table" in elem:
                start_idx = elem.get("startIndex", 0)
                if start_idx >= index:
                    table_element = elem
                    break

        if table_element:
            table = table_element["table"]
            table_rows = table.get("tableRows", [])
            cell_requests: list[dict[str, Any]] = []

            for row_idx, row in enumerate(table_rows):
                if row_idx >= len(data):
                    break
                cells = row.get("tableCells", [])
                for col_idx, cell in enumerate(cells):
                    if col_idx >= len(data[row_idx]):
                        break
                    cell_content = data[row_idx][col_idx]
                    if cell_content:
                        # Get the paragraph inside the cell
                        cell_paragraphs = cell.get("content", [])
                        if cell_paragraphs:
                            para = cell_paragraphs[0]
                            if "paragraph" in para:
                                cell_start = para.get("startIndex", 0)
                                cell_requests.append(
                                    {
                                        "insertText": {
                                            "location": {"index": cell_start},
                                            "text": cell_content,
                                        }
                                    }
                                )
                                # Normalize cell paragraph to NORMAL_TEXT so
                                # font size matches document body text
                                cell_requests.append(
                                    {
                                        "updateParagraphStyle": {
                                            "range": {
                                                "startIndex": cell_start,
                                                "endIndex": cell_start + 1,
                                            },
                                            "paragraphStyle": {
                                                "namedStyleType": "NORMAL_TEXT"
                                            },
                                            "fields": "namedStyleType",
                                        }
                                    }
                                )

            if cell_requests:
                # Execute cell content insertions from last to first
                cell_requests.reverse()
                return batch_update(document_id, cell_requests)

    return result


def update_table_cell_style(
    document_id: str,
    table_start_index: int,
    row_index: int,
    column_index: int,
    row_span: int = 1,
    column_span: int = 1,
    background_color: dict[str, float] | None = None,
    border_color: dict[str, float] | None = None,
    border_width: float | None = None,
    padding: float | None = None,
    content_alignment: str | None = None,
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Update styling for table cells.

    Args:
        document_id: The document ID or URL.
        table_start_index: The start index of the table in the document.
        row_index: Zero-based row index of the cell.
        column_index: Zero-based column index of the cell.
        row_span: Number of rows the style applies to (default 1).
        column_span: Number of columns the style applies to (default 1).
        background_color: Cell background color as RGB dict (e.g., {"red": 0.9, "green": 0.9, "blue": 1.0}).
        border_color: Border color as RGB dict, applied to all four sides.
        border_width: Border width in points, applied to all four sides.
        padding: Cell padding in points, applied to all four sides.
        content_alignment: Vertical alignment: "TOP", "MIDDLE", or "BOTTOM".
        tab_id: Optional tab ID.
    """
    doc_id = extract_document_id(document_id)
    table_cell_style: dict[str, Any] = {}
    fields: list[str] = []

    if background_color is not None:
        table_cell_style["backgroundColor"] = {"color": {"rgbColor": background_color}}
        fields.append("backgroundColor")

    if border_color is not None or border_width is not None:
        border_style: dict[str, Any] = {}
        if border_color is not None:
            border_style["color"] = {"color": {"rgbColor": border_color}}
        if border_width is not None:
            border_style["width"] = {"magnitude": border_width, "unit": "PT"}
            border_style["dashStyle"] = "SOLID"
        for side in ("borderTop", "borderBottom", "borderLeft", "borderRight"):
            table_cell_style[side] = border_style
            fields.append(side)

    if padding is not None:
        padding_val = {"magnitude": padding, "unit": "PT"}
        for side in ("paddingTop", "paddingBottom", "paddingLeft", "paddingRight"):
            table_cell_style[side] = padding_val
            fields.append(side)

    if content_alignment is not None:
        table_cell_style["contentAlignment"] = content_alignment
        fields.append("contentAlignment")

    requests = [
        {
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_start_index},
                        "rowIndex": row_index,
                        "columnIndex": column_index,
                    },
                    "rowSpan": row_span,
                    "columnSpan": column_span,
                },
                "tableCellStyle": table_cell_style,
                "fields": ",".join(fields),
            }
        }
    ]
    return batch_update(doc_id, requests, tab_id)


def _find_table_at_index(
    content: list[dict[str, Any]], table_start_index: int
) -> dict[str, Any] | None:
    """Find a table element at the given start index."""
    for elem in content:
        if "table" in elem and elem.get("startIndex") == table_start_index:
            return elem
    return None


def _build_cell_index_map(
    table_rows: list[dict[str, Any]],
) -> dict[tuple[int, int], int]:
    """Build a mapping from (row, col) to cell paragraph startIndex."""
    cell_index_map: dict[tuple[int, int], int] = {}
    for row_idx, row in enumerate(table_rows):
        for col_idx, cell in enumerate(row.get("tableCells", [])):
            cell_content = cell.get("content", [])
            if cell_content and "paragraph" in cell_content[0]:
                cell_index_map[(row_idx, col_idx)] = cell_content[0].get(
                    "startIndex", 0
                )
    return cell_index_map


def _build_insert_requests(
    cells_to_insert: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    """Build insertText and updateTextStyle requests for cell text inserts."""
    requests: list[dict[str, Any]] = []
    for idx, text in cells_to_insert:
        requests.append({"insertText": {"location": {"index": idx}, "text": text}})
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": idx, "endIndex": idx + len(text)},
                    "textStyle": {},
                    "fields": "bold,italic,underline,strikethrough,fontSize",
                }
            }
        )
    return requests


def _build_cell_style_requests(
    cell_updates: list[dict[str, Any]],
    table_start_index: int,
    num_rows: int,
    num_cols: int,
) -> list[dict[str, Any]]:
    """Build updateTableCellStyle requests for background colors."""
    requests: list[dict[str, Any]] = []
    for update in cell_updates:
        row, col = update.get("row", 0), update.get("col", 0)
        bg = update.get("background_color")
        if bg is None or row >= num_rows or col >= num_cols:
            continue
        requests.append(
            {
                "updateTableCellStyle": {
                    "tableRange": {
                        "tableCellLocation": {
                            "tableStartLocation": {"index": table_start_index},
                            "rowIndex": row,
                            "columnIndex": col,
                        },
                        "rowSpan": 1,
                        "columnSpan": 1,
                    },
                    "tableCellStyle": {"backgroundColor": {"color": {"rgbColor": bg}}},
                    "fields": "backgroundColor",
                }
            }
        )
    return requests


def update_table_cells(
    document_id: str,
    table_start_index: int,
    cell_updates: list[dict[str, Any]],
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Bulk insert text and apply background colors to table cells.

    Each item in cell_updates is a dict with:
        row (int): Zero-based row index.
        col (int): Zero-based column index.
        text (str, optional): Text to insert into the cell.
        background_color (dict, optional): RGB dict, e.g. {"red": 0.9, "green": 0.9, "blue": 1.0}.

    Args:
        document_id: The document ID or URL.
        table_start_index: The startIndex of the table element in the document.
        cell_updates: List of cell update dicts.
        tab_id: Optional tab ID.
    """
    doc_id = extract_document_id(document_id)

    doc_result = get_document(doc_id, tab_id)
    if not doc_result.get("success"):
        return doc_result

    body = _get_body_from_doc(doc_result["data"], tab_id)
    content = body.get("content", [])

    table_element = _find_table_at_index(content, table_start_index)
    if table_element is None:
        return {
            "success": False,
            "error": f"No table found at startIndex {table_start_index}",
        }

    table = table_element["table"]
    table_rows = table.get("tableRows", [])
    num_rows = len(table_rows)
    num_cols = len(table_rows[0].get("tableCells", [])) if table_rows else 0

    cell_index_map = _build_cell_index_map(table_rows)
    warnings: list[str] = []

    cells_to_insert: list[tuple[int, str]] = []
    for update in cell_updates:
        row, col = update.get("row", 0), update.get("col", 0)
        if row >= num_rows or col >= num_cols:
            warnings.append(f"Skipping out-of-bounds cell ({row}, {col})")
            continue
        text = update.get("text")
        if text is not None and (row, col) in cell_index_map:
            cells_to_insert.append((cell_index_map[(row, col)], text))

    cells_to_insert.sort(key=lambda x: x[0], reverse=True)
    insert_requests = _build_insert_requests(cells_to_insert)

    if insert_requests:
        insert_result = batch_update(doc_id, insert_requests, tab_id)
        if not insert_result.get("success"):
            return insert_result

    style_requests = _build_cell_style_requests(
        cell_updates, table_start_index, num_rows, num_cols
    )

    if style_requests:
        style_result = batch_update(doc_id, style_requests, tab_id)
        if not style_result.get("success"):
            return style_result

    result_data: dict[str, Any] = {
        "message": f"Updated {len(cells_to_insert)} text inserts and {len(style_requests)} cell styles"
    }
    if warnings:
        result_data["warnings"] = warnings
    return {"success": True, "data": result_data}


def set_column_widths(
    document_id: str,
    table_start_index: int,
    column_widths: list[dict],
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Set column widths for a table.

    Args:
        document_id: The document ID or URL.
        table_start_index: The start index of the table in the document.
        column_widths: List of dicts, each with "column_index" (int) and
            "width_pt" (float, width in points).
        tab_id: Optional tab ID.
    """
    doc_id = extract_document_id(document_id)
    requests = []
    for col in column_widths:
        requests.append(
            {
                "updateTableColumnProperties": {
                    "tableStartLocation": {"index": table_start_index},
                    "columnIndices": [col["column_index"]],
                    "tableColumnProperties": {
                        "widthType": "FIXED_WIDTH",
                        "width": {"magnitude": col["width_pt"], "unit": "PT"},
                    },
                    "fields": "widthType,width",
                }
            }
        )
    return batch_update(doc_id, requests, tab_id)


def create_bullet_list(
    document_id: str,
    start_index: int,
    end_index: int,
    bullet_preset: str = "BULLET_DISC_CIRCLE_SQUARE",
) -> dict[str, Any]:
    """Apply bullet list formatting to a range of paragraphs.

    Args:
        document_id: The document ID or URL
        start_index: Start of the range
        end_index: End of the range
        bullet_preset: The bullet style preset

    Available presets:
        - BULLET_DISC_CIRCLE_SQUARE (default)
        - BULLET_DIAMONDX_ARROW3D_SQUARE
        - BULLET_CHECKBOX
        - NUMBERED_DECIMAL_ALPHA_ROMAN
        - NUMBERED_DECIMAL_ALPHA_ROMAN_PARENS
        - NUMBERED_DECIMAL_NESTED
        - NUMBERED_UPPERALPHA_ALPHA_ROMAN
        - NUMBERED_UPPERROMAN_UPPERALPHA_DECIMAL
        - NUMBERED_ZERODECIMAL_ALPHA_ROMAN

    Returns:
        Result of the batch update
    """
    requests = [
        {
            "createParagraphBullets": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": end_index,
                },
                "bulletPreset": bullet_preset,
            }
        }
    ]
    return batch_update(document_id, requests)


def insert_bullet_list(
    document_id: str,
    items: list[str],
    index: int = 1,
    bullet_preset: str = "BULLET_DISC_CIRCLE_SQUARE",
) -> dict[str, Any]:
    """Insert a bullet list at the specified index.

    Args:
        document_id: The document ID or URL
        items: List of text items for the bullet points
        index: Where to insert the list
        bullet_preset: The bullet style preset

    Returns:
        Result of the batch update
    """
    if not items:
        return {"success": True, "data": {"message": "No items to insert"}}

    # First, insert all the text as separate lines
    text = "\n".join(items) + "\n"
    text_result = insert_text(document_id, text, index)

    if not text_result.get("success"):
        return text_result

    # Calculate the range for bullet formatting
    start_index = index
    end_index = index + _utf16_len(text)

    # Apply bullet formatting
    return create_bullet_list(document_id, start_index, end_index, bullet_preset)


def insert_markdown(
    document_id: str, markdown_text: str, index: int = 1, tab_id: str | None = None
) -> dict[str, Any]:
    """Insert markdown text and convert it to Google Docs formatting.

    Supports:
    - # Heading 1 through ###### Heading 6
    - **bold** and __bold__
    - *italic* and _italic_
    - `inline code` (monospace + gray background)
    - ```code blocks``` (monospace + gray background)
    - | tables | with | pipes | (converted to native GDoc tables)
    - - bullet lists and * bullet lists
    - 1. numbered lists
    - ~~strikethrough~~
    - [text](url) links
    - Backslash escapes for _ * `

    Tables require separate API calls (insert structure, compute/discover cell
    indices, populate cells), so documents with tables make multiple round-trips.

    Args:
        document_id: The document ID or URL.
        markdown_text: The markdown text to insert.
        index: The index at which to insert (default: 1).
        tab_id: Optional tab ID to insert markdown into a specific tab.
    """
    if index < 1:
        index = 1

    blocks = _parse_markdown_blocks(markdown_text)

    pending_requests: list[dict[str, Any]] = []
    current_index = index
    last_result: dict[str, Any] = {"success": True}
    # Track whether we're in a "post-list" state where paragraphs inherit
    # bullet formatting and need it cleared.
    needs_bullet_clear = True

    for block_type, content in blocks:
        if block_type == "table":
            # Tables need their own API calls, so flush pending requests first
            if pending_requests:
                last_result = batch_update(document_id, pending_requests, tab_id)
                if not last_result.get("success"):
                    return last_result
                pending_requests = []
            # Don't reset needs_bullet_clear -- list formatting can bleed
            # past tables in Google Docs

            # Always re-read the document after table insertion to get
            # correct indices.  The formula-based path (use_formula=True)
            # doesn't account for the -1 index shift caused by
            # _remove_extra_table_paragraph, so after multiple tables
            # the cumulative drift makes tableStartLocation invalid.
            table_result = _insert_markdown_table(
                document_id,
                content,
                current_index,
                tab_id,
                use_formula=False,
            )

            if not table_result.get("success"):
                return table_result
            current_index = table_result.get("end_index", current_index)

        elif block_type == "code_block":
            current_index = _build_code_block_requests(
                content,
                current_index,
                pending_requests,
                clear_bullets=needs_bullet_clear,
            )

        elif block_type == "bullet_list":
            current_index = _build_bullet_list_requests(
                content, current_index, pending_requests
            )
            # Flush immediately after lists to prevent bullet formatting
            # from bleeding into subsequently inserted paragraphs.
            if pending_requests:
                last_result = batch_update(document_id, pending_requests, tab_id)
                if not last_result.get("success"):
                    return last_result
                pending_requests = []
            needs_bullet_clear = True

        elif block_type == "numbered_list":
            current_index = _build_numbered_list_requests(
                content, current_index, pending_requests
            )
            # Flush immediately after lists (same reason as bullet_list)
            if pending_requests:
                last_result = batch_update(document_id, pending_requests, tab_id)
                if not last_result.get("success"):
                    return last_result
                pending_requests = []
            needs_bullet_clear = True

        elif block_type == "horizontal_rule":
            current_index = _build_horizontal_rule_requests(
                current_index, pending_requests, clear_bullets=needs_bullet_clear
            )

        elif block_type == "block_quote":
            current_index = _build_block_quote_requests(
                content,
                current_index,
                pending_requests,
                clear_bullets=needs_bullet_clear,
            )

        elif block_type == "text":
            current_index = _build_text_line_requests(
                content,
                current_index,
                pending_requests,
                clear_bullets=needs_bullet_clear,
            )

        # Flush pending requests in chunks to avoid Google API proxy timeouts
        if len(pending_requests) >= 500:
            last_result = batch_update(document_id, pending_requests, tab_id)
            if not last_result.get("success"):
                return last_result
            pending_requests = []

    # Flush remaining requests
    if pending_requests:
        last_result = batch_update(document_id, pending_requests, tab_id)

    return last_result


def insert_html(
    document_id: str, html_text: str, index: int = 1, tab_id: str | None = None
) -> dict[str, Any]:
    """Insert HTML text and convert it to Google Docs formatting.

    Supports:
    - <b>, <strong> - bold text
    - <i>, <em> - italic text
    - <u> - underlined text
    - <s>, <strike>, <del> - strikethrough text
    - <code> - inline code (monospace + gray background)
    - <pre> - preformatted code block
    - <sup>, <sub> - superscript/subscript
    - <a href="url"> - hyperlinks
    - <span style="color: #ff0000"> - colored text (hex, rgb, named)
    - <span style="background-color: yellow"> - background/highlight color
    - <font color="red"> - legacy colored text
    - <h1> through <h6> - heading styles
    - <p>, <div>, <br> - paragraphs and line breaks
    - <ul>, <ol>, <li> - bullet and numbered lists
    - <table>, <tr>, <td>, <th> - tables

    Args:
        document_id: The document ID or URL.
        html_text: The HTML text to insert.
        index: The index at which to insert (default: 1).
        tab_id: Optional tab ID to insert HTML into a specific tab.
    """
    if index < 1:
        index = 1

    # Parse HTML into elements and formatting operations
    parsed = _parse_html(html_text)

    pending_requests: list[dict[str, Any]] = []
    current_index = index
    last_result: dict[str, Any] = {"success": True}

    for element in parsed:
        elem_type = element.get("type", "text")

        if elem_type == "text":
            text = element.get("text", "")
            if not text:
                continue

            pending_requests.append(
                {
                    "insertText": {
                        "location": {"index": current_index},
                        "text": text,
                    }
                }
            )

            # Apply any formatting to this text
            text_end = current_index + _utf16_len(text)
            for fmt in element.get("formats", []):
                _add_html_format_request(pending_requests, current_index, text_end, fmt)

            current_index = text_end

        elif elem_type == "newline":
            pending_requests.append(
                {
                    "insertText": {
                        "location": {"index": current_index},
                        "text": "\n",
                    }
                }
            )
            current_index += 1

        elif elem_type == "heading":
            text = element.get("text", "")
            level = element.get("level", 1)
            text_with_newline = text + "\n"

            pending_requests.append(
                {
                    "insertText": {
                        "location": {"index": current_index},
                        "text": text_with_newline,
                    }
                }
            )

            heading_map = {
                1: "HEADING_1",
                2: "HEADING_2",
                3: "HEADING_3",
                4: "HEADING_4",
                5: "HEADING_5",
                6: "HEADING_6",
            }
            pending_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": current_index,
                            "endIndex": current_index + _utf16_len(text_with_newline),
                        },
                        "paragraphStyle": {
                            "namedStyleType": heading_map.get(level, "HEADING_1")
                        },
                        "fields": "namedStyleType",
                    }
                }
            )
            current_index += _utf16_len(text_with_newline)

        elif elem_type == "paragraph":
            text = element.get("text", "")
            if not text:
                continue

            text_with_newline = text + "\n"
            pending_requests.append(
                {
                    "insertText": {
                        "location": {"index": current_index},
                        "text": text_with_newline,
                    }
                }
            )

            pending_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": current_index,
                            "endIndex": current_index + _utf16_len(text_with_newline),
                        },
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "fields": "namedStyleType",
                    }
                }
            )

            # Apply inline formatting within paragraph
            for fmt in element.get("formats", []):
                start = current_index + _utf16_offset(text, fmt.get("start", 0))
                end = current_index + _utf16_offset(text, fmt.get("end", len(text)))
                _add_html_format_request(pending_requests, start, end, fmt)

            current_index += _utf16_len(text_with_newline)

        elif elem_type == "table":
            # Tables need their own API call
            if pending_requests:
                last_result = batch_update(document_id, pending_requests, tab_id)
                if not last_result.get("success"):
                    return last_result
                pending_requests = []

            table_result = _insert_html_table(
                document_id, element, current_index, tab_id
            )
            if not table_result.get("success"):
                return table_result
            current_index = table_result.get("end_index", current_index)
            last_result = table_result

        elif elem_type == "bullet_list":
            items = element.get("items", [])
            start_index = current_index

            for item in items:
                item_text = item.get("text", "") + "\n"
                pending_requests.append(
                    {
                        "insertText": {
                            "location": {"index": current_index},
                            "text": item_text,
                        }
                    }
                )
                # Apply item-level formatting
                for fmt in item.get("formats", []):
                    item_raw_text = item.get("text", "")
                    start = current_index + _utf16_offset(
                        item_raw_text, fmt.get("start", 0)
                    )
                    end = current_index + _utf16_offset(
                        item_raw_text, fmt.get("end", len(item_raw_text))
                    )
                    _add_html_format_request(pending_requests, start, end, fmt)

                current_index += _utf16_len(item_text)

            pending_requests.append(
                {
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": start_index,
                            "endIndex": current_index,
                        },
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

        elif elem_type == "numbered_list":
            items = element.get("items", [])
            start_index = current_index

            for item in items:
                item_text = item.get("text", "") + "\n"
                pending_requests.append(
                    {
                        "insertText": {
                            "location": {"index": current_index},
                            "text": item_text,
                        }
                    }
                )
                # Apply item-level formatting
                for fmt in item.get("formats", []):
                    item_raw_text = item.get("text", "")
                    start = current_index + _utf16_offset(
                        item_raw_text, fmt.get("start", 0)
                    )
                    end = current_index + _utf16_offset(
                        item_raw_text, fmt.get("end", len(item_raw_text))
                    )
                    _add_html_format_request(pending_requests, start, end, fmt)

                current_index += _utf16_len(item_text)

            pending_requests.append(
                {
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": start_index,
                            "endIndex": current_index,
                        },
                        "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
                    }
                }
            )

    # Flush remaining requests
    if pending_requests:
        last_result = batch_update(document_id, pending_requests, tab_id)

    return last_result


# ---------------------------------------------------------------------------
# HTML parser and helpers
# ---------------------------------------------------------------------------


# Named HTML colors to RGB (0.0-1.0 range)
_HTML_COLORS: dict[str, dict[str, float]] = {
    "black": {"red": 0.0, "green": 0.0, "blue": 0.0},
    "white": {"red": 1.0, "green": 1.0, "blue": 1.0},
    "red": {"red": 1.0, "green": 0.0, "blue": 0.0},
    "green": {"red": 0.0, "green": 0.5, "blue": 0.0},
    "blue": {"red": 0.0, "green": 0.0, "blue": 1.0},
    "yellow": {"red": 1.0, "green": 1.0, "blue": 0.0},
    "cyan": {"red": 0.0, "green": 1.0, "blue": 1.0},
    "magenta": {"red": 1.0, "green": 0.0, "blue": 1.0},
    "gray": {"red": 0.5, "green": 0.5, "blue": 0.5},
    "grey": {"red": 0.5, "green": 0.5, "blue": 0.5},
    "orange": {"red": 1.0, "green": 0.65, "blue": 0.0},
    "purple": {"red": 0.5, "green": 0.0, "blue": 0.5},
    "pink": {"red": 1.0, "green": 0.75, "blue": 0.8},
    "brown": {"red": 0.65, "green": 0.16, "blue": 0.16},
    "lime": {"red": 0.0, "green": 1.0, "blue": 0.0},
    "navy": {"red": 0.0, "green": 0.0, "blue": 0.5},
    "teal": {"red": 0.0, "green": 0.5, "blue": 0.5},
    "maroon": {"red": 0.5, "green": 0.0, "blue": 0.0},
    "olive": {"red": 0.5, "green": 0.5, "blue": 0.0},
}


def _parse_html_color(color_str: str) -> dict[str, float] | None:
    """Parse a CSS color string to RGB dict (0.0-1.0 range).

    Supports:
    - Hex: #rgb, #rrggbb
    - RGB: rgb(r, g, b)
    - Named: red, blue, green, etc.
    """
    color_str = color_str.strip().lower()
    if not color_str:
        return None

    # Hex color
    if color_str.startswith("#"):
        hex_val = color_str[1:]
        if len(hex_val) == 3:
            hex_val = hex_val[0] * 2 + hex_val[1] * 2 + hex_val[2] * 2
        if len(hex_val) == 6 and all(c in "0123456789abcdef" for c in hex_val):
            r = int(hex_val[0:2], 16) / 255.0
            g = int(hex_val[2:4], 16) / 255.0
            b = int(hex_val[4:6], 16) / 255.0
            return {"red": r, "green": g, "blue": b}

    # RGB function
    rgb_match = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", color_str)
    if rgb_match:
        r = min(255, int(rgb_match.group(1))) / 255.0
        g = min(255, int(rgb_match.group(2))) / 255.0
        b = min(255, int(rgb_match.group(3))) / 255.0
        return {"red": r, "green": g, "blue": b}

    # Named color
    return _HTML_COLORS.get(color_str)


def _parse_inline_style(style: str) -> dict[str, str]:
    """Parse CSS inline style string to key-value pairs."""
    result: dict[str, str] = {}
    for part in style.split(";"):
        if ":" in part:
            key, value = part.split(":", 1)
            result[key.strip().lower()] = value.strip()
    return result


def _add_html_format_request(
    requests: list[dict[str, Any]],
    start: int,
    end: int,
    fmt: dict[str, Any],
) -> None:
    """Add an updateTextStyle request for an HTML formatting operation."""
    fmt_type = fmt.get("type", "")

    if fmt_type == "bold":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"bold": True},
                    "fields": "bold",
                }
            }
        )
    elif fmt_type == "italic":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"italic": True},
                    "fields": "italic",
                }
            }
        )
    elif fmt_type == "underline":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"underline": True},
                    "fields": "underline",
                }
            }
        )
    elif fmt_type == "strikethrough":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"strikethrough": True},
                    "fields": "strikethrough",
                }
            }
        )
    elif fmt_type == "code":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {
                        "weightedFontFamily": {
                            "fontFamily": "Courier New",
                            "weight": 400,
                        },
                    },
                    "fields": "weightedFontFamily",
                }
            }
        )
    elif fmt_type == "link":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"link": {"url": fmt.get("url", "")}},
                    "fields": "link",
                }
            }
        )
    elif fmt_type == "superscript":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"baselineOffset": "SUPERSCRIPT"},
                    "fields": "baselineOffset",
                }
            }
        )
    elif fmt_type == "subscript":
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": start, "endIndex": end},
                    "textStyle": {"baselineOffset": "SUBSCRIPT"},
                    "fields": "baselineOffset",
                }
            }
        )
    elif fmt_type == "foreground_color":
        color = fmt.get("color")
        if color:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "foregroundColor": {"color": {"rgbColor": color}}
                        },
                        "fields": "foregroundColor",
                    }
                }
            )
    elif fmt_type == "background_color":
        color = fmt.get("color")
        if color:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "backgroundColor": {"color": {"rgbColor": color}}
                        },
                        "fields": "backgroundColor",
                    }
                }
            )


def _parse_html(html: str) -> list[dict[str, Any]]:
    """Parse HTML string into a list of element dictionaries.

    Each element has a 'type' and type-specific data.
    Uses Python's html.parser for basic HTML parsing.
    """
    from html.parser import HTMLParser

    elements: list[dict[str, Any]] = []
    format_stack: list[dict[str, Any]] = []
    current_text = ""
    current_formats: list[dict[str, Any]] = []
    in_list: str | None = None  # "ul" or "ol"
    list_items: list[dict[str, Any]] = []
    in_table = False
    table_rows: list[list[dict[str, Any]]] = []
    current_row: list[dict[str, Any]] = []
    cell_meta_stack: list[dict[str, Any]] = []

    class HtmlContentParser(HTMLParser):
        def handle_starttag(
            self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
            nonlocal current_text, in_list, list_items
            nonlocal in_table, table_rows, current_row, cell_meta_stack

            attrs_dict = {k: v for k, v in attrs if v is not None}
            tag = tag.lower()

            if tag in ("b", "strong"):
                format_stack.append({"type": "bold", "start": len(current_text)})
            elif tag in ("i", "em"):
                format_stack.append({"type": "italic", "start": len(current_text)})
            elif tag == "u":
                format_stack.append({"type": "underline", "start": len(current_text)})
            elif tag in ("s", "strike", "del"):
                format_stack.append(
                    {"type": "strikethrough", "start": len(current_text)}
                )
            elif tag == "code":
                format_stack.append({"type": "code", "start": len(current_text)})
            elif tag == "sup":
                format_stack.append({"type": "superscript", "start": len(current_text)})
            elif tag == "sub":
                format_stack.append({"type": "subscript", "start": len(current_text)})
            elif tag == "a":
                href = attrs_dict.get("href", "")
                format_stack.append(
                    {"type": "link", "start": len(current_text), "url": href}
                )
            elif tag == "span":
                style = attrs_dict.get("style", "")
                styles = _parse_inline_style(style)
                if "color" in styles:
                    color = _parse_html_color(styles["color"])
                    if color:
                        format_stack.append(
                            {
                                "type": "foreground_color",
                                "start": len(current_text),
                                "color": color,
                            }
                        )
                if "background-color" in styles:
                    color = _parse_html_color(styles["background-color"])
                    if color:
                        format_stack.append(
                            {
                                "type": "background_color",
                                "start": len(current_text),
                                "color": color,
                            }
                        )
            elif tag == "font":
                color_attr = attrs_dict.get("color", "")
                if color_attr:
                    color = _parse_html_color(color_attr)
                    if color:
                        format_stack.append(
                            {
                                "type": "foreground_color",
                                "start": len(current_text),
                                "color": color,
                            }
                        )
            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                _flush_text()
                level = int(tag[1])
                format_stack.append({"type": "heading", "level": level})
            elif tag == "p":
                _flush_text()
            elif tag == "br":
                current_text += "\n"
            elif tag == "ul":
                _flush_text()
                in_list = "ul"
                list_items = []
            elif tag == "ol":
                _flush_text()
                in_list = "ol"
                list_items = []
            elif tag == "li":
                pass  # Text will be collected
            elif tag == "table":
                _flush_text()
                in_table = True
                table_rows = []
            elif tag == "tr":
                current_row = []
            elif tag in ("td", "th"):
                meta: dict[str, Any] = {}
                if tag == "th":
                    meta["is_header"] = True
                style = attrs_dict.get("style", "")
                if style:
                    styles = _parse_inline_style(style)
                    bg_str = styles.get("background-color") or styles.get("background")
                    if bg_str:
                        bg_color = _parse_html_color(bg_str)
                        if bg_color:
                            meta["background_color"] = bg_color
                bgcolor_attr = attrs_dict.get("bgcolor", "")
                if bgcolor_attr and "background_color" not in meta:
                    bg_color = _parse_html_color(bgcolor_attr)
                    if bg_color:
                        meta["background_color"] = bg_color
                cell_meta_stack.append(meta)

        def handle_endtag(self, tag: str) -> None:
            nonlocal current_text, current_formats
            nonlocal in_list, list_items, in_table, table_rows, current_row
            nonlocal cell_meta_stack

            tag = tag.lower()

            if tag in (
                "b",
                "strong",
                "i",
                "em",
                "u",
                "s",
                "strike",
                "del",
                "code",
                "sup",
                "sub",
                "a",
                "span",
                "font",
            ):
                # Find matching format in stack
                for i in range(len(format_stack) - 1, -1, -1):
                    fmt = format_stack[i]
                    if (
                        (tag in ("b", "strong") and fmt["type"] == "bold")
                        or (tag in ("i", "em") and fmt["type"] == "italic")
                        or (tag == "u" and fmt["type"] == "underline")
                        or (
                            tag in ("s", "strike", "del")
                            and fmt["type"] == "strikethrough"
                        )
                        or (tag == "code" and fmt["type"] == "code")
                        or (tag == "sup" and fmt["type"] == "superscript")
                        or (tag == "sub" and fmt["type"] == "subscript")
                        or (tag == "a" and fmt["type"] == "link")
                        or (
                            tag == "span"
                            and fmt["type"] in ("foreground_color", "background_color")
                        )
                        or (tag == "font" and fmt["type"] == "foreground_color")
                    ):
                        fmt["end"] = len(current_text)
                        current_formats.append(fmt)
                        format_stack.pop(i)
                        break

            elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
                # Find heading in stack
                for i in range(len(format_stack) - 1, -1, -1):
                    fmt = format_stack[i]
                    if fmt["type"] == "heading":
                        elements.append(
                            {
                                "type": "heading",
                                "text": current_text.strip(),
                                "level": fmt["level"],
                            }
                        )
                        current_text = ""
                        current_formats = []
                        format_stack.pop(i)
                        break

            elif tag == "p":
                if current_text.strip():
                    elements.append(
                        {
                            "type": "paragraph",
                            "text": current_text.strip(),
                            "formats": current_formats,
                        }
                    )
                current_text = ""
                current_formats = []

            elif tag == "li":
                if in_list:
                    list_items.append(
                        {"text": current_text.strip(), "formats": current_formats}
                    )
                    current_text = ""
                    current_formats = []

            elif tag in ("ul", "ol"):
                if in_list == "ul":
                    elements.append({"type": "bullet_list", "items": list_items})
                elif in_list == "ol":
                    elements.append({"type": "numbered_list", "items": list_items})
                in_list = None
                list_items = []

            elif tag in ("td", "th"):
                cell_entry: dict[str, Any] = {
                    "text": current_text.strip(),
                    "formats": current_formats,
                }
                if cell_meta_stack:
                    meta = cell_meta_stack.pop()
                    if meta.get("is_header"):
                        cell_entry["is_header"] = True
                    if "background_color" in meta:
                        cell_entry["background_color"] = meta["background_color"]
                current_row.append(cell_entry)
                current_text = ""
                current_formats = []

            elif tag == "tr":
                if current_row:
                    table_rows.append(current_row)
                current_row = []

            elif tag == "table":
                if table_rows:
                    elements.append({"type": "table", "rows": table_rows})
                in_table = False
                table_rows = []

        def handle_data(self, data: str) -> None:
            nonlocal current_text
            # Normalize whitespace
            data = re.sub(r"[\r\n\t]+", " ", data)
            current_text += data

        def _flush_text() -> None:
            nonlocal current_text, current_formats
            if current_text.strip():
                elements.append(
                    {
                        "type": "text",
                        "text": current_text,
                        "formats": current_formats,
                    }
                )
            current_text = ""
            current_formats = []

    def _flush_text() -> None:
        nonlocal current_text, current_formats
        if current_text.strip():
            elements.append(
                {
                    "type": "text",
                    "text": current_text,
                    "formats": current_formats,
                }
            )
        current_text = ""
        current_formats = []

    parser = HtmlContentParser()
    parser._flush_text = _flush_text  # type: ignore
    parser.feed(html)

    # Flush any remaining text
    _flush_text()

    return elements


def _insert_html_table(
    document_id: str,
    table_element: dict[str, Any],
    index: int,
    tab_id: str | None = None,
) -> dict[str, Any]:
    """Insert an HTML table as a native Google Docs table.

    Returns dict with 'success' and 'end_index'.
    """
    rows = table_element.get("rows", [])
    if not rows:
        return {"success": True, "end_index": index}

    num_rows = len(rows)
    num_cols = max(len(row) for row in rows) if rows else 0

    if num_cols == 0:
        return {"success": True, "end_index": index}

    # Step 1: Insert empty table
    table_requests = [
        {
            "insertTable": {
                "location": {"index": index},
                "rows": num_rows,
                "columns": num_cols,
            }
        }
    ]
    result = batch_update(document_id, table_requests, tab_id)
    if not result.get("success"):
        return result

    # Clean up the extra empty paragraph that insertTable creates
    _remove_extra_table_paragraph(document_id, index, tab_id)

    # Step 2: Read document to get cell paragraph indices
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    if tab_id:
        endpoint += "?includeTabsContent=true"
    doc_result = call_google_api("GET", endpoint)
    if not doc_result.get("success"):
        return doc_result

    body = _get_body_from_doc(doc_result["data"], tab_id)
    content_elements = body.get("content", [])

    # Find the table element
    table_obj = None
    table_start = index
    for elem in content_elements:
        if "table" in elem and elem.get("startIndex", 0) >= index:
            table_obj = elem["table"]
            table_start = elem.get("startIndex", index)
            break

    if not table_obj:
        return {"success": False, "error": "Could not find inserted table"}

    table_rows_api = table_obj.get("tableRows", [])

    # Step 3: Populate cells (reverse order)
    cell_requests: list[dict[str, Any]] = []
    total_inserted = 0

    for row_idx in range(len(table_rows_api) - 1, -1, -1):
        row = table_rows_api[row_idx]
        cells = row.get("tableCells", [])

        for col_idx in range(len(cells) - 1, -1, -1):
            if row_idx >= len(rows) or col_idx >= len(rows[row_idx]):
                continue

            cell_data = rows[row_idx][col_idx]
            cell_text = cell_data.get("text", "")
            if not cell_text:
                continue

            total_inserted += _utf16_len(cell_text)

            cell = cells[col_idx]
            cell_paragraphs = cell.get("content", [])
            if not cell_paragraphs or "paragraph" not in cell_paragraphs[0]:
                continue

            cell_start = cell_paragraphs[0].get("startIndex", 0)

            cell_requests.append(
                {
                    "insertText": {
                        "location": {"index": cell_start},
                        "text": cell_text,
                    }
                }
            )

            # Normalize cell paragraph to NORMAL_TEXT so
            # font size matches document body text
            cell_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": cell_start,
                            "endIndex": cell_start + 1,
                        },
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "fields": "namedStyleType",
                    }
                }
            )

            # Apply cell formatting
            for fmt in cell_data.get("formats", []):
                start = cell_start + _utf16_offset(cell_text, fmt.get("start", 0))
                end = cell_start + _utf16_offset(
                    cell_text, fmt.get("end", len(cell_text))
                )
                _add_html_format_request(cell_requests, start, end, fmt)

            # Auto-bold header cells
            if cell_data.get("is_header") and cell_text:
                cell_requests.append(
                    {
                        "updateTextStyle": {
                            "range": {
                                "startIndex": cell_start,
                                "endIndex": cell_start + len(cell_text),
                            },
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    }
                )

    if cell_requests:
        result = batch_update(document_id, cell_requests, tab_id)
        if not result.get("success"):
            return result

    # Step 4: Apply cell background colors
    style_requests: list[dict[str, Any]] = []
    for row_idx, row_data in enumerate(rows):
        for col_idx, cell_data in enumerate(row_data):
            bg_color = cell_data.get("background_color")
            if bg_color is None:
                continue
            style_requests.append(
                {
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": table_start},
                                "rowIndex": row_idx,
                                "columnIndex": col_idx,
                            },
                            "rowSpan": 1,
                            "columnSpan": 1,
                        },
                        "tableCellStyle": {
                            "backgroundColor": {"color": {"rgbColor": bg_color}}
                        },
                        "fields": "backgroundColor",
                    }
                }
            )
    if style_requests:
        result = batch_update(document_id, style_requests, tab_id)
        if not result.get("success"):
            return result

    # Compute end index
    original_table_end = (
        content_elements[-1].get("endIndex", index) if content_elements else index
    )
    end_index = table_start + (num_rows * num_cols * 2) + total_inserted + 4

    return {"success": True, "end_index": end_index}


# ---------------------------------------------------------------------------
# Markdown block parser and helpers
# ---------------------------------------------------------------------------


def _get_body_from_doc(
    doc: dict[str, Any], tab_id: str | None = None
) -> dict[str, Any]:
    """Extract body content from a document, optionally from a specific tab.

    Traverses child tabs recursively so that nested/sub-tabs are found.
    """
    if tab_id:

        def _find_body(tabs: list[dict[str, Any]]) -> dict[str, Any] | None:
            for tab in tabs:
                if tab.get("tabProperties", {}).get("tabId") == tab_id:
                    return tab.get("documentTab", {}).get("body", {})
                result = _find_body(tab.get("childTabs", []))
                if result is not None:
                    return result
            return None

        return _find_body(doc.get("tabs", [])) or {}
    tabs = doc.get("tabs", [])
    if tabs:
        return tabs[0].get("documentTab", {}).get("body", {})
    return doc.get("body", {})


def _get_lists_from_doc(
    doc: dict[str, Any], tab_id: str | None = None
) -> dict[str, Any]:
    """Extract the lists map from a document, optionally from a specific tab.

    The lists map defines list types (bullet vs numbered) and glyph styles
    per nesting level.
    """
    tabs = doc.get("tabs", [])
    if not tabs:
        return doc.get("lists", {})

    if tab_id:

        def _find_lists(tabs: list[dict[str, Any]]) -> dict[str, Any] | None:
            for tab in tabs:
                if tab.get("tabProperties", {}).get("tabId") == tab_id:
                    return tab.get("documentTab", {}).get("lists", {})
                result = _find_lists(tab.get("childTabs", []))
                if result is not None:
                    return result
            return None

        return _find_lists(tabs) or {}

    return tabs[0].get("documentTab", {}).get("lists", {})


def _remove_extra_table_paragraph(
    document_id: str,
    index: int,
    tab_id: str | None = None,
) -> None:
    """Remove the extra empty paragraph that insertTable creates before a table.

    After insertTable, Google Docs inserts an empty paragraph before the table.
    This function finds and removes it by deleting the newline at the end of the
    preceding paragraph, merging the two elements.
    """
    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    if tab_id:
        endpoint += "?includeTabsContent=true"
    doc_result = call_google_api("GET", endpoint)
    if not doc_result.get("success"):
        return

    body = _get_body_from_doc(doc_result["data"], tab_id)
    content = body.get("content", [])

    for i in range(len(content) - 1):
        elem = content[i]
        next_elem = content[i + 1]
        if "paragraph" not in elem or "table" not in next_elem:
            continue
        if next_elem.get("startIndex", 0) < index:
            continue
        # Check if the paragraph is empty (just a newline)
        para_text = ""
        for pe in elem["paragraph"].get("elements", []):
            para_text += pe.get("textRun", {}).get("content", "")
        if para_text not in ("\n", ""):
            continue
        start = elem.get("startIndex", 0)
        # Don't merge into the very first element (section break)
        if start > 1:
            batch_update(
                doc_id,
                [
                    {
                        "deleteContentRange": {
                            "range": {"startIndex": start - 1, "endIndex": start}
                        }
                    }
                ],
                tab_id,
            )
        break


def _parse_markdown_blocks(
    markdown_text: str,
) -> list[tuple[str, dict[str, Any]]]:
    """Parse markdown into a list of (block_type, content) tuples.

    Block types and their content dicts:
    - "text":           {"line": str}
    - "code_block":     {"language": str, "lines": [str]}
    - "table":          {"headers": [str], "rows": [[str]]}
    - "bullet_list":    {"items": [{"text": str, "indent": int}]}
    - "numbered_list":  {"items": [{"text": str, "indent": int}]}
    - "horizontal_rule": {}
    - "block_quote":    {"lines": [str]}
    """
    blocks: list[tuple[str, dict[str, Any]]] = []
    lines = markdown_text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # --- Fenced code block ---
        if line.startswith("```"):
            language = line[3:].strip()
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1  # skip closing ```
            blocks.append(("code_block", {"language": language, "lines": code_lines}))
            continue

        # --- Horizontal rule (---, ***, ___  with optional spaces) ---
        if re.match(r"^(\s*[-*_]\s*){3,}$", line):
            blocks.append(("horizontal_rule", {}))
            i += 1
            continue

        # --- Table (lines starting with |) ---
        if line.startswith("|"):
            table_lines: list[str] = []
            while i < len(lines) and lines[i].startswith("|"):
                table_lines.append(lines[i])
                i += 1
            headers, rows = _parse_table_lines(table_lines)
            if headers:
                blocks.append(("table", {"headers": headers, "rows": rows}))
            continue

        # --- Block quote (lines starting with >) ---
        if line.startswith(">"):
            quote_lines: list[str] = []
            while i < len(lines) and lines[i].startswith(">"):
                # Strip the > prefix (and optional space after it)
                stripped = re.sub(r"^>\s?", "", lines[i])
                quote_lines.append(stripped)
                i += 1
            blocks.append(("block_quote", {"lines": quote_lines}))
            continue

        # --- Bullet list (- or * or + followed by a space, with optional indent) ---
        if re.match(r"^(\s*)[-*+] ", line):
            items: list[dict[str, str | int]] = []
            while i < len(lines) and re.match(r"^(\s*)[-*+] ", lines[i]):
                m = re.match(r"^(\s*)[-*+] (.*)", lines[i])
                if m:
                    indent = len(m.group(1)) // 2  # 2 spaces = 1 nesting level
                    items.append({"text": m.group(2), "indent": indent})
                i += 1
            blocks.append(("bullet_list", {"items": items}))
            continue

        # --- Numbered list (digit(s) followed by . and space, with optional indent) ---
        if re.match(r"^(\s*)\d+\. ", line):
            n_items: list[dict[str, str | int]] = []
            while i < len(lines) and re.match(r"^(\s*)\d+\. ", lines[i]):
                m = re.match(r"^(\s*)\d+\. (.*)", lines[i])
                if m:
                    indent = len(m.group(1)) // 3  # 3 spaces = 1 nesting level
                    n_items.append({"text": m.group(2), "indent": indent})
                i += 1
            blocks.append(("numbered_list", {"items": n_items}))
            continue

        # --- Regular text line (headings, paragraphs, blank lines) ---
        blocks.append(("text", {"line": line}))
        i += 1

    # Post-process: remove blank lines adjacent to headings.
    # In markdown, blank lines around headings are required syntax but
    # create excessive whitespace in a Google Doc.
    return _remove_heading_adjacent_blanks(blocks)


def _remove_heading_adjacent_blanks(
    blocks: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, dict[str, Any]]]:
    """Remove blank text lines that are immediately before or after a heading,
    horizontal rule, or list block.

    Markdown requires blank lines around these elements, but in a Google Doc
    they create excessive vertical whitespace.  Blank lines between normal
    content paragraphs are preserved.

    Blank lines immediately after lists are also removed (unless they precede
    a heading/horizontal rule) because in Google Docs the blank paragraph
    inherits the preceding list's bullet/number formatting, causing the
    following paragraph to be rendered as a list member.
    """

    def _is_blank(b: tuple[str, dict[str, Any]]) -> bool:
        return b[0] == "text" and b[1]["line"].strip() == ""

    def _is_structural(b: tuple[str, dict[str, Any]]) -> bool:
        """Return True for block types that don't need adjacent blank lines."""
        if b[0] == "horizontal_rule":
            return True
        return b[0] == "text" and b[1]["line"].startswith("#")

    def _is_list(b: tuple[str, dict[str, Any]]) -> bool:
        return b[0] in ("bullet_list", "numbered_list")

    result: list[tuple[str, dict[str, Any]]] = []
    for i, block in enumerate(blocks):
        if _is_blank(block):
            prev_structural = i > 0 and _is_structural(blocks[i - 1])
            next_structural = i < len(blocks) - 1 and _is_structural(blocks[i + 1])
            prev_is_list = i > 0 and _is_list(blocks[i - 1])
            # Keep blank lines after lists when followed by a heading/HR --
            # needed to prevent Google Docs from merging the last list item
            # with the heading paragraph.
            if prev_is_list and next_structural:
                result.append(block)
                continue
            # Remove blank lines right after lists to prevent the following
            # paragraph from inheriting list formatting when inserted into
            # Google Docs.
            if prev_is_list:
                continue
            if prev_structural or next_structural:
                continue
        result.append(block)
    return result


def _parse_table_lines(
    table_lines: list[str],
) -> tuple[list[str], list[list[str]]]:
    """Parse markdown table lines into (headers, data_rows).

    Skips separator rows like |---|---|.
    Respects backtick-quoted content (pipes inside `...` are not separators).
    """
    if not table_lines:
        return [], []

    headers: list[str] = []
    rows: list[list[str]] = []

    for line in table_lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]

        cells = _split_table_row(stripped)

        # Skip separator rows (cells are all dashes, optionally with colons)
        non_empty = [c for c in cells if c]
        if non_empty and all(re.match(r"^:?-+:?$", c) for c in non_empty):
            continue

        if not headers:
            headers = cells
        else:
            # Pad / trim to match header column count
            while len(cells) < len(headers):
                cells.append("")
            rows.append(cells[: len(headers)])

    return headers, rows


def _split_table_row(text: str) -> list[str]:
    """Split a table row on | but respect backtick-quoted spans.

    Pipes inside `...` are treated as literal characters, not separators.
    """
    cells: list[str] = []
    current = ""
    in_backtick = False

    for ch in text:
        if ch == "`":
            in_backtick = not in_backtick
            current += ch
        elif ch == "|" and not in_backtick:
            cells.append(current.strip())
            current = ""
        else:
            current += ch

    cells.append(current.strip())
    return cells


def _add_inline_format_requests(
    format_ranges: list[dict[str, Any]],
    base_index: int,
    requests: list[dict[str, Any]],
    text: str = "",
) -> None:
    """Append updateTextStyle requests for inline bold / italic / code / link / strikethrough."""
    for fmt in format_ranges:
        if text:
            start = base_index + _utf16_offset(text, fmt["start"])
            end = base_index + _utf16_offset(text, fmt["end"])
        else:
            start = base_index + fmt["start"]
            end = base_index + fmt["end"]
        style = fmt["style"]

        if style == "bold":
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                }
            )
        elif style == "italic":
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"italic": True},
                        "fields": "italic",
                    }
                }
            )
        elif style == "strikethrough":
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {"strikethrough": True},
                        "fields": "strikethrough",
                    }
                }
            )
        elif style == "code":
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "weightedFontFamily": {
                                "fontFamily": "Courier New",
                                "weight": 400,
                            },
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.95,
                                        "green": 0.95,
                                        "blue": 0.95,
                                    }
                                }
                            },
                        },
                        "fields": "weightedFontFamily,backgroundColor",
                    }
                }
            )
        elif style == "link":
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {"startIndex": start, "endIndex": end},
                        "textStyle": {
                            "link": {"url": fmt.get("url", "")},
                        },
                        "fields": "link",
                    }
                }
            )


_PARAGRAPH_RESET_FIELDS = (
    "namedStyleType,"
    "indentStart,indentFirstLine,indentEnd,"
    "shading,"
    "borderTop,borderBottom,borderLeft,borderRight,borderBetween,"
    "spaceAbove,spaceBelow"
)


def _build_text_line_requests(
    content: dict[str, Any],
    current_index: int,
    requests: list[dict[str, Any]],
    clear_bullets: bool = False,
) -> int:
    """Build batch requests for a single text line. Returns new current_index."""
    line = content["line"]
    heading_level = 0
    text = line

    heading_match = re.match(r"^(#{1,6})\s+(.*)", line)
    if heading_match:
        heading_level = len(heading_match.group(1))
        text = heading_match.group(2)

    stripped_text, format_ranges = strip_and_parse_inline_formatting(text)
    text_with_newline = stripped_text + "\n"

    requests.append(
        {
            "insertText": {
                "location": {"index": current_index},
                "text": text_with_newline,
            }
        }
    )

    # Clear inherited list formatting from a preceding bullet/numbered list
    if clear_bullets:
        requests.append(
            {
                "deleteParagraphBullets": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": current_index + _utf16_len(text_with_newline),
                    }
                }
            }
        )

    heading_map = {
        1: "HEADING_1",
        2: "HEADING_2",
        3: "HEADING_3",
        4: "HEADING_4",
        5: "HEADING_5",
        6: "HEADING_6",
    }
    named_style = heading_map[heading_level] if heading_level > 0 else "NORMAL_TEXT"
    requests.append(
        {
            "updateParagraphStyle": {
                "range": {
                    "startIndex": current_index,
                    "endIndex": current_index + _utf16_len(text_with_newline),
                },
                "paragraphStyle": {"namedStyleType": named_style},
                "fields": _PARAGRAPH_RESET_FIELDS,
            }
        }
    )

    _add_inline_format_requests(format_ranges, current_index, requests, stripped_text)

    return current_index + _utf16_len(text_with_newline)


def _build_code_block_requests(
    content: dict[str, Any],
    current_index: int,
    requests: list[dict[str, Any]],
    clear_bullets: bool = False,
) -> int:
    """Build batch requests for a fenced code block. Returns new current_index.

    Creates a visually distinct code block using paragraph-level borders and
    shading plus monospace font.
    """
    code_lines = content["lines"]
    code_text = "\n".join(code_lines) + "\n"

    if not code_text.strip():
        # Empty code block - just insert a blank line
        requests.append(
            {
                "insertText": {
                    "location": {"index": current_index},
                    "text": "\n",
                }
            }
        )
        return current_index + 1

    requests.append(
        {
            "insertText": {
                "location": {"index": current_index},
                "text": code_text,
            }
        }
    )

    code_end = current_index + _utf16_len(code_text)

    # Clear inherited list formatting
    if clear_bullets:
        requests.append(
            {
                "deleteParagraphBullets": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": code_end,
                    }
                }
            }
        )

    # Monospace font
    requests.append(
        {
            "updateTextStyle": {
                "range": {
                    "startIndex": current_index,
                    "endIndex": code_end,
                },
                "textStyle": {
                    "weightedFontFamily": {
                        "fontFamily": "Courier New",
                        "weight": 400,
                    },
                },
                "fields": "weightedFontFamily",
            }
        }
    )

    # Paragraph-level shading and borders to create a code block box.
    _BORDER = {
        "color": {"color": {"rgbColor": {"red": 0.82, "green": 0.82, "blue": 0.82}}},
        "width": {"magnitude": 0.5, "unit": "PT"},
        "padding": {"magnitude": 6, "unit": "PT"},
        "dashStyle": "SOLID",
    }
    _NO_BORDER = {
        "color": {},
        "width": {"unit": "PT"},
        "padding": {"unit": "PT"},
        "dashStyle": "SOLID",
    }

    requests.append(
        {
            "updateParagraphStyle": {
                "range": {
                    "startIndex": current_index,
                    "endIndex": code_end,
                },
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "shading": {
                        "backgroundColor": {
                            "color": {
                                "rgbColor": {
                                    "red": 0.95,
                                    "green": 0.95,
                                    "blue": 0.95,
                                }
                            }
                        }
                    },
                    "borderTop": _BORDER,
                    "borderBottom": _BORDER,
                    "borderLeft": _BORDER,
                    "borderRight": _BORDER,
                    "borderBetween": _NO_BORDER,
                    "lineSpacing": 100,
                    "spaceAbove": {"unit": "PT"},
                    "spaceBelow": {"unit": "PT"},
                },
                "fields": _PARAGRAPH_RESET_FIELDS + ",lineSpacing",
            }
        }
    )

    return code_end


def _build_bullet_list_requests(
    content: dict[str, Any],
    current_index: int,
    requests: list[dict[str, Any]],
) -> int:
    """Build batch requests for a bullet list with nesting. Returns new current_index."""
    items = content["items"]
    start_index = current_index

    for item in items:
        # Support both old format (str) and new format (dict with text/indent)
        if isinstance(item, str):
            text = item
            indent = 0
        else:
            text = item["text"]
            indent = item.get("indent", 0)

        stripped_text, format_ranges = strip_and_parse_inline_formatting(text)
        # Tab chars before text establish nesting when createParagraphBullets runs
        prefix = "\t" * indent if indent > 0 else ""
        item_text = prefix + stripped_text + "\n"

        requests.append(
            {
                "insertText": {
                    "location": {"index": current_index},
                    "text": item_text,
                }
            }
        )
        # Offset format ranges past the tab prefix
        _add_inline_format_requests(
            format_ranges, current_index + len(prefix), requests, stripped_text
        )

        # Reset inherited formatting and set indent level for all items.
        # indent=0 → indentStart=36, indentFirstLine=18 (standard bullet indent)
        # indent=N → indentStart=36*(N+1), indentFirstLine=18*(N+1) (nested)
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": current_index + _utf16_len(item_text),
                    },
                    "paragraphStyle": {
                        "namedStyleType": "NORMAL_TEXT",
                        "indentStart": {
                            "magnitude": 36 * (indent + 1),
                            "unit": "PT",
                        },
                        "indentFirstLine": {
                            "magnitude": 18 * (indent + 1),
                            "unit": "PT",
                        },
                    },
                    "fields": _PARAGRAPH_RESET_FIELDS,
                }
            }
        )

        current_index += _utf16_len(item_text)

    # Apply bullet formatting to the whole range
    requests.append(
        {
            "createParagraphBullets": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": current_index,
                },
                "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
            }
        }
    )

    return current_index


def _build_numbered_list_requests(
    content: dict[str, Any],
    current_index: int,
    requests: list[dict[str, Any]],
) -> int:
    """Build batch requests for a numbered list with nesting. Returns new current_index."""
    items = content["items"]
    start_index = current_index

    for item in items:
        if isinstance(item, str):
            text = item
            indent = 0
        else:
            text = item["text"]
            indent = item.get("indent", 0)

        stripped_text, format_ranges = strip_and_parse_inline_formatting(text)
        # Tab chars before text establish nesting when createParagraphBullets runs
        prefix = "\t" * indent if indent > 0 else ""
        item_text = prefix + stripped_text + "\n"

        requests.append(
            {
                "insertText": {
                    "location": {"index": current_index},
                    "text": item_text,
                }
            }
        )
        # Offset format ranges past the tab prefix
        _add_inline_format_requests(
            format_ranges, current_index + len(prefix), requests, stripped_text
        )

        # Reset inherited formatting and set indent level for all items.
        # indent=0 → indentStart=36, indentFirstLine=18 (standard list indent)
        # indent=N → indentStart=36*(N+1), indentFirstLine=18*(N+1) (nested)
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": current_index + _utf16_len(item_text),
                    },
                    "paragraphStyle": {
                        "namedStyleType": "NORMAL_TEXT",
                        "indentStart": {
                            "magnitude": 36 * (indent + 1),
                            "unit": "PT",
                        },
                        "indentFirstLine": {
                            "magnitude": 18 * (indent + 1),
                            "unit": "PT",
                        },
                    },
                    "fields": _PARAGRAPH_RESET_FIELDS,
                }
            }
        )

        current_index += _utf16_len(item_text)

    requests.append(
        {
            "createParagraphBullets": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": current_index,
                },
                "bulletPreset": "NUMBERED_DECIMAL_ALPHA_ROMAN",
            }
        }
    )

    return current_index


def _build_horizontal_rule_requests(
    current_index: int,
    requests: list[dict[str, Any]],
    clear_bullets: bool = False,
) -> int:
    """Build batch requests for a horizontal rule. Returns new current_index.

    Inserts a blank paragraph styled with a bottom border to create a visible
    horizontal divider line.
    """
    requests.append(
        {
            "insertText": {
                "location": {"index": current_index},
                "text": "\n",
            }
        }
    )

    if clear_bullets:
        requests.append(
            {
                "deleteParagraphBullets": {
                    "range": {
                        "startIndex": current_index,
                        "endIndex": current_index + 1,
                    }
                }
            }
        )

    requests.append(
        {
            "updateParagraphStyle": {
                "range": {
                    "startIndex": current_index,
                    "endIndex": current_index + 1,
                },
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "borderBottom": {
                        "color": {
                            "color": {
                                "rgbColor": {
                                    "red": 0.7,
                                    "green": 0.7,
                                    "blue": 0.7,
                                }
                            }
                        },
                        "width": {"magnitude": 1, "unit": "PT"},
                        "padding": {"magnitude": 6, "unit": "PT"},
                        "dashStyle": "SOLID",
                    },
                    "spaceBelow": {"magnitude": 6, "unit": "PT"},
                },
                "fields": _PARAGRAPH_RESET_FIELDS,
            }
        }
    )

    return current_index + 1


def _build_block_quote_requests(
    content: dict[str, Any],
    current_index: int,
    requests: list[dict[str, Any]],
    clear_bullets: bool = False,
) -> int:
    """Build batch requests for a block quote. Returns new current_index.

    Creates a visually distinct block quote with a left border bar, indentation,
    and italic text.
    """
    quote_lines = content["lines"]
    start_index = current_index

    for line in quote_lines:
        stripped_text, format_ranges = strip_and_parse_inline_formatting(line)
        line_text = stripped_text + "\n"

        requests.append(
            {
                "insertText": {
                    "location": {"index": current_index},
                    "text": line_text,
                }
            }
        )
        _add_inline_format_requests(
            format_ranges, current_index, requests, stripped_text
        )
        current_index += _utf16_len(line_text)

    # Apply block quote paragraph styling: left border + indent + italic
    _QUOTE_BORDER = {
        "color": {"color": {"rgbColor": {"red": 0.6, "green": 0.6, "blue": 0.6}}},
        "width": {"magnitude": 2, "unit": "PT"},
        "padding": {"magnitude": 8, "unit": "PT"},
        "dashStyle": "SOLID",
    }

    requests.append(
        {
            "updateParagraphStyle": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": current_index,
                },
                "paragraphStyle": {
                    "namedStyleType": "NORMAL_TEXT",
                    "borderLeft": _QUOTE_BORDER,
                    "indentStart": {"magnitude": 18, "unit": "PT"},
                },
                "fields": _PARAGRAPH_RESET_FIELDS,
            }
        }
    )

    # Make quote text italic
    requests.append(
        {
            "updateTextStyle": {
                "range": {
                    "startIndex": start_index,
                    "endIndex": current_index,
                },
                "textStyle": {"italic": True},
                "fields": "italic",
            }
        }
    )

    # Clear inherited list formatting
    if clear_bullets:
        requests.append(
            {
                "deleteParagraphBullets": {
                    "range": {
                        "startIndex": start_index,
                        "endIndex": current_index,
                    }
                }
            }
        )

    return current_index


def _validate_table_index_formula(
    document_id: str,
    table_insertion_index: int,
    tab_id: str | None = None,
) -> bool:
    """Check whether the closed-form cell-index formula matches reality.

    Inserts nothing — just reads the document after a table was inserted at
    *table_insertion_index* and compares the first cell's actual paragraph
    startIndex against the predicted value.

    Returns True if the formula is correct (or if validation is inconclusive),
    False if the formula is definitively wrong.
    """
    table_start = table_insertion_index + 1
    predicted_first_cell = table_start + 3  # para[0][0]

    doc_id = extract_document_id(document_id)
    endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
    if tab_id:
        endpoint += "?includeTabsContent=true"
    doc_result = call_google_api("GET", endpoint)

    if not doc_result.get("success"):
        return True  # can't validate — assume formula is fine

    body = _get_body_from_doc(doc_result["data"], tab_id)
    for elem in body.get("content", []):
        if "table" in elem and elem.get("startIndex", 0) >= table_insertion_index:
            table_rows = elem["table"].get("tableRows", [])
            if table_rows:
                first_cells = table_rows[0].get("tableCells", [])
                if first_cells:
                    content = first_cells[0].get("content", [])
                    if content and "paragraph" in content[0]:
                        actual = content[0].get("startIndex", 0)
                        return actual == predicted_first_cell
            break

    return True  # couldn't find table or cell — assume formula is fine


def _insert_markdown_table(
    document_id: str,
    table_data: dict[str, Any],
    index: int,
    tab_id: str | None = None,
    use_formula: bool = True,
) -> dict[str, Any]:
    """Insert a markdown table as a native Google Docs table.

    This is a multi-step operation:
    1. Insert empty table structure
    2. Compute cell paragraph indices (formula if *use_formula* is True,
       otherwise re-read the document to discover them)
    3. Populate cells with content (reverse order to preserve indices)
    4. Bold the header row and apply gray background
    5. Pin header row

    Returns dict with 'success' and 'end_index'.
    """
    headers = table_data["headers"]
    rows = table_data["rows"]

    num_cols = len(headers)
    num_rows = len(rows) + 1  # +1 for header row

    if num_cols == 0:
        return {"success": True, "end_index": index}

    # Step 1: insert empty table
    table_requests: list[dict[str, Any]] = [
        {
            "insertTable": {
                "location": {"index": index},
                "rows": num_rows,
                "columns": num_cols,
            }
        }
    ]
    result = batch_update(document_id, table_requests, tab_id)
    if not result.get("success"):
        return result

    # Clean up the extra empty paragraph that insertTable creates
    _remove_extra_table_paragraph(document_id, index, tab_id)

    # Step 2: determine cell paragraph indices.
    # Formula (verified experimentally for empty tables):
    #   table_start = insertion_index + 1
    #   para[r][c] = table_start + 3 + r*(1 + num_cols*2) + c*2
    #   table_end  = table_start + 2 + num_rows*(num_cols*2 + 1)
    table_start = index + 1
    cell_indices: dict[tuple[int, int], int] | None = None  # None = use formula

    if use_formula:
        original_table_end = table_start + 2 + num_rows * (num_cols * 2 + 1)
    else:
        # Formula was invalidated — re-read document for actual indices
        doc_id = extract_document_id(document_id)
        endpoint = f"https://docs.googleapis.com/v1/documents/{doc_id}"
        if tab_id:
            endpoint += "?includeTabsContent=true"
        doc_result = call_google_api("GET", endpoint)
        if not doc_result.get("success"):
            return doc_result

        body = _get_body_from_doc(doc_result["data"], tab_id)
        table_element = None
        for elem in body.get("content", []):
            if "table" in elem and elem.get("startIndex", 0) >= index:
                table_element = elem
                break

        if not table_element:
            return {"success": False, "error": "Could not find inserted table"}

        # Use actual table start from re-read (paragraph removal may have
        # shifted it from the predicted index + 1).
        table_start = table_element.get("startIndex", index)

        cell_indices = {}
        for r_idx, row in enumerate(table_element["table"].get("tableRows", [])):
            for c_idx, cell in enumerate(row.get("tableCells", [])):
                cell_content = cell.get("content", [])
                if cell_content and "paragraph" in cell_content[0]:
                    cell_indices[(r_idx, c_idx)] = cell_content[0].get("startIndex", 0)
        original_table_end = table_element.get("endIndex", index)

    all_data = [headers] + rows

    # Step 3: populate cells (reverse order so indices stay valid)
    cell_requests: list[dict[str, Any]] = []
    total_inserted = 0

    for row_idx in range(num_rows - 1, -1, -1):
        for col_idx in range(num_cols - 1, -1, -1):
            if row_idx >= len(all_data) or col_idx >= len(all_data[row_idx]):
                continue

            raw_cell_content = all_data[row_idx][col_idx]
            if not raw_cell_content:
                continue

            # Strip inline formatting from cell content
            stripped_cell, fmt_ranges = strip_and_parse_inline_formatting(
                raw_cell_content
            )
            if not stripped_cell:
                continue

            total_inserted += _utf16_len(stripped_cell)

            if cell_indices is not None:
                ci = cell_indices.get((row_idx, col_idx))
                if ci is None:
                    continue
                cell_start = ci
            else:
                cell_start = (
                    table_start + 3 + row_idx * (1 + num_cols * 2) + col_idx * 2
                )

            # Insert cell text
            cell_requests.append(
                {
                    "insertText": {
                        "location": {"index": cell_start},
                        "text": stripped_cell,
                    }
                }
            )

            # Normalize cell paragraph to NORMAL_TEXT so
            # font size matches document body text
            cell_requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": cell_start,
                            "endIndex": cell_start + 1,
                        },
                        "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        "fields": "namedStyleType",
                    }
                }
            )

            # Inline formatting (code, bold, italic within cell text)
            _add_inline_format_requests(
                fmt_ranges, cell_start, cell_requests, stripped_cell
            )

            # Bold entire header row
            if row_idx == 0:
                cell_requests.append(
                    {
                        "updateTextStyle": {
                            "range": {
                                "startIndex": cell_start,
                                "endIndex": cell_start + _utf16_len(stripped_cell),
                            },
                            "textStyle": {"bold": True},
                            "fields": "bold",
                        }
                    }
                )

    # Style header row with light grey background
    cell_requests.append(
        {
            "updateTableCellStyle": {
                "tableRange": {
                    "tableCellLocation": {
                        "tableStartLocation": {"index": table_start},
                        "rowIndex": 0,
                        "columnIndex": 0,
                    },
                    "rowSpan": 1,
                    "columnSpan": num_cols,
                },
                "tableCellStyle": {
                    "backgroundColor": {
                        "color": {
                            "rgbColor": {
                                "red": 0.85,
                                "green": 0.85,
                                "blue": 0.85,
                            }
                        }
                    }
                },
                "fields": "backgroundColor",
            }
        }
    )

    # Pin header row
    cell_requests.append(
        {
            "pinTableHeaderRows": {
                "tableStartLocation": {"index": table_start},
                "pinnedHeaderRowsCount": 1,
            }
        }
    )

    if cell_requests:
        result = batch_update(document_id, cell_requests, tab_id)
        if not result.get("success"):
            return result

    # End index: computed empty table end + total inserted text length
    end_index = original_table_end + total_inserted

    return {"success": True, "end_index": end_index}


def strip_and_parse_inline_formatting(
    text: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Strip markdown markers and return clean text with formatting positions.

    Returns a tuple of (stripped_text, format_ranges) where format_ranges is a list
    of dicts with 'start', 'end', and 'style' keys representing positions in the
    stripped text.

    Handles backslash escapes: \\_  \\*  \\` are treated as literal characters
    and will not trigger formatting.

    Code spans (`...`) are extracted first so their content is treated as
    literal text -- no escapes or formatting are processed inside them.

    When formats are nested (e.g. **`code`:**), earlier-recorded ranges are
    adjusted as later passes strip their own markers, so all ranges are valid
    offsets into the final stripped_text.
    """
    # Unicode Private Use Area placeholders for escapes and code span
    # protection.  These are replaced with real characters before the text
    # is sent to the API.  If user content contains these PUA codepoints
    # (extremely unlikely), they would be corrupted.
    _CODE_PLACEHOLDER = "\ue010"
    _ESC_UNDERSCORE = "\ue000"
    _ESC_ASTERISK = "\ue001"
    _ESC_BACKTICK = "\ue002"

    # Step 1: Protect code span contents from escape/formatting processing.
    # This runs BEFORE escape replacement so that \` inside code spans is
    # preserved literally (backslashes have no special meaning inside code
    # spans).  The negative lookbehind (?<!\\) prevents \` from acting as
    # a code span delimiter, so \`not code\` won't create a code span.
    # The backreference \1 ensures the closing delimiter matches the
    # opening one; the greedy {1,2} tries double-backtick first, so
    # ``x`y`` is treated as one span containing a literal backtick.
    code_span_contents: list[str] = []

    def _protect_code(m: re.Match[str]) -> str:
        content = m.group(2)
        code_span_contents.append(content)
        return "`" + _CODE_PLACEHOLDER * len(content) + "`"

    text = re.sub(r"(?<!\\)(`{1,2})(.+?)(?<!\\)\1", _protect_code, text)

    # Step 2: Process escaped backticks (only outside code spans now,
    # since code span content has been replaced with placeholders).
    text = text.replace("\\`", _ESC_BACKTICK)

    # Step 3: Remaining escape handling (underscore, asterisk).
    text = text.replace("\\_", _ESC_UNDERSCORE)
    text = text.replace("\\*", _ESC_ASTERISK)

    # Step 4: Process all formatting patterns.
    patterns = [
        (r"\[([^\]]+)\]\(([^)]+)\)", "link", 0),
        (r"\*\*(.+?)\*\*", "bold", 2),
        (r"(?<!\w)__(.+?)__(?!\w)", "bold", 2),
        (r"~~(.+?)~~", "strikethrough", 2),
        (r"\*(.+?)\*", "italic", 1),
        (r"(?<!\w)_([^_]+)_(?!\w)", "italic", 1),
        (r"`([^`]+)`", "code", 1),
    ]

    format_ranges: list[dict[str, Any]] = []
    result = text

    for pattern, style, _marker_len in patterns:
        new_result = ""
        last_end = 0
        prev_range_count = len(format_ranges)

        # Collect intervals removed by this pass (in old-string coordinates).
        removed_intervals: list[tuple[int, int]] = []

        for match in re.finditer(pattern, result):
            new_result += result[last_end : match.start()]
            content = match.group(1)
            content_start = len(new_result)
            new_result += content
            content_end = len(new_result)
            last_end = match.end()

            range_entry: dict[str, Any] = {
                "start": content_start,
                "end": content_end,
                "style": style,
            }
            if style == "link" and match.lastindex is not None and match.lastindex >= 2:
                range_entry["url"] = match.group(2)
            format_ranges.append(range_entry)

            # Record removed marker intervals
            if match.start(1) > match.start():
                removed_intervals.append((match.start(), match.start(1)))
            if match.end() > match.end(1):
                removed_intervals.append((match.end(1), match.end()))

        new_result += result[last_end:]

        # Adjust all previously-recorded ranges for markers removed in this pass.
        if removed_intervals:
            for i in range(prev_range_count):
                fmt = format_ranges[i]
                fmt["start"] = _adjust_pos_for_removals(fmt["start"], removed_intervals)
                fmt["end"] = _adjust_pos_for_removals(fmt["end"], removed_intervals)

        result = new_result

    # Step 5: Restore code span contents FIRST.  Placeholders have the same
    # length as the (post-step-1) content, so positions stay valid.
    span_idx = 0
    for fmt in format_ranges:
        if fmt["style"] == "code" and span_idx < len(code_span_contents):
            start = fmt["start"]
            end = fmt["end"]
            original = code_span_contents[span_idx]
            result = result[:start] + original + result[end:]
            span_idx += 1

    # Step 6: Restore ALL escape placeholders (including any that were inside
    # code span content).  Google Docs strips PUA characters, so we must
    # convert every placeholder to a real character before insertion.
    result = result.replace(_ESC_UNDERSCORE, "_")
    result = result.replace(_ESC_ASTERISK, "*")
    result = result.replace(_ESC_BACKTICK, "`")

    return result, format_ranges


def _adjust_pos_for_removals(pos: int, removed_intervals: list[tuple[int, int]]) -> int:
    """Map a position from old string coordinates to new string coordinates.

    removed_intervals is a sorted list of (start, end) half-open intervals
    of characters that were removed.
    """
    adjustment = 0
    for rm_start, rm_end in removed_intervals:
        if pos <= rm_start:
            break
        elif pos < rm_end:
            # Position is inside a removed region -- clamp to the start
            adjustment += pos - rm_start
            return pos - adjustment
        else:
            adjustment += rm_end - rm_start
    return pos - adjustment


def action_to_requests(action_params: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a high-level action to batch update request(s).

    This enables batching multiple actions into a single API call for efficiency.
    """
    action_type = action_params.get("action")
    requests: list[dict[str, Any]] = []

    if action_type == "insert_text":
        idx = action_params.get("index", 1)
        text = action_params.get("text", "")
        requests.append(
            {
                "insertText": {
                    "location": {"index": idx},
                    "text": text,
                }
            }
        )
        # Reset character formatting to prevent style bleeding
        requests.append(
            {
                "updateTextStyle": {
                    "range": {"startIndex": idx, "endIndex": idx + len(text)},
                    "textStyle": {},
                    "fields": "bold,italic,underline,strikethrough",
                }
            }
        )

    elif action_type == "delete_text":
        requests.append(
            {
                "deleteContentRange": {
                    "range": {
                        "startIndex": action_params.get("start_index", 0),
                        "endIndex": action_params.get("end_index", 0),
                    }
                }
            }
        )

    elif action_type == "format_text":
        text_style: dict[str, Any] = {}
        fields = []

        if action_params.get("bold") is not None:
            text_style["bold"] = action_params["bold"]
            fields.append("bold")
        if action_params.get("italic") is not None:
            text_style["italic"] = action_params["italic"]
            fields.append("italic")
        if action_params.get("underline") is not None:
            text_style["underline"] = action_params["underline"]
            fields.append("underline")
        if action_params.get("font_size") is not None:
            text_style["fontSize"] = {
                "magnitude": action_params["font_size"],
                "unit": "PT",
            }
            fields.append("fontSize")
        if action_params.get("foreground_color") is not None:
            text_style["foregroundColor"] = {
                "color": {"rgbColor": action_params["foreground_color"]}
            }
            fields.append("foregroundColor")
        if action_params.get("background_color") is not None:
            text_style["backgroundColor"] = {
                "color": {"rgbColor": action_params["background_color"]}
            }
            fields.append("backgroundColor")
        if action_params.get("link") is not None:
            text_style["link"] = {"url": action_params["link"]}
            fields.append("link")
        if action_params.get("font_family") is not None:
            text_style["weightedFontFamily"] = {
                "fontFamily": action_params["font_family"],
                "weight": 400,
            }
            fields.append("weightedFontFamily")
        if action_params.get("strikethrough") is not None:
            text_style["strikethrough"] = action_params["strikethrough"]
            fields.append("strikethrough")

        if fields:
            requests.append(
                {
                    "updateTextStyle": {
                        "range": {
                            "startIndex": action_params.get("start_index", 0),
                            "endIndex": action_params.get("end_index", 0),
                        },
                        "textStyle": text_style,
                        "fields": ",".join(fields),
                    }
                }
            )

    elif action_type == "apply_heading":
        heading_map = {
            1: "HEADING_1",
            2: "HEADING_2",
            3: "HEADING_3",
            4: "HEADING_4",
            5: "HEADING_5",
            6: "HEADING_6",
        }
        heading_level = action_params.get("heading_level", 1)
        named_style = heading_map.get(heading_level, "NORMAL_TEXT")
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": {
                        "startIndex": action_params.get("start_index", 0),
                        "endIndex": action_params.get("end_index", 0),
                    },
                    "paragraphStyle": {"namedStyleType": named_style},
                    "fields": "namedStyleType",
                }
            }
        )

    elif action_type == "find_replace":
        requests.append(
            {
                "replaceAllText": {
                    "containsText": {
                        "text": action_params.get("find_text", ""),
                        "matchCase": True,
                    },
                    "replaceText": action_params.get("replace_text", ""),
                }
            }
        )

    elif action_type == "set_paragraph_style":
        paragraph_style: dict[str, Any] = {}
        p_fields: list[str] = []
        if action_params.get("alignment") is not None:
            paragraph_style["alignment"] = action_params["alignment"]
            p_fields.append("alignment")
        if action_params.get("named_style") is not None:
            paragraph_style["namedStyleType"] = action_params["named_style"]
            p_fields.append("namedStyleType")
        if action_params.get("line_spacing") is not None:
            paragraph_style["lineSpacing"] = action_params["line_spacing"]
            p_fields.append("lineSpacing")
        for attr, api_key in [
            ("space_above", "spaceAbove"),
            ("space_below", "spaceBelow"),
            ("indent_start", "indentStart"),
            ("indent_end", "indentEnd"),
            ("indent_first_line", "indentFirstLine"),
        ]:
            if action_params.get(attr) is not None:
                paragraph_style[api_key] = {
                    "magnitude": action_params[attr],
                    "unit": "PT",
                }
                p_fields.append(api_key)
        if action_params.get("shading_color") is not None:
            paragraph_style["shading"] = {
                "backgroundColor": {
                    "color": {"rgbColor": action_params["shading_color"]}
                }
            }
            p_fields.append("shading")
        if p_fields:
            requests.append(
                {
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": action_params.get("start_index", 0),
                            "endIndex": action_params.get("end_index", 0),
                        },
                        "paragraphStyle": paragraph_style,
                        "fields": ",".join(p_fields),
                    }
                }
            )

    elif action_type == "update_table_cell_style":
        cell_style: dict[str, Any] = {}
        c_fields: list[str] = []
        if action_params.get("background_color") is not None:
            cell_style["backgroundColor"] = {
                "color": {"rgbColor": action_params["background_color"]}
            }
            c_fields.append("backgroundColor")
        if c_fields:
            requests.append(
                {
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {
                                    "index": action_params.get("table_start_index", 0)
                                },
                                "rowIndex": action_params.get("row_index", 0),
                                "columnIndex": action_params.get("column_index", 0),
                            },
                            "rowSpan": action_params.get("row_span", 1),
                            "columnSpan": action_params.get("column_span", 1),
                        },
                        "tableCellStyle": cell_style,
                        "fields": ",".join(c_fields),
                    }
                }
            )

    return requests


def execute_batched_actions(
    document_id: str, actions: list[dict[str, Any]], tab_id: str | None = None
) -> dict[str, Any]:
    """Execute multiple actions in a single batched API call.

    This is more efficient than calling each action separately, as it reduces
    the number of API round-trips from N to 1.

    Args:
        document_id: The document ID or URL.
        actions: List of action dicts with 'action' key and action-specific params.
        tab_id: Optional tab ID to apply all actions to a specific tab.

    Example:
        execute_batched_actions(doc_id, [
            {"action": "insert_text", "text": "Hello\\n", "index": 1},
            {"action": "format_text", "start_index": 1, "end_index": 6, "bold": True},
        ])
    """
    all_requests: list[dict[str, Any]] = []
    for action in actions:
        all_requests.extend(action_to_requests(action))

    if not all_requests:
        return {"success": True, "data": {"message": "No requests to execute"}}

    return batch_update(document_id, all_requests, tab_id)


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "No input provided"}))
        sys.exit(1)

    # Parse CLI flags before JSON input
    args = sys.argv[1:]
    global _SKIP_AUTHOR_CHECK
    if "--no-author-check" in args:
        args.remove("--no-author-check")
        _SKIP_AUTHOR_CHECK = True
    if "--sandbox-host" in args:
        idx = args.index("--sandbox-host")
        if idx + 1 < len(args):
            set_sandbox_host(args[idx + 1])
            args = args[:idx] + args[idx + 2 :]

    if not args:
        print(json.dumps({"success": False, "error": "No input provided"}))
        sys.exit(1)

    try:
        raw_input = args[0]
        if raw_input.startswith("@"):
            with open(raw_input[1:]) as f:
                raw_input = f.read()
        params = json.loads(raw_input)
    except (json.JSONDecodeError, OSError) as e:
        print(json.dumps({"success": False, "error": f"Invalid JSON input: {e}"}))
        sys.exit(1)

    # Set sandbox host if provided (for ondemand environments)
    if params.get("sandbox_host"):
        set_sandbox_host(params["sandbox_host"])

    result: dict[str, Any]

    # Multi-action mode: batch multiple operations into a single API call
    if "actions" in params:
        doc_id = params.get("document_id", "")
        if not doc_id:
            print(
                json.dumps(
                    {
                        "success": False,
                        "error": "document_id required for batched actions",
                    }
                )
            )
            sys.exit(1)
        result = execute_batched_actions(
            doc_id, params["actions"], params.get("tab_id")
        )
        print(json.dumps(result, indent=2))
        return

    # Single action mode (existing behavior)
    action = params.get("action")
    if not action:
        print(json.dumps({"success": False, "error": "No action specified"}))
        sys.exit(1)

    if action == "get_document":
        result = get_document(params.get("document_id", ""), params.get("tab_id"))
    elif action == "get_document_body":
        result = get_document_body(params.get("document_id", ""), params.get("tab_id"))
    elif action == "get_document_formatting":
        result = get_document_formatting(
            params.get("document_id", ""), params.get("tab_id")
        )
    elif action == "get_document_raw":
        result = get_document_raw(params.get("document_id", ""), params.get("tab_id"))
    elif action == "get_document_tabs":
        result = get_document_tabs(params.get("document_id", ""))
    elif action == "create_tab":
        result = create_tab(
            params.get("document_id", ""),
            params.get("title"),
            params.get("parent_tab_id"),
        )
    elif action == "create_document":
        result = create_document(
            params.get("title", "Untitled"),
            params.get("initial_content"),
            params.get("folder_id"),
            params.get("format_from_markdown", False),
            params.get("format_from_html", False),
        )
    elif action == "get_comments":
        result = get_comments(params.get("document_id", ""))
    elif action == "add_comment":
        result = add_comment(
            params.get("document_id", ""),
            params.get("comment", ""),
            params.get("anchor", ""),
            params.get("anchor_id", ""),
        )
    elif action == "get_heading_ids":
        result = get_heading_ids(params.get("document_id", ""))
    elif action == "reply_to_comment":
        result = reply_to_comment(
            params.get("document_id", ""),
            params.get("comment_id", ""),
            params.get("reply_content", ""),
        )
    elif action == "resolve_comment":
        result = resolve_comment(
            params.get("document_id", ""),
            params.get("comment_id", ""),
        )
    elif action == "delete_comment":
        result = delete_comment(
            params.get("document_id", ""),
            params.get("comment_id", ""),
        )
    elif action == "get_revisions":
        result = get_revisions(params.get("document_id", ""))
    elif action == "get_revision_content":
        result = get_revision_content(
            params.get("document_id", ""),
            params.get("revision_id", ""),
        )
    elif action == "unshare_document":
        result = unshare_document(
            params.get("document_id", ""),
            params.get("email_addresses", ""),
        )
    elif action == "insert_inline_image":
        result = insert_inline_image(
            params.get("document_id", ""),
            params.get("image_uri", ""),
            params.get("index", 1),
            params.get("width"),
            params.get("height"),
            params.get("tab_id"),
        )
    elif action == "move_document":
        result = move_document(
            params.get("document_id", ""),
            params.get("target_folder_id", ""),
        )
    elif action == "get_permissions":
        result = get_permissions(params.get("document_id", ""))
    elif action == "share_document":
        result = share_document(
            params.get("document_id", ""),
            params.get("email", ""),
            params.get("role", "reader"),
        )
    elif action == "export_document":
        result = export_document(
            params.get("document_id", ""), params.get("export_format", "pdf")
        )
    elif action == "batch_update":
        result = batch_update(
            params.get("document_id", ""),
            params.get("requests", []),
            params.get("tab_id"),
        )
    elif action == "insert_text":
        result = insert_text(
            params.get("document_id", ""),
            params.get("text", ""),
            params.get("index", 1),
            params.get("tab_id"),
        )
    elif action == "format_text":
        result = format_text(
            params.get("document_id", ""),
            params.get("start_index", 0),
            params.get("end_index", 0),
            params.get("bold"),
            params.get("italic"),
            params.get("underline"),
            params.get("font_size"),
            params.get("foreground_color"),
            params.get("background_color"),
            params.get("link"),
            params.get("font_family"),
            params.get("strikethrough"),
            params.get("tab_id"),
            params.get("clear_other_fields", False),
        )
    elif action == "apply_heading":
        result = apply_heading(
            params.get("document_id", ""),
            params.get("start_index", 0),
            params.get("end_index", 0),
            params.get("heading_level", 1),
            params.get("tab_id"),
        )
    elif action == "set_paragraph_style":
        result = set_paragraph_style(
            params.get("document_id", ""),
            params.get("start_index", 0),
            params.get("end_index", 0),
            params.get("alignment"),
            params.get("named_style"),
            params.get("line_spacing"),
            params.get("space_above"),
            params.get("space_below"),
            params.get("indent_start"),
            params.get("indent_end"),
            params.get("indent_first_line"),
            params.get("shading_color"),
            params.get("tab_id"),
            params.get("clear_other_fields", False),
        )
    elif action == "delete_document":
        result = delete_document(params.get("document_id", ""))
    elif action == "insert_markdown":
        result = insert_markdown(
            params.get("document_id", ""),
            params.get("markdown_text") or params.get("text", ""),
            params.get("index", 1),
            params.get("tab_id"),
        )
    elif action == "insert_html":
        result = insert_html(
            params.get("document_id", ""),
            params.get("html_text") or params.get("text", ""),
            params.get("index", 1),
            params.get("tab_id"),
        )
    elif action == "insert_table":
        result = insert_table(
            params.get("document_id", ""),
            params.get("rows", 2),
            params.get("columns", 2),
            params.get("index", 1),
            params.get("data"),
        )
    elif action == "update_table_cell_style":
        result = update_table_cell_style(
            params.get("document_id", ""),
            params.get("table_start_index", 0),
            params.get("row_index", 0),
            params.get("column_index", 0),
            params.get("row_span", 1),
            params.get("column_span", 1),
            params.get("background_color"),
            params.get("border_color"),
            params.get("border_width"),
            params.get("padding"),
            params.get("content_alignment"),
            params.get("tab_id"),
        )
    elif action == "update_table_cells":
        result = update_table_cells(
            params.get("document_id", ""),
            params.get("table_start_index", 0),
            params.get("cell_updates", []),
            params.get("tab_id"),
        )
    elif action == "set_column_widths":
        result = set_column_widths(
            params.get("document_id", ""),
            params.get("table_start_index", 0),
            params.get("column_widths", []),
            params.get("tab_id"),
        )
    elif action == "create_bullet_list":
        result = create_bullet_list(
            params.get("document_id", ""),
            params.get("start_index", 1),
            params.get("end_index", 2),
            params.get("bullet_preset", "BULLET_DISC_CIRCLE_SQUARE"),
        )
    elif action == "insert_bullet_list":
        result = insert_bullet_list(
            params.get("document_id", ""),
            params.get("items", []),
            params.get("index", 1),
            params.get("bullet_preset", "BULLET_DISC_CIRCLE_SQUARE"),
        )
    elif action == "copy_doc":
        result = copy_doc(
            params.get("document_id", ""),
            params.get("title"),
            params.get("folder_id"),
        )
    elif action == "find_replace":
        result = find_replace(
            params.get("document_id", ""),
            params.get("find_text", ""),
            params.get("replace_text", ""),
        )
    elif action == "replace_document_content":
        result = replace_document_content(
            params.get("document_id", ""),
            params.get("content")
            or params.get("markdown_text")
            or params.get("text", ""),
            params.get("format", "markdown"),
            params.get("tab_id"),
        )
    else:
        result = {"success": False, "error": f"Unknown action: {action}"}

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
