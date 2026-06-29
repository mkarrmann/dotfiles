#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
# pyre-strict

"""
Formatter for test plan finder skill.

This module provides formatting utilities for displaying test plans
retrieved from historical diffs.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict

logger: logging.Logger = logging.getLogger(__name__)


class TestPlanFormatter:
    """Formats test plans for display."""

    def __init__(self, max_examples: int = 5, min_words: int = 15) -> None:
        self.max_examples = max_examples
        self.min_words = min_words

    def format_test_plans(self, test_plans: list[dict[str, str | list[str]]]) -> str:
        """Format a collection of test plans into readable output."""
        if not test_plans:
            return "No test plans found."

        # Filter by minimum word count
        filtered_plans = filter_by_word_count(test_plans, self.min_words)

        if not filtered_plans:
            return "No test plans found."

        formatted = []
        for plan in filtered_plans[: self.max_examples]:
            formatted.append(self._format_single_plan(plan))

        return "\n\n---\n\n".join(formatted)

    def _format_single_plan(self, plan: dict[str, str | list[str]]) -> str:
        """Format a single test plan entry."""
        lines = []

        # Add diff information
        diff_num = plan.get("diff_number", "Unknown")
        author = plan.get("author", "Unknown")
        lines.append(f"**Diff {diff_num}** by {author}")

        # Add file changes
        if "files" in plan:
            files = plan["files"]
            if isinstance(files, list):
                lines.append(f"Files: {', '.join(files)}")

        # Add test plan content
        content = plan.get("test_plan", "")
        if content:
            lines.append("\nTest Plan:")
            lines.append(str(content))

        return "\n".join(lines)

    def group_by_pattern(
        self, test_plans: list[dict[str, str | list[str]]]
    ) -> dict[str, list[dict[str, str | list[str]]]]:
        """Group test plans by common patterns."""
        patterns: dict[str, list[dict[str, str | list[str]]]] = defaultdict(list)

        for plan in test_plans:
            pattern_key = self._extract_pattern(plan)
            patterns[pattern_key].append(plan)

        return dict(patterns)

    def _extract_pattern(self, plan: dict[str, str | list[str]]) -> str:
        """Extract a pattern identifier from a test plan."""
        content = str(plan.get("test_plan", "")).lower()

        if "unit test" in content:
            return "unit_tests"
        elif "manual" in content:
            return "manual_testing"
        elif "integration" in content:
            return "integration_tests"
        else:
            return "other"

    def summarize_patterns(
        self, grouped_plans: dict[str, list[dict[str, str | list[str]]]]
    ) -> str:
        """Summarize patterns found in test plans."""
        summary = []

        for pattern, plans in grouped_plans.items():
            summary.append(f"{pattern}: {len(plans)} examples")

        return ", ".join(summary)


def filter_by_word_count(
    test_plans: list[dict[str, str | list[str]]], min_words: int = 15
) -> list[dict[str, str | list[str]]]:
    """Filter test plans by minimum word count."""
    filtered = []

    for plan in test_plans:
        content = str(plan.get("test_plan", ""))
        word_count = len(content.split())

        if word_count >= min_words:
            filtered.append(plan)

    return filtered


def extract_test_commands(test_plan_text: str) -> list[str]:
    """Extract test commands from test plan text."""
    lines = test_plan_text.split("\n")
    commands = []

    for line in lines:
        line = line.strip()

        # Look for common test command patterns
        if any(
            cmd in line for cmd in ["buck test", "buck2 test", "arc test", "pytest"]
        ):
            commands.append(line)

    return commands


def format_for_commit_message(
    test_plans: list[dict[str, str | list[str]]],
) -> str:
    """Format test plans suitable for a commit message."""
    formatter = TestPlanFormatter()

    # Group by pattern
    grouped = formatter.group_by_pattern(test_plans)

    # Build commit message test plan
    lines = ["Test Plan:"]

    # Add summary
    summary = formatter.summarize_patterns(grouped)
    lines.append(f"Based on {len(test_plans)} similar diffs ({summary}):")
    lines.append("")

    # Extract common test commands
    all_commands: list[str] = []
    for plan in test_plans:
        commands = extract_test_commands(str(plan.get("test_plan", "")))
        all_commands.extend(commands)

    # Deduplicate and add commands
    unique_commands = list(dict.fromkeys(all_commands))
    if unique_commands:
        lines.append("Common test commands:")
        for cmd in unique_commands[:5]:  # Limit to 5 commands
            lines.append(f"  {cmd}")

    return "\n".join(lines)


def main() -> None:
    """Example usage of the formatter."""
    # Example test plans
    example_plans: list[dict[str, str | list[str]]] = [
        {
            "diff_number": "D12345",
            "author": "user1",
            "files": ["file1.py", "file2.py"],
            "test_plan": "Ran buck test //path/to:target\nManual testing of UI changes",
        },
        {
            "diff_number": "D12346",
            "author": "user2",
            "files": ["file3.py"],
            "test_plan": "Unit tests:\nbuck2 test //path/to:tests",
        },
    ]

    formatter = TestPlanFormatter()

    # Format all plans
    formatted = formatter.format_test_plans(example_plans)
    print("Formatted test plans:")
    print(formatted)

    # Group by pattern
    grouped = formatter.group_by_pattern(example_plans)
    print("\nGrouped by pattern:")
    print(json.dumps(grouped, indent=2))

    # Format for commit
    commit_msg = format_for_commit_message(example_plans)
    print("\nCommit message format:")
    print(commit_msg)


if __name__ == "__main__":
    main()
