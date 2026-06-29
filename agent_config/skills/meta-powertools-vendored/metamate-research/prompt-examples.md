# Example Metamate Prompts

Example prompts for different scenarios, from simple lookups to complex debugging.

## Debugging Examples

### Ent Privacy Exception

```
## Context
I'm implementing a GraphQL mutation that writes to the UserSettings ent.
Getting EntPrivacyDeniedException on writes but reads work fine.
I've verified the viewer context is correct.

## Working Environment
- Repo: www
- Recent files: flib/user/settings/UserSettingsEntMutator.php, flib/site/graphql/mutations/UpdateUserSettingsMutation.php

## Question
What causes EntPrivacyDeniedException on writes but not reads?
What privacy rules must be satisfied for ent mutations?

## Research Hints
To personalize your response, please also consider:
- My recent diffs and my team's diffs for ent mutation patterns
- My recent chats for discussions about this issue
```

### GraphQL Error

```
## Context
My GraphQL query is returning null for a field that should have data.
I've verified the data exists in the database and the ent loads correctly in isolation.

## Working Environment
- Repo: www
- Recent files: flib/site/graphql/types/UserType.php, flib/graphql/connections/UserConnectionTypes.php

## Question
What are common causes of null GraphQL fields when the underlying data exists?
How do privacy and visibility rules affect GraphQL field resolution?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for GraphQL type implementations
- Code examples in the files mentioned above
```

### Test Failure

```
## Context
My WWWTest is failing with "GenAsyncFailedToResolve" error.
The test passes locally but fails in Sandcastle.
The test involves async Ent loading.

## Working Environment
- Repo: www
- Recent files: flib/user/__tests__/UserSettingsTest.php

## Question
What causes GenAsyncFailedToResolve in WWWTest?
Why might a test pass locally but fail in Sandcastle?

## Research Hints
To personalize your response, please also consider:
- My recent diffs for changes that might affect test behavior
- My team's diffs for similar test patterns
```

## Framework Learning Examples

### Understanding Ent Framework

```
## Context
I need to create a new Ent for storing user notification preferences.
This is my first time creating an Ent from scratch.

## Working Environment
- Repo: www
- Recent files: flib/user/notifications/

## Question
What's the recommended process for creating a new Ent?
What files do I need to create and what's the relationship between EntSchema, Ent, and EntMutator?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for new Ent creations
- Examples in the notifications directory
```

### Learning XController

```
## Context
I need to add a new internal tool page.
I've used XController before but not for internal tools.

## Working Environment
- Repo: www
- Recent files: flib/intern/tools/

## Question
How does XController differ for internal tools vs production pages?
What's the recommended pattern for internal tool XControllers?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for internal tool implementations
```

### Understanding GK/QE

```
## Context
I'm adding a new feature that needs to be behind a feature flag.
Not sure whether to use GK, QE, or JK.

## Question
When should I use GK vs QE vs JK for feature gating?
What's the process for creating a new GK?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for feature flag usage
```

## Policy/Process Examples

### Code Review Requirements

```
## Context
I'm making changes to a privacy-sensitive component.
Need to understand what approvals are required.

## Working Environment
- Repo: www
- Recent files: flib/privacy/

## Question
What's the code review policy for privacy-sensitive changes?
Are there specific oncalls or teams I need approval from?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for similar privacy changes
```

### GraphQL Schema Changes

```
## Context
I need to add a new field to an existing GraphQL type.
The field exposes user data.

## Question
What's the policy for adding new GraphQL fields?
What privacy review is required for fields exposing user data?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs for GraphQL field additions
```

## Architecture/Design Examples

### Choosing Between Approaches

```
## Context
I'm adding a new feature that requires storing user preferences.
Trying to decide between using Ent framework vs TAO directly.

## Question
What are the tradeoffs between Ent and TAO for simple key-value user preferences?
When should I use one vs the other?

## Research Hints
To personalize your response, please also consider:
- My team's recent diffs to see which approach we typically use
```

### Service Architecture

```
## Context
I need to call an external service from my GraphQL mutation.
Trying to understand the best pattern for service-to-service communication.

## Question
What's the recommended pattern for calling Thrift services from GraphQL mutations?
How should I handle errors and retries?

## Research Hints
To personalize your response, please also consider:
- My team's diffs for service integration patterns
```

## Simple Lookup Examples

For straightforward questions where context isn't necessary:

### Acronym/Term Lookup

```
Search the wiki, Workplace groups, Google Docs, and other internal sources for:
What is MAST and how is it used at Meta?
```

### Quick Policy Check

```
Search the wiki, Workplace groups, Google Docs, and other internal sources for:
What are the oncall responsibilities for the Ads Infrastructure team?
```

### Documentation Lookup

```
Search the wiki, Workplace groups, Google Docs, and other internal sources for:
Where is the documentation for the Ent privacy system?
```

## Anti-Patterns (What NOT to Do)

### Too Vague

```
## Question
How do I fix my code?
```

### No Context

```
## Question
Why am I getting an error?
```

### Too Long

```
## Context
[500 words of background about the project history...]
```

### Dumping Code

```
## Context
Here's my entire 200-line file:
[entire file contents]
```

### Including New/Local Files

```
## Working Environment
- Recent files: flib/my/new/file/that/only/exists/locally.php
```
(Metamate can't see files that only exist locally—only committed files)
