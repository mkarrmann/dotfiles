#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Query GSD project information."""

import argparse
import json
import re
import sys
from typing import Any

from .gsd_utils import run_jf_graphql


def query_gsd_data(
    gsd_id: str | None = None,
    project_id: str | None = None,
    url: str | None = None,
    show_structure: bool = False,
) -> dict[str, Any]:
    """Query GSD project information.

    Args:
        gsd_id: GSD team FBID
        project_id: GSD project FBID
        url: GSD URL to parse
        show_structure: Show full structure

    Returns:
        Dictionary with project information
    """
    # Parse URL if provided
    if url:
        match = re.search(r"/gsd/(\d+)(?:/(\d+))?", url)
        if match:
            gsd_id = match.group(1)
            project_id = match.group(2)
        else:
            raise ValueError("Invalid GSD URL format")

    # Validate required parameters
    if not gsd_id or not project_id:
        raise ValueError("Must provide either --url or both --gsd-id and --project-id")

    # Build GraphQL query (uses team_id internally for GraphQL variables)
    query = """
    query ($plannable_id: ID!, $team_id: ID!) {
        xfb_tasks_gsd_plannable(plannable_id: $plannable_id, team_id: $team_id) {
            id
            name
            __typename
            ... on TasksGSDTheme {
                projects(team_id: $team_id) {
                    nodes {
                        id
                        name
                        sections(team_id: $team_id) {
                            nodes {
                                id
                                name
                            }
                        }
                    }
                }
            }
            ... on TasksGSDProject {
                sections(team_id: $team_id) {
                    nodes {
                        id
                        name
                        tasks(team_id: $team_id) {
                            nodes {
                                id
                                task_number
                            }
                        }
                    }
                }
            }
            ... on TasksGSDSection {
                project {
                    id
                    name
                }
            }
        }
    }
    """

    result = run_jf_graphql(query, {"plannable_id": project_id, "team_id": gsd_id})

    # Build output
    if show_structure:
        return {
            "success": True,
            "team_id": gsd_id,
            "project_id": project_id,
            "structure": result["xfb_tasks_gsd_plannable"],
        }
    else:
        return {
            "success": True,
            "team_id": gsd_id,
            "project_id": project_id,
            "project_name": result["xfb_tasks_gsd_plannable"]["name"],
            "type": result["xfb_tasks_gsd_plannable"]["__typename"],
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query GSD project information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --url 'https://www.internalfb.com/gsd/729330146869874/4251442578437015/'
  %(prog)s --gsd-id 729330146869874 --project-id 4251442578437015
  %(prog)s --gsd-id 729330146869874 --project-id 4251442578437015 --show-structure
""",
    )

    parser.add_argument("--url", help="GSD URL")
    parser.add_argument("--gsd-id", help="GSD team FBID")
    parser.add_argument("--project-id", help="GSD project FBID")
    parser.add_argument(
        "--show-structure",
        action="store_true",
        help="Show full structure",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = query_gsd_data(
            gsd_id=args.gsd_id,
            project_id=args.project_id,
            url=args.url,
            show_structure=args.show_structure,
        )

        print(json.dumps(result, indent=2))

        if not result.get("success", True):
            sys.exit(1)

    except (RuntimeError, ValueError) as e:
        error_result = {"success": False, "error": str(e)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
