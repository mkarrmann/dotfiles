# Deep Research — Synthesis & Reporting

## Table of Contents

- [Information Integration](#information-integration)
- [Report Structure](#report-structure)
- [Delivering the Report](#delivering-the-report)
  - [Step 1: Generate markdown report file](#step-1-generate-markdown-report-file)
  - [Step 2: Convert to Google Doc and send via pingme](#step-2-convert-to-google-doc-and-send-via-pingme)
  - [Common Pitfalls to Avoid](#common-pitfalls-to-avoid)
  - [Step 3: Alternative — Upload to pastry](#step-3-alternative--upload-to-pastry)
  - [Step 4: Confirm delivery](#step-4-confirm-delivery)
- [Resource Budgets](#resource-budgets)

## Information Integration

After all tasks are complete (or enough data collected):
1. Identify consensus findings across multiple agents
2. Note any conflicts or discrepancies
3. Prioritize based on source quality and recency
4. Cross-reference with Meta's internal documentation

## Report Structure

```markdown
# Research Report: [Topic]

## Executive Summary
- Key findings in 3-5 bullet points
- Critical Meta-specific context

## Detailed Findings

### [Perspective/Component 1]
- Finding with evidence (file_path:line_number references)
- Meta context (team, platform, history)

### [Perspective/Component 2]
...

## Recommendations
- Actionable next steps
- Relevant Meta teams/experts to consult
- Related diffs/tasks to review

## Sources
- Primary: [Specific files, diffs, Buck targets]
- Secondary: [Related documentation, GraphQL queries]
```

## Delivering the Report

After synthesizing the research findings:

### Step 1: Generate markdown report file

```bash
# Create report with timestamp
cat > "/tmp/meta_research_report_$(date +%s).md" << 'EOF'
[Your complete markdown report following the structure above]
EOF
```

### Step 2: Convert to Google Doc and send via pingme

**IMPORTANT**: Use simple sequential bash commands to avoid syntax errors.  do NOT use complex variable assignments with `$()` in the same command line.

**Step-by-step approach:**

```bash
# Step 1: Get the report filename
ls -t /tmp/meta_research_report_*.md | head -1
```

Copy the actual filename from the output, then:

```bash
# Step 2: Convert to Google Doc (replace TIMESTAMP with actual value from step 1)
phps TextToGoogleDocScript --file /tmp/meta_research_report_TIMESTAMP.md 2>&1
```

Extract the Google Doc URL from the output (looks like: `https://docs.google.com/document/d/DOCUMENT_ID/edit`), then:

```bash
# Step 3: Send notification via pingme (use -i flag to read from stdin, run in background with &)
echo "Meta Deep Research Complete: [Brief Topic] - Summary of findings. View report: https://docs.google.com/document/d/DOCUMENT_ID/edit" | pingme -i &
```

### Common Pitfalls to Avoid

- DON'T: `DOC_URL=$(phps TextToGoogleDocScript ...)` - complex variable assignment can cause parse errors
- DON'T: use `--context` or `--priority` flags with pingme - they don't exist
- DON'T: try to capture output and send in one command
- DO: run simple sequential commands and copy values between them
- DO: use `pingme -i` to read message from stdin
- DO: run pingme in background with `&` at the end

### Step 3: Alternative — Upload to pastry

(if Google Docs conversion fails):
```bash
ls -t /tmp/meta_research_report_*.md | head -1
cat /tmp/meta_research_report_TIMESTAMP.md | pastry
echo "Meta Deep Research Complete - View at: https://www.internalfb.com/phabricator/paste/view/PASTRY_ID" | pingme -i &
```

### Step 4: Confirm delivery

- Inform the user that the full report has been converted to Google Doc and sent via pingme
- Include both the Google Doc URL and local file path in your response
- Provide a brief summary (2-3 sentences) in the response

## Resource Budgets

Enforce limits to prevent system overload:
- **Simple queries**: 20 tool calls per task
- **Medium complexity**: 40 tool calls per task
- **High complexity**: 60 tool calls per task (hard limit: 80)
- **File limits**: always use `--limit 80` or similar flags
