# Context Guide for Metamate Prompts

How to construct each section of a structured Metamate prompt for personalized, high-quality responses.

## The Context Section

**Purpose**: Help Metamate understand what you're trying to accomplish so it can tailor its response.

### What to Include

| Include | Example |
|---------|---------|
| Problem summary (2-3 sentences) | "I'm implementing a new GraphQL mutation that needs to write to the UserSettings ent." |
| What you've tried | "I've checked that I'm using the correct viewer context, but still getting errors." |
| Your goal | "I need to understand the privacy rules for ent mutations." |
| Error messages (if debugging) | "Getting EntPrivacyDeniedException on writes but reads work fine." |

### What NOT to Include

- Full stack traces (summarize the key error)
- Entire file contents (just mention the file path)
- Lengthy background (be concise)

### Good vs Bad Context

**Good**:
```
## Context
I'm implementing a GraphQL mutation that writes to the UserSettings ent.
Getting EntPrivacyDeniedException on writes but reads work fine.
I've verified the viewer context is correct.
```

**Bad** (too vague):
```
## Context
I'm having issues with ents.
```

**Bad** (too long):
```
## Context
So I started this project last week where we need to build a settings page for users.
The PM asked for several features including... [500 words of background]
```

## The Working Environment Section

**Purpose**: Let Metamate look at your actual code and codebase patterns.

### What to Include

| Field | What to Put |
|-------|-------------|
| Repo | `www`, `fbsource`, `fbcode`, etc. |
| Recent files | 2-4 relevant **pre-existing** files you've been working on |

### File Selection Guidelines

- **DO** include files that exist in the repo (Metamate can read them)
- **DO** include files relevant to your question
- **DON'T** include new files you just created (they're only local)
- **DON'T** include more than 4-5 files (keep it focused)

### Example

```
## Working Environment
- Repo: www
- Recent files:
  - flib/user/settings/UserSettingsEntMutator.php
  - flib/site/graphql/mutations/UpdateUserSettingsMutation.php
```

## The Question Section

**Purpose**: Be specific about what you need to know.

### Guidelines

- Be precise—avoid open-ended questions
- Ask for what you actually need (code patterns, concepts, policies, debugging help)
- If you have multiple questions, list them explicitly

### Good vs Bad Questions

**Good** (specific):
```
## Question
What causes EntPrivacyDeniedException on writes but not reads?
What privacy rules must be satisfied for ent mutations?
```

**Bad** (too vague):
```
## Question
How do ents work?
```

**Bad** (too broad):
```
## Question
Tell me everything about the Ent framework.
```

## The Research Hints Section

**Purpose**: Guide Metamate to look at additional personalized context that might be relevant.

### Available Hints

| Hint | When to Use | What It Does |
|------|-------------|--------------|
| "My recent diffs" | When your own work patterns are relevant | Metamate looks at your recent code changes |
| "My team's recent diffs" | When team conventions matter | Metamate looks at your team's code patterns |
| "My recent chats" | When there may be relevant discussions | Metamate searches your Workplace/Google Chat |
| "Code examples in [files]" | When you want patterns from specific files | Metamate analyzes the mentioned files |

### When to Include Each Hint

**Include "my recent diffs"** when:
- You want patterns from your own recent work
- You're asking about something you've done before
- You want consistency with your recent changes

**Include "my team's diffs"** when:
- You want to follow team conventions
- You're implementing something your team has done before
- You want to learn from teammates' patterns

**Include "my recent chats"** when:
- You've discussed this topic with colleagues
- There might be relevant context in Workplace/Chat
- You're following up on a previous conversation

**Include "code examples in [files]"** when:
- The files you mentioned contain relevant patterns
- You want Metamate to analyze specific implementations

### Example Research Hints

**For debugging:**
```
## Research Hints
To personalize your response, please also consider:
- My recent diffs for context on what I changed
- My team's recent diffs for similar debugging patterns
- My recent chats for discussions about this error
```

**For learning a new pattern:**
```
## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for how they implement this pattern
- Code examples in flib/core/ent/ for canonical implementations
```

**For policy questions:**
```
## Research Hints
To personalize your response, please also consider:
- My team's recent diffs to see how they handle similar requirements
```

## Putting It All Together

### Full Prompt Template

```
## Context
[2-3 sentences about your problem, what you've tried, and your goal]

## Working Environment
- Repo: [www/fbsource/fbcode]
- Recent files: [2-4 relevant pre-existing files]

## Question
[Specific question(s) you need answered]

## Research Hints
To personalize your response, please also consider:
- [Relevant hints from the table above]
```

### Token Budget Guidelines

Keep your prompt concise to leave room for Metamate's response:

| Section | Target Length |
|---------|---------------|
| Context | 2-4 sentences |
| Working Environment | 3-5 lines |
| Question | 1-3 specific questions |
| Research Hints | 2-4 bullet points |
| **Total** | Under 200 words |
