# Global Development Preferences

## Do's

- DO: Bias toward encoding logic and contracts in a type-safe manner, elegantly leveraging the type system of the programming language.
- DO: Bias toward following the style and conventions of the existing codebase. HOWEVER, do NOT follow conventions blindly. When you think it might be best to use a different style/convention/approach than the exist codebase is using, raise this with me. We will discuss the trade-offs to determine whether it is best to use your new convention, follow the existing conventions, or to refactor the existing codebase.
- DO: proactively detect bug, style issues, and poor quality in the existing codebase which you spot during your development work. HOWEVER, do NOT fix these issues unless it directly contributes to the goal you've been tasked with. Instead, mark these issues with TODO comments for my later review, and carry on with your work.

## Do Not's

- DO NOT make unecessary code comments. Code snippets rarely require code comments. In general, your code itself should be the source of truth, and you should write code in a manner such that it understandable by anyone with sufficient context, making comments unnecessary. The absolute ONLY time to make code comments are:
    - To provide context regarding the semantics and/or assumptions of a class/file/struct/etc. (depending on the language in question).
    - To explain a particular hack in the code which is may not be understood without a comment. (Of cousre, avoiding the hack in the first place is preferable.). Such comments should generally be prefeced by `HACK:`.
    - Leaving TODO comments for yourself, or anything else you need for your internal use, if you expect them to be removed shortly.
    - As needed in order to follow clear patterns of the existing codebase (e.g. documentation standards)



