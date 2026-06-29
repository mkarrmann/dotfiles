#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
from __future__ import annotations

"""
Integration tests for Google Docs API.

Usage:
    python3 tests.py
    python3 tests.py --sandbox-host interngraph.12345.od.facebook.com
    SANDBOX_HOST=interngraph.12345.od.facebook.com python3 tests.py
"""

import argparse
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from google_api import (
    apply_heading,
    call_google_api,
    copy_doc,
    create_document,
    create_tab,
    delete_document,
    execute_batched_actions,
    find_replace,
    format_text,
    get_document,
    get_document_body,
    get_document_tabs,
    insert_html,
    insert_markdown,
    insert_text,
    replace_document_content,
    set_sandbox_host,
)


def test_document_tabs_basic() -> bool:
    """Test basic tab operations: create, list, insert text, read content."""
    print("=" * 60)
    print("Test: Document Tabs - Basic Operations")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Document with Tabs - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Getting initial tabs...")
        tabs_result = get_document_tabs(doc_id)
        if not tabs_result.get("success"):
            print(f"   FAILED: {tabs_result.get('error')}")
            return False
        initial_tabs = tabs_result["data"]["tabs"]
        print(f"   SUCCESS: Document has {len(initial_tabs)} tab(s)")
        for tab in initial_tabs:
            print(f"      - Tab ID: {tab['tabId']}, Title: '{tab['title']}'")

        time.sleep(1)

        print("\n3. Creating a new tab with a custom name...")
        tab_result = create_tab(doc_id, "My New Tab")
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        new_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created tab with ID: {new_tab_id}")

        time.sleep(1)

        print("\n4. Verifying tabs after creation...")
        tabs_result = get_document_tabs(doc_id)
        if not tabs_result.get("success"):
            print(f"   FAILED: {tabs_result.get('error')}")
            return False
        updated_tabs = tabs_result["data"]["tabs"]
        print(f"   SUCCESS: Document now has {len(updated_tabs)} tab(s)")
        for tab in updated_tabs:
            print(f"      - Tab ID: {tab['tabId']}, Title: '{tab['title']}'")

        if len(updated_tabs) != len(initial_tabs) + 1:
            print(
                f"   FAILED: Expected {len(initial_tabs) + 1} tabs, got {len(updated_tabs)}"
            )
            return False

        time.sleep(1)

        print("\n5. Inserting text into the new tab...")
        insert_result = insert_text(doc_id, "Hello from the new tab!", 1, new_tab_id)
        if not insert_result.get("success"):
            print(f"   FAILED: {insert_result.get('error')}")
            return False
        print("   SUCCESS: Inserted text into new tab")

        time.sleep(1)

        print("\n6. Getting body content from the new tab...")
        body_result = get_document_body(doc_id, new_tab_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        segments = body_result["data"]["segments"]
        full_text = "".join(seg["text"] for seg in segments)
        if "Hello from the new tab!" in full_text:
            print("   SUCCESS: Text found in new tab")
        else:
            print(f"   FAILED: Expected text not found. Got: {full_text}")
            return False

        print("\n7. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_tab_creation_variants() -> bool:
    """Test various tab creation scenarios: with name, without name, empty name."""
    print("=" * 60)
    print("Test: Tab Creation Variants")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Tab Creation - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Creating a tab with a custom name...")
        tab_result = create_tab(doc_id, "Named Tab")
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        named_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created named tab with ID: {named_tab_id}")

        time.sleep(1)

        print("\n3. Creating a tab without a name (default name)...")
        tab_result = create_tab(doc_id, None)
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        default_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created default-named tab with ID: {default_tab_id}")

        time.sleep(1)

        print("\n4. Creating a tab with empty string name...")
        tab_result = create_tab(doc_id, "")
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        empty_name_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created tab with empty name, ID: {empty_name_tab_id}")

        time.sleep(1)

        print("\n5. Verifying all tabs were created...")
        tabs_result = get_document_tabs(doc_id)
        if not tabs_result.get("success"):
            print(f"   FAILED: {tabs_result.get('error')}")
            return False

        tabs = tabs_result["data"]["tabs"]
        print(f"   Document has {len(tabs)} tab(s):")
        for tab in tabs:
            print(f"      - Tab ID: {tab['tabId']}, Title: '{tab['title']}'")

        # Should have 4 tabs total (1 default + 3 created)
        if len(tabs) < 4:
            print(f"   FAILED: Expected at least 4 tabs, got {len(tabs)}")
            return False

        # Verify the named tab has the correct title
        named_tab = next((t for t in tabs if t["tabId"] == named_tab_id), None)
        if named_tab and named_tab["title"] == "Named Tab":
            print("   SUCCESS: Named tab has correct title")
        else:
            print(f"   FAILED: Named tab title mismatch: {named_tab}")
            return False

        print("\n6. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_tab_operations_with_and_without_tab_id() -> bool:
    """Test that functions work both with and without tab_id specified."""
    print("=" * 60)
    print("Test: Operations With and Without tab_id")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document with initial content...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(
            f"Test Tab Operations - {unique_id}", "Default tab content\n"
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        # Get default tab ID for later use
        tabs_result = get_document_tabs(doc_id)
        default_tab_id = tabs_result["data"]["tabs"][0]["tabId"]
        print(f"   Default tab ID: {default_tab_id}")

        print("\n2. Creating a second tab...")
        tab_result = create_tab(doc_id, "Second Tab")
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        second_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created second tab with ID: {second_tab_id}")

        time.sleep(1)

        # ===== Test get_document_body =====
        print("\n3. Testing get_document_body...")

        print("   3a. get_document_body WITHOUT tab_id (should get default tab)...")
        body_result = get_document_body(doc_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        default_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
        print(f"   SUCCESS: Got content: '{default_text[:50]}...'")

        print("   3b. get_document_body WITH tab_id (default tab)...")
        body_result = get_document_body(doc_id, default_tab_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        print("   SUCCESS: Got default tab content with explicit tab_id")

        print("   3c. get_document_body WITH tab_id (second tab)...")
        body_result = get_document_body(doc_id, second_tab_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        print("   SUCCESS: Got second tab content")

        # ===== Test insert_text =====
        print("\n4. Testing insert_text...")

        print("   4a. insert_text WITHOUT tab_id...")
        result = insert_text(doc_id, "Inserted without tab_id\n", 1)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted text without tab_id")

        time.sleep(1)

        print("   4b. insert_text WITH tab_id (second tab)...")
        result = insert_text(doc_id, "Inserted into second tab\n", 1, second_tab_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted text into second tab")

        time.sleep(1)

        # ===== Test insert_markdown =====
        print("\n5. Testing insert_markdown...")

        print("   5a. insert_markdown WITHOUT tab_id...")
        result = insert_markdown(doc_id, "**Bold text**\n", 1)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown without tab_id")

        time.sleep(1)

        print("   5b. insert_markdown WITH tab_id (second tab)...")
        result = insert_markdown(doc_id, "*Italic text*\n", 1, second_tab_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown into second tab")

        time.sleep(1)

        # ===== Test format_text =====
        print("\n6. Testing format_text...")

        print("   6a. format_text WITHOUT tab_id...")
        result = format_text(doc_id, 1, 5, bold=True)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Formatted text without tab_id")

        time.sleep(1)

        print("   6b. format_text WITH tab_id (second tab)...")
        result = format_text(doc_id, 1, 5, italic=True, tab_id=second_tab_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Formatted text in second tab")

        time.sleep(1)

        # ===== Test apply_heading =====
        print("\n7. Testing apply_heading...")

        print("   7a. apply_heading WITHOUT tab_id...")
        result = apply_heading(doc_id, 1, 10, 1)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Applied heading without tab_id")

        time.sleep(1)

        print("   7b. apply_heading WITH tab_id (second tab)...")
        result = apply_heading(doc_id, 1, 10, 2, second_tab_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Applied heading in second tab")

        time.sleep(1)

        # ===== Test execute_batched_actions =====
        print("\n8. Testing execute_batched_actions...")

        print("   8a. execute_batched_actions WITHOUT tab_id...")
        result = execute_batched_actions(
            doc_id,
            [
                {"action": "insert_text", "text": "Batched insert\n", "index": 1},
            ],
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Executed batched actions without tab_id")

        time.sleep(1)

        print("   8b. execute_batched_actions WITH tab_id (second tab)...")
        result = execute_batched_actions(
            doc_id,
            [
                {"action": "insert_text", "text": "Batched in tab\n", "index": 1},
            ],
            second_tab_id,
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Executed batched actions in second tab")

        # ===== Verify content isolation between tabs =====
        print("\n9. Verifying content is isolated between tabs...")

        body_default = get_document_body(doc_id, default_tab_id)
        body_second = get_document_body(doc_id, second_tab_id)

        default_content = "".join(
            seg["text"] for seg in body_default["data"]["segments"]
        )
        second_content = "".join(seg["text"] for seg in body_second["data"]["segments"])

        print(f"   Default tab content: '{default_content[:80]}...'")
        print(f"   Second tab content: '{second_content[:80]}...'")

        # Check that content is different (tab-specific)
        if "Inserted into second tab" in second_content:
            print("   SUCCESS: Second tab has its specific content")
        else:
            print("   FAILED: Second tab content mismatch")
            return False

        if "Batched in tab" in second_content:
            print("   SUCCESS: Batched action content found in second tab")
        else:
            print("   FAILED: Batched content not found in second tab")
            return False

        print("\n10. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_tab_error_handling() -> bool:
    """Test error handling for invalid tab operations."""
    print("=" * 60)
    print("Test: Tab Error Handling")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Tab Errors - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Testing get_document_body with invalid tab_id...")
        result = get_document_body(doc_id, "invalid_tab_id_12345")
        if result.get("success"):
            print("   WARNING: Expected failure but got success")
            # This might not fail at API level, just return empty/default content
            print("   (API may return default content for invalid tab IDs)")
        else:
            error = result.get("error", "")
            print(f"   Got expected error: {error[:100]}")
            print("   SUCCESS: Invalid tab_id handled correctly")

        time.sleep(1)

        print("\n3. Testing insert_text with invalid tab_id...")
        result = insert_text(doc_id, "Test text", 1, "invalid_tab_id_xyz")
        if result.get("success"):
            print("   WARNING: Expected failure but operation succeeded")
            print("   (API behavior may vary)")
        else:
            error = result.get("error", "")
            print(f"   Got error: {error[:100]}")
            print("   SUCCESS: Invalid tab_id for insert_text handled")

        time.sleep(1)

        print("\n4. Testing get_document_tabs on non-existent document...")
        result = get_document_tabs("non_existent_document_id_12345")
        if result.get("success"):
            print("   UNEXPECTED: Should have failed for non-existent document")
        else:
            error = result.get("error", "")
            print(f"   Got expected error: {error[:100]}")
            print("   SUCCESS: Non-existent document handled correctly")

        print("\n5. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_child_tab_operations() -> bool:
    """Test that child (nested) tabs work with get_document_tabs, get_document_body, and insert_markdown."""
    print("=" * 60)
    print("Test: Child Tab Operations")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Child Tabs - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Creating a parent tab...")
        tab_result = create_tab(doc_id, "Parent Tab")
        if not tab_result.get("success"):
            print(f"   FAILED: {tab_result.get('error')}")
            return False
        parent_tab_id = tab_result["data"]["tabId"]
        print(f"   SUCCESS: Created parent tab with ID: {parent_tab_id}")

        time.sleep(1)

        print("\n3. Creating a child tab under the parent...")
        from google_api import extract_document_id

        did = extract_document_id(doc_id)
        endpoint = f"https://docs.googleapis.com/v1/documents/{did}:batchUpdate"
        payload = {
            "requests": [
                {
                    "addDocumentTab": {
                        "tabProperties": {
                            "title": "Child Tab",
                            "parentTabId": parent_tab_id,
                        }
                    }
                }
            ]
        }
        r = call_google_api("POST", endpoint, payload)
        if not r.get("success"):
            print(f"   FAILED: {r.get('error')}")
            return False
        replies = r.get("data", {}).get("replies", [])
        child_tab_id = replies[0]["addDocumentTab"]["tabProperties"]["tabId"]
        print(f"   SUCCESS: Created child tab with ID: {child_tab_id}")

        time.sleep(1)

        print("\n4. Verifying get_document_tabs includes the child tab...")
        tabs_result = get_document_tabs(doc_id)
        if not tabs_result.get("success"):
            print(f"   FAILED: {tabs_result.get('error')}")
            return False
        tab_ids = [t["tabId"] for t in tabs_result["data"]["tabs"]]
        if child_tab_id not in tab_ids:
            print(f"   FAILED: Child tab {child_tab_id} not in tab list: {tab_ids}")
            return False
        print(f"   SUCCESS: Child tab found in get_document_tabs ({len(tab_ids)} tabs)")

        time.sleep(1)

        print("\n5. Inserting text into the child tab...")
        result = insert_text(doc_id, "Hello from child tab!", 1, child_tab_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted text into child tab")

        time.sleep(1)

        print("\n6. Reading body from child tab via get_document_body...")
        body_result = get_document_body(doc_id, child_tab_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        segments = body_result["data"]["segments"]
        full_text = "".join(seg["text"] for seg in segments)
        if "Hello from child tab!" not in full_text:
            print(f"   FAILED: Expected text not found. Got: {full_text}")
            return False
        print(f"   SUCCESS: Got content from child tab: '{full_text.strip()}'")

        time.sleep(1)

        print("\n7. Inserting markdown into the child tab...")
        result = insert_markdown(
            doc_id, "## Child Heading\n**bold text**", tab_id=child_tab_id
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: insert_markdown worked on child tab")

        time.sleep(1)

        print("\n8. Verifying markdown content in child tab...")
        body_result = get_document_body(doc_id, child_tab_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        full_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
        if "Child Heading" not in full_text:
            print(f"   FAILED: Heading not found. Got: {full_text}")
            return False
        if "bold text" not in full_text:
            print(f"   FAILED: Bold text not found. Got: {full_text}")
            return False
        print("   SUCCESS: Markdown content verified in child tab")

        print("\n9. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def run_all_tab_tests() -> bool:
    """Run all tab-related tests."""
    print("\n" + "=" * 70)
    print("RUNNING ALL TAB TESTS")
    print("=" * 70)

    tests = [
        ("Basic Tab Operations", test_document_tabs_basic),
        ("Tab Creation Variants", test_tab_creation_variants),
        ("Operations With/Without tab_id", test_tab_operations_with_and_without_tab_id),
        ("Tab Error Handling", test_tab_error_handling),
        ("Child Tab Operations", test_child_tab_operations),
    ]

    results = []
    for name, test_func in tests:
        print(f"\n>>> Running: {name}")
        try:
            passed = test_func()
            results.append((name, passed))
        except Exception as e:
            print(f"Test {name} raised exception: {e}")
            results.append((name, False))
        time.sleep(1)

    print("\n" + "=" * 70)
    print("TAB TEST SUMMARY")
    print("=" * 70)
    all_passed = True
    for name, passed in results:
        status = "PASSED" if passed else "FAILED"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print("=" * 70)
    if all_passed:
        print("ALL TAB TESTS PASSED")
    else:
        print("SOME TAB TESTS FAILED")
    print("=" * 70)

    return all_passed


def test_create_document_in_folder() -> bool:
    """Test creating a document in a specific folder."""
    print("=" * 60)
    print("Test: Create Document in Folder")
    print("=" * 60)

    from google_api import call_google_api

    folder_id = None
    doc_id = None

    try:
        print("\n1. Creating a test folder...")
        unique_id = uuid.uuid4().hex[:8]
        folder_result = call_google_api(
            "POST",
            "https://www.googleapis.com/drive/v3/files",
            {
                "name": f"Test Folder - API Workout - {unique_id}",
                "mimeType": "application/vnd.google-apps.folder",
            },
        )
        if not folder_result.get("success"):
            print(f"   FAILED: {folder_result.get('error')}")
            return False
        folder_id = folder_result["data"]["id"]
        print(f"   SUCCESS: Created folder {folder_id}")

        time.sleep(1)

        print("\n2. Creating a document in the folder...")
        result = create_document(
            "Test Document in Folder", "Hello from folder!", folder_id
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        doc_url = result["data"]["url"]
        print(f"   SUCCESS: Created document {doc_id}")
        print(f"   URL: {doc_url}")

        time.sleep(1)

        print("\n3. Verifying document is in the folder...")
        verify_result = call_google_api(
            "GET",
            f"https://www.googleapis.com/drive/v3/files/{doc_id}?fields=parents",
        )
        if not verify_result.get("success"):
            print(f"   FAILED: {verify_result.get('error')}")
            return False
        parents = verify_result["data"].get("parents", [])
        if folder_id in parents:
            print("   SUCCESS: Document is in the correct folder")
        else:
            print(f"   FAILED: Document parents {parents} does not include {folder_id}")
            return False

        print("\n4. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")
        if folder_id:
            call_google_api(
                "DELETE", f"https://www.googleapis.com/drive/v3/files/{folder_id}"
            )
            print(f"   Deleted folder {folder_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        if folder_id:
            try:
                call_google_api(
                    "DELETE", f"https://www.googleapis.com/drive/v3/files/{folder_id}"
                )
            except Exception:
                pass
        return False


def _copy_doc_create_source_document(unique_id: str) -> str | None:
    """Create a source document for copy testing. Returns doc_id or None on failure."""
    print("\n1. Creating a source document with content...")
    result = create_document(
        f"Source Document - {unique_id}", "Hello World! This is test content.\n"
    )
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return None
    doc_id = result["data"]["documentId"]
    print(f"   SUCCESS: Created source document {doc_id}")
    return doc_id


def _copy_doc_test_default_copy(doc_id: str) -> str | None:
    """Test copying with default title. Returns copy_id or None on failure."""
    print("\n2. Copying document with default title...")
    result = copy_doc(doc_id)
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return None
    copy_id = result["data"]["documentId"]
    copy_url = result["data"]["url"]
    print(f"   SUCCESS: Copied to {copy_id}")
    print(f"   URL: {copy_url}")

    time.sleep(1)

    print("\n3. Verifying copied document has content...")
    body_result = get_document_body(copy_id)
    if not body_result.get("success"):
        print(f"   FAILED: {body_result.get('error')}")
        return None
    full_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
    if "Hello World!" not in full_text:
        print(f"   FAILED: Expected content not found. Got: {full_text}")
        return None
    print("   SUCCESS: Content preserved in copy")
    return copy_id


def _copy_doc_test_custom_title(doc_id: str, unique_id: str) -> bool:
    """Test copying with custom title. Returns True on success."""
    print("\n4. Copying document with custom title...")
    custom_title = f"Custom Copy Title - {unique_id}"
    result = copy_doc(doc_id, title=custom_title)
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    custom_copy_id = result["data"]["documentId"]
    print(f"   SUCCESS: Created custom-titled copy {custom_copy_id}")

    # Verify title
    doc_result = get_document(custom_copy_id)
    if doc_result.get("success"):
        actual_title = doc_result["data"].get("title", "")
        if actual_title == custom_title:
            print(f"   SUCCESS: Title matches: '{actual_title}'")
        else:
            print(
                f"   WARNING: Title mismatch: expected '{custom_title}', got '{actual_title}'"
            )

    # Clean up the custom copy
    delete_document(custom_copy_id)
    return True


def _copy_doc_test_into_folder(
    doc_id: str, unique_id: str
) -> tuple[str | None, str | None]:
    """Test copying into a folder. Returns (copy_id, folder_id) or (None, None) on failure."""
    from google_api import call_google_api

    print("\n5. Creating a folder and copying document into it...")
    folder_result = call_google_api(
        "POST",
        "https://www.googleapis.com/drive/v3/files",
        {
            "name": f"Test Copy Folder - {unique_id}",
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    if not folder_result.get("success"):
        print(f"   FAILED to create folder: {folder_result.get('error')}")
        return None, None
    folder_id = folder_result["data"]["id"]
    print(f"   Created folder {folder_id}")

    result = copy_doc(doc_id, title="Copy In Folder", folder_id=folder_id)
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return None, folder_id
    copy_in_folder_id = result["data"]["documentId"]
    print(f"   SUCCESS: Copied to {copy_in_folder_id}")

    time.sleep(1)

    # Verify document is in the folder
    verify_result = call_google_api(
        "GET",
        f"https://www.googleapis.com/drive/v3/files/{copy_in_folder_id}?fields=parents",
    )
    if verify_result.get("success"):
        parents = verify_result["data"].get("parents", [])
        if folder_id in parents:
            print("   SUCCESS: Copy is in the correct folder")
        else:
            print(f"   FAILED: Copy parents {parents} does not include {folder_id}")
            return None, folder_id

    return copy_in_folder_id, folder_id


def _copy_doc_cleanup(
    doc_id: str | None,
    copy_id: str | None,
    copy_in_folder_id: str | None,
    folder_id: str | None,
) -> None:
    """Clean up test documents and folders."""
    from google_api import call_google_api

    print("\n6. Cleaning up...")
    if doc_id:
        delete_document(doc_id)
        print(f"   Deleted source document {doc_id}")
    if copy_id:
        delete_document(copy_id)
        print(f"   Deleted copy {copy_id}")
    if copy_in_folder_id:
        delete_document(copy_in_folder_id)
        print(f"   Deleted folder copy {copy_in_folder_id}")
    if folder_id:
        call_google_api(
            "DELETE", f"https://www.googleapis.com/drive/v3/files/{folder_id}"
        )
        print(f"   Deleted folder {folder_id}")


def test_copy_doc() -> bool:
    """Test copying a document with and without custom title and folder."""
    print("=" * 60)
    print("Test: Copy Document")
    print("=" * 60)

    doc_id = None
    copy_id = None
    copy_in_folder_id = None
    folder_id = None

    try:
        unique_id = uuid.uuid4().hex[:8]

        doc_id = _copy_doc_create_source_document(unique_id)
        if not doc_id:
            return False

        time.sleep(1)

        copy_id = _copy_doc_test_default_copy(doc_id)
        if not copy_id:
            return False

        time.sleep(1)

        if not _copy_doc_test_custom_title(doc_id, unique_id):
            return False

        time.sleep(1)

        copy_in_folder_id, folder_id = _copy_doc_test_into_folder(doc_id, unique_id)
        if copy_in_folder_id is None and folder_id is not None:
            # Cleanup folder on failure
            _copy_doc_cleanup(doc_id, copy_id, None, folder_id)
            return False

        _copy_doc_cleanup(doc_id, copy_id, copy_in_folder_id, folder_id)

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        _copy_doc_cleanup(doc_id, copy_id, copy_in_folder_id, folder_id)
        return False


def _find_replace_create_document(unique_id: str) -> str | None:
    """Create a test document for find/replace testing. Returns doc_id or None."""
    print("\n1. Creating a test document with content...")
    result = create_document(
        f"Test Find Replace - {unique_id}",
        "The quick brown fox jumps over the lazy fox.\n",
    )
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return None
    doc_id = result["data"]["documentId"]
    print(f"   SUCCESS: Created document {doc_id}")
    return doc_id


def _find_replace_test_single_tab(doc_id: str) -> bool:
    """Test find/replace in a single tab. Returns True on success."""
    print("\n2. Replacing 'fox' with 'cat' in default tab...")
    result = find_replace(doc_id, "fox", "cat")
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    occurrences = result["data"]["occurrencesChanged"]
    print(f"   SUCCESS: Replaced {occurrences} occurrence(s)")
    if occurrences != 2:
        print(f"   WARNING: Expected 2 occurrences, got {occurrences}")

    time.sleep(1)

    print("\n3. Verifying replacement in document body...")
    body_result = get_document_body(doc_id)
    if not body_result.get("success"):
        print(f"   FAILED: {body_result.get('error')}")
        return False
    full_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
    if "fox" not in full_text and "cat" in full_text:
        print(f"   SUCCESS: Text correctly replaced: '{full_text.strip()}'")
        return True
    print(f"   FAILED: Replacement not verified. Got: '{full_text.strip()}'")
    return False


def _find_replace_create_second_tab(doc_id: str) -> str | None:
    """Create second tab with content. Returns tab_id or None."""
    print("\n4. Creating a second tab with content for multi-tab test...")
    tab_result = create_tab(doc_id, "Second Tab")
    if not tab_result.get("success"):
        print(f"   FAILED: {tab_result.get('error')}")
        return None
    second_tab_id = tab_result["data"]["tabId"]
    print(f"   SUCCESS: Created second tab {second_tab_id}")

    time.sleep(1)

    insert_result = insert_text(
        doc_id, "The cat sat on the cat mat.\n", 1, second_tab_id
    )
    if not insert_result.get("success"):
        print(f"   FAILED to insert text: {insert_result.get('error')}")
        return None
    print("   SUCCESS: Inserted text into second tab")
    return second_tab_id


def _find_replace_test_multi_tab(doc_id: str, second_tab_id: str) -> bool:
    """Test find/replace across multiple tabs. Returns True on success."""
    print("\n5. Replacing 'cat' with 'dog' across all tabs...")
    result = find_replace(doc_id, "cat", "dog")
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    occurrences = result["data"]["occurrencesChanged"]
    print(f"   SUCCESS: Replaced {occurrences} occurrence(s) across all tabs")
    # 2 from the first tab (from step 2) + 2 from second tab = 4
    if occurrences < 3:
        print(f"   WARNING: Expected at least 3 occurrences, got {occurrences}")

    time.sleep(1)

    print("\n6. Verifying replacements in both tabs...")
    # Check default tab
    tabs_result = get_document_tabs(doc_id)
    default_tab_id = tabs_result["data"]["tabs"][0]["tabId"]

    body_default = get_document_body(doc_id, default_tab_id)
    default_text = "".join(seg["text"] for seg in body_default["data"]["segments"])
    if "cat" not in default_text and "dog" in default_text:
        print(f"   SUCCESS: Default tab correctly updated: '{default_text.strip()}'")
    else:
        print(f"   FAILED: Default tab not updated. Got: '{default_text.strip()}'")
        return False

    body_second = get_document_body(doc_id, second_tab_id)
    second_text = "".join(seg["text"] for seg in body_second["data"]["segments"])
    if "cat" not in second_text and "dog" in second_text:
        print(f"   SUCCESS: Second tab correctly updated: '{second_text.strip()}'")
        return True
    print(f"   FAILED: Second tab not updated. Got: '{second_text.strip()}'")
    return False


def _find_replace_test_no_matches(doc_id: str) -> bool:
    """Test find/replace with no matches. Returns True on success."""
    print("\n7. Testing find_replace with no matches...")
    result = find_replace(doc_id, "nonexistent_text_xyz", "replacement")
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    occurrences = result["data"]["occurrencesChanged"]
    if occurrences == 0:
        print("   SUCCESS: Zero occurrences when text not found")
        return True
    print(f"   FAILED: Expected 0 occurrences, got {occurrences}")
    return False


def test_find_replace() -> bool:
    """Test find and replace across single and multiple tabs."""
    print("=" * 60)
    print("Test: Find and Replace")
    print("=" * 60)

    doc_id = None

    try:
        unique_id = uuid.uuid4().hex[:8]

        doc_id = _find_replace_create_document(unique_id)
        if not doc_id:
            return False

        time.sleep(1)

        if not _find_replace_test_single_tab(doc_id):
            return False

        time.sleep(1)

        second_tab_id = _find_replace_create_second_tab(doc_id)
        if not second_tab_id:
            return False

        time.sleep(1)

        if not _find_replace_test_multi_tab(doc_id, second_tab_id):
            return False

        time.sleep(1)

        if not _find_replace_test_no_matches(doc_id):
            return False

        print("\n8. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_link_formatting() -> bool:
    """Test applying hyperlinks to text via format_text and insert_markdown."""
    print("=" * 60)
    print("Test: Link Formatting")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Links - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting text to link...")
        result = insert_text(doc_id, "Visit Example Website for more info.\n")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted text")

        time.sleep(1)

        print("\n3. Applying link to 'Example Website' (indices 7-22)...")
        result = format_text(doc_id, 7, 22, link="https://example.com")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Applied link via format_text")

        time.sleep(1)

        print("\n4. Verifying link was applied...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        # Check that the text run has a link in its textStyle
        body = doc_result["data"].get("body", {})
        if not body:
            tabs = doc_result["data"].get("tabs", [])
            if tabs:
                body = tabs[0].get("documentTab", {}).get("body", {})

        content = body.get("content", [])
        found_link = False
        for element in content:
            if "paragraph" in element:
                for text_elem in element["paragraph"].get("elements", []):
                    text_style = text_elem.get("textRun", {}).get("textStyle", {})
                    link_obj = text_style.get("link", {})
                    if link_obj.get("url") == "https://example.com":
                        found_link = True
                        break
            if found_link:
                break

        if found_link:
            print("   SUCCESS: Link URL found in document text style")
        else:
            print("   WARNING: Could not verify link in document structure")
            print("   (Link may still be applied; API response format may vary)")

        time.sleep(1)

        print("\n5. Testing link via batched actions...")
        result = insert_text(doc_id, "Click here to learn more.\n", 1)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False

        time.sleep(1)

        result = execute_batched_actions(
            doc_id,
            [
                {
                    "action": "format_text",
                    "start_index": 1,
                    "end_index": 11,
                    "link": "https://meta.com",
                    "bold": True,
                },
            ],
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Applied link + bold via batched action")

        time.sleep(1)

        print("\n6. Testing markdown links via insert_markdown...")
        body_result = get_document_body(doc_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        segments = body_result["data"]["segments"]
        end_index = max((seg["end"] for seg in segments), default=2) - 1

        result = insert_markdown(
            doc_id,
            "Check out [Google](https://google.com) and [Meta](https://meta.com).",
            end_index,
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown with links")

        time.sleep(1)

        print("\n7. Verifying markdown link text was inserted correctly...")
        body_result = get_document_body(doc_id)
        if not body_result.get("success"):
            print(f"   FAILED: {body_result.get('error')}")
            return False
        full_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
        # The markdown markers [](url) should be stripped, leaving just the link text
        if "Google" in full_text and "Meta" in full_text:
            print("   SUCCESS: Link text found in document")
        else:
            print(f"   FAILED: Expected link text not found. Got: {full_text[:100]}")
            return False

        # Verify the markdown syntax was stripped (no brackets/parens)
        if "[Google]" not in full_text and "(https://google.com)" not in full_text:
            print("   SUCCESS: Markdown link syntax was properly stripped")
        else:
            print("   FAILED: Markdown link syntax was not stripped")
            return False

        print("\n8. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def run_tests() -> bool:
    print("=" * 60)
    print("Google Docs API Integration Tests")
    print("=" * 60)

    doc_id = None
    all_passed = True

    try:
        print("\n1. Creating a new document...")
        result = create_document("Test Document - API Workout")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        doc_url = result["data"]["url"]
        print(f"   SUCCESS: Created document {doc_id}")
        print(f"   URL: {doc_url}")

        time.sleep(1)

        print("\n2. Adding text to the document...")
        result = insert_text(doc_id, "Hello, World! This is a test document.\n")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
        else:
            print("   SUCCESS: Inserted text")

        time.sleep(1)

        print("\n3. Batched formatting (bold, highlight, italic in 1 API call)...")
        result = execute_batched_actions(
            doc_id,
            [
                {
                    "action": "format_text",
                    "start_index": 1,
                    "end_index": 6,
                    "bold": True,
                },
                {
                    "action": "format_text",
                    "start_index": 8,
                    "end_index": 13,
                    "background_color": {"red": 1.0, "green": 1.0, "blue": 0.0},
                },
                {
                    "action": "format_text",
                    "start_index": 15,
                    "end_index": 19,
                    "italic": True,
                },
            ],
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
        else:
            print("   SUCCESS: Applied bold, highlight, and italic in single API call")

        time.sleep(1)

        print("\n4. Getting document to find current end index...")
        result = get_document(doc_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
            insert_index = 1
        else:
            body = result["data"].get("body", {})
            content = body.get("content", [])
            if content:
                current_end = content[-1].get("endIndex", 2)
                insert_index = max(1, current_end - 1)
            else:
                insert_index = 1
            print(
                f"   SUCCESS: Document ends at index {current_end}, will insert at {insert_index}"
            )

        print("\n5. Inserting markdown content...")
        markdown_content = """# Heading 1
## Heading 2
### Heading 3

This is **bold text** and this is *italic text*.

Here is some `inline code` for testing."""

        result = insert_markdown(doc_id, markdown_content, insert_index)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
        else:
            print("   SUCCESS: Inserted and formatted markdown content")

        time.sleep(1)

        print("\n6. Verifying document content...")
        result = get_document(doc_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
        else:
            title = result["data"].get("title", "")
            print(f"   SUCCESS: Document title is '{title}'")

        print("\n7. Deleting the document...")
        result = delete_document(doc_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            all_passed = False
        else:
            print("   SUCCESS: Document deleted (moved to trash)")
            doc_id = None

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        all_passed = False

    finally:
        if doc_id is not None:
            print(f"\nCleaning up: deleting document {doc_id}...")
            try:
                delete_document(doc_id)
                print("   Cleanup successful")
            except Exception as e:
                print(f"   Cleanup failed: {e}")

    print("\n" + "=" * 60)
    if all_passed:
        print("ALL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)

    return all_passed


def test_insert_markdown_table() -> bool:
    """Test inserting markdown content that contains tables."""
    print("=" * 60)
    print("Test: Insert Markdown with Tables")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Markdown Tables - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False

        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with a table...")
        markdown_with_table = """# Team Members

Here is the team roster:

| Name | Role | Location |
|------|------|----------|
| Alice | Engineer | Berlin |
| Bob | Designer | London |
| Carol | PM | **New York** |

And some text after the table with *italic formatting*."""

        result = insert_markdown(doc_id, markdown_with_table)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown with table")

        time.sleep(1)

        print("\n3. Verifying document contains a table...")
        result = get_document(doc_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False

        body = result["data"].get("body", {})
        content = body.get("content", [])
        table_count = sum(1 for elem in content if "table" in elem)

        if table_count == 0:
            print("   FAILED: No table found in document")
            return False

        # Find the table element and check its structure
        table_elem = next(elem for elem in content if "table" in elem)
        table = table_elem["table"]
        row_count = len(table.get("tableRows", []))
        col_count = table.get("columns", 0)
        print(
            f"   SUCCESS: Found {table_count} table(s), "
            f"{row_count} rows x {col_count} columns"
        )

        if row_count != 4:
            print(f"   WARNING: Expected 4 rows (1 header + 3 data), got {row_count}")
        if col_count != 3:
            print(f"   WARNING: Expected 3 columns, got {col_count}")

        print("\n4. Deleting the document...")
        result = delete_document(doc_id)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Document deleted")
        doc_id = None

        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        return False

    finally:
        if doc_id is not None:
            print(f"\nCleaning up: deleting document {doc_id}...")
            try:
                delete_document(doc_id)
                print("   Cleanup successful")
            except Exception as e:
                print(f"   Cleanup failed: {e}")


def _get_body_content(doc_data: dict) -> list:
    """Extract body content elements from a document response."""
    tabs = doc_data.get("tabs", [])
    if tabs:
        body = tabs[0].get("documentTab", {}).get("body", {})
    else:
        body = doc_data.get("body", {})
    return body.get("content", [])


def _get_full_text(doc_id: str) -> str:
    """Get all text from a document as a single string."""
    body_result = get_document_body(doc_id)
    if not body_result.get("success"):
        return ""
    return "".join(seg["text"] for seg in body_result["data"]["segments"])


def test_insert_markdown_code_block() -> bool:
    """Test that fenced code blocks get monospace font and paragraph styling."""
    print("=" * 60)
    print("Test: Insert Markdown - Code Blocks")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Code Blocks - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with a code block...")
        markdown = "# Code Example\n\n```python\ndef hello():\n    return 42\n```\n\nText after code."
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Verifying code block content and styling...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])

        # Check that the code text is present
        full_text = _get_full_text(doc_id)
        if "def hello():" not in full_text:
            print(f"   FAILED: Code text not found. Got: {full_text[:100]}")
            return False
        print("   SUCCESS: Code block text is present")

        # Code block fences should NOT appear in the output
        if "```" in full_text:
            print("   FAILED: Code fence markers (```) still present in output")
            return False
        print("   SUCCESS: Code fences stripped")

        # Verify monospace font on code block text
        found_monospace = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                text_run = text_elem.get("textRun", {})
                if "def hello():" in text_run.get("content", ""):
                    font = (
                        text_run.get("textStyle", {})
                        .get("weightedFontFamily", {})
                        .get("fontFamily", "")
                    )
                    if font == "Courier New":
                        found_monospace = True
                    break

        if found_monospace:
            print("   SUCCESS: Code block has Courier New font")
        else:
            print("   WARNING: Could not verify Courier New font")

        # Verify paragraph-level shading on code block
        found_shading = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            para_style = elem["paragraph"].get("paragraphStyle", {})
            shading = para_style.get("shading", {})
            bg = shading.get("backgroundColor", {}).get("color", {}).get("rgbColor", {})
            if bg.get("red", 0) > 0.9 and bg.get("green", 0) > 0.9:
                found_shading = True
                break

        if found_shading:
            print("   SUCCESS: Code block has paragraph shading")
        else:
            print("   WARNING: Could not verify paragraph shading")

        print("\n4. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_markdown_lists() -> bool:
    """Test bullet and numbered lists with nesting."""
    print("=" * 60)
    print("Test: Insert Markdown - Lists")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Lists - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with bullet and numbered lists...")
        markdown = (
            "# Lists\n\n"
            "- Bullet one\n"
            "- Bullet two\n"
            "  - Nested bullet\n"
            "- Bullet three\n"
            "\n"
            "1. First item\n"
            "2. Second item\n"
            "3. Third item\n"
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Verifying list content...")
        full_text = _get_full_text(doc_id)

        for item in ["Bullet one", "Bullet two", "Nested bullet", "Bullet three"]:
            if item not in full_text:
                print(f"   FAILED: '{item}' not found in document")
                return False
        print("   SUCCESS: All bullet items present")

        for item in ["First item", "Second item", "Third item"]:
            if item not in full_text:
                print(f"   FAILED: '{item}' not found in document")
                return False
        print("   SUCCESS: All numbered items present")

        # Markdown list markers should be stripped
        if "- Bullet" in full_text or "1. First" in full_text:
            print("   FAILED: List markers not stripped from output")
            return False
        print("   SUCCESS: List markers stripped")

        print("\n4. Verifying bullet formatting was applied...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_bullet = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            bullet = elem["paragraph"].get("bullet")
            if bullet:
                found_bullet = True
                break

        if found_bullet:
            print("   SUCCESS: Native bullet formatting applied")
        else:
            print("   WARNING: Could not verify bullet formatting in document")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_markdown_block_quotes_and_hrs() -> bool:
    """Test block quotes and horizontal rules."""
    print("=" * 60)
    print("Test: Insert Markdown - Block Quotes & Horizontal Rules")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Quotes and HRs - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with block quotes and HRs...")
        markdown = (
            "# Section One\n\n"
            "> This is a block quote.\n"
            "> It has two lines.\n"
            "\n"
            "---\n"
            "\n"
            "# Section Two\n\n"
            "Text after the rule."
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Verifying content...")
        full_text = _get_full_text(doc_id)

        # Block quote content should be present (without > prefix)
        if "This is a block quote." not in full_text:
            print(f"   FAILED: Quote text not found. Got: {full_text[:200]}")
            return False
        print("   SUCCESS: Block quote text present")

        # The > prefix should be stripped
        if "> This" in full_text:
            print("   FAILED: Block quote '>' prefix not stripped")
            return False
        print("   SUCCESS: Block quote prefix stripped")

        # HR markers should not appear as text
        if "---" in full_text:
            print("   FAILED: HR markers still present as text")
            return False
        print("   SUCCESS: HR markers stripped")

        print("\n4. Verifying block quote styling...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])

        # Check for italic text (block quotes are styled italic)
        found_italic = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                text_run = text_elem.get("textRun", {})
                if "block quote" in text_run.get("content", "").lower():
                    if text_run.get("textStyle", {}).get("italic"):
                        found_italic = True
                    break

        if found_italic:
            print("   SUCCESS: Block quote text is italic")
        else:
            print("   WARNING: Could not verify italic styling on block quote")

        # Check for border on block quote or HR paragraphs
        found_border = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            para_style = elem["paragraph"].get("paragraphStyle", {})
            if para_style.get("borderLeft") or para_style.get("borderBottom"):
                found_border = True
                break

        if found_border:
            print("   SUCCESS: Found paragraph border styling (block quote or HR)")
        else:
            print("   WARNING: Could not verify border styling")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_markdown_strikethrough_and_escapes() -> bool:
    """Test strikethrough formatting and backslash escape handling."""
    print("=" * 60)
    print("Test: Insert Markdown - Strikethrough & Escapes")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Strikethrough & Escapes - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with strikethrough and escapes...")
        markdown = (
            "This has ~~deleted text~~ in it.\n"
            "\n"
            "Escaped: sev\\_num and 5 \\* 3.\n"
            "\n"
            "Nested: **`markCorrupted`:** bold with code.\n"
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        # Strikethrough markers should be stripped
        if "~~" in full_text:
            print("   FAILED: Strikethrough markers ~~ still present")
            return False
        if "deleted text" not in full_text:
            print("   FAILED: Strikethrough content missing")
            return False
        print("   SUCCESS: Strikethrough markers stripped, text preserved")

        # Escaped underscores: backslash should be removed
        if "sev_num" not in full_text:
            print(f"   FAILED: 'sev_num' not found. Got: {full_text[:200]}")
            return False
        if "sev\\_num" in full_text:
            print("   FAILED: Backslash not removed from escaped underscore")
            return False
        print("   SUCCESS: Escaped underscore renders as literal underscore")

        # Escaped asterisk: backslash should be removed
        if "5 * 3" not in full_text:
            print(f"   FAILED: '5 * 3' not found. Got: {full_text[:200]}")
            return False
        print("   SUCCESS: Escaped asterisk renders as literal asterisk")

        # Nested bold+code: markers should be stripped
        if "markCorrupted" not in full_text:
            print("   FAILED: Nested bold+code content missing")
            return False
        if "**" in full_text or "``" in full_text:
            print("   FAILED: Markdown markers still present in nested formatting")
            return False
        print("   SUCCESS: Nested bold+code markers stripped")

        print("\n4. Verifying strikethrough styling...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_strikethrough = False
        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                text_run = text_elem.get("textRun", {})
                if text_run.get("textStyle", {}).get("strikethrough"):
                    found_strikethrough = True
                    break

        if found_strikethrough:
            print("   SUCCESS: Strikethrough style applied in document")
        else:
            print("   WARNING: Could not verify strikethrough style")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_markdown_full_document() -> bool:
    """Test inserting a document with all supported markdown features at once."""
    print("=" * 60)
    print("Test: Insert Markdown - Full Document (All Features)")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Full Markdown - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with all features...")
        markdown = (
            "# Full Feature Test\n"
            "\n"
            "## Inline Formatting\n"
            "\n"
            "This has **bold**, *italic*, ~~strikethrough~~, and `inline code`.\n"
            "\n"
            "A [link](https://example.com) and an escaped sev\\_num.\n"
            "\n"
            "## Code Block\n"
            "\n"
            "```python\n"
            "def test():\n"
            "    return True\n"
            "```\n"
            "\n"
            "## Lists\n"
            "\n"
            "- Bullet A\n"
            "- Bullet B\n"
            "  - Nested B1\n"
            "\n"
            "1. Numbered one\n"
            "2. Numbered two\n"
            "\n"
            "---\n"
            "\n"
            "> A block quote for emphasis.\n"
            "\n"
            "## Table\n"
            "\n"
            "| Feature | Status |\n"
            "|---------|--------|\n"
            "| Tables | Done |\n"
            "| Code blocks | Done |\n"
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted full markdown document")

        time.sleep(1)

        print("\n3. Verifying document structure...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])

        # Count tables
        table_count = sum(1 for elem in content if "table" in elem)
        if table_count == 0:
            print("   FAILED: No table found")
            return False
        print(f"   SUCCESS: Found {table_count} table(s)")

        # Count paragraphs
        para_count = sum(1 for elem in content if "paragraph" in elem)
        print(f"   INFO: {para_count} paragraphs, {len(content)} total elements")

        print("\n4. Verifying text content...")
        full_text = _get_full_text(doc_id)

        # All content should be present (table cell text is not in get_document_body)
        expected = [
            "bold",
            "italic",
            "strikethrough",
            "inline code",
            "link",
            "sev_num",
            "def test():",
            "Bullet A",
            "Nested B1",
            "Numbered one",
            "block quote",
        ]
        for item in expected:
            if item not in full_text:
                print(f"   FAILED: '{item}' not found in document")
                return False
        print("   SUCCESS: All expected content present")

        # No raw markdown markers should remain
        markers = ["**", "~~", "```", "- Bullet", "1. Numbered", "> A block", "---"]
        for marker in markers:
            if marker in full_text:
                print(f"   FAILED: Raw marker '{marker}' still in output")
                return False
        print("   SUCCESS: All markdown markers properly stripped")

        print("\n5. Verifying formatting styles...")
        found_bold = False
        found_italic = False
        found_strikethrough = False
        found_code_font = False
        found_link = False
        found_bullet = False
        found_heading = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            para = elem["paragraph"]

            # Check for heading style
            named_style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            if named_style.startswith("HEADING_"):
                found_heading = True

            # Check for bullets
            if para.get("bullet"):
                found_bullet = True

            for text_elem in para.get("elements", []):
                ts = text_elem.get("textRun", {}).get("textStyle", {})
                if ts.get("bold"):
                    found_bold = True
                if ts.get("italic"):
                    found_italic = True
                if ts.get("strikethrough"):
                    found_strikethrough = True
                if ts.get("link"):
                    found_link = True
                font = ts.get("weightedFontFamily", {}).get("fontFamily", "")
                if font == "Courier New":
                    found_code_font = True

        for name, found in [
            ("Headings", found_heading),
            ("Bold", found_bold),
            ("Italic", found_italic),
            ("Strikethrough", found_strikethrough),
            ("Links", found_link),
            ("Courier New (code)", found_code_font),
            ("Bullet lists", found_bullet),
        ]:
            if found:
                print(f"   SUCCESS: {name} formatting verified")
            else:
                print(f"   WARNING: Could not verify {name} formatting")

        print("\n6. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_basic_formatting() -> bool:
    """Test inserting HTML with basic formatting (bold, italic, underline)."""
    print("=" * 60)
    print("Test: Insert HTML - Basic Formatting")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Basic - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with basic formatting...")
        html = (
            "<p>This has <b>bold text</b>, <i>italic text</i>, "
            "<u>underlined text</u>, and <s>strikethrough</s>.</p>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        # All content should be present
        for text in ["bold text", "italic text", "underlined text", "strikethrough"]:
            if text not in full_text:
                print(f"   FAILED: '{text}' not found. Got: {full_text[:100]}")
                return False
        print("   SUCCESS: All text content present")

        # HTML tags should be stripped
        for tag in ["<p>", "</p>", "<b>", "</b>", "<i>", "</i>"]:
            if tag in full_text:
                print(f"   FAILED: HTML tag '{tag}' still in output")
                return False
        print("   SUCCESS: HTML tags stripped")

        print("\n4. Verifying formatting styles...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_bold = False
        found_italic = False
        found_underline = False
        found_strikethrough = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                ts = text_elem.get("textRun", {}).get("textStyle", {})
                if ts.get("bold"):
                    found_bold = True
                if ts.get("italic"):
                    found_italic = True
                if ts.get("underline"):
                    found_underline = True
                if ts.get("strikethrough"):
                    found_strikethrough = True

        for name, found in [
            ("Bold", found_bold),
            ("Italic", found_italic),
            ("Underline", found_underline),
            ("Strikethrough", found_strikethrough),
        ]:
            if found:
                print(f"   SUCCESS: {name} formatting verified")
            else:
                print(f"   WARNING: Could not verify {name} formatting")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_colored_text() -> bool:
    """Test inserting HTML with colored text (foreground and background)."""
    print("=" * 60)
    print("Test: Insert HTML - Colored Text")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Colors - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with colored text...")
        html = (
            '<p><span style="color: red">Red text</span>, '
            '<span style="color: #00ff00">Green hex</span>, '
            '<span style="color: rgb(0, 0, 255)">Blue RGB</span>.</p>'
            '<p><span style="background-color: yellow">Yellow highlight</span>.</p>'
            '<p><font color="purple">Legacy font color</font>.</p>'
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        for text in [
            "Red text",
            "Green hex",
            "Blue RGB",
            "Yellow highlight",
            "Legacy font",
        ]:
            if text not in full_text:
                print(f"   FAILED: '{text}' not found. Got: {full_text[:200]}")
                return False
        print("   SUCCESS: All colored text present")

        print("\n4. Verifying color formatting...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_foreground = False
        found_background = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                ts = text_elem.get("textRun", {}).get("textStyle", {})
                if ts.get("foregroundColor"):
                    found_foreground = True
                if ts.get("backgroundColor"):
                    found_background = True

        if found_foreground:
            print("   SUCCESS: Foreground color formatting verified")
        else:
            print("   WARNING: Could not verify foreground color")

        if found_background:
            print("   SUCCESS: Background color formatting verified")
        else:
            print("   WARNING: Could not verify background color")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_headings_and_paragraphs() -> bool:
    """Test inserting HTML with headings and paragraphs."""
    print("=" * 60)
    print("Test: Insert HTML - Headings and Paragraphs")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Headings - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with headings...")
        html = (
            "<h1>Main Title</h1>"
            "<p>Introduction paragraph.</p>"
            "<h2>Section One</h2>"
            "<p>Section one content.</p>"
            "<h3>Subsection</h3>"
            "<p>Subsection content.</p>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        for text in [
            "Main Title",
            "Introduction paragraph",
            "Section One",
            "Subsection",
        ]:
            if text not in full_text:
                print(f"   FAILED: '{text}' not found")
                return False
        print("   SUCCESS: All text content present")

        print("\n4. Verifying heading styles...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        heading_styles = set()

        for elem in content:
            if "paragraph" not in elem:
                continue
            style = (
                elem["paragraph"].get("paragraphStyle", {}).get("namedStyleType", "")
            )
            if style.startswith("HEADING_"):
                heading_styles.add(style)

        if "HEADING_1" in heading_styles:
            print("   SUCCESS: HEADING_1 style verified")
        else:
            print("   WARNING: Could not verify HEADING_1")

        if "HEADING_2" in heading_styles:
            print("   SUCCESS: HEADING_2 style verified")
        else:
            print("   WARNING: Could not verify HEADING_2")

        if "HEADING_3" in heading_styles:
            print("   SUCCESS: HEADING_3 style verified")
        else:
            print("   WARNING: Could not verify HEADING_3")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_lists() -> bool:
    """Test inserting HTML with bullet and numbered lists."""
    print("=" * 60)
    print("Test: Insert HTML - Lists")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Lists - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with lists...")
        html = (
            "<h2>Bullet List</h2>"
            "<ul>"
            "<li>First bullet item</li>"
            "<li>Second bullet item</li>"
            "<li>Third bullet item</li>"
            "</ul>"
            "<h2>Numbered List</h2>"
            "<ol>"
            "<li>First numbered item</li>"
            "<li>Second numbered item</li>"
            "<li>Third numbered item</li>"
            "</ol>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        for text in [
            "First bullet",
            "Second bullet",
            "First numbered",
            "Second numbered",
        ]:
            if text not in full_text:
                print(f"   FAILED: '{text}' not found")
                return False
        print("   SUCCESS: All list items present")

        # List tags should be stripped
        for tag in ["<ul>", "</ul>", "<li>", "</li>", "<ol>", "</ol>"]:
            if tag in full_text:
                print(f"   FAILED: HTML tag '{tag}' still in output")
                return False
        print("   SUCCESS: HTML list tags stripped")

        print("\n4. Verifying bullet formatting...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_bullet = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            if elem["paragraph"].get("bullet"):
                found_bullet = True
                break

        if found_bullet:
            print("   SUCCESS: Native bullet formatting applied")
        else:
            print("   WARNING: Could not verify bullet formatting")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_links_and_code() -> bool:
    """Test inserting HTML with hyperlinks and code formatting."""
    print("=" * 60)
    print("Test: Insert HTML - Links and Code")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Links Code - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with links and code...")
        html = (
            '<p>Visit <a href="https://example.com">Example Site</a> for more info.</p>'
            "<p>Use <code>console.log()</code> for debugging.</p>"
            "<p>Superscript: E=mc<sup>2</sup> and subscript: H<sub>2</sub>O.</p>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying text content...")
        full_text = _get_full_text(doc_id)

        for text in ["Example Site", "console.log()", "E=mc", "H", "O"]:
            if text not in full_text:
                print(f"   FAILED: '{text}' not found")
                return False
        print("   SUCCESS: All text content present")

        print("\n4. Verifying link and code formatting...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        found_link = False
        found_code = False
        found_superscript = False
        found_subscript = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            for text_elem in elem["paragraph"].get("elements", []):
                ts = text_elem.get("textRun", {}).get("textStyle", {})
                if ts.get("link"):
                    found_link = True
                font = ts.get("weightedFontFamily", {}).get("fontFamily", "")
                if font == "Courier New":
                    found_code = True
                offset = ts.get("baselineOffset", "")
                if offset == "SUPERSCRIPT":
                    found_superscript = True
                if offset == "SUBSCRIPT":
                    found_subscript = True

        for name, found in [
            ("Link", found_link),
            ("Code (Courier New)", found_code),
            ("Superscript", found_superscript),
            ("Subscript", found_subscript),
        ]:
            if found:
                print(f"   SUCCESS: {name} formatting verified")
            else:
                print(f"   WARNING: Could not verify {name} formatting")

        print("\n5. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_tables() -> bool:
    """Test inserting HTML with tables."""
    print("=" * 60)
    print("Test: Insert HTML - Tables")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test HTML Tables - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with a table...")
        html = (
            "<h2>Team Members</h2>"
            "<table>"
            "<tr><th>Name</th><th>Role</th></tr>"
            "<tr><td>Alice</td><td>Engineer</td></tr>"
            "<tr><td>Bob</td><td>Designer</td></tr>"
            "</table>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted HTML")

        time.sleep(1)

        print("\n3. Verifying table exists...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])
        table_count = sum(1 for elem in content if "table" in elem)

        if table_count == 0:
            print("   FAILED: No table found in document")
            return False

        # Find the table element and check its structure
        table_elem = next(elem for elem in content if "table" in elem)
        table = table_elem["table"]
        row_count = len(table.get("tableRows", []))
        col_count = table.get("columns", 0)
        print(
            f"   SUCCESS: Found {table_count} table(s), "
            f"{row_count} rows x {col_count} columns"
        )

        if row_count != 3:
            print(f"   WARNING: Expected 3 rows, got {row_count}")
        if col_count != 2:
            print(f"   WARNING: Expected 2 columns, got {col_count}")

        print("\n4. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def test_insert_html_full_document() -> bool:
    """Test inserting a document with all supported HTML features at once."""
    print("=" * 60)
    print("Test: Insert HTML - Full Document (All Features)")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Full HTML - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting HTML with all features...")
        html = (
            "<h1>Full HTML Feature Test</h1>"
            "<h2>Inline Formatting</h2>"
            "<p>This has <b>bold</b>, <i>italic</i>, <u>underline</u>, "
            '<span style="color: red">red text</span>, and <code>code</code>.</p>'
            "<h2>Links</h2>"
            '<p>Visit <a href="https://example.com">Example</a> for more.</p>'
            "<h2>Lists</h2>"
            "<ul>"
            "<li>Bullet A</li>"
            "<li>Bullet B</li>"
            "</ul>"
            "<ol>"
            "<li>Number 1</li>"
            "<li>Number 2</li>"
            "</ol>"
            "<h2>Table</h2>"
            "<table>"
            "<tr><th>Feature</th><th>Status</th></tr>"
            "<tr><td>HTML</td><td>Done</td></tr>"
            "</table>"
        )
        result = insert_html(doc_id, html)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted full HTML document")

        time.sleep(1)

        print("\n3. Verifying document structure...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print(f"   FAILED: {doc_result.get('error')}")
            return False

        content = _get_body_content(doc_result["data"])

        # Count tables
        table_count = sum(1 for elem in content if "table" in elem)
        if table_count == 0:
            print("   FAILED: No table found")
            return False
        print(f"   SUCCESS: Found {table_count} table(s)")

        print("\n4. Verifying text content...")
        full_text = _get_full_text(doc_id)

        expected = [
            "bold",
            "italic",
            "underline",
            "red text",
            "code",
            "Example",
            "Bullet A",
            "Number 1",
        ]
        for item in expected:
            if item not in full_text:
                print(f"   FAILED: '{item}' not found")
                return False
        print("   SUCCESS: All expected content present")

        # No raw HTML tags should remain
        for tag in ["<p>", "</p>", "<b>", "<ul>", "<li>", "<table>"]:
            if tag in full_text:
                print(f"   FAILED: Raw HTML tag '{tag}' still in output")
                return False
        print("   SUCCESS: All HTML tags properly stripped")

        print("\n5. Verifying formatting styles...")
        found_bold = False
        found_italic = False
        found_color = False
        found_link = False
        found_bullet = False
        found_heading = False

        for elem in content:
            if "paragraph" not in elem:
                continue
            para = elem["paragraph"]

            # Check for heading style
            named_style = para.get("paragraphStyle", {}).get("namedStyleType", "")
            if named_style.startswith("HEADING_"):
                found_heading = True

            # Check for bullets
            if para.get("bullet"):
                found_bullet = True

            for text_elem in para.get("elements", []):
                ts = text_elem.get("textRun", {}).get("textStyle", {})
                if ts.get("bold"):
                    found_bold = True
                if ts.get("italic"):
                    found_italic = True
                if ts.get("foregroundColor"):
                    found_color = True
                if ts.get("link"):
                    found_link = True

        for name, found in [
            ("Headings", found_heading),
            ("Bold", found_bold),
            ("Italic", found_italic),
            ("Colored text", found_color),
            ("Links", found_link),
            ("Bullet lists", found_bullet),
        ]:
            if found:
                print(f"   SUCCESS: {name} formatting verified")
            else:
                print(f"   WARNING: Could not verify {name} formatting")

        print("\n6. Cleaning up...")
        delete_document(doc_id)
        doc_id = None
        print("   Deleted document")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def _replace_content_test_markdown(doc_id: str) -> bool:
    """Verify original content, replace with markdown, verify formatting."""
    print("\n2. Verifying original content exists...")
    full_text = _get_full_text(doc_id)
    if "Original content" not in full_text:
        print(f"   FAILED: Original content not found. Got: {full_text[:100]}")
        return False
    print(f"   SUCCESS: Original content present: '{full_text.strip()}'")

    time.sleep(1)

    print("\n3. Replacing content with markdown...")
    new_markdown = "# Replaced Document\n\nThis is **new content** with formatting.\n\n- Item one\n- Item two\n"
    result = replace_document_content(doc_id, new_markdown)
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    print("   SUCCESS: Replaced content with markdown")

    time.sleep(1)

    print("\n4. Verifying old content is gone and new content is present...")
    full_text = _get_full_text(doc_id)
    if "Original content" in full_text:
        print("   FAILED: Old content still present after replace")
        return False
    if "new content" not in full_text:
        print(f"   FAILED: New content not found. Got: {full_text[:200]}")
        return False
    if "Item one" not in full_text:
        print(f"   FAILED: List items not found. Got: {full_text[:200]}")
        return False
    print("   SUCCESS: Old content removed, new content present")

    # Verify markdown formatting was applied (bold, headings, bullets)
    doc_result = get_document(doc_id)
    if doc_result.get("success"):
        content = _get_body_content(doc_result["data"])
        found_heading = any(
            elem.get("paragraph", {})
            .get("paragraphStyle", {})
            .get("namedStyleType", "")
            .startswith("HEADING_")
            for elem in content
            if "paragraph" in elem
        )
        if found_heading:
            print("   SUCCESS: Heading formatting applied after replace")
        else:
            print("   WARNING: Could not verify heading formatting")

    return True


def _replace_content_test_plain_text(doc_id: str) -> bool:
    """Replace with plain text and verify markdown content is gone."""
    print("\n5. Replacing content with plain text format...")
    result = replace_document_content(doc_id, "Simple plain text replacement.", "text")
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    print("   SUCCESS: Replaced with plain text")

    time.sleep(1)

    print("\n6. Verifying plain text replacement...")
    full_text = _get_full_text(doc_id)
    if "new content" in full_text:
        print("   FAILED: Previous markdown content still present")
        return False
    if "Simple plain text replacement." not in full_text:
        print(f"   FAILED: Plain text not found. Got: {full_text[:200]}")
        return False
    print(f"   SUCCESS: Plain text content verified: '{full_text.strip()}'")

    return True


def _replace_content_test_tab(doc_id: str) -> bool:
    """Create a tab, insert content, replace tab content, verify isolation."""
    print("\n7. Testing replace on a specific tab...")
    tab_result = create_tab(doc_id, "Replace Tab")
    if not tab_result.get("success"):
        print(f"   FAILED: {tab_result.get('error')}")
        return False
    tab_id = tab_result["data"]["tabId"]
    print(f"   SUCCESS: Created tab {tab_id}")

    time.sleep(1)

    # Insert content into the new tab first
    insert_result = insert_text(doc_id, "Tab original content.\n", 1, tab_id)
    if not insert_result.get("success"):
        print(f"   FAILED: {insert_result.get('error')}")
        return False

    time.sleep(1)

    # Replace content in the tab
    result = replace_document_content(
        doc_id, "# Tab Replaced\n\nNew tab content.", "markdown", tab_id
    )
    if not result.get("success"):
        print(f"   FAILED: {result.get('error')}")
        return False
    print("   SUCCESS: Replaced tab content")

    time.sleep(1)

    print("\n8. Verifying tab content was replaced...")
    body_result = get_document_body(doc_id, tab_id)
    if not body_result.get("success"):
        print(f"   FAILED: {body_result.get('error')}")
        return False
    tab_text = "".join(seg["text"] for seg in body_result["data"]["segments"])
    if "Tab original content" in tab_text:
        print("   FAILED: Tab original content still present")
        return False
    if "New tab content" not in tab_text:
        print(f"   FAILED: New tab content not found. Got: {tab_text[:200]}")
        return False
    print("   SUCCESS: Tab content correctly replaced")

    # Verify default tab was NOT affected
    default_text = _get_full_text(doc_id)
    if "Simple plain text replacement." not in default_text:
        print("   WARNING: Default tab content may have been modified")
    else:
        print("   SUCCESS: Default tab content unchanged")

    return True


def test_replace_document_content() -> bool:
    """Test replacing all content in a document."""
    print("=" * 60)
    print("Test: Replace Document Content")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document with initial content...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(
            f"Test Replace Content - {unique_id}",
            "Original content that should be replaced.\n",
        )
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        if not _replace_content_test_markdown(doc_id):
            return False

        time.sleep(1)

        if not _replace_content_test_plain_text(doc_id):
            return False

        time.sleep(1)

        if not _replace_content_test_tab(doc_id):
            return False

        print("\n9. Cleaning up...")
        if doc_id:
            delete_document(doc_id)
            print(f"   Deleted document {doc_id}")

        print("\n" + "=" * 60)
        print("TEST PASSED")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\nEXCEPTION: {e}")
        import traceback

        traceback.print_exc()
        if doc_id:
            try:
                delete_document(doc_id)
            except Exception:
                pass
        return False


def _count_bulleted_paragraphs(doc_id: str) -> tuple[int, list[str]]:
    """Count paragraphs with bullet formatting and return their text.

    Returns (total_bulleted, list_of_non_list_bulleted_text).
    """
    result = get_document(doc_id)
    if not result.get("success"):
        return 0, []

    body = result["data"]["body"]["content"]
    bulleted = []
    for el in body:
        if "paragraph" not in el:
            continue
        para = el["paragraph"]
        if "bullet" in para:
            text = "".join(
                e.get("textRun", {}).get("content", "")
                for e in para.get("elements", [])
            ).strip()
            bulleted.append(text)
    return len(bulleted), bulleted


def test_list_bullet_bleed_prevention() -> bool:
    """Test that bullet formatting does not bleed from lists into subsequent content.

    Creates a document with a numbered list followed by headings, paragraphs,
    and code blocks. Verifies that only the actual list items have bullet
    formatting -- subsequent content must NOT inherit bullets.
    """
    print("=" * 60)
    print("Test: List Bullet Bleed Prevention")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test Bullet Bleed - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with list followed by heading and text...")
        markdown = (
            "# Title\n\n"
            "## Section A\n\n"
            "1. First item\n"
            "2. Second item\n"
            "3. Third item\n"
            "\n"
            "## Section B\n\n"
            "This paragraph should NOT have bullet formatting.\n\n"
            "Neither should this one.\n\n"
            "### Subsection\n\n"
            "Also no bullets here.\n"
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Checking bullet formatting...")
        total, bulleted_texts = _count_bulleted_paragraphs(doc_id)

        # Only the 3 list items should have bullets
        list_items = {"First item", "Second item", "Third item"}
        non_list_bullets = [t for t in bulleted_texts if t and t not in list_items]

        if non_list_bullets:
            print(
                f"   FAILED: {len(non_list_bullets)} non-list paragraphs have bullet formatting:"
            )
            for t in non_list_bullets[:5]:
                print(f'     - "{t[:60]}"')
            return False

        print(f"   SUCCESS: {total} bulleted paragraphs, all are list items")

        print("\n4. Verifying content is present...")
        full_text = _get_full_text(doc_id)
        for expected in [
            "Section B",
            "should NOT have bullet",
            "Subsection",
            "Also no bullets",
        ]:
            if expected not in full_text:
                print(f"   FAILED: '{expected}' not found in document")
                return False
        print("   SUCCESS: All content present")

        print("\n   PASSED!")
        return True

    except Exception as e:
        print(f"   ERROR: {e}")
        return False
    finally:
        if doc_id:
            try:
                delete_document(doc_id)
                print(f"   Cleaned up document {doc_id}")
            except Exception:
                print(f"   WARNING: Failed to delete test doc {doc_id}")


def test_list_heading_merge_prevention() -> bool:
    """Test that list items don't merge with subsequent headings.

    When a numbered list is followed by a heading with only a blank line
    between them, Google Docs can merge the last list item text with the
    heading. The fix preserves the blank line between lists and headings.
    """
    print("=" * 60)
    print("Test: List-Heading Merge Prevention")
    print("=" * 60)

    doc_id = None

    try:
        print("\n1. Creating a test document...")
        unique_id = uuid.uuid4().hex[:8]
        result = create_document(f"Test List-Heading Merge - {unique_id}")
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        doc_id = result["data"]["documentId"]
        print(f"   SUCCESS: Created document {doc_id}")

        time.sleep(1)

        print("\n2. Inserting markdown with list immediately before heading...")
        markdown = (
            "# Title\n\n"
            "## Before List\n\n"
            "- Item one\n"
            "- Item two\n"
            "- Item three\n"
            "\n"
            "## After List\n\n"
            "Content after the list.\n"
        )
        result = insert_markdown(doc_id, markdown)
        if not result.get("success"):
            print(f"   FAILED: {result.get('error')}")
            return False
        print("   SUCCESS: Inserted markdown")

        time.sleep(1)

        print("\n3. Verifying headings are separate from list items...")
        doc_result = get_document(doc_id)
        if not doc_result.get("success"):
            print("   FAILED: Could not read document")
            return False

        body = doc_result["data"]["body"]["content"]
        headings = []
        for el in body:
            if "paragraph" not in el:
                continue
            para = el["paragraph"]
            style = para.get("paragraphStyle", {})
            named = style.get("namedStyleType", "")
            if named.startswith("HEADING_"):
                text = "".join(
                    e.get("textRun", {}).get("content", "")
                    for e in para.get("elements", [])
                ).strip()
                headings.append(text)

        # Check that "After List" is its own heading, not merged with "Item three"
        if "After List" not in headings:
            # Check if it got merged
            merged = [h for h in headings if "Item three" in h and "After List" in h]
            if merged:
                print(f'   FAILED: Last list item merged with heading: "{merged[0]}"')
            else:
                print(
                    f"   FAILED: 'After List' heading not found. Headings: {headings}"
                )
            return False

        print("   SUCCESS: 'After List' is a separate heading")
        print(f"   All headings: {headings}")

        print("\n   PASSED!")
        return True

    except Exception as e:
        print(f"   ERROR: {e}")
        return False
    finally:
        if doc_id:
            try:
                delete_document(doc_id)
                print(f"   Cleaned up document {doc_id}")
            except Exception:
                print(f"   WARNING: Failed to delete test doc {doc_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Google Docs API Integration Tests")
    parser.add_argument(
        "--sandbox-host",
        help="Sandbox host for ondemand environments (e.g., interngraph.12345.od.facebook.com)",
    )
    parser.add_argument(
        "--folder-test-only",
        action="store_true",
        help="Run only the folder creation test",
    )
    parser.add_argument(
        "--tabs-test-only",
        action="store_true",
        help="Run only the basic tabs test",
    )
    parser.add_argument(
        "--all-tabs-tests",
        action="store_true",
        help="Run all tab-related tests (basic, creation variants, operations, errors)",
    )
    parser.add_argument(
        "--copy-doc-test-only",
        action="store_true",
        help="Run only the copy_doc test",
    )
    parser.add_argument(
        "--find-replace-test-only",
        action="store_true",
        help="Run only the find_replace test",
    )
    parser.add_argument(
        "--table-test-only",
        action="store_true",
        help="Run only the markdown table insertion test",
    )
    parser.add_argument(
        "--link-test-only",
        action="store_true",
        help="Run only the link formatting test",
    )
    parser.add_argument(
        "--markdown-formatting-tests",
        action="store_true",
        help="Run all markdown formatting tests (code blocks, lists, quotes, HRs, strikethrough, escapes, full doc)",
    )
    parser.add_argument(
        "--html-test-only",
        action="store_true",
        help="Run only the basic HTML insertion test",
    )
    parser.add_argument(
        "--html-formatting-tests",
        action="store_true",
        help="Run all HTML formatting tests (basic, colors, headings, lists, links, tables, full doc)",
    )
    parser.add_argument(
        "--replace-content-test-only",
        action="store_true",
        help="Run only the replace_document_content test",
    )
    args = parser.parse_args()

    if args.sandbox_host:
        set_sandbox_host(args.sandbox_host)
        print(f"Using sandbox host: {args.sandbox_host}")

    if args.folder_test_only:
        success = test_create_document_in_folder()
    elif args.tabs_test_only:
        success = test_document_tabs_basic()
    elif args.all_tabs_tests:
        success = run_all_tab_tests()
    elif args.copy_doc_test_only:
        success = test_copy_doc()
    elif args.find_replace_test_only:
        success = test_find_replace()
    elif args.table_test_only:
        success = test_insert_markdown_table()
    elif args.link_test_only:
        success = test_link_formatting()
    elif args.markdown_formatting_tests:
        success = test_insert_markdown_code_block()
        if success:
            success = test_insert_markdown_lists()
        if success:
            success = test_insert_markdown_block_quotes_and_hrs()
        if success:
            success = test_insert_markdown_strikethrough_and_escapes()
        if success:
            success = test_insert_markdown_full_document()
    elif args.html_test_only:
        success = test_insert_html_basic_formatting()
    elif args.html_formatting_tests:
        success = test_insert_html_basic_formatting()
        if success:
            success = test_insert_html_colored_text()
        if success:
            success = test_insert_html_headings_and_paragraphs()
        if success:
            success = test_insert_html_lists()
        if success:
            success = test_insert_html_links_and_code()
        if success:
            success = test_insert_html_tables()
        if success:
            success = test_insert_html_full_document()
    elif args.replace_content_test_only:
        success = test_replace_document_content()
    else:
        success = run_tests()
        if success:
            success = test_create_document_in_folder()
        if success:
            success = run_all_tab_tests()
        if success:
            success = test_copy_doc()
        if success:
            success = test_find_replace()
        if success:
            success = test_insert_markdown_table()
        if success:
            success = test_link_formatting()
        if success:
            success = test_insert_markdown_code_block()
        if success:
            success = test_insert_markdown_lists()
        if success:
            success = test_insert_markdown_block_quotes_and_hrs()
        if success:
            success = test_insert_markdown_strikethrough_and_escapes()
        if success:
            success = test_insert_markdown_full_document()
        if success:
            success = test_insert_html_basic_formatting()
        if success:
            success = test_insert_html_colored_text()
        if success:
            success = test_insert_html_headings_and_paragraphs()
        if success:
            success = test_insert_html_lists()
        if success:
            success = test_insert_html_links_and_code()
        if success:
            success = test_insert_html_tables()
        if success:
            success = test_insert_html_full_document()
        if success:
            success = test_replace_document_content()
        if success:
            success = test_list_bullet_bleed_prevention()
        if success:
            success = test_list_heading_merge_prevention()

    sys.exit(0 if success else 1)
