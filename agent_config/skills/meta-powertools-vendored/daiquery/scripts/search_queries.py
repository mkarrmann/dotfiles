# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

# pyre-strict

"""
Search for saved queries in DaiQuery by name or content.

This script provides a CLI interface to search for saved queries across
all accessible workspaces in DaiQuery.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from daiquery.daiquerycli.daiqueryapi import DaiqueryApi

logger: logging.Logger = logging.getLogger(__name__)


def _parse_date(date_str: str) -> datetime:
    """Parse a date string in YYYY-MM-DD format to a timezone-aware datetime."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _format_timestamp(ts: int) -> str:
    """Format a unix timestamp as a human-readable UTC datetime string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def search_queries(
    search_term: str,
    limit: int = 10,
    workspace_id: int | None = None,
    updated_after: str | None = None,
    updated_before: str | None = None,
) -> dict[str, Any]:
    """
    Search for saved queries by name or SQL content, with optional time filtering.

    Args:
        search_term: Term to search for in query names or SQL content
        limit: Maximum number of results to return
        workspace_id: Optional specific workspace to search in
        updated_after: Optional date string (YYYY-MM-DD) to filter queries
            modified on or after this date
        updated_before: Optional date string (YYYY-MM-DD) to filter queries
            modified on or before this date (end of day)

    Returns:
        Dictionary with search results
    """
    try:
        # Parse date filters
        after_ts: int | None = None
        before_ts: int | None = None
        if updated_after is not None:
            after_ts = int(_parse_date(updated_after).timestamp())
        if updated_before is not None:
            # Use end of day (23:59:59) for the "before" date
            before_dt = _parse_date(updated_before).replace(
                hour=23, minute=59, second=59
            )
            before_ts = int(before_dt.timestamp())

        with DaiqueryApi() as api:
            all_matching_queries: list[dict[str, Any]] = []

            # Get workspaces to search
            if workspace_id is not None:
                # Search specific workspace - get workspace info
                workspaces = [
                    ws for ws in api.my_workspaces() if ws.workspace_id == workspace_id
                ]
                if not workspaces:
                    return {
                        "success": False,
                        "error": f"Workspace {workspace_id} not found or not accessible.",
                    }
            else:
                workspaces = api.my_workspaces()

            # Search through each workspace
            for workspace in workspaces:
                reports = api.reports_by_workspace_id([workspace.workspace_id])
                for report in reports.values():
                    # Apply time range filter if specified
                    modify_time: int = getattr(report, "modify_time", 0)
                    time_filter_active = after_ts is not None or before_ts is not None
                    if time_filter_active and not modify_time:
                        logger.warning(
                            "Query %s (%s) has no modify_time; "
                            "skipping time range filter for this entry.",
                            report.query_id,
                            report.name,
                        )
                    if modify_time:
                        if after_ts is not None and modify_time < after_ts:
                            continue
                        if before_ts is not None and modify_time > before_ts:
                            continue

                    # Check if search term matches name or SQL content
                    name_match = search_term.lower() in report.name.lower()
                    sql_match = False
                    sql_content = ""

                    if hasattr(report, "sql") and report.sql:
                        sql_content = report.sql
                        sql_match = search_term.lower() in report.sql.lower()

                    if name_match or sql_match:
                        query_url = (
                            f"https://www.internalfb.com/intern/daiquery/workspace/"
                            f"{workspace.workspace_id}/{report.query_id}/"
                        )
                        query_entry: dict[str, Any] = {
                            "query_id": report.query_id,
                            "name": report.name,
                            "workspace_id": workspace.workspace_id,
                            "workspace_name": workspace.name,
                            "url": query_url,
                            "sql_preview": (
                                sql_content[:200] + "..."
                                if len(sql_content) > 200
                                else sql_content
                            ),
                            "match_type": "name" if name_match else "sql_content",
                        }
                        if modify_time:
                            query_entry["modify_time"] = modify_time
                            query_entry["modify_time_str"] = _format_timestamp(
                                modify_time
                            )
                        create_time: int = getattr(report, "create_time", 0)
                        if create_time:
                            query_entry["create_time"] = create_time
                            query_entry["create_time_str"] = _format_timestamp(
                                create_time
                            )
                        all_matching_queries.append(query_entry)

            # Sort by modify_time descending when time filtering is active
            if after_ts is not None or before_ts is not None:
                all_matching_queries.sort(
                    key=lambda q: q.get("modify_time", 0), reverse=True
                )

            # Limit results
            limited_results = all_matching_queries[:limit]

            return {
                "success": True,
                "search_term": search_term,
                "total_found": len(all_matching_queries),
                "results_returned": len(limited_results),
                "queries": limited_results,
            }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def list_workspaces() -> dict[str, Any]:
    """
    List all accessible workspaces.

    Returns:
        Dictionary with workspace information
    """
    try:
        with DaiqueryApi() as api:
            workspaces = api.my_workspaces()

            workspace_list = [
                {
                    "workspace_id": ws.workspace_id,
                    "name": ws.name,
                    "url": f"https://www.internalfb.com/intern/daiquery/workspace/{ws.workspace_id}/",
                }
                for ws in workspaces
            ]

            return {
                "success": True,
                "total_workspaces": len(workspace_list),
                "workspaces": workspace_list,
            }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def get_query_details(query_id: int) -> dict[str, Any]:
    """
    Get detailed information about a specific query.

    Args:
        query_id: ID of the query to retrieve

    Returns:
        Dictionary with query details
    """
    try:
        with DaiqueryApi() as api:
            reports = api.reports_by_id([query_id])

            if not reports:
                return {
                    "success": False,
                    "error": f"Query {query_id} not found.",
                }

            report = reports[query_id]

            result: dict[str, Any] = {
                "success": True,
                "query_id": query_id,
                "name": report.name,
            }

            if hasattr(report, "sql") and report.sql:
                result["sql"] = report.sql

            # Get workspace info from the query's container
            if hasattr(report, "_query"):
                query = report._query
                if hasattr(query, "container_id") and query.container_id:
                    result["workspace_id"] = query.container_id
                    result["url"] = (
                        f"https://www.internalfb.com/intern/daiquery/workspace/"
                        f"{query.container_id}/{query_id}/"
                    )

            return result

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }


def _output_list_workspaces(result: dict[str, Any]) -> None:
    """Output workspace list in text format."""
    print("Available Workspaces")
    print("=" * 60)
    for ws in result["workspaces"]:
        print(f"ID: {ws['workspace_id']} | Name: {ws['name']}")
        print(f"   URL: {ws['url']}")
    print(f"\nTotal: {result['total_workspaces']} workspaces")


def _output_query_details(result: dict[str, Any]) -> None:
    """Output query details in text format."""
    print(f"Query Details: {result['query_id']}")
    print("=" * 60)
    print(f"Name: {result['name']}")
    if "workspace_id" in result:
        print(f"Workspace ID: {result['workspace_id']}")
    if "url" in result:
        print(f"URL: {result['url']}")
    if "sql" in result:
        print("\nSQL:")
        print("-" * 40)
        print(result["sql"])


def _output_search_results(result: dict[str, Any]) -> None:
    """Output search results in text format."""
    print(f"Search Results for '{result['search_term']}'")
    print("=" * 60)
    if result["queries"]:
        for i, q in enumerate(result["queries"], 1):
            print(f"\n{i}. {q['name']}")
            print(f"   Query ID: {q['query_id']}")
            print(f"   Workspace: {q['workspace_name']} ({q['workspace_id']})")
            print(f"   Match: {q['match_type']}")
            if "modify_time_str" in q:
                print(f"   Modified: {q['modify_time_str']}")
            if "create_time_str" in q:
                print(f"   Created: {q['create_time_str']}")
            print(f"   URL: {q['url']}")
            if q["sql_preview"]:
                print(f"   SQL: {q['sql_preview'][:80]}...")
        print(
            f"\nShowing {result['results_returned']} of {result['total_found']} matches"
        )
    else:
        print("No queries found matching your search term.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search for saved queries in DaiQuery",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Search for queries by keyword
  buck2 run :search_queries -- --search "revenue"

  # Search within a specific workspace
  buck2 run :search_queries -- --search "users" --workspace-id 12345

  # List all accessible workspaces
  buck2 run :search_queries -- --list-workspaces

  # Get details for a specific query
  buck2 run :search_queries -- --query-id 67890

  # Increase result limit
  buck2 run :search_queries -- --search "metrics" --limit 25

  # Search by time range (queries modified between two dates)
  buck2 run :search_queries -- --search "" --workspace-id 12345 \\
    --updated-after 2026-02-23 --updated-before 2026-02-28

  # Find all queries modified after a date
  buck2 run :search_queries -- --search "" -w 12345 --updated-after 2026-01-01
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--search",
        "-s",
        help="Search term to find in query names or SQL content",
    )
    group.add_argument(
        "--list-workspaces",
        "-l",
        action="store_true",
        help="List all accessible workspaces",
    )
    group.add_argument(
        "--query-id",
        "-q",
        type=int,
        help="Get details for a specific query by ID",
    )

    parser.add_argument(
        "--workspace-id",
        "-w",
        type=int,
        default=None,
        help="Limit search to a specific workspace",
    )
    parser.add_argument(
        "--updated-after",
        default=None,
        help="Filter queries modified on or after this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--updated-before",
        default=None,
        help="Filter queries modified on or before this date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum number of results to return (default: 10)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["json", "text"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args()

    # Execute the appropriate action
    if args.list_workspaces:
        result = list_workspaces()
    elif args.query_id:
        result = get_query_details(args.query_id)
    else:
        result = search_queries(
            search_term=args.search,
            limit=args.limit,
            workspace_id=args.workspace_id,
            updated_after=args.updated_after,
            updated_before=args.updated_before,
        )

    # Output results
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        if not result["success"]:
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)

        if args.list_workspaces:
            _output_list_workspaces(result)
        elif args.query_id:
            _output_query_details(result)
        else:
            _output_search_results(result)


if __name__ == "__main__":
    main()
