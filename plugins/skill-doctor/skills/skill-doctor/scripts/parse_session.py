#!/usr/bin/env python3
"""
Parse Claude Code JSONL session logs and extract friction events.

Friction events are moments where something went wrong during a session:
- User rejected a tool action
- User interrupted the assistant
- User corrected the assistant's approach
- Tests or commands failed
- Assistant retried the same action multiple times
"""

import json
import sys
import argparse
import re
from pathlib import Path
from typing import Optional


# Patterns that indicate corrective user messages
CORRECTION_PATTERNS = [
    r'\bno[,.\s!]',
    r'\bnot that\b',
    r'\bwrong\b',
    r'\bstop\b',
    r'\binstead\b',
    r'\bactually[,\s]',
    r'\bdon\'t\b',
    r'\bdo not\b',
    r'\bnope\b',
    r'\bI said\b',
    r'\bI already\b',
    r'\bjust do\b',
    r'\bonly do\b',
    r'\bleave .* alone\b',
    r'\bdon\'t touch\b',
    r'\bwhy did you\b',
    r'\bthat\'s not\b',
    r'\buse .* not\b',
    r'\buse .* instead\b',
]

# Patterns that indicate test/command failures
FAILURE_PATTERNS = [
    r'Exit code [1-9]',
    r'\bFAIL\b',
    r'AssertionError',
    r'Error:.*\bat\b',
    r'FAILED',
    r'expected .* but received',
    r'expected .* to (be|equal|match|contain)',
    r'TypeError:',
    r'ReferenceError:',
    r'SyntaxError:',
    r'panic:',
    r'FAIL\s+\w+/',
    r'--- FAIL:',
]

INTERRUPTION_MARKERS = [
    '[Request interrupted by user for tool use]',
    '[Request interrupted by user]',
]

REJECTION_MARKERS = [
    "The user doesn't want to proceed with this tool use",
    "was rejected",
    "STOP what you are doing",
]


def parse_message_content(content):
    """Extract text and tool information from message content."""
    if isinstance(content, str):
        return content, []
    if isinstance(content, list):
        texts = []
        tools = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get('type', '')
            if btype == 'text':
                texts.append(block.get('text', ''))
            elif btype == 'tool_use':
                tools.append({
                    'name': block.get('name', ''),
                    'input': block.get('input', {}),
                    'id': block.get('id', ''),
                })
            elif btype == 'tool_result':
                result_content = block.get('content', '')
                if isinstance(result_content, str):
                    texts.append(result_content)
                elif isinstance(result_content, list):
                    for sub in result_content:
                        if isinstance(sub, dict) and sub.get('type') == 'text':
                            texts.append(sub.get('text', ''))
                if block.get('is_error'):
                    tools.append({
                        'name': '__error__',
                        'content': result_content if isinstance(result_content, str) else str(result_content),
                        'tool_use_id': block.get('tool_use_id', ''),
                    })
        return '\n'.join(texts), tools
    return '', []


def get_context(entries, idx, before=3, after=2):
    """Get surrounding messages for context."""
    context_before = []
    context_after = []

    for i in range(max(0, idx - before), idx):
        e = entries[i]
        msg = e.get('message', {})
        role = msg.get('role', '')
        text, _ = parse_message_content(msg.get('content', ''))
        if text and role:
            context_before.append({
                'role': role,
                'text': text[:500],
                'line': i,
            })

    for i in range(idx + 1, min(len(entries), idx + after + 1)):
        e = entries[i]
        msg = e.get('message', {})
        role = msg.get('role', '')
        text, _ = parse_message_content(msg.get('content', ''))
        if text and role:
            context_after.append({
                'role': role,
                'text': text[:500],
                'line': i,
            })

    return context_before, context_after


def find_preceding_assistant_action(entries, idx):
    """Find the assistant's action that preceded a friction event."""
    for i in range(idx - 1, max(0, idx - 10), -1):
        e = entries[i]
        msg = e.get('message', {})
        if msg.get('role') == 'assistant':
            text, tools = parse_message_content(msg.get('content', ''))
            if tools:
                tool_names = [t.get('name', '') for t in tools if t.get('name') != '__error__']
                tool_inputs = {t.get('name', ''): t.get('input', {}) for t in tools if t.get('name') != '__error__'}
                return {
                    'text': text[:300],
                    'tools': tool_names,
                    'tool_inputs': {k: str(v)[:200] for k, v in tool_inputs.items()},
                }
            elif text:
                return {'text': text[:300], 'tools': [], 'tool_inputs': {}}
    return None


