#!/usr/bin/env python3
# (c) Meta Platforms, Inc. and affiliates. Confidential and proprietary.

"""
Sync the skill-creator skill from upstream anthropics/skills repo.

Clones (or updates) the anthropics/skills GitHub repo via fwdproxy,
copies the skill-creator skill into this directory.

Usage:
    python3 scripts/sync_from_github.py
"""

import os
import shutil
import subprocess
import sys

REMOTE_REPO = "https://github.com/anthropics/skills.git"
PROXY = "http://fwdproxy:8080"
DEFAULT_BRANCH = "main"
UPSTREAM_SKILL_DIR = "skills/skill-creator"

# Resolve paths relative to this script's location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DEST = os.path.dirname(SCRIPT_DIR)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, **kwargs)


def clone_or_update_repo(repo_dir: str) -> str:
    """Clone or update the upstream repo. Returns the current commit hash."""
    env = {**os.environ, "http_proxy": PROXY, "https_proxy": PROXY}
    git_dir = os.path.join(repo_dir, ".git")

    if os.path.isdir(git_dir):
        print("Updating existing clone...")
        run(
            ["git", "-c", f"http.proxy={PROXY}", "fetch", "origin", DEFAULT_BRANCH],
            cwd=repo_dir,
            env=env,
        )
        run(
            ["git", "checkout", DEFAULT_BRANCH],
            cwd=repo_dir,
            env=env,
        )
        run(
            ["git", "reset", "--hard", f"origin/{DEFAULT_BRANCH}"],
            cwd=repo_dir,
            env=env,
        )
    else:
        print("Cloning upstream repo...")
        run(
            ["git", "-c", f"http.proxy={PROXY}", "clone", REMOTE_REPO, repo_dir],
            env=env,
        )

    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def copy_skill_files(repo_dir: str) -> None:
    """Copy skill-creator files from upstream, preserving scripts."""
    src = os.path.join(repo_dir, UPSTREAM_SKILL_DIR)
    if not os.path.isdir(src):
        print(
            f"ERROR: {UPSTREAM_SKILL_DIR} not found in upstream repo",
            file=sys.stderr,
        )
        sys.exit(1)

    # Preserve local-only files before wiping
    local_files = ["sync_from_github.py", "HASH", "TARGETS"]
    preserve = {}
    for name in local_files:
        path = os.path.join(SCRIPT_DIR, name)
        if os.path.isfile(path):
            with open(path, "r") as f:
                preserve[name] = f.read()

    # Preserve tests/ directory (Meta-specific, not in upstream)
    tests_dir = os.path.join(SCRIPT_DIR, "tests")
    tests_backup_dir = os.path.join(os.path.dirname(SKILL_DEST), ".tests-backup")
    if os.path.isdir(tests_dir):
        shutil.copytree(tests_dir, tests_backup_dir)

    # Remove existing skill content
    if os.path.isdir(SKILL_DEST):
        shutil.rmtree(SKILL_DEST)
    os.makedirs(SKILL_DEST, exist_ok=True)

    # Copy everything except LICENSE and README
    skip = {"LICENSE.txt", "README.md"}
    for item in os.listdir(src):
        if item in skip:
            print(f"  Skipping {item}")
            continue
        src_path = os.path.join(src, item)
        dst_path = os.path.join(SKILL_DEST, item)
        if os.path.isdir(src_path):
            shutil.copytree(src_path, dst_path)
            print(f"  Copied {item}/")
        else:
            shutil.copy2(src_path, dst_path)
            print(f"  Copied {item}")

    # Restore local-only files
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    for name, content in preserve.items():
        path = os.path.join(SCRIPT_DIR, name)
        with open(path, "w") as f:
            f.write(content)
        if name.endswith(".py"):
            os.chmod(path, 0o755)
        print(f"  Restored scripts/{name}")

    # Restore tests/ directory
    if os.path.isdir(tests_backup_dir):
        shutil.copytree(tests_backup_dir, tests_dir)
        shutil.rmtree(tests_backup_dir)
        print("  Restored scripts/tests/")


