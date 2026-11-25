#!/usr/bin/env python3
"""
Generate regression test cases for each patch.

Tests are formatted to be compatible with the skill-creator's evals/evals.json schema,
so they can be added directly to the patient skill's test suite.
"""

import json
import argparse
from pathlib import Path


def generate_test_for_patch(patch: dict) -> dict:
    """Generate a regression test case from a patch."""
    categories = patch.get('categories', [])
    evidence = patch.get('evidence', [])
    friction_types = patch.get('friction_types', [])

    # Build a realistic prompt that would trigger the friction
    prompt = build_test_prompt(categories, evidence, friction_types)

    # Define expected behavior (what should happen WITH the fix)
    expected = build_expected_behavior(categories, patch)

    # Define anti-pattern (what used to happen WITHOUT the fix)
    anti_pattern = build_anti_pattern(categories, evidence)

    # Build assertion
    assertion = build_assertion(categories, patch)

    return {
        'test_id': patch['patch_id'].replace('patch-', 'test-'),
        'patch_id': patch['patch_id'],
        'prompt': prompt,
        'expected_behavior': expected,
        'anti_pattern': anti_pattern,
        'assertion': assertion,
        'categories': categories,
        'severity': patch.get('severity', 'medium'),
    }


def build_test_prompt(categories: list, evidence: list, friction_types: list) -> str:
    """Build a realistic test prompt based on the friction pattern."""
    prompts_by_category = {
        'missing-rule': (
            "Give the agent a task that requires knowledge the skill doesn't currently provide. "
            "The agent should handle it correctly after the fix is applied."
        ),
        'insufficient-rule': (
            "Give the agent a task where the existing rule is ambiguous. "
            "The agent should follow the more specific guidance from the fix."
        ),
        'wrong-scope': (
            "Ask the agent to fix a specific bug. Verify it only touches the relevant code "
            "and doesn't refactor or 'improve' surrounding code."
        ),
        'skipped-step': (
            "Ask the agent to complete a workflow. Verify all required steps are executed "
            "in the correct order."
        ),
        'wrong-approach': (
            "Give the agent a task where multiple approaches exist. "
            "Verify it chooses the approach specified in the skill."
        ),
        'missing-context': (
            "Give the agent a task in a project with existing conventions. "
            "Verify it reads and follows existing patterns before writing new code."
        ),
        'premature-action': (
            "Give the agent a task that requires understanding existing code. "
            "Verify it reads the codebase before making changes."
        ),
        'config-gap': (
            "Give the agent a task that requires specific environment setup. "
            "Verify it checks/sets up the required configuration before proceeding."
        ),
        'communication-gap': (
            "Give the agent a large or potentially surprising task. "
            "Verify it explains its plan before executing."
        ),
        'repetition': (
            "Run the same scenario that previously caused repeated friction. "
            "Verify the fix prevents the pattern from recurring."
        ),
    }

    base_prompt = prompts_by_category.get(categories[0] if categories else '', 'Test the fix.')

    if evidence:
        base_prompt += f"\n\nOriginal user feedback that triggered this fix:\n"
        for e in evidence[:3]:
            base_prompt += f'- "{e}"\n'

    return base_prompt


def build_expected_behavior(categories: list, patch: dict) -> str:
    """Describe what should happen with the fix applied."""
    expected_by_category = {
        'missing-rule': 'Agent follows the new rule and handles the situation correctly',
        'insufficient-rule': 'Agent follows the strengthened rule with proper specificity',
        'wrong-scope': 'Agent stays within the scope of the request',
        'skipped-step': 'Agent executes all required workflow steps in order',
        'wrong-approach': 'Agent uses the correct approach as specified in the skill',
        'missing-context': 'Agent reads existing code/config before acting',
        'premature-action': 'Agent gathers context before making changes',
        'config-gap': 'Agent validates configuration before proceeding',
        'communication-gap': 'Agent explains its plan before executing large changes',
        'repetition': 'Agent does not repeat the previously-corrected mistake',
    }
    cat = categories[0] if categories else ''
    return expected_by_category.get(cat, 'Agent handles the situation without friction')


