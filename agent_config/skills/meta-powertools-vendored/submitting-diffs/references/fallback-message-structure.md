# Fallback commit message structure

Use this **only** when step 5.4's template browsing finds no usable Phabricator diff template (or the lookup fails). When a template *was* found, adopt that template's own structure instead — this file is the safety net, not the default.

```
[Tag1][Tag2] Title summarizing the change and why

**Context:**
Why this change matters

**Motivation:**
Problem being solved

**This diff:**
- Specific change 1
- Specific change 2
- Specific change 3

Test Plan: <how the change was verified>
```

Notes:

- Show ALL sections — don't submit a title-only message.
- `Test Plan:` is a section header (like `Summary:`), **not** a markdown subtitle. Don't write `**Test Plan:**`.
- The title line + prefix tags are required in every message, template or fallback.
