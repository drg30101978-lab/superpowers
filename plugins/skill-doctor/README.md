# skill-doctor

A post-session diagnostic skill for Claude Code that analyzes session logs to find friction events, diagnoses skill gaps, generates patches with regression tests, and submits PRs to fix the patient skill.

## Installation

```bash
npx skills add https://github.com/sunfmin/skill-doctor
```

## What it does

After a development session where a skill caused friction (wrong approach, user corrections, test failures), run skill-doctor to:

1. **Parse** JSONL session logs and extract friction events (rejections, interruptions, corrections, failures, retry loops)
2. **Categorize** each event (missing rule, wrong scope, skipped step, config gap, etc.)
3. **Cross-reference** against the patient skill's SKILL.md to find which rules are missing or insufficient
4. **Generate patches** with explanatory comments for each gap
5. **Create regression tests** compatible with skill-creator's eval format
6. **Present a summary** for approval, then submit a PR to the patient skill's repo

## Usage

Invoke in Claude Code:

```
/skill-doctor
```

Or just say:
- "run skill-doctor on the last session"
- "fix the iterative-dev skill based on what went wrong"
- "audit the go-react-app skill"

## How it works

Claude Code stores session transcripts as JSONL files in `~/.claude/projects/`. The skill-doctor parses these to detect:

| Signal | Detection Method |
|--------|-----------------|
| User rejected an action | `tool_result` with `is_error: true` + rejection message |
| User interrupted | `[Request interrupted by user]` prefix |
| User corrected approach | Corrective language patterns after assistant actions |
| Tests failed | Non-zero exit codes with failure patterns |
| Retry loops | Same tool called 4+ times with errors in sequence |

Each friction event is categorized, cross-referenced against the skill's instructions, and turned into a concrete patch.

## License

MIT
