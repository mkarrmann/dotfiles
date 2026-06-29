---
name: add-diff-comment
description: Post comments to Phabricator diffs programmatically. Use when the user asks to add comments, feedback, or reviews to diffs - both general diff-level comments and inline comments on specific code lines.
mcp_file: add-diff-comment.json
---

# Add Diff Comment

## Overview

This skill enables posting comments to Phabricator diffs through the AddDiffComment MCP tool. You can post both general diff-level comments and inline comments targeting specific files and line numbers.

## Prerequisites

1. Valid diff number (accessible to the user)
2. Appropriate permissions to comment on the diff

## When to Use

Use this skill for ANY diff commenting requests:
- "Add a comment to diff D12345 saying..."
- "Post inline feedback on line 42 of file.js in D67890"
- "Comment on this diff that the tests look good"
- "Add a review comment about the error handling in main.py line 150"
- "Post feedback to multiple lines in the diff"

## Comment Types

### Diff-Level Comments

General comments posted to the entire diff (not tied to specific code).

**When to use:**
- Overall feedback on the diff
- Questions about the approach
- Approval or high-level suggestions
- Cross-cutting concerns

**Example:**
```json
{
  "diff_num": "D12345678",
  "comment": "Great work! The implementation looks solid. Just a few minor suggestions in the inline comments."
}
```

### Inline Comments

Comments posted to specific file paths and line numbers.

**When to use:**
- Feedback on specific code lines
- Suggesting changes to particular functions
- Pointing out potential bugs or improvements
- Code review feedback

**Single-line inline comment:**
```json
{
  "diff_num": "D12345678",
  "file_path": "src/utils/helper.js",
  "line_number": 42,
  "comment": "Consider adding null check here to prevent potential crashes."
}
```

### Reply Comments

Threaded replies to existing inline or diff-level comments. Requires the parent comment's FBID (integer).

**When to use:**
- Responding to reviewer feedback
- Answering questions on existing comment threads
- Marking comments as addressed with context

**Example:**
```json
{
  "diff_num": "D12345678",
  "parent_comment_fbid": 987654321,
  "comment": "Addressed in the latest revision - renamed the variable as suggested."
}
```

**Note:** The `parent_comment_fbid` is the integer FBID of the comment you want to reply to. You can obtain comment FBIDs by querying the diff's comments via the `get_phabricator_diff_details` tool. Note: this parameter only works for inline comments — replies to top-level (non-inline) diff comments are not supported by the underlying GraphQL mutation.

#### Reply to Reviewer Comments

When addressing review feedback, reply directly to reviewer comments to create threaded conversations:

1. Get the diff's comments (via `get_phabricator_diff_details`) to find comment FBIDs
2. Reply to each comment:
   ```json
   {
     "diff_num": "D12345678",
     "parent_comment_fbid": 987654321,
     "comment": "Done - refactored this into a separate function as suggested."
   }
   ```

**Multi-line inline comment:**
```json
{
  "diff_num": "D12345678",
  "file_path": "src/components/Button.tsx",
  "line_number": 15,
  "line_length": 5,
  "comment": "This entire block could be simplified using the new utility function from D12345000."
}
```

**Comment on old file version:**
```json
{
  "diff_num": "D12345678",
  "file_path": "legacy/old_api.py",
  "line_number": 100,
  "is_new_file": false,
  "comment": "This pattern was already problematic in the old version."
}
```

## Parameters

### Required Parameters

| Parameter | Description | Used In |
|-----------|-------------|---------|
| `diff_num` | Diff number (e.g., "D12345678" or "12345678") | All comments |
| `comment` | Comment text in Markdown format | All comments |

### Optional Parameters (For Inline Comments)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `file_path` | string | - | Path to file in diff (triggers inline comment) |
| `line_number` | integer | - | Line number to comment on (required if file_path provided) |
| `line_length` | integer | 0 | Number of lines the comment spans |
| `is_new_file` | boolean | true | Comment on new/right side (true) or old/left side (false) |

### Optional Parameters (For Reply Comments)

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `parent_comment_fbid` | integer | - | FBID of an existing comment to reply to (creates threaded reply). Only works for inline comments. |

## Markdown Formatting

Comments support full Markdown formatting:

```markdown
**Bold text** for emphasis
*Italic text* for subtle emphasis
`code snippets` for inline code
- Bullet lists for multiple points
1. Numbered lists for ordered feedback
[Links](https://internalfb.com/diff/D12345) to related diffs
```

## Common Workflows

### Review a Diff with Multiple Inline Comments

1. Read the diff content using get_diff_details or get_phabricator_diff_details
2. Analyze the code changes
3. Post inline comments for each issue found
4. Post a diff-level summary comment

### Post Constructive Feedback

