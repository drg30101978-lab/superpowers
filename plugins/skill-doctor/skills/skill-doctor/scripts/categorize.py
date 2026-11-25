#!/usr/bin/env python3
"""
Categorize friction events using heuristics based on context analysis.

Categories are defined in references/friction_taxonomy.md.
This provides an automated first pass — the LLM should review and refine.
"""

import json
import re
import argparse
from pathlib import Path


CATEGORIES = {
    'missing-rule': 'Skill has no instruction covering this situation',
    'insufficient-rule': 'Rule exists but is too vague or weak',
    'wrong-scope': 'Agent acted outside the requested scope',
    'skipped-step': 'Agent skipped a required workflow step',
    'wrong-approach': 'Agent chose a suboptimal strategy',
    'missing-context': 'Agent lacked project-specific knowledge',
    'premature-action': 'Agent acted before gathering enough info',
    'config-gap': 'Missing configuration or environment setup',
    'communication-gap': 'Agent didn\'t explain what it was doing',
    'repetition': 'Same friction pattern occurred multiple times',
}

# Keywords that hint at specific categories
SCOPE_KEYWORDS = [
    r'\bjust\b', r'\bonly\b', r'\bdon\'t touch\b', r'\bleave .* alone\b',
    r'\btoo much\b', r'\bdidn\'t ask\b', r'\bnot related\b',
]

APPROACH_KEYWORDS = [
    r'\binstead\b', r'\buse .* not\b', r'\bthat\'s not how\b',
    r'\bwrong way\b', r'\bnot like that\b', r'\bdon\'t use\b',
]

CONTEXT_KEYWORDS = [
    r'\bdid you read\b', r'\bcheck the\b', r'\blook at\b',
    r'\balready exists\b', r'\bthere\'s already\b',
]

CONFIG_KEYWORDS = [
    r'\benv\b', r'\bconfig\b', r'\benvironment\b', r'\bvariable\b',
    r'\bport\b', r'\bpath\b', r'\bsetup\b', r'\binstall\b',
    r'DATABASE_URL', r'API_KEY', r'\.env\b',
]

STEP_KEYWORDS = [
    r'\bfirst\b', r'\bbefore\b', r'\bforgot to\b', r'\bshould have\b',
    r'\bdidn\'t run\b', r'\bskipped\b', r'\bmissed\b',
]


def categorize_event(event: dict) -> tuple[str, str]:
    """Assign a category and explanation to a friction event. Returns (category, explanation)."""

    etype = event.get('type', '')
    user_msg = (event.get('user_message') or '').lower()
    error_output = (event.get('error_output') or '').lower()
    raw_error = (event.get('raw_error') or '').lower()
    combined_text = f"{user_msg} {error_output} {raw_error}"

    # Retry loops are clearly repetition
    if etype == 'retry_loop':
        tool = event.get('tool_name', 'unknown')
        count = event.get('retry_count', 0)
        return 'repetition', f'Tool "{tool}" called {count} times in quick succession, suggesting a retry loop'

    # Test failures — check if config-related
    if etype == 'test_failure':
        if any(re.search(pat, combined_text, re.IGNORECASE) for pat in CONFIG_KEYWORDS):
            return 'config-gap', f'Test failure related to configuration: {error_output[:200]}'
        return 'skipped-step', f'Test/command failure suggests a prerequisite step was missed: {error_output[:200]}'

    # For user corrections and rejections, analyze the user's message
    if etype in ('user_correction', 'user_rejection', 'user_interruption'):
        # Check scope issues
        if any(re.search(pat, user_msg, re.IGNORECASE) for pat in SCOPE_KEYWORDS):
            return 'wrong-scope', f'User indicated agent went beyond requested scope: "{user_msg[:200]}"'

        # Check approach issues
        if any(re.search(pat, user_msg, re.IGNORECASE) for pat in APPROACH_KEYWORDS):
            return 'wrong-approach', f'User redirected agent to different approach: "{user_msg[:200]}"'

        # Check missing context
        if any(re.search(pat, user_msg, re.IGNORECASE) for pat in CONTEXT_KEYWORDS):
            return 'missing-context', f'User pointed to existing information agent missed: "{user_msg[:200]}"'

        # Check config issues
        if any(re.search(pat, user_msg, re.IGNORECASE) for pat in CONFIG_KEYWORDS):
            return 'config-gap', f'User correction related to configuration: "{user_msg[:200]}"'

        # Check skipped steps
        if any(re.search(pat, user_msg, re.IGNORECASE) for pat in STEP_KEYWORDS):
            return 'skipped-step', f'User indicated a step was skipped: "{user_msg[:200]}"'

        # For interruptions without clear category, it's often premature action
        if etype == 'user_interruption':
            return 'premature-action', f'User interrupted agent mid-action: "{user_msg[:200]}"'

        # For rejections without clear category, often wrong approach
        if etype == 'user_rejection':
            return 'wrong-approach', f'User rejected agent action: "{user_msg[:200]}"'

        # Default for corrections
        return 'missing-rule', f'User correction suggests missing guidance: "{user_msg[:200]}"'

    # Fallback
    return 'missing-rule', f'Friction event of type "{etype}" — needs manual review'


def detect_repetition_patterns(events: list) -> list:
    """Find events that share the same pattern across the session(s)."""
    # Group by (type, category, tool_name)
    patterns = {}
    for e in events:
        key = (e.get('type'), e.get('category'), e.get('tool_name'))
        if key not in patterns:
            patterns[key] = []
        patterns[key].append(e['id'])

    repeated = set()
    for key, ids in patterns.items():
        if len(ids) >= 2:
            for eid in ids:
                repeated.add(eid)

    return repeated


def main():
    parser = argparse.ArgumentParser(description='Categorize friction events')
    parser.add_argument('input', help='Path to friction_events.json from parse_session.py')
    parser.add_argument('--output', '-o', required=True, help='Output JSON file path')
    args = parser.parse_args()

    with open(args.input) as f:
        data = json.load(f)

    events = data['events']

    # First pass: categorize each event
    for event in events:
        category, explanation = categorize_event(event)
        event['category'] = category
        event['explanation'] = explanation

    # Second pass: detect repetition patterns
    repeated_ids = detect_repetition_patterns(events)
    for event in events:
        if event['id'] in repeated_ids:
            event['is_repeated'] = True
            if event['category'] != 'repetition':
                event['original_category'] = event['category']
                # Upgrade severity for repeated patterns but keep original category
                event['repeated'] = True

    # Compute severity
    for event in events:
        if event.get('is_repeated') or event.get('repeated'):
            event['severity'] = 'high'
        elif event.get('correction_score', 0) >= 2:
            event['severity'] = 'high'
        elif event['type'] in ('user_rejection', 'user_interruption'):
            event['severity'] = 'medium'
        elif event['type'] == 'test_failure':
            event['severity'] = 'medium'
        else:
            event['severity'] = 'low'

    # Summary
    by_category = {}
    for e in events:
        cat = e['category']
        by_category[cat] = by_category.get(cat, 0) + 1

    data['events'] = events
    data['events_by_category'] = by_category
    data['repeated_pattern_count'] = len(repeated_ids)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output_path), 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Categorized {len(events)} events:", file=__import__('sys').stderr)
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}", file=__import__('sys').stderr)
    print(f"Repeated patterns: {len(repeated_ids)} events", file=__import__('sys').stderr)
    print(f"Output written to {args.output}", file=__import__('sys').stderr)


if __name__ == '__main__':
    main()
