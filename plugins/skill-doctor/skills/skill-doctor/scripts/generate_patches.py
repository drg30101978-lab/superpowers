#!/usr/bin/env python3
"""
Generate patch suggestions for each identified gap in the patient skill.

This script creates structured patch data that the LLM uses to produce actual diffs.
It groups related gaps and generates patch metadata with context.
"""

import json
import argparse
from pathlib import Path
from collections import defaultdict


def group_gaps_by_location(gaps: list) -> dict:
    """Group gaps that affect the same section of the skill."""
    groups = defaultdict(list)
    for gap in gaps:
        location = gap.get('existing_location') or gap.get('suggested_section', 'new-section')
        groups[location].append(gap)
    return dict(groups)


def generate_patch_metadata(group_key: str, gaps: list) -> dict:
    """Generate patch metadata for a group of related gaps."""
    # Determine the highest severity in the group
    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    max_severity = min(gaps, key=lambda g: severity_order.get(g.get('severity', 'low'), 9))['severity']

    total_occurrences = sum(g.get('occurrence_count', 1) for g in gaps)

    categories = list(set(g['category'] for g in gaps))
    friction_types = list(set(g['friction_type'] for g in gaps))

    # Collect user messages as evidence
    evidence = []
    for g in gaps:
        msg = g.get('user_message', '')
        if msg and msg not in evidence:
            evidence.append(msg[:200])

    return {
        'patch_id': f"patch-{hash(group_key) % 10000:04d}",
        'target_location': group_key,
        'gap_type': gaps[0].get('gap_type', 'missing'),
        'severity': max_severity,
        'total_occurrences': total_occurrences,
        'categories': categories,
        'friction_types': friction_types,
        'gap_count': len(gaps),
        'evidence': evidence[:5],
        'fix_suggestions': [g.get('suggested_fix', '') for g in gaps],
        'friction_event_ids': [g['friction_event_id'] for g in gaps],
        'instruction_for_llm': generate_instruction(group_key, gaps),
    }


def generate_instruction(group_key: str, gaps: list) -> str:
    """Generate instructions for the LLM to create the actual patch content."""
    gap_type = gaps[0].get('gap_type', 'missing')
    categories = list(set(g['category'] for g in gaps))

    if gap_type == 'missing':
        return (
            f"ADD a new rule/section to the skill. Location hint: {group_key}.\n"
            f"Categories to address: {', '.join(categories)}.\n"
            f"The rule should:\n"
            f"- Explain WHY this behavior matters (not just WHAT to do)\n"
            f"- Be concise (1-3 sentences)\n"
            f"- Be general enough to cover similar situations, not just the exact case\n"
            f"- Include the reasoning so the model can apply judgment in edge cases\n"
            f"Evidence from user corrections:\n"
            + '\n'.join(f'  - "{g.get("user_message", "")[:150]}"' for g in gaps if g.get('user_message'))
        )
    else:
        existing = gaps[0].get('existing_rule', '')
        return (
            f"STRENGTHEN the existing rule at {group_key}.\n"
            f"Current rule preview: {existing[:200]}\n"
            f"Categories to address: {', '.join(categories)}.\n"
            f"The current rule is insufficient because:\n"
            + '\n'.join(f'  - {g.get("explanation", "")[:150]}' for g in gaps)
            + f"\nMake the rule more specific while keeping it concise. "
            f"Add the missing nuance without making it rigid."
        )


def main():
    parser = argparse.ArgumentParser(description='Generate patch metadata for skill gaps')
    parser.add_argument('gaps_file', help='Path to gaps.json from cross_reference.py')
    parser.add_argument('skill_path', help='Path to the patient skill SKILL.md')
    parser.add_argument('--output', '-o', required=True, help='Output directory for patch files')
    args = parser.parse_args()

    with open(args.gaps_file) as f:
        data = json.load(f)

    gaps = data['gaps']
    if not gaps:
        print("No gaps found — nothing to patch.", file=__import__('sys').stderr)
        return

    # Group gaps by location
    grouped = group_gaps_by_location(gaps)

    # Generate patch metadata for each group
    patches = []
    for group_key, group_gaps in grouped.items():
        patch = generate_patch_metadata(group_key, group_gaps)
        patches.append(patch)

    # Sort patches by severity
    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    patches.sort(key=lambda p: severity_order.get(p['severity'], 9))

    # Write individual patch files and index
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    for patch in patches:
        patch_file = output_dir / f"{patch['patch_id']}.json"
        with open(str(patch_file), 'w') as f:
            json.dump(patch, f, indent=2, ensure_ascii=False)

    # Write index
    index = {
        'patient_skill': args.skill_path,
        'total_patches': len(patches),
        'patches': [{
            'patch_id': p['patch_id'],
            'target_location': p['target_location'],
            'severity': p['severity'],
            'gap_count': p['gap_count'],
            'total_occurrences': p['total_occurrences'],
            'categories': p['categories'],
        } for p in patches],
    }
    with open(str(output_dir / 'index.json'), 'w') as f:
        json.dump(index, f, indent=2, ensure_ascii=False)

    import sys
    print(f"Generated {len(patches)} patch files in {args.output}", file=sys.stderr)
    for p in patches:
        print(f"  {p['patch_id']} [{p['severity']}] {p['target_location']} "
              f"({p['gap_count']} gaps, {p['total_occurrences']} occurrences)", file=sys.stderr)


if __name__ == '__main__':
    main()
