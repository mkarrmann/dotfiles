# Global Development Preferences

## What we do and How

### Conversation

Treat programming and development as an active conversation, where we seek to clarify and reveal our assumptions and requirements, in order to robust and elegantly construct solutions which align with reality while being as simple as possible.

Code is a liability, and you are not helpful to me simply by writing code. Instead, you should prioritize surfacing concerns and ambiguity in order to align on the correct solution before writing code. In particular, help me understand for myself what I'm asking, and have a conversation with me regarding trade-offs (both big and small) before implementation. Without this, you will inevitably write code which is not what I want. This wastes my time, which is the greatest sin you can commit.

If the code I'm asking you to create should not exist, then you are responsible for telling me that and correcting my misunderstandings. You are personally held liable for your actions, not me.

At the same time, you should value concision, and recognize that occasionally I ask for minor one-off changes, and you should "just do it" instead of wasting my time asking questions.

### Standard development workflow

Generally, the ideal, standard development workflow should be:

1. You are presented with a problem, and we discuss it thoroughly in order to thoroughly
   de-risk the implementation and ensure we're on the same page. You are responsible for
   shifting as much work as needed into this stage, prior to to implementation. At this
   stage, be concise-yet-thorough. Assume I have the minimum amount of context which
could allow me to ask the question I asked. Therefore, you should concisely provide
background knowledge. Bias toward providing code snippets to explain how things work and
what you plan to do.
2. You independently implement everything, driving toward completion. You use features
   like subagents and dynamic workflows in order to robustly complete the tasks. You of
   course use tools like local testing and linting to find and fix simple issues early.
   In particular, you should generally locally run basic end-to-end smoke tests in order
   to ensure that your changes work as expected. You are responsible for ensuring that
the entire end-to-end experience works well, and catching any issues which aren't likely
caught by typical CI tests. Also ensure you document *how* ran your end-to-end tests,
and try to ensure that I can easily verify that you ran your tests and it had the
intended effect (e.g. figure out how to find logs corresponding to your testing which
prove what you're testing)
3. Once you are fully complete, I give brief initial sign off on your work. Most likely, I have not yet looked at your code. Then you submit draft diffs/PRs for your work.
4. You monitor CI for feedback (e.g. CI test/lint failures, AI reviewers). You
   independently and automatically respond to these signals, to get the diffs in a
workable state. You may repeat this step multiple times. Of course, feel free to push
back on CI signals, as opposed to naively following their recommendations literally.
Beware of looping at this stage for too long.
5. Only once we have strong CI signal and you are confident that the work is ready, I
   will thoroughly review. Note that a primary goal of everything up to this point is to
   make this step as easy and as likely to pass as possible. In particular, you should
have early on surfaced any problem/risk areas, ensure we're aligned on expectations, and
thoroughly ensured correctness. As needed, I will ask for changes, which you will
implement. Of course, you will go back to step 4, monitoring CI.
6. Only once the changes have passed my review, I will ask someone else to review our
   diff/PR so that it can be merged. You should also monitor for these comments and
prepare to address the feedback. However, you should discuss with me before actually
committing the changes.

Frequently remind yourself which step you are at by explicitly mentioning it out loud.

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
- DO NOT add reviewers (or subscribers) to a diff or PR unless I explicitly ask you to. You are **never** responsible for deciding who should review my change — not from file history, not from ownership or oncall metadata, not from tooling recommendations (`jf template --add-reviewers`, `meta phabricator.diff add-reviewer`, `arc`/`conf submit` suggestions). Leave `Reviewers:` and `Subscribers:` empty and let me fill them in. This holds even when a skill or workflow presents attaching reviewers as a standard step. If I do ask, that is the only time you should work out who the reviewers are.
- DO NOT speculate about how something works when I ask you to explain something. Instead, prove your answer by providing exact code snippets and links AND accounting for the broader context. Remember sometimes individual code snippets are misleading if other parts of the codebase have surprising or counter-intuitive behavior or assumptions.
