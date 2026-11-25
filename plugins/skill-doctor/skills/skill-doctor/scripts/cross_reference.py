#!/usr/bin/env python3
"""
Cross-reference categorized friction events against a patient skill's SKILL.md
to identify which rules are missing or insufficient.

This script does a text-based search of the skill file for keywords related to
each friction event. The LLM should review and refine the output, as automated
keyword matching is a rough heuristic.
"""

import json
import re
import argparse
from pathlib import Path


def load_skill_sections(skill_path: str) -> list[dict]:
    """Parse a SKILL.md into sections by heading."""
    with open(skill_path) as f:
        content = f.read()

    lines = content.split('\n')
    sections = []
    current_section = {'heading': 'frontmatter', 'level': 0, 'start_line': 1, 'lines': []}

    in_frontmatter = False
    for i, line in enumerate(lines, 1):
        if line.strip() == '---':
            if i <= 2:
                in_frontmatter = True
                current_section['lines'].append(line)
                continue
            elif in_frontmatter:
                in_frontmatter = False
                current_section['lines'].append(line)
                sections.append(current_section)
                current_section = {'heading': 'body', 'level': 0, 'start_line': i + 1, 'lines': []}
                continue

        if in_frontmatter:
            current_section['lines'].append(line)
            continue

        heading_match = re.match(r'^(#{1,6})\s+(.+)', line)
        if heading_match:
            if current_section['lines'] or current_section['heading'] != 'body':
                sections.append(current_section)
            level = len(heading_match.group(1))
            current_section = {
                'heading': heading_match.group(2).strip(),
                'level': level,
                'start_line': i,
                'lines': [line],
            }
        else:
            current_section['lines'].append(line)

    if current_section['lines']:
        sections.append(current_section)

    return sections


def extract_keywords_from_event(event: dict) -> list[str]:
    """Extract searchable keywords from a friction event."""
    keywords = []

    # From user message
    user_msg = event.get('user_message', '') or ''
    # Extract meaningful words (skip common words)
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
                  'do', 'does', 'did', 'not', 'no', 'yes', 'and', 'or', 'but',
                  'in', 'on', 'at', 'to', 'for', 'of', 'with', 'that', 'this',
                  'it', 'i', 'you', 'we', 'they', 'my', 'your', 'our'}
    words = re.findall(r'\b[a-zA-Z]{3,}\b', user_msg.lower())
    keywords.extend([w for w in words if w not in stop_words])

    # From tool name
    tool = event.get('tool_name') or ''
    if tool and tool != '__error__':
        keywords.append(tool.lower())

    # From assistant action
    action = event.get('assistant_action') or {}
    if isinstance(action, dict):
        for t in action.get('tools', []):
            keywords.append(t.lower())
        action_text = action.get('text', '')
        words = re.findall(r'\b[a-zA-Z]{3,}\b', action_text.lower())
        keywords.extend([w for w in words if w not in stop_words])

    # From category
    category = event.get('category', '')
    keywords.append(category)

    # From error output
    error = event.get('error_output', '') or ''
    words = re.findall(r'\b[a-zA-Z]{3,}\b', error.lower())
    keywords.extend([w for w in words[:10] if w not in stop_words])

    return list(set(keywords))


def search_sections_for_keywords(sections: list[dict], keywords: list[str]) -> list[dict]:
    """Find sections that mention any of the keywords."""
    matches = []
    for section in sections:
        section_text = '\n'.join(section['lines']).lower()
        matched_keywords = [k for k in keywords if k in section_text]
        if matched_keywords:
            matches.append({
                'heading': section['heading'],
                'start_line': section['start_line'],
                'matched_keywords': matched_keywords,
                'match_count': len(matched_keywords),
                'text_preview': '\n'.join(section['lines'])[:300],
            })
    matches.sort(key=lambda x: -x['match_count'])
    return matches


def determine_gap(event: dict, matching_sections: list[dict]) -> dict:
    """Determine if a friction event represents a missing or insufficient rule."""
    category = event.get('category', '')
    severity = event.get('severity', 'low')

    if not matching_sections:
        return {
            'gap_type': 'missing',
            'existing_rule': None,
            'existing_location': None,
            'suggested_section': suggest_section(category),
        }
    else:
        best = matching_sections[0]
        return {
            'gap_type': 'insufficient',
            'existing_rule': best['text_preview'],
            'existing_location': f"Line {best['start_line']}: ## {best['heading']}",
            'matched_keywords': best['matched_keywords'],
        }