```json
{
  "diff_num": "D87654321",
  "file_path": "server/api/handler.py",
  "line_number": 203,
  "comment": "**Suggestion:** Consider extracting this validation logic into a separate function for better testability and reuse. Example:\n\n```python\ndef validate_request(data):\n    # validation logic here\n    return is_valid\n```"
}
```

### Combine General and Inline Feedback

1. First, post inline comments on specific issues:
   - Error handling on line 50
   - Performance concern on line 120
   - Style suggestion on lines 200-205

2. Then, post diff-level summary:
   ```json
   {
     "diff_num": "D12345678",
     "comment": "Overall this looks great! I've left a few inline comments about error handling and performance. Once those are addressed, this is ready to land. Nice work!"
   }
   ```

## Detailed Operation Examples

### Example 1: Simple Diff-Level Comment

Post general feedback on the entire diff:

```json
{
  "diff_num": "D88663193",
  "comment": "LGTM! The MCP integration looks clean and follows the established pattern."
}
```

### Example 2: Inline Comment with Suggestion

Provide specific feedback on a particular line:

```json
{
  "diff_num": "D88663193",
  "file_path": "fbcode/claude-templates/components/helpers/google-docs-tools.md",
  "line_number": 23,
  "comment": "Consider adding a note about the GK requirement in the description_override as well."
}
```

### Example 3: Multi-Line Inline Comment

Comment on an entire block of code:

```json
{
  "diff_num": "D88663193",
  "file_path": "fbcode/claude-templates/components/mcps/google-docs.json",
  "line_number": 4,
  "line_length": 7,
  "comment": "The mcpServers configuration looks good. This follows the stdio pattern correctly with devmate_mux."
}
```

### Example 4: Comment with Code Suggestion

Provide feedback with formatted code example:

```json
{
  "diff_num": "D12345678",
  "file_path": "src/utils/parser.ts",
  "line_number": 89,
  "comment": "This error handling could be more robust:\n\n```typescript\ntry {\n  const result = parseData(input);\n  return result;\n} catch (error) {\n  logger.error('Parse failed', { error, input });\n  throw new ParseError('Invalid input format', { cause: error });\n}\n```"
}
```

### Example 5: Comment on Old File Version

Comment on code from the left/old side of the diff:

```json
{
  "diff_num": "D12345678",
  "file_path": "legacy/deprecated_api.py",
  "line_number": 45,
  "is_new_file": false,
  "comment": "Good catch removing this - it was causing the race condition mentioned in T123456."
}
```

## Tips

1. **Diff Number Format**: Both "D12345678" and "12345678" are accepted
2. **File Paths**: Must match exactly as shown in the diff (use full path from repository root)
3. **Line Numbers**: Refer to new file by default; set `is_new_file: false` for old file
4. **Multi-line Comments**: Use `line_length` to span multiple lines (e.g., commenting on an entire function)
5. **Markdown**: Use formatting to make comments clear and actionable
6. **Be Specific**: For inline comments, clearly explain the concern and suggest improvements
7. **Comments Posted Immediately**: Comments are posted immediately and are visible to all reviewers
8. **Identity**: Comments are posted as the authenticated user (not as a bot)

## Error Handling

Common errors and solutions:

- **"Diff not found"**: Verify the diff number exists and is accessible to you
- **"File not found in diff"**: Check that file_path exactly matches the file path shown in the diff
- **"Invalid line number"**: Ensure line_number exists in the specified file version
- **"Permission denied"**: You must have permission to comment on the diff

## Related Skills

- `get-diff-details` / `get-phabricator-diff-details`: Read diff content before commenting
- `diff-search`: Find diffs to comment on
- `jellyfish`: Manage diff workflows

## Examples by Use Case

### Code Review Feedback

```json
{
  "diff_num": "D12345678",
  "file_path": "src/models/User.php",
  "line_number": 156,
  "comment": "⚠️ **Security concern**: This query is vulnerable to SQL injection. Please use parameterized queries:\n\n```php\n$stmt = $db->prepare('SELECT * FROM users WHERE id = ?');\n$stmt->execute([$userId]);\n```"
}
```

### Approval with Minor Suggestions

```json
{
  "diff_num": "D87654321",
  "comment": "Excellent work! The architecture is solid and the code is well-tested. ✅\n\nI've left a couple of minor inline comments about variable naming, but these are optional improvements. Feel free to land as-is or address them - either way works!"
}
```

### Question About Implementation

```json
{
  "diff_num": "D11111111",
  "file_path": "backend/services/cache.rs",
  "line_number": 78,
  "comment": "❓ Quick question: Have we considered using TTL-based eviction here instead of LRU? Given the access patterns from T987654, TTL might be more efficient.\n\nNot blocking - just curious about the tradeoff analysis."
}
```

### Performance Suggestion

```json
{
  "diff_num": "D22222222",
  "file_path": "www/lib/data/QueryBuilder.php",
  "line_number": 203,
  "line_length": 15,
  "comment": "🚀 **Performance optimization**: This nested loop has O(n²) complexity. Consider using a hash map for O(n) lookup:\n\n```php\n$indexMap = array_column($items, 'value', 'id');\nforeach ($queries as $query) {\n  $result = $indexMap[$query['id']] ?? null;\n  // process result\n}\n```\n\nThis should significantly improve performance for large datasets."
}
```
