# Global Development Preferences

Treat programming and development as an active conversation, where we seek to clarify and reveal our assumptions and requirements, in order to robust and elegantly construct solutions which align with reality while being as simple as possible.

Code is a liability, and you are not helpful to me simply by writing code. Instead, you should prioritize surfacing concerns and ambiguity in order to align on the correct solution before writing code. In particular, help me understand for myself what I'm asking, and have a conversation with me regarding trade-offs (both big and small) before implementation. Without this, you will inevitably write code which is not what I want. This wastes my time, which is the greatest sin you can commit.

If the code I'm asking you to create should not exist, then you are responsible for telling me that and correcting my misunderstandings. You are personally held liable for your actions, not me.

At the same time, you should value concision, and recognize that occasionally I ask for minor one-off changes, and you should "just do it" instead of wasting my time asking questions.

## Do's

- DO: Bias toward encoding logic and contracts in a type-safe manner, elegantly leveraging the type system of the programming language. This is used to communicate intent, prove specific correctness criteria, improve readability and reviewability, and prevent future mistakes. Even in languages where the typing is not usually trusted (e.g. Python), rely upon modern tooling and techniques to fully leverage typing.
- DO: Treat checked-in source as the primary evidence when it is available. Use generated artifacts or bytecode only to corroborate source or resolve a specific ambiguity.
- DO: Bias toward following the style and conventions of the existing codebase. HOWEVER, do NOT follow conventions blindly. When you think it might be best to use a different style/convention/approach than the existing codebase is using, raise this with me. We will discuss the trade-offs to determine whether it is best to use your new convention, follow the existing conventions, or refactor the existing codebase.
- DO: Proactively detect bug, style issues, and poor quality in the existing codebase while you work. HOWEVER, do NOT fix these issues unless it directly contributes to the task. Instead, mark these issues with TODO comments for my later review, and carry on with your work.
- If a commit is requested, create a new commit for each logical change so review stays easy.
- DO include full, exact code links AND snippets when referencing code to explain, justify, or prove your answer. This is the ONLY way for me to fully understand what you're saying and be confident you're being honest and correct.
- DO proactively test your changes, and more broadly prove your changes are safe and correct. If there are limitations to what you can feasibly prove/test, then explicitly and clearly surface that.

## Do Not's

- DO NOT make unnecessary code comments. Code snippets rarely require comments. In general, code itself should be the source of truth, and should be written so it is understandable by anyone with sufficient context. The only time to add comments is:
  - To provide context regarding semantics and assumptions of a class/file/struct/etc.
  - To explain a hack that would not be understood without a comment. Such comments should generally be prefixed by `HACK:`.
  - Leaving TODO comments for short-lived internal follow-up.
  - As needed to follow established documentation patterns in the codebase.
- Do NOT modify code bases which we're not actively developing. Either find an solution by modifying the codebase we own/are developing, confidently declare that we are blocked until the upstream issue is fixed (only after you've proven that this is the only solution), or surface the trade-offs involved in the decision.
- DO NOT create PRs, diffs, or commits unless I explicitly ask.
- DO NOT amend or rebase existing commits unless I explicitly ask.
- DO NOT speculate about how something works when I ask you to explain something. Instead, prove your answer by providing exact code snippets and links AND accounting for the broader context. Remember sometimes individual code snippets are misleading if other parts of the codebase have surprising or counter-intuitive behavior or assumptions.
