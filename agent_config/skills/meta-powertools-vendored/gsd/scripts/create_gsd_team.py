#!/usr/bin/env fbpython
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.
# pyre-strict

"""Create a new GSD team."""

import argparse
import json
import sys
from typing import Any

from .gsd_utils import get_current_user_fbid, run_jf_graphql


def create_gsd_team(
    name: str,
    members: list[str],
    parent_team: str | None = None,
    privacy_owner: str | None = None,
) -> dict[str, Any]:
    """Create a new GSD team.

    Args:
        name: Team name
        members: List of member FBIDs
        parent_team: Optional parent team FBID
        privacy_owner: Optional privacy owner FBID

    Returns:
        Dictionary with team_id, team_name, and url
    """
    mutation = """
    mutation createTasksGSDTeam (
        $actor_id: ID!,
        $team_name: String!,
        $team_members: [ID!]!,
        $parent_team: ID,
        $privacy_owner: ID
    ) {
        create_tasks_gsd_team(data: {
            actor_id: $actor_id,
            team_name: $team_name,
            team_members: $team_members,
            parent_team: $parent_team,
            privacy_owner: $privacy_owner
        }) {
            tasks_gsd_team {
                id
                name
            }
        }
    }
    """

    actor_id = get_current_user_fbid()
    variables: dict[str, Any] = {
        "actor_id": actor_id,
        "team_name": name,
        "team_members": members,
    }

    if parent_team:
        variables["parent_team"] = parent_team

    if privacy_owner:
        variables["privacy_owner"] = privacy_owner

    result = run_jf_graphql(mutation, variables)

    team_id = result["create_tasks_gsd_team"]["tasks_gsd_team"]["id"]
    team_name = result["create_tasks_gsd_team"]["tasks_gsd_team"]["name"]

    return {
        "success": True,
        "team_id": team_id,
        "team_name": team_name,
        "url": f"https://www.internalfb.com/gsd/{team_id}/",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new GSD team",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Get your user ID first:
  USER_ID=$(jf graphql --query 'query { me { id } }' | jq -r '.me.id')

  # Then create team:
  %(prog)s --name 'My Test Team' --members $USER_ID
  %(prog)s --name 'Team Project' --members 111111111 222222222 \\
    --parent-team 999999999
""",
    )

    parser.add_argument("--name", required=True, help="Team name")
    parser.add_argument(
        "--members",
        nargs="+",
        required=True,
        help="Member FBIDs (space-separated)",
    )
    parser.add_argument("--parent-team", help="Parent team FBID")
    parser.add_argument("--privacy-owner", help="Privacy owner FBID")

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        result = create_gsd_team(
            name=args.name,
            members=args.members,
            parent_team=args.parent_team,
            privacy_owner=args.privacy_owner,
        )

        print(json.dumps(result, indent=2))

    except RuntimeError as e:
        error_result = {"success": False, "error": str(e)}
        print(json.dumps(error_result, indent=2), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
