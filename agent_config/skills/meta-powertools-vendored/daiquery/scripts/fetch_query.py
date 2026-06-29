# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

"""
Fetch DaiQuery query details from URLs.

This script provides a CLI interface to fetch query metadata and SQL content
from DaiQuery URLs or fburl short links. For executing queries, use the
presto-query skill instead.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import parse_qs, urlparse

from daiquery.daiquerycli.daiqueryapi import DaiqueryApi
from libfb.py.fburl import resolve_fburl


def find_fbsource_root() -> str:
    """
    Find the fbsource repository root by walking up from cwd looking for .hg.

    Returns:
        Path to the fbsource root directory.
    """
    cwd = os.getcwd()
    candidate = cwd
    while candidate != "/" and not os.path.exists(os.path.join(candidate, ".hg")):
        candidate = os.path.dirname(candidate)

    if candidate != "/":
        return candidate

    # Fallback to common paths
    home_fbsource = os.path.expanduser("~/fbsource")
    if os.path.exists(home_fbsource):
        return home_fbsource
    return "/home/sws/fbsource"


def resolve_url(url: str) -> str:
    """
    Resolve fburl short links to their final destination.

    Args:
        url: URL to resolve (can be fburl or direct URL)

    Returns:
        Resolved URL
    """
    if "fburl.com" in url or "/u/" in url:
        try:
            resolved = resolve_fburl(url)
            if resolved:
                return resolved
        except Exception:
            pass
    return url


def fetch_query_via_cli(query_id: int) -> dict[str, Any]:
    """
    Fetch query/notebook SQL using the daiquerycli CLI tool.

    This is used as a fallback when the API can't access a query,
    particularly for notebooks.

    Args:
        query_id: The query or notebook ID to fetch

    Returns:
        Dictionary with query details including SQL
    """
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".sql", delete=False
    ) as temp_file:
        temp_path = temp_file.name

    try:
        fbsource_root = find_fbsource_root()

        result = subprocess.run(
            [
                "buck2",
                "run",
                "fbcode//daiquery/daiquerycli:daiquerycli",
                "--",
                "fetch",
                str(query_id),
                "-o",
                temp_path,
                "--force",
            ],
            cwd=fbsource_root,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            return {
                "success": False,
                "error": f"CLI fetch failed: {result.stderr or result.stdout}",
                "query_id": query_id,
            }

        with open(temp_path) as f:
            sql_content = f.read()

        if not sql_content.strip():
            return {
                "success": False,
                "error": "CLI returned empty SQL content",
                "query_id": query_id,
            }

        return {
            "success": True,
            "query_id": query_id,
            "sql": sql_content,
            "fetched_via": "cli",
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "CLI fetch timed out after 60 seconds",
            "query_id": query_id,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"CLI fetch error: {str(e)}",
            "query_id": query_id,
        }
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def extract_query_id(url: str) -> int | None:
    """
    Extract query ID from various DaiQuery URL formats.

    Supported formats:
    - https://www.internalfb.com/intern/daiquery/workspace/?queryid=123
    - https://www.internalfb.com/intern/daiquery/workspace/456/123/
    - https://www.internalfb.com/intern/daiquery/query/123/
    - https://www.internalfb.com/daiquery/?queryid=123

    Args:
        url: DaiQuery URL

    Returns:
        Query ID if found, None otherwise
    """
    parsed = urlparse(url)

    query_params = parse_qs(parsed.query)
    if "queryid" in query_params:
        try:
            return int(query_params["queryid"][0])
        except (ValueError, IndexError):
            pass

    path = parsed.path

    # Pattern: /workspace/{workspace_id}/{query_id}/
    workspace_match = re.search(r"/workspace/\d+/(\d+)/?", path)
    if workspace_match:
        return int(workspace_match.group(1))

    # Pattern: /query/{query_id}/
    query_match = re.search(r"/query/(\d+)/?", path)
    if query_match:
        return int(query_match.group(1))

    return None


def _extract_report_details(
    report: Any,
    query_id: int,
) -> dict[str, Any]:
    """
    Extract metadata fields from a DaiqueryApi report object.

    Args:
        report: Report object from DaiqueryApi.reports_by_id
        query_id: The query ID

    Returns:
        Dictionary with extracted fields (data_source, namespace, workspace_id, url)
    """
    details: dict[str, Any] = {}

    if hasattr(report, "sql") and report.sql:
        details["sql"] = report.sql

    if hasattr(report, "_version"):
        version = report._version
        if hasattr(version, "type") and version.type:
            details["data_source"] = version.type

    if hasattr(report, "_config"):
        config = report._config
        if hasattr(config, "namespace_name") and config.namespace_name:
            details["namespace"] = config.namespace_name

    if hasattr(report, "_query"):
        query = report._query
        if hasattr(query, "container_id") and query.container_id:
            details["workspace_id"] = query.container_id
            details["url"] = (
                f"https://www.internalfb.com/intern/daiquery/workspace/"
                f"{query.container_id}/{query_id}/"
            )

    return details


def _build_cli_fallback_result(
    cli_result: dict[str, Any],
    query_id: int,
    url: str,
    resolved_url: str,
) -> dict[str, Any]:
    """
    Build a result dict from a successful CLI fallback fetch.

    Args:
        cli_result: Result from fetch_query_via_cli
        query_id: The query ID
        url: Original URL
        resolved_url: Resolved URL

    Returns:
        Result dictionary
    """
    return {
        "success": True,
        "query_id": query_id,
        "name": f"Query {query_id}",
        "sql": cli_result["sql"],
        "original_url": url,
        "resolved_url": resolved_url,
        "fetched_via": "cli",
        "data_source": "unknown",
        "namespace": "unknown",
    }


def get_query_from_url(url: str) -> dict[str, Any]:
    """
    Fetch query details from a DaiQuery URL.

    Args:
        url: DaiQuery URL or fburl

    Returns:
        Dictionary with query details
    """
    resolved_url = resolve_url(url)

    query_id = extract_query_id(resolved_url)
    if query_id is None:
        return {
            "success": False,
            "error": f"Could not extract query ID from URL: {resolved_url}",
            "original_url": url,
            "resolved_url": resolved_url,
        }

    try:
        with DaiqueryApi() as api:
            reports = api.reports_by_id([query_id])

            if not reports or query_id not in reports:
                cli_result = fetch_query_via_cli(query_id)
                if cli_result["success"]:
                    return _build_cli_fallback_result(
                        cli_result, query_id, url, resolved_url
                    )

                return {
                    "success": False,
                    "error": (
                        f"Query {query_id} not found or not accessible. "
                        "The query may have been deleted or you may not "
                        "have permission to access it."
                    ),
                    "query_id": query_id,
                    "original_url": url,
                    "resolved_url": resolved_url,
                    "hint": "Try opening the URL in your browser to verify "
                    "the query exists.",
                }

            report = reports[query_id]
            result: dict[str, Any] = {
                "success": True,
                "query_id": query_id,
                "name": report.name,
                "original_url": url,
                "resolved_url": resolved_url,
            }
            result.update(_extract_report_details(report, query_id))
            return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "query_id": query_id,
            "original_url": url,
            "resolved_url": resolved_url,
        }


# Regex patterns for notebook detection that avoid matching inside SQL comments.
# We match only when these appear as actual Python statements, not inside
# single-line (--) or block (/* */) SQL comments.
_NOTEBOOK_IMPORT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(?!.*--).*\bfrom\s+daiquery\.daiquerycli\b", re.MULTILINE),
    re.compile(r"^(?!.*--).*\bfrom\s+daiquery\s+import\b", re.MULTILINE),
    re.compile(r"^(?!.*--).*\bimport\s+daiquery\b", re.MULTILINE),
]

# Report( and Macro( must appear as function calls — preceded by whitespace or
# start-of-line, not inside a SQL comment line.
_NOTEBOOK_CALL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^(?!.*--).*(?:^|[\s=])Report\s*\(", re.MULTILINE),
    re.compile(r"^(?!.*--).*(?:^|[\s=])Macro\s*\(", re.MULTILINE),
]


def is_notebook_query(sql: str) -> bool:
    """
    Detect if the query content is a DaiQuery notebook (Python code).

    Uses regex patterns that avoid matching inside SQL comments.

    Args:
        sql: The SQL/content from the query

    Returns:
        True if this appears to be a notebook, False otherwise
    """
    if not sql:
        return False

    sql_stripped = sql.strip()

    if sql_stripped.startswith("#!/usr/bin/env python"):
        return True

    for pattern in _NOTEBOOK_IMPORT_PATTERNS:
        if pattern.search(sql_stripped):
            return True

    for pattern in _NOTEBOOK_CALL_PATTERNS:
        if pattern.search(sql_stripped):
            return True

    return False


def _format_notebook_output(result: dict[str, Any]) -> None:
    """Print notebook detection output and exit."""
    print("=" * 60)
    print("DaiQuery Notebook Detected")
    print("=" * 60)
    print()
    print("This is a DaiQuery notebook (Python code), which cannot be")
    print("executed programmatically via Claude yet.")
    print()
    if result.get("name"):
        print(f"Notebook Name: {result['name']}")
    if result.get("query_id"):
        print(f"Query ID: {result['query_id']}")
    print()
    print("Please open the link below in your browser to run it manually:")
    print()
    url_to_show = result.get("url") or result.get("original_url")
    if url_to_show:
        print(f"  {url_to_show}")
    print()
    sys.exit(0)


def _format_error_output(result: dict[str, Any]) -> None:
    """Print error output and exit."""
    print(f"Error: {result['error']}", file=sys.stderr)
    if "resolved_url" in result and result["resolved_url"] != result.get(
        "original_url"
    ):
        print(
            f"Resolved URL: {result['resolved_url']}",
            file=sys.stderr,
        )
    if "hint" in result:
        print(f"\nHint: {result['hint']}", file=sys.stderr)
    sys.exit(1)


def _format_success_output(result: dict[str, Any]) -> None:
    """Print successful query details."""
    print("Query Details")
    print("=" * 60)
    print(f"Query ID: {result.get('query_id', 'N/A')}")
    print(f"Name: {result.get('name', 'N/A')}")
    if "workspace_id" in result:
        print(f"Workspace ID: {result['workspace_id']}")
    if "data_source" in result:
        print(f"Data Source: {result['data_source']}")
    if "namespace" in result:
        print(f"Namespace: {result['namespace']}")
    if "url" in result:
        print(f"URL: {result['url']}")
    if "sql" in result:
        if is_notebook_query(result["sql"]):
            print("\nNote: This is a DaiQuery notebook (Python code).")
            print("Notebooks can be managed via the notebook CLI commands.")
        else:
            print("\nSQL:")
            print("-" * 40)
            print(result["sql"])


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch DaiQuery query details from URLs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get query details from a DaiQuery URL
  buck2 run :fetch_query -- --url "https://www.internalfb.com/intern/daiquery/workspace/?queryid=123"

  # Get query details from an fburl
  buck2 run :fetch_query -- --url "https://fburl.com/daiquery/abc123"

  # JSON output
  buck2 run :fetch_query -- --url "https://..." --format json
        """,
    )

    parser.add_argument(
        "--url",
        "-u",
        required=True,
        help="DaiQuery URL or fburl to fetch query from",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args()
    result = get_query_from_url(args.url)

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
        return

    if not result["success"]:
        if result.get("is_notebook"):
            _format_notebook_output(result)
        else:
            _format_error_output(result)
    else:
        _format_success_output(result)


if __name__ == "__main__":
    main()
