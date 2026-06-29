# Resource Validation

This document covers path and resource validation for skills.

## Required Checks

### No Hardcoded Absolute Paths (🔴 Error)

Check all scripts for hardcoded absolute paths:
- `/Users/` (macOS home)
- `/home/` (Linux home)
- `/data/users/` (Meta devserver)
- `/data/sandcastle/` (Sandcastle)

**Why:** Hardcoded paths break when run on different machines.

### All Markdown Links Resolve (🔴 Error)

Check all `[text](path)` links in `.md` files. Each relative path must point to an existing file.

Skip:
- External links (`http://`, `https://`, `mailto:`)
- Anchor links (`#section`)
- Links inside code blocks

**Why:** Broken links frustrate users and indicate incomplete documentation.

### No Empty Directories (🔵 Suggestion)

Empty `scripts/`, `references/`, or `assets/` directories should be deleted.

**Why:** Empty directories are noise and suggest incomplete cleanup.

## Judgment Areas

### Placeholder Content

Beyond the specific placeholder files checked in [CONTENT.md](CONTENT.md), also look for:
- Other files with placeholder content ("TODO", "replace this", "example")
- Empty or near-empty files
- Files that don't match the skill's purpose

### Documentation/Implementation Match

Check whether SKILL.md accurately describes what scripts do:
- Does SKILL.md mention flags that scripts don't support?
- Are documented examples actually runnable?
- Do output format examples match what scripts produce?

**Example mismatch:**
```markdown
## SKILL.md says:
Supports `--format csv` and `--format json`

## But script only has:
parser.add_argument('--format', choices=['json'])  # No CSV!
```

### Script Quality

If the skill has scripts, assess:

1. **Error handling:** Do scripts fail gracefully with clear messages?
   ```python
   # Good
   if not input_file.exists():
       sys.exit(f"Error: File not found: {input_file}")

   # Bad
   data = json.load(open(sys.argv[1]))  # Crashes cryptically
   ```

2. **Relative paths:** Scripts should find assets relative to themselves
   ```python
   # Good
   ASSET_DIR = Path(__file__).parent.parent / "assets"

   # Bad
   ASSET_DIR = "/path/to/skill/assets"
   ```

3. **Dependencies:** Are all imports available in the target environment?

### File Organization

Check that files are in the right places:
- Scripts in `scripts/`, not scattered
- Reference docs in `references/`, not root
- Assets in `assets/`, not mixed with docs

**Exception:** SKILL.md always at root.

### Deep Reference Chains (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

References should be one level deep from SKILL.md. Flag chains where SKILL.md links to file A, which links to file B for the actual content.

**How to check:** For each file linked from SKILL.md, scan it for `[text](relative_path.md)` links to other local files. If those files also link to more local files, flag the chain.

**Bad:** SKILL.md → advanced.md → details.md → actual information
**Good:** SKILL.md → advanced.md, SKILL.md → details.md (all direct)

**Message:** "Reference chain detected: SKILL.md → [file A] → [file B]. Link [file B] directly from SKILL.md instead."

### Long Reference Files Without TOC (🔵 Suggestion)

Reference files longer than 100 lines should have a table of contents at the top. Claude may use `head -100` to preview, missing later content without a TOC.

**Message:** "Reference file is [N] lines with no table of contents. Add a TOC so Claude can see the full scope when previewing."
