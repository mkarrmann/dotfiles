#!/usr/bin/env python3
# pyre-strict
"""
Find duplicate or similar skills across skill directories.

Detects duplicates via:
- Name similarity (prefix matching, edit distance ≤2)
- Keyword overlap (Jaccard similarity >25%)

Usage (from fbcode directory):
    # Scan a local directory
    buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- /path/to/skills

    # Scan all of fbsource (requires devserver with xbgs)
    buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource

    # Filter by confidence level
    buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource --min-confidence high

    # Check one skill against all others
    buck2 run //claude-templates/components/skills/skill-linter/scripts:find_duplicates -- --fbsource --skill my-skill-name
"""

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set


@dataclass
class Skill:
    """Represents a parsed skill."""

    name: str
    description: str
    path: str
    keywords: Set[str]


def parse_frontmatter(content: str) -> Dict[str, str]:
    """Extract YAML frontmatter from skill content."""
    match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return {}

    frontmatter = {}
    for line in match.group(1).split("\n"):
        if ":" in line:
            key, value = line.split(":", 1)
            frontmatter[key.strip()] = value.strip()
    return frontmatter


def extract_keywords(text: str) -> Set[str]:
    """Extract meaningful keywords from text."""
    # Common words to ignore
    stopwords = {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "use",
        "used",
        "using",
        "make",
        "made",
        "making",
        "work",
        "works",
        "working",
        "for",
        "to",
        "from",
        "with",
        "by",
        "at",
        "in",
        "on",
        "of",
        "and",
        "or",
        "not",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "if",
        "when",
        "where",
        "what",
        "which",
        "who",
        "how",
        "why",
        "all",
        "any",
        "some",
        "no",
        "each",
        "every",
        "most",
        "other",
        "new",
        "first",
        "last",
        "long",
        "little",
        "own",
        "same",
        "than",
        "too",
        "very",
        "just",
        "also",
        "more",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "because",
        "as",
        "until",
        "while",
        "user",
        "users",
        "file",
        "files",
        "code",
        "skill",
        "skills",
        "claude",
    }

    words = re.findall(r"\b[a-z]{3,}\b", text.lower())
    return {w for w in words if w not in stopwords}


def edit_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return edit_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def jaccard_similarity(set1: Set[str], set2: Set[str]) -> float:
    """Calculate Jaccard similarity between two sets."""
    if not set1 or not set2:
        return 0.0
    intersection = len(set1 & set2)
    union = len(set1 | set2)
    return intersection / union if union > 0 else 0.0


def is_name_similar(name1: str, name2: str) -> tuple[bool, str]:
    """Check if two skill names are similar."""
    # Exact match
    if name1 == name2:
        return True, "exact"

    # Prefix matching (one is prefix of other)
    if name1.startswith(name2) or name2.startswith(name1):
        return True, "prefix"

    # Edit distance ≤2
    if edit_distance(name1, name2) <= 2:
        return True, "edit_distance"

    return False, ""


def calculate_similarity(skill1: Skill, skill2: Skill) -> tuple[float, list[str]]:
    """Calculate similarity between two skills, return score and reasons."""
    reasons = []
    score = 0.0

    # Name similarity
    name_similar, match_type = is_name_similar(skill1.name, skill2.name)
    if name_similar:
        if match_type == "exact":
            score += 0.8
            reasons.append("exact name match")
        elif match_type == "prefix":
            score += 0.5
            reasons.append("name prefix match")
        elif match_type == "edit_distance":
            score += 0.3
            reasons.append("similar name (edit distance ≤2)")

    # Keyword overlap
    keyword_sim = jaccard_similarity(skill1.keywords, skill2.keywords)
    if keyword_sim > 0.25:
        score += keyword_sim * 0.5
        overlap = skill1.keywords & skill2.keywords
        if overlap:
            reasons.append(
                f"keyword overlap ({keyword_sim:.0%}): {', '.join(sorted(overlap)[:5])}"
            )

    return min(score, 1.0), reasons


def get_confidence(score: float) -> str:
    """Convert similarity score to confidence level."""
    if score >= 0.6:
        return "high"
    elif score >= 0.4:
        return "medium"
    elif score >= 0.25:
        return "low"
    return "none"


