# Friction Event Taxonomy

This document defines the categories used to classify friction events extracted from session logs.

## Categories

### missing-rule
The patient skill has no instruction that covers the situation where friction occurred.
This is the most common gap — the skill author simply didn't anticipate this scenario.

**Signals:** The assistant took an action that seems reasonable in general but wrong for this specific
project/workflow. The user corrects with context that isn't in the skill.

**Example:** Skill says nothing about monorepo structure. Agent edits files in the wrong package.

### insufficient-rule
A rule exists in the skill but it's too vague, too weak, or missing important nuance.
The assistant may have technically followed the rule but still caused friction.

**Signals:** The assistant did something partially correct. The user's correction adds specificity
to something the skill already mentions.

**Example:** Skill says "run tests before committing" but doesn't specify which test suite.
Agent runs unit tests but misses integration tests.

### wrong-scope
The assistant acted outside the scope of what was requested. This often manifests as
"helpful" cleanup, refactoring, or improvements that the user didn't ask for.

**Signals:** User says "I didn't ask for that", "just do X", "too much", or rejects edits
to files unrelated to the task.

**Example:** User asks to fix a bug. Agent also refactors surrounding code and adds type annotations.

### skipped-step
The assistant skipped a step that the workflow requires. This could be a step in the skill's
own workflow or a project-specific convention.

**Signals:** Something fails downstream because a prerequisite wasn't met. Or the user points
out a missing step after the fact.

**Example:** Skill requires running the linter before committing. Agent commits without linting.

### wrong-approach
The assistant chose a strategy that doesn't match the project's conventions or the skill's
guidance. Different from missing-rule in that guidance exists but was misinterpreted or ignored.

**Signals:** User explicitly redirects the approach: "don't use X, use Y instead",
"that's not how we do it here".

**Example:** Skill says to use real database for tests. Agent uses mocks anyway.

### missing-context
The assistant lacked project-specific knowledge that it should have gathered before acting.
This is about information that exists in the codebase but wasn't read.

**Signals:** Agent makes assumptions that are contradicted by existing code. User says
"did you read X?" or "check the config".

**Example:** Agent doesn't know about a custom test runner configured in package.json.

### premature-action
The assistant started executing before gathering enough context. This is a sequencing problem —
the action itself might be correct but was taken too early.

**Signals:** Agent starts writing code before reading existing implementations.
Agent makes a plan without understanding the codebase structure.

**Example:** Agent creates a new utility function that already exists in the codebase.

### config-gap
Missing configuration, environment setup, or prerequisite that the skill should ensure
is in place before proceeding.

**Signals:** Commands fail due to missing env vars, missing dependencies, wrong ports,
or incorrect paths.

**Example:** Test runner needs DATABASE_URL set but skill doesn't mention this.

### communication-gap
The assistant didn't adequately communicate what it was doing or why, leading to user
confusion or premature interruption.

**Signals:** User interrupts to ask "what are you doing?" or "why?". User rejects an
action that would have been fine if explained first.

**Example:** Agent starts a large refactor without explaining the plan first.

### repetition
The same friction pattern occurs multiple times across different sessions. This is a
meta-category — the underlying cause is usually one of the above, but the fact that it
repeats makes it high-priority.

**Signals:** Same type of correction from the user across multiple sessions. Same test
failures recurring. Same tool rejections.

**Example:** User has corrected the agent 3 times across sessions about not using mocks.

## Severity Scoring

| Severity | Criteria |
|----------|----------|
| **critical** | Occurred 3+ times OR caused data loss / destructive action |
| **high** | Occurred 2+ times OR user expressed frustration |
| **medium** | Occurred once, required significant user intervention |
| **low** | Occurred once, minor course correction |

## Friction Detection Heuristics

When parsing JSONL logs, use these patterns to detect friction:

### Tool Rejections
```json
{"type": "tool_result", "is_error": true, "content": "The user doesn't want to proceed..."}
```

### User Interruptions
```
[Request interrupted by user for tool use]
[Request interrupted by user]
```

### Corrective User Messages
Look for these patterns in user messages that follow assistant actions:
- Negation: "no", "not that", "wrong", "nope", "stop"
- Redirection: "instead", "rather", "actually", "use X not Y"
- Frustration: "I said", "I already told you", "again", "why did you"
- Scope correction: "just", "only", "don't touch", "leave X alone"

### Test Failures
```
Exit code 1  (or any non-zero)
FAIL
Error:
AssertionError
Expected .* but received
```

### Retry Loops
Same tool called 3+ times within 10 messages with similar input patterns.

### Scope Violations
Assistant edits files not mentioned in the user's request, followed by user correction.
