# Global Development Preferences

## Workflow Constraints

- DO NOT create PRs, diffs, or commits unless I explicitly ask.
- DO NOT amend or rebase existing commits unless I explicitly ask.
- If a commit is requested, create a new commit for each logical change so review stays easy.
- If the file you are editing contains commented-out code, do NOT modify those comments unless asked.

## Do's

- DO: Bias toward encoding logic and contracts in a type-safe manner, elegantly leveraging the type system of the programming language.
- DO: Bias toward following the style and conventions of the existing codebase. HOWEVER, do NOT follow conventions blindly. When you think it might be best to use a different style/convention/approach than the existing codebase is using, raise this with me. We will discuss the trade-offs to determine whether it is best to use your new convention, follow the existing conventions, or refactor the existing codebase.
- DO: Proactively detect bug, style issues, and poor quality in the existing codebase while you work. HOWEVER, do NOT fix these issues unless it directly contributes to the task. Instead, mark these issues with TODO comments for my later review, and carry on with your work.

## Do Not's

- DO NOT make unnecessary code comments. Code snippets rarely require comments. In general, code itself should be the source of truth, and should be written so it is understandable by anyone with sufficient context. The only time to add comments is:
  - To provide context regarding semantics and assumptions of a class/file/struct/etc.
  - To explain a hack that would not be understood without a comment. Such comments should generally be prefixed by `HACK:`.
  - Leaving TODO comments for short-lived internal follow-up.
  - As needed to follow established documentation patterns in the codebase.