def scan_directory(directory: Path) -> list[Skill]:
    """Scan a directory for SKILL.md files."""
    skills = []

    for skill_file in directory.rglob("SKILL.md"):
        try:
            content = skill_file.read_text()
            frontmatter = parse_frontmatter(content)
            name = frontmatter.get("name", skill_file.parent.name)
            description = frontmatter.get("description", "")

            skills.append(
                Skill(
                    name=name,
                    description=description,
                    path=str(skill_file),
                    keywords=extract_keywords(f"{name} {description}"),
                )
            )
        except Exception as e:
            print(f"Warning: Could not parse {skill_file}: {e}", file=sys.stderr)

    return skills


def scan_fbsource() -> list[Skill]:
    """Scan all of fbsource for SKILL.md files using xbgs."""
    try:
        # Use xbgs to find all SKILL.md files
        result = subprocess.run(
            ["xbgs", "-l", "^---", "-g", "SKILL.md"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"Warning: xbgs failed: {result.stderr}", file=sys.stderr)
            return []

        skills = []
        for path in result.stdout.strip().split("\n"):
            if not path:
                continue
            try:
                content = Path(path).read_text()
                frontmatter = parse_frontmatter(content)
                name = frontmatter.get("name", Path(path).parent.name)
                description = frontmatter.get("description", "")

                skills.append(
                    Skill(
                        name=name,
                        description=description,
                        path=path,
                        keywords=extract_keywords(f"{name} {description}"),
                    )
                )
            except Exception as e:
                print(f"Warning: Could not parse {path}: {e}", file=sys.stderr)

        return skills
    except FileNotFoundError:
        print("Error: xbgs not found. Are you on a devserver?", file=sys.stderr)
        sys.exit(1)
    except subprocess.TimeoutExpired:
        print("Error: xbgs timed out", file=sys.stderr)
        sys.exit(1)


def find_duplicates(
    skills: list[Skill],
    min_confidence: str = "low",
    target_skill: str | None = None,
) -> list[tuple[Skill, Skill, float, str, list[str]]]:
    """Find duplicate skill pairs."""
    confidence_levels = {"high": 0.6, "medium": 0.4, "low": 0.25}
    min_score = confidence_levels.get(min_confidence, 0.25)

    duplicates = []

    for i, skill1 in enumerate(skills):
        if target_skill and skill1.name != target_skill:
            continue

        for skill2 in skills[i + 1 :]:
            if target_skill and skill2.name == target_skill:
                # If filtering by skill, compare in other direction
                skill1, skill2 = skill2, skill1

            # Skip comparing skill to itself (same path)
            if skill1.path == skill2.path:
                continue

            score, reasons = calculate_similarity(skill1, skill2)
            if score >= min_score:
                confidence = get_confidence(score)
                duplicates.append((skill1, skill2, score, confidence, reasons))

    # Sort by score descending
    duplicates.sort(key=lambda x: x[2], reverse=True)
    return duplicates


def main() -> None:
    parser = argparse.ArgumentParser(description="Find duplicate skills")
    parser.add_argument("directory", nargs="?", help="Directory to scan")
    parser.add_argument(
        "--fbsource", action="store_true", help="Scan all of fbsource using xbgs"
    )
    parser.add_argument(
        "--min-confidence",
        choices=["high", "medium", "low"],
        default="low",
        help="Minimum confidence level to report",
    )
    parser.add_argument("--skill", help="Only check duplicates for this skill name")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not args.directory and not args.fbsource:
        parser.error("Either provide a directory or use --fbsource")

    # Scan for skills
    if args.fbsource:
        print("Scanning fbsource for skills...", file=sys.stderr)
        skills = scan_fbsource()
    else:
        skills = scan_directory(Path(args.directory))

    print(f"Found {len(skills)} skills", file=sys.stderr)

    # Find duplicates
    duplicates = find_duplicates(skills, args.min_confidence, args.skill)

    if args.json:
        import json

        output = [
            {
                "skill1": {"name": d[0].name, "path": d[0].path},
                "skill2": {"name": d[1].name, "path": d[1].path},
                "score": d[2],
                "confidence": d[3],
                "reasons": d[4],
            }
            for d in duplicates
        ]
        print(json.dumps(output, indent=2))
    else:
        if not duplicates:
            print("No duplicates found.")
            return

        print(f"\nFound {len(duplicates)} potential duplicate pairs:\n")
        for skill1, skill2, score, confidence, reasons in duplicates:
            print(f"[{confidence.upper()}] {skill1.name} <-> {skill2.name}")
            print(f"  Score: {score:.2f}")
            for reason in reasons:
                print(f"  - {reason}")
            print("  Paths:")
            print(f"    {skill1.path}")
            print(f"    {skill2.path}")
            print()


if __name__ == "__main__":
    main()