def suggest_section(category: str) -> str:
    """Suggest where in the SKILL.md a new rule should be added."""
    suggestions = {
        'missing-rule': 'Add a new section or subsection with specific guidance',
        'insufficient-rule': 'Strengthen the existing rule with more specificity',
        'wrong-scope': 'Add a "Scope" or "Boundaries" section',
        'skipped-step': 'Add to the workflow/checklist section',
        'wrong-approach': 'Add to an "Approach" or "Strategy" section',
        'missing-context': 'Add a "Before you start" or "Prerequisites" section',
        'premature-action': 'Add to the workflow ordering — emphasize reading before acting',
        'config-gap': 'Add a "Setup" or "Configuration" section',
        'communication-gap': 'Add guidance on when to explain actions to the user',
        'repetition': 'Strengthen the relevant existing rule or add enforcement',
    }
    return suggestions.get(category, 'Add a new section')


def main():
    parser = argparse.ArgumentParser(description='Cross-reference friction events against skill')
    parser.add_argument('categorized', help='Path to categorized.json')
    parser.add_argument('skill_path', help='Path to the patient skill SKILL.md')
    parser.add_argument('--output', '-o', required=True, help='Output JSON file path')
    args = parser.parse_args()

    with open(args.categorized) as f:
        data = json.load(f)

    sections = load_skill_sections(args.skill_path)

    gaps = []
    for event in data['events']:
        keywords = extract_keywords_from_event(event)
        matching_sections = search_sections_for_keywords(sections, keywords)
        gap_info = determine_gap(event, matching_sections)

        gaps.append({
            'friction_event_id': event['id'],
            'friction_type': event['type'],
            'category': event['category'],
            'severity': event.get('severity', 'low'),
            'user_message': event.get('user_message', ''),
            'explanation': event.get('explanation', ''),
            'keywords': keywords[:15],
            **gap_info,
            'suggested_fix': generate_fix_suggestion(event, gap_info),
            'occurrence_count': 1,  # Will be aggregated below
        })

    # Aggregate: count similar gaps
    pattern_counts = {}
    for gap in gaps:
        pattern_key = (gap['category'], gap['gap_type'],
                       gap.get('existing_location') or gap.get('suggested_section', ''))
        pattern_counts[pattern_key] = pattern_counts.get(pattern_key, 0) + 1

    for gap in gaps:
        pattern_key = (gap['category'], gap['gap_type'],
                       gap.get('existing_location') or gap.get('suggested_section', ''))
        gap['occurrence_count'] = pattern_counts[pattern_key]
        # Upgrade severity based on occurrence
        if gap['occurrence_count'] >= 3:
            gap['severity'] = 'critical'
        elif gap['occurrence_count'] >= 2 and gap['severity'] == 'low':
            gap['severity'] = 'medium'

    # Sort by severity then occurrence count
    severity_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    gaps.sort(key=lambda g: (severity_order.get(g['severity'], 9), -g['occurrence_count']))

    result = {
        'patient_skill': args.skill_path,
        'total_gaps': len(gaps),
        'gaps_by_type': {
            'missing': sum(1 for g in gaps if g['gap_type'] == 'missing'),
            'insufficient': sum(1 for g in gaps if g['gap_type'] == 'insufficient'),
        },
        'gaps_by_severity': {},
        'gaps': gaps,
    }
    for g in gaps:
        s = g['severity']
        result['gaps_by_severity'][s] = result['gaps_by_severity'].get(s, 0) + 1

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output_path), 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    import sys
    print(f"Found {len(gaps)} gaps:", file=sys.stderr)
    print(f"  Missing rules: {result['gaps_by_type']['missing']}", file=sys.stderr)
    print(f"  Insufficient rules: {result['gaps_by_type']['insufficient']}", file=sys.stderr)
    for sev, count in sorted(result['gaps_by_severity'].items()):
        print(f"  {sev}: {count}", file=sys.stderr)
    print(f"Output written to {args.output}", file=sys.stderr)


def generate_fix_suggestion(event: dict, gap_info: dict) -> str:
    """Generate a natural language fix suggestion."""
    category = event.get('category', '')
    user_msg = event.get('user_message', '') or ''
    gap_type = gap_info.get('gap_type', '')

    if gap_type == 'missing':
        return (f"Add a rule addressing '{category}': "
                f"Based on friction where user said: \"{user_msg[:100]}\". "
                f"{gap_info.get('suggested_section', '')}")
    else:
        return (f"Strengthen existing rule at {gap_info.get('existing_location', 'unknown')}: "
                f"Current rule didn't prevent '{category}' friction. "
                f"User feedback: \"{user_msg[:100]}\"")


if __name__ == '__main__':
    main()
