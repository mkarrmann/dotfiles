---
name: reviewing-changes
description: >-
  Use whenever a code review is involved, in either direction: when you ask
  another agent (subagent, child session, cross-model reviewer) to review your
  changes, a diff, or a PR, and when you are the agent asked to review them.
  Covers how the reviewer prioritizes and tags findings, and how the requester
  briefs the reviewer and keeps the review in scope. Trigger keywords:
  review, code review, reviewer, second opinion, review my diff, review this
  PR, review these changes, cross-model review.
---

# Reviewing changes

This skill has two sides. Read both: the requester should know what the
reviewer needs, and the reviewer should know what the requester will do with
the report.

## Doing a review

Your priorities in order are:

1. Highest priority:
   - **1a**: Identify real, problematic bugs / issues which can directly impact users in practice in production.
   - **1b**: Ensuring changes are well-scoped and accomplish the goal they actually intend to accomplish.
2. Next:
   - **2a**: Checking code is "good". This includes checking code follows relevant convention, and checking for the existence of "code smells". That is, you should be checking if anything in the design is "off". Note that this is separate from identifying a specific case in which the code behaves problematically-this instead comes from having taste, assessing how clear and consistent the semantics are, etc.
3. Lowest priority:
   - **3a**: Style issues, including nits
   - **3b**: Potential bugs which don't fall under 1a. This includes things like error-handling for marginal and/or theoretical edge cases which may or may not necessarily matter much in practice.

The distinctions here are important. In fact, you must make sure to mark which category each of your review comments fall under in your response.

This is critical because AI reviewers tend to focus too much on 3a and 3b. These are fine to mention, but requesters are entitled to filter them out.

Code review is a very broad and general task, and you are responsible for exercising your best judgement. If anything doesn't fall under the above but is still worth mentioning, please mention it.

## Requesting a review

Firstly, you must ensure the reviewer has sufficient context about the problem and scope in order to fully do their job.

Name the `reviewing-changes` skill in your brief. A reviewer that has it usually loads it on its own, but a narrowly worded brief can steer it past the skill; naming it makes sure its comments come back tagged. If the reviewer runs where this skill isn't installed (a sandbox, or a machine without these dotfiles), paste in the "Doing a review" section instead.

By far the biggest risk of agents asking other agents for review is gradual scope creep which comes from reviewers asking to improve behavior in marginal / unimportant cases. Remember, it is your responsibility to keep everything well scoped and focused on the actual problem at hand, without wasting space in your diffs handling cases that really don't need to be handled. Above all, we need to prioritize easy review of the code, and that sometimes can entail leaving aside theoretical edge cases, which add undue burden to the reviewers to fully internalize.

Of course, this isn't an excuse to just leave bugs or anything like that. Again, it comes down to exercising judgment. Often, the most important thing to do is to raise this with me, the human, and discuss whether or not different bugs actually are in scope.