def build_anti_pattern(categories: list, evidence: list) -> str:
    """Describe what used to happen (the friction behavior)."""
    anti_by_category = {
        'missing-rule': 'Agent had no guidance and made incorrect assumptions',
        'insufficient-rule': 'Agent followed vague rule and produced suboptimal result',
        'wrong-scope': 'Agent modified code outside the requested scope',
        'skipped-step': 'Agent skipped required steps in the workflow',
        'wrong-approach': 'Agent chose wrong approach despite available guidance',
        'missing-context': 'Agent acted without reading existing code first',
        'premature-action': 'Agent started coding before understanding the codebase',
        'config-gap': 'Agent proceeded without required configuration',
        'communication-gap': 'Agent made large changes without explaining',
        'repetition': 'Agent repeated the same mistake despite prior correction',
    }
    cat = categories[0] if categories else ''
    base = anti_by_category.get(cat, 'Agent caused friction')

    if evidence:
        base += f'. User had to correct: "{evidence[0][:100]}"'
    return base


def build_assertion(categories: list, patch: dict) -> str:
    """Build a verifiable assertion for the test."""
    assertion_by_category = {
        'missing-rule': 'Agent output follows the new rule added by the patch',
        'insufficient-rule': 'Agent output shows increased specificity matching the strengthened rule',
        'wrong-scope': 'Agent only modifies files mentioned in the request (check git diff)',
        'skipped-step': 'All workflow steps appear in the agent transcript in correct order',
        'wrong-approach': 'Agent uses the specified approach (check tool calls)',
        'missing-context': 'Agent reads relevant files before editing (check tool call order)',
        'premature-action': 'Read/Glob/Grep tool calls precede Edit/Write calls in transcript',
        'config-gap': 'Agent checks or sets up configuration before main task execution',
        'communication-gap': 'Agent sends explanatory text before executing large changes',
        'repetition': 'The specific friction pattern does not recur in the transcript',
    }
    cat = categories[0] if categories else ''
    return assertion_by_category.get(cat, 'No friction event occurs during execution')


def main():
    parser = argparse.ArgumentParser(description='Generate regression tests for patches')
    parser.add_argument('gaps_file', help='Path to gaps.json')
    parser.add_argument('patches_dir', help='Path to patches directory')
    parser.add_argument('--output', '-o', required=True, help='Output JSON file')
    args = parser.parse_args()

    patches_dir = Path(args.patches_dir)
    index_file = patches_dir / 'index.json'
    if not index_file.exists():
        print("No patches/index.json found", file=__import__('sys').stderr)
        return

    with open(str(index_file)) as f:
        index = json.load(f)

    tests = []
    for patch_summary in index['patches']:
        patch_file = patches_dir / f"{patch_summary['patch_id']}.json"
        with open(str(patch_file)) as f:
            patch = json.load(f)
        test = generate_test_for_patch(patch)
        tests.append(test)

    # Format as skill-creator compatible evals
    evals = {
        'skill_name': Path(index.get('patient_skill', 'unknown')).parent.name,
        'generated_by': 'skill-doctor',
        'evals': [{
            'id': i,
            'test_id': t['test_id'],
            'patch_id': t['patch_id'],
            'prompt': t['prompt'],
            'expected_output': t['expected_behavior'],
            'anti_pattern': t['anti_pattern'],
            'assertions': [t['assertion']],
            'severity': t['severity'],
        } for i, t in enumerate(tests)],
    }

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output_path), 'w') as f:
        json.dump(evals, f, indent=2, ensure_ascii=False)

    import sys
    print(f"Generated {len(tests)} regression tests", file=sys.stderr)
    print(f"Output written to {args.output}", file=sys.stderr)


if __name__ == '__main__':
    main()