def add_nolint(directory: str) -> None:
    """Add @nolint to synced Python files that don't already have it."""
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, "r") as f:
                content = f.read()
            if "@nolint" in content:
                continue
            if content.startswith("#!"):
                # Insert after shebang line
                first_newline = content.index("\n")
                content = (
                    content[: first_newline + 1]
                    + "# @nolint\n"
                    + content[first_newline + 1 :]
                )
            else:
                content = "# @nolint\n" + content
            with open(path, "w") as f:
                f.write(content)
            rel_path = os.path.relpath(path, directory)
            print(f"  Added @nolint to {rel_path}")


def append_meta_note(skill_md_path: str) -> None:
    """Append a note about Meta's skills guide to SKILL.md."""
    note = (
        "\n## Creating a Claude Templates Skill\n\n"
        "If creating a skill inside fbcode/claude-templates/components, "
        "also refer to fbcode/claude-templates/components/skills/CLAUDE.md "
        "for Meta-specific conventions and requirements.\n"
    )
    with open(skill_md_path, "r") as f:
        content = f.read()
    if "claude-templates/components/skills/CLAUDE.md" in content:
        return
    with open(skill_md_path, "a") as f:
        f.write(note)


def ensure_trailing_newlines(directory: str) -> None:
    """Ensure all .md files in directory end with a trailing newline."""
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            with open(path, "r") as f:
                content = f.read()
            if content and not content.endswith("\n"):
                with open(path, "w") as f:
                    f.write(content + "\n")


def write_hash_file(commit_hash: str) -> None:
    """Write the synced commit hash to a HASH file."""
    hash_path = os.path.join(SCRIPT_DIR, "HASH")
    with open(hash_path, "w") as f:
        f.write(commit_hash + "\n")


def main() -> None:
    print(f"Syncing skill-creator from {REMOTE_REPO}\n")

    # Use a persistent working directory under $HOME
    home = os.environ.get("HOME", os.path.expanduser("~"))
    work_dir = os.path.join(home, "local", "anthropic_skills_sync")
    repo_dir = os.path.join(work_dir, "skills")
    os.makedirs(work_dir, exist_ok=True)

    # Read previous hash if available
    hash_path = os.path.join(SCRIPT_DIR, "HASH")
    prev_hash = None
    if os.path.isfile(hash_path):
        with open(hash_path, "r") as f:
            prev_hash = f.read().strip()
        print(f"Previous sync: {prev_hash[:7]}")
    else:
        print("No previous sync found (initial sync)")
    print()

    # 1. Clone or update
    print("[1/6] Fetching upstream repo...")
    commit_hash = clone_or_update_repo(repo_dir)
    print(f"  HEAD: {commit_hash[:7]}\n")

    if prev_hash == commit_hash:
        print("Already up to date. No changes since last sync.")
        return

    # 2. Copy files
    print("[2/6] Copying skill files...")
    copy_skill_files(repo_dir)
    print()

    # 3. Add @nolint to synced Python files
    print("[3/6] Adding @nolint to synced Python files...")
    add_nolint(SKILL_DEST)
    print("  Done\n")

    # 4. Append Meta-specific note to SKILL.md
    skill_md = os.path.join(SKILL_DEST, "SKILL.md")
    print("[4/6] Appending Meta-specific note to SKILL.md...")
    append_meta_note(skill_md)
    print("  Done\n")

    # 5. Ensure all .md files end with trailing newline
    print("[5/6] Ensuring .md files end with trailing newline...")
    ensure_trailing_newlines(SKILL_DEST)
    print("  Done\n")

    # 6. Write hash file
    print("[6/6] Writing HASH file...")
    write_hash_file(commit_hash)
    print(f"  Synced to: {commit_hash}\n")

    # Summary
    if prev_hash:
        print(f"Updated: {prev_hash[:7]} -> {commit_hash[:7]}")
    else:
        print(f"Initial sync complete: {commit_hash[:7]}")


if __name__ == "__main__":
    main()
