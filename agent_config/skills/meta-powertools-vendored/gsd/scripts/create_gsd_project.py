#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Create a new GSD project."""

import argparse
import json
import sys
from typing import Any

from .gsd_utils import get_current_user_fbid, run_jf_graphql


def create_gsd_project(
    gsd_id: str,
    name: str,
    additional_members: list[str] | None = None,
    view: str = "LIST",
) -> dict[str, Any]:
    """Create a new GSD project.

    Args:
        gsd_id: GSD team FBID
        name: Project name
        additional_members: Optional list of additional member FBIDs
        view: Default view (LIST or BOARD)

    Returns:
        Dictionary with project_id, project_name, team_id, and url
    """
    # GraphQL enum values must be inlined in the query string, not passed as variables
    actor_id: str = get_current_user_fbid()

    if additional_members:
        mutation = f"""
        mutation useTasksGSDCreateProjectMutation (
            $actor_id: ID!,
            $project_name: String!,
            $team_id: ID!,
            $additional_members: [ID!]
        ) {{
            create_tasks_gsd_project(data: {{
                actor_id: $actor_id,
                name: $project_name,
                team: $team_id,
                additional_members: $additional_members,
                default_view: {view}
            }}) {{
                tasks_gsd_project {{
                    id
                    name
                }}
            }}
        }}
        """

        variables: dict[str, Any] = {
            "actor_id": actor_id,
            "project_name": name,
            "team_id": gsd_id,
            "additional_members": additional_members,
        }
    else:
        mutation = f"""
        mutation useTasksGSDCreateProjectMutation (
            $actor_id: ID!,
            $project_name: String!,
            $team_id: ID!
        ) {{
            create_tasks_gsd_project(data: {{
                actor_id: $actor_id,
                name: $project_name,
                team: $team_id,
                default_view: {view}
            }}) {{
                tasks_gsd_project {{
                    id
                    name
                }}
            }}
        }}
        """

        variables = {
            "actor_id": actor_id,
            "project_name": name,
            "team_id": gsd_id,
        }

    result = run_jf_graphql(mutation, variables)

    project_id = result["create_tasks_gsd_project"]["tasks_gsd_project"]["id"]
    project_name = result["create_tasks_gsd_project"]["tasks_gsd_project"]["name"]

    return {
        "success": True,
        "project_id": project_id,
        "project_name": project_name,
        "team_id": gsd_id,
        "url": f"https://www.internalfb.com/gsd/{gsd_id}/{project_id}/",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new GSD project",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --gsd-id 729330146869874 --name 'Q2 2025 Goals'
  %(prog)s --gsd-id 729330146869874 --name 'Team Project' \\
    --additional-members 111111111 222222222 --view BOARD
""",
    )

    parser.add_argument("--gsd-id", required=True, help="GSD team FBID")
    parser.add_argument("--name", required=True, help="Project name")
    parser.add_argument(
        "--additional-members",
        nargs="+",
        help="Additional member FBIDs (space-separated)",
    )
    parser.add_argument(
        "--view",
        choices=["LIST", "BOARD"],
        default="LIST",
        help="Default view (default: LIST)",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = create_gsd_project(
            gsd_id=args.gsd_id,
            name=args.name,
            additional_members=args.additional_members,
            view=args.view,
        )

        print(json.dumps(result, indent=2))

    except RuntimeError as e:
        error_result = {"success": False, "error": str(e)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