def detect_retry_loops(entries):
    """Detect sequences where the same tool is called 3+ times with similar input AND fails.

    Normal agent work involves calling Read/Edit/Bash many times — that's not friction.
    A retry loop is when the same tool is called repeatedly on similar inputs because
    the previous attempt failed or produced wrong results. We detect this by requiring
    that at least one call in the sequence was followed by an error or the same tool
    was called again with very similar input (suggesting a retry, not new work).
    """
    loops = []
    window = 12

    # Collect all tool calls with their inputs and whether they errored
    tool_events = []
    for i, e in enumerate(entries):
        msg = e.get('message', {})
        if msg.get('role') != 'assistant':
            # Check if this is an error response to a tool
            content = msg.get('content', '')
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get('is_error'):
                        tool_events.append({
                            'line': i, 'type': 'error',
                            'tool_use_id': block.get('tool_use_id', ''),
                        })
            continue
        _, tools = parse_message_content(msg.get('content', ''))
        for t in tools:
            name = t.get('name', '')
            if name and name != '__error__':
                input_str = str(t.get('input', ''))[:200]
                tool_events.append({
                    'line': i, 'type': 'call', 'name': name,
                    'input_hash': hash(input_str),
                    'input_preview': input_str[:100],
                    'id': t.get('id', ''),
                })

    # Find sequences of same-tool calls where at least one had an error nearby
    error_tool_ids = {e['tool_use_id'] for e in tool_events if e['type'] == 'error'}

    for start_idx in range(len(tool_events)):
        if tool_events[start_idx]['type'] != 'call':
            continue
        name = tool_events[start_idx]['name']
        sequence = [tool_events[start_idx]]
        has_error = tool_events[start_idx].get('id', '') in error_tool_ids

        for j in range(start_idx + 1, min(start_idx + window, len(tool_events))):
            if tool_events[j]['type'] == 'error':
                has_error = True
                continue
            if tool_events[j]['type'] == 'call' and tool_events[j]['name'] == name:
                sequence.append(tool_events[j])
                if tool_events[j].get('id', '') in error_tool_ids:
                    has_error = True

        # Only flag as retry loop if: 4+ calls of same tool AND at least one error
        if len(sequence) >= 4 and has_error:
            loops.append({
                'tool_name': name,
                'count': len(sequence),
                'line_numbers': [s['line'] for s in sequence],
                'first_line': sequence[0]['line'],
            })

    # Deduplicate overlapping detections — merge loops that share lines
    if not loops:
        return []

    loops.sort(key=lambda x: x['first_line'])
    merged = [loops[0]]
    for loop in loops[1:]:
        prev = merged[-1]
        # If this loop overlaps with the previous one (same tool, overlapping lines)
        if (loop['tool_name'] == prev['tool_name'] and
                loop['first_line'] <= prev['line_numbers'][-1]):
            # Merge: extend the line numbers
            all_lines = sorted(set(prev['line_numbers'] + loop['line_numbers']))
            prev['line_numbers'] = all_lines
            prev['count'] = len(all_lines)
        else:
            merged.append(loop)

    return merged


