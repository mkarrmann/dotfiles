# Cross-Stack Analysis Reference

Detailed analysis patterns for Phase 2 (Analyze) of diff-stack-review. This file covers both per-diff analysis lenses and cross-stack-specific patterns.

## Per-Diff Analysis Lenses

While analyzing each diff individually, you have already read ALL diffs in the stack. Use that knowledge. If D1 introduces a function and D3 calls it incorrectly, that's a finding on D3.

### Logic & Reasoning Flaws

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **Circular Reasoning** | Solution depends on itself. "We'll handle errors by handling them properly." | Major |
| **Hand-Waving Complexity** | Hard problems treated as trivial in comments or TODOs. "Just sync the distributed state." | Critical |
| **False Equivalence** | Treating dev/test behavior as proof of production behavior. | Critical |
| **Survivorship Bias** | Only testing/handling the happy path. No error paths exercised. | Critical |
| **Unstated Assumptions** | Critical assumptions not documented or enforced. Assuming input format, network availability. | Major |

### Completeness Gaps

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **Missing Error Handling** | Unchecked return values, bare try/catch, no timeout on external calls. | Critical |
| **Missing Input Validation** | User/external input used without sanitization or type checking. | Critical |
| **No Rollback Strategy** | Schema migrations without down migration, feature flags that can't be disabled. | Critical |
| **Missing Edge Cases** | Empty collections, null values, concurrent access, boundary values. | Major |
| **Undefined Behavior** | No specification for double-call, crash mid-operation, or race condition. | Major |

### Scale & Performance

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **N+1 Query** | Database/API calls inside loops. | Major |
| **Memory Bomb** | Loading unbounded data into memory. Reading entire files, fetching all rows. | Critical |
| **Unbounded Growth** | Caches without eviction, logs without rotation, queues without bounds. | Critical |
| **Blocking Operations** | Synchronous external calls in request handlers or hot paths. | Major |
| **No Rate Limiting** | Public or expensive endpoints without throttling. | Critical |

### Security

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **Injection** | String interpolation in SQL, shell commands, or file paths with user input. | Critical |
| **Exposed Secrets** | Hardcoded API keys, passwords, tokens. Credentials in logs/errors. | Critical |
| **Missing Authentication** | Protected operations accessible without auth check. | Critical |
| **Missing Authorization** | Auth check present but no permission/ownership verification. | Critical |

### Testing Gaps

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **No Tests** | New production code with zero test coverage. | Major |
| **Tests That Don't Test** | Assertions that only check `!= null` or `assertTrue(true)`. | Major |
| **Missing Integration Tests** | Components tested in isolation but not together. | Major |
| **Test Plan Mismatch** | Test plan claims coverage that doesn't exist in actual test code. | Major |
| **Weak Test Plan** | Test plan is vague ("tested locally"), names no specific tests. | Minor |

### Maintenance & Operations

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **No Monitoring** | New features without logging, metrics, or alerting. | Major |
| **Single Point of Failure** | Critical path with no redundancy or fallback. | Critical |
| **No Versioning Strategy** | API/schema changes breaking backward compatibility. | Critical |
| **God Function** | One function doing too many things (>100 lines, >3 responsibilities). | Major |
| **Copy-Paste Code** | Same logic duplicated across multiple locations. | Major |

### Code Quality

| Pattern | What to Look For | Default Severity |
|---------|-----------------|------------------|
| **Magic Numbers** | Unexplained constants: `if len(x) > 73: sleep(2.5)` | Minor |
| **Premature Optimization** | Complex optimization for cold path while hot paths remain unoptimized. | Minor |
| **Dead Code** | New code that's unreachable or never called. | Minor |
| **Inconsistent Naming** | New identifiers that don't match codebase naming conventions. | Minor |

## Cross-Stack Analysis Steps

These patterns ONLY emerge from seeing multiple diffs together. This is the unique value of stack review. If your cross-stack analysis produces zero findings on a multi-diff stack with coupled files, re-examine more carefully. However, a well-partitioned stack may legitimately have few cross-stack issues.

### Step 1: Trace Data Flow Across Diffs

For each new type, struct, function, or API introduced in the stack, trace its lifecycle:
- Where is it defined? (which diff)
- Where is it called/used? (which diffs)
- What are the implicit contracts? (parameter types, return values, error behavior, nullability)
- Do all callers honor the contract as defined?

### Step 2: Trace Shared File Mutations

For each file in the overlap map that appears in 2+ diffs:
- Read the complete diff for that file from EACH diff
- Identify which functions/methods/classes each diff modifies
- Check for: conflicting modifications to the same function, one diff adding code that another's changes make unreachable, shared state mutations that interact badly
- Pay special attention to files touched by 3+ diffs — highest-risk areas

### Step 3: Check Dependency Ordering

Walk the stack bottom-up and verify:
- Does each diff only use symbols/APIs that exist at its position in the stack?
- D2 adds a new class/function — does D1 (lower) try to use it? Breaks if D1 lands alone.
- D1 removes or renames something — do higher diffs still reference the old name?

### Step 4: Evaluate Test Architecture

- Map which diffs contain test files and which contain only production code
- For each production-code diff, identify whether tests exist in the same diff, a different diff, or nowhere
- Flag structural fragility: if dropping or reordering any single diff would leave production code untested

### Step 5: Check for Emergent Behavior

Consider the stack's combined effect:
- Does the full set of changes create a new execution path that no individual diff tests?
- Do the diffs together introduce a concurrency pattern whose correctness depends on all diffs being present?
- Is there a failure mode that only exists when all diffs interact? (e.g., D1 adds a cache, D3 adds a write path — cache invalidation needed but nobody added it)

## Named Cross-Stack Patterns

For categorizing findings from the steps above:

| Pattern | Description | Severity |
|---------|-------------|----------|
| **API Contract Drift** | Function signature, return type, or behavior changed in one diff but callers in another use the old contract. | Critical (runtime error) or Major (behavior change) |
| **Shared File Conflicts** | Multiple diffs make conflicting modifications to the same function or shared state. | Major (conflicting logic) or Minor (independent changes) |
| **Dependency Ordering Issues** | A diff uses something that doesn't exist at its stack position. | Critical (breaks if diffs land out of order) |
| **Test Coverage Gaps Across Stack** | Production code in D1-D3 with tests only in D4. Structurally fragile. | Major |
| **Assumption Invalidation** | Changes in one diff silently break assumptions in another. | Major |
| **Cumulative Complexity** | Simple diffs that combine into emergent complexity needing integration testing. | Major |
| **Context Propagation** | A finding in one diff changes severity based on what other diffs do. | Varies |

## Severity Calibration

- **Critical**: Will cause production failures, security vulnerabilities, data loss
- **Major**: Significant quality/performance/maintainability issues under real conditions
- **Minor**: Refinements, code quality improvements, non-critical edge cases

When in doubt, downgrade severity. False alarms erode trust faster than missed minors.
