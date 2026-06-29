# Content Quality

This document covers content quality checks for skill files.

## Required Checks

### No `[TODO:` Markers (🔴 Error in SKILL.md, 🟡 Warning in other files)

Check all `.md` files for `[TODO:` markers. Ignore markers inside code blocks (fenced or inline).

**Why:** TODO markers indicate incomplete content that should be finished before the skill is used.

### No Template Sections (🔴 Error)

Check for `## Structuring This Skill` heading in SKILL.md.

**Why:** This is guidance from the skill template that should be deleted after the skill is written.

### No Placeholder Files (🟡 Warning)

Check for these files with placeholder content:
- `scripts/example.py` - "This is a placeholder script"
- `references/api_reference.md` - "This is a placeholder for detailed reference"
- `assets/example_asset.txt` - "This placeholder represents where asset files"

**Why:** These are created by `init_skill.py` and should be replaced or deleted.

## Judgment Areas

### Quick Start Quality

Assess whether the Quick Start section is actually useful:
- Is there a runnable example?
- Does the example use realistic data?
- Can someone copy-paste and succeed?

**Good:**
```markdown
## Quick Start

Convert a CSV to JSON:
```bash
python3 scripts/convert.py sales.csv --output sales.json
```
```

**Bad:**
```markdown
## Quick Start

Use the tool with appropriate options.
```

### Content Length

**Guideline:** SKILL.md should be under ~5000 words.

**Signs it's too long:**
- Multiple detailed workflows in one file
- Extensive API documentation inline
- Many examples for different use cases

**Fix:** Split into reference files:
```
skill/
├── SKILL.md              # Overview + quick start (~1000 words)
└── references/
    ├── WORKFLOW-A.md     # Detailed workflow
    └── API.md            # API reference
```

### Code in Markdown

**Problem:** Code snippets embedded in markdown get rewritten each invocation.

**Flag these issues:**
- Python/bash code blocks longer than ~20 lines
- Same code appearing multiple times
- Code that could be a reusable script

**Good:** Point to bundled script
```markdown
To process the file:
```bash
python3 scripts/process.py input.json
```
```

**Bad:** Embed the code
```markdown
To process the file, use this code:
```python
import json
import re
# ... 50 lines ...
```
```

See [COMMON-MISTAKES.md](../../skill-creator/references/COMMON-MISTAKES.md) for more examples.

### Writing Style

**Prefer imperative form:**
```markdown
# Good
Run the extraction script.
Configure the output format.

# Avoid
You should run the extraction script.
You need to configure the output format.
```

### Navigation

**For skills with multiple files:**
- SKILL.md should link to all reference files
- Reference files should link back to SKILL.md
- No orphaned files

**Check:** Are all files in `references/` mentioned somewhere?

### Over-Explaining Known Concepts (🟡 Warning - STRICT tier, 🔵 Suggestion - others)

Flag paragraphs that explain things Claude already knows: what PDFs are, how libraries work, what JSON is, basic programming concepts.

**Bad:**
```markdown
PDF (Portable Document Format) files are a common file format that contains
text, images, and other content. To extract text from a PDF, you'll need to
use a library. There are many libraries available...
```

**Good:**
```markdown
Use pdfplumber for text extraction:
```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```

**Message:** "This explanation covers concepts Claude already knows. Remove general knowledge and keep only project-specific context."

### Too Many Options Without Default (🟡 Warning)

Flag when 3+ libraries, tools, or approaches are listed without recommending one. Pick a default and mention alternatives only when they serve a distinct use case.

**Bad:** "You can use pypdf, or pdfplumber, or PyMuPDF, or pdf2image..."

**Good:** "Use pdfplumber for text extraction. For scanned PDFs requiring OCR, use pdf2image with pytesseract instead."

**Message:** "Multiple approaches listed without a recommended default. Pick one primary approach and note alternatives only for different use cases."

### Inconsistent Terminology (🟡 Warning)

Flag when 3+ different terms are used for the same concept across all `.md` files. Examples:
- "API endpoint" / "URL" / "API route" / "path" for the same thing
- "extract" / "pull" / "get" / "retrieve" for the same operation

**Message:** "Multiple terms used for the same concept: [terms]. Pick one and use it consistently."

### Time-Sensitive Information (🟡 Warning)

Flag date-conditional logic: "Before August 2025, use...", "After v2 launches...", "Starting next quarter..."

**Better pattern:** Use "Current method" / "Old patterns" sections:
```markdown
## Current method
Use the v2 API endpoint.

## Old patterns (deprecated 2025-08)
<details><summary>Legacy v1 API</summary>
The v1 endpoint is no longer supported.
</details>
```

**Message:** "Time-sensitive instruction found. Use 'Current method' / 'Old patterns' sections instead of date-conditional logic."