def extract_friction_events(entries, source_file: str):
    """Extract all friction events from parsed JSONL entries."""
    events = []
    event_id = 0

    for idx, entry in enumerate(entries):
        msg = entry.get('message', {})
        role = msg.get('role', '')
        content = msg.get('content', '')
        text, tools = parse_message_content(content)
        timestamp = entry.get('timestamp', '')

        # 1. Tool rejections
        for tool in tools:
            if tool.get('name') == '__error__':
                tool_content = tool.get('content', '')
                if any(marker in tool_content for marker in REJECTION_MARKERS):
                    preceding = find_preceding_assistant_action(entries, idx)
                    ctx_before, ctx_after = get_context(entries, idx)
                    events.append({
                        'id': event_id,
                        'type': 'user_rejection',
                        'source_file': source_file,
                        'line_number': idx,
                        'timestamp': timestamp,
                        'context_before': ctx_before,
                        'context_after': ctx_after,
                        'tool_name': tool.get('tool_use_id', ''),
                        'user_message': '',
                        'assistant_action': preceding,
                        'raw_error': tool_content[:300],
                    })
                    event_id += 1

        # 2. User interruptions
        if role == 'user' and isinstance(text, str):
            for marker in INTERRUPTION_MARKERS:
                if marker in text:
                    preceding = find_preceding_assistant_action(entries, idx)
                    ctx_before, ctx_after = get_context(entries, idx)
                    # Get the actual user message after the interruption marker
                    user_msg = text.replace(marker, '').strip()
                    events.append({
                        'id': event_id,
                        'type': 'user_interruption',
                        'source_file': source_file,
                        'line_number': idx,
                        'timestamp': timestamp,
                        'context_before': ctx_before,
                        'context_after': ctx_after,
                        'tool_name': None,
                        'user_message': user_msg[:500],
                        'assistant_action': preceding,
                    })
                    event_id += 1
                    break

        # 3. User corrections (user message with corrective language after assistant action)
        if role == 'user' and isinstance(text, str) and len(text) > 3:
            # Skip if this is already captured as an interruption
            if not any(marker in text for marker in INTERRUPTION_MARKERS):
                correction_score = sum(
                    1 for pat in CORRECTION_PATTERNS
                    if re.search(pat, text, re.IGNORECASE)
                )
                if correction_score >= 2 and len(text) < 500:
                    # Verify there was a preceding assistant action
                    preceding = find_preceding_assistant_action(entries, idx)
                    if preceding:
                        ctx_before, ctx_after = get_context(entries, idx)
                        events.append({
                            'id': event_id,
                            'type': 'user_correction',
                            'source_file': source_file,
                            'line_number': idx,
                            'timestamp': timestamp,
                            'context_before': ctx_before,
                            'context_after': ctx_after,
                            'tool_name': None,
                            'user_message': text[:500],
                            'assistant_action': preceding,
                            'correction_score': correction_score,
                        })
                        event_id += 1

        # 4. Test/command failures
        for tool in tools:
            if tool.get('name') == '__error__':
                tool_content = tool.get('content', '')
                # Skip rejections (already handled)
                if any(marker in tool_content for marker in REJECTION_MARKERS):
                    continue
                if any(re.search(pat, tool_content, re.IGNORECASE) for pat in FAILURE_PATTERNS):
                    preceding = find_preceding_assistant_action(entries, idx)
                    ctx_before, ctx_after = get_context(entries, idx)
                    events.append({
                        'id': event_id,
                        'type': 'test_failure',
                        'source_file': source_file,
                        'line_number': idx,
                        'timestamp': timestamp,
                        'context_before': ctx_before,
                        'context_after': ctx_after,
                        'tool_name': preceding.get('tools', [None])[0] if preceding else None,
                        'user_message': None,
                        'assistant_action': preceding,
                        'error_output': tool_content[:500],
                    })
                    event_id += 1

    # 5. Retry loops
    retry_loops = detect_retry_loops(entries)
    for loop in retry_loops:
        ctx_before, ctx_after = get_context(entries, loop['first_line'])
        events.append({
            'id': event_id,
            'type': 'retry_loop',
            'source_file': source_file,
            'line_number': loop['first_line'],
            'timestamp': entries[loop['first_line']].get('timestamp', ''),
            'context_before': ctx_before,
            'context_after': ctx_after,
            'tool_name': loop['tool_name'],
            'user_message': None,
            'assistant_action': None,
            'retry_count': loop['count'],
            'retry_lines': loop['line_numbers'],
        })
        event_id += 1

    return events


def parse_jsonl_file(filepath: str):
    """Parse a JSONL file into a list of entries."""
    entries = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def main():
    parser = argparse.ArgumentParser(description='Parse session logs for friction events')
    parser.add_argument('sessions', nargs='+', help='Path(s) to JSONL session files')
    parser.add_argument('--output', '-o', required=True, help='Output JSON file path')
    parser.add_argument('--include-subagents', action='store_true', default=True,
                        help='Also parse subagent logs in sibling subagents/ directory')
    args = parser.parse_args()

    all_events = []
    files_parsed = []

    for session_path in args.sessions:
        path = Path(session_path)
        if not path.exists():
            print(f"Warning: {session_path} does not exist, skipping", file=sys.stderr)
            continue

        # Parse main session
        print(f"Parsing {path.name}...", file=sys.stderr)
        entries = parse_jsonl_file(str(path))
        events = extract_friction_events(entries, str(path))
        all_events.extend(events)
        files_parsed.append(str(path))

        # Parse subagent logs
        if args.include_subagents:
            session_id = path.stem
            subagents_dir = path.parent / session_id / 'subagents'
            if subagents_dir.exists():
                for sub_log in sorted(subagents_dir.glob('*.jsonl')):
                    print(f"  Parsing subagent {sub_log.name}...", file=sys.stderr)
                    sub_entries = parse_jsonl_file(str(sub_log))
                    sub_events = extract_friction_events(sub_entries, str(sub_log))
                    # Tag subagent events
                    for e in sub_events:
                        e['is_subagent'] = True
                    all_events.extend(sub_events)
                    files_parsed.append(str(sub_log))

    # Re-number events sequentially
    for i, event in enumerate(all_events):
        event['id'] = i

    # Write output
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = {
        'files_parsed': files_parsed,
        'total_events': len(all_events),
        'events_by_type': {},
        'events': all_events,
    }
    for e in all_events:
        t = e['type']
        result['events_by_type'][t] = result['events_by_type'].get(t, 0) + 1

    with open(str(output_path), 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nExtracted {len(all_events)} friction events from {len(files_parsed)} files", file=sys.stderr)
    for t, c in sorted(result['events_by_type'].items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}", file=sys.stderr)
    print(f"\nOutput written to {args.output}", file=sys.stderr)


if __name__ == '__main__':
    main()
