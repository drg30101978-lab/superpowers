#!/usr/bin/env python3
"""
Find the GitHub source repository for an installed skill.

Reads ~/.agents/.skill-lock.json to look up the source repo,
then falls back to scanning README.md for installation URLs.
"""

import json
import sys
import argparse
from pathlib import Path


LOCK_FILE = Path.home() / '.agents' / '.skill-lock.json'
SKILLS_DIR = Path.home() / '.agents' / 'skills'
SKILLS_SYMLINK_DIR = Path.home() / '.claude' / 'skills'


def load_lock_file() -> dict:
    """Load the skill lock file."""
    if not LOCK_FILE.exists():
        return {}
    with open(LOCK_FILE) as f:
        data = json.load(f)
    return data.get('skills', {})


def find_repo_from_lock(skill_name: str, lock_data: dict) -> dict | None:
    """Look up skill in the lock file."""
    # Try exact match first
    if skill_name in lock_data:
        entry = lock_data[skill_name]
        return {
            'source': entry.get('source', ''),
            'source_url': entry.get('sourceUrl', ''),
            'skill_path_in_repo': entry.get('skillPath', ''),
        }

    # Try case-insensitive match
    for key, entry in lock_data.items():
        if key.lower() == skill_name.lower():
            return {
                'source': entry.get('source', ''),
                'source_url': entry.get('sourceUrl', ''),
                'skill_path_in_repo': entry.get('skillPath', ''),
            }

    return None


def find_repo_from_readme(skill_dir: Path) -> str | None:
    """Scan README.md for a GitHub URL."""
    readme = skill_dir / 'README.md'
    if not readme.exists():
        return None

    import re
    with open(readme) as f:
        content = f.read()

    # Look for npx skills add <url> pattern
    match = re.search(r'npx skills add (https://github\.com/[^\s)]+)', content)
    if match:
        return match.group(1)

    # Look for any github URL
    match = re.search(r'https://github\.com/[\w-]+/[\w.-]+', content)
    if match:
        return match.group(0)

    return None


def find_skill_directory(skill_name: str) -> Path | None:
    """Find the installed skill directory."""
    # Check symlink dir first
    candidate = SKILLS_SYMLINK_DIR / skill_name
    if candidate.exists():
        return candidate.resolve()

    # Check skills dir
    candidate = SKILLS_DIR / skill_name
    if candidate.exists():
        return candidate

    # Fuzzy match
    for d in SKILLS_DIR.iterdir():
        if d.is_dir() and d.name.lower() == skill_name.lower():
            return d

    return None


def main():
    parser = argparse.ArgumentParser(description='Find GitHub repo for an installed skill')
    parser.add_argument('skill_name', help='Name of the installed skill')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    args = parser.parse_args()

    lock_data = load_lock_file()
    skill_dir = find_skill_directory(args.skill_name)

    result = {
        'skill_name': args.skill_name,
        'skill_dir': str(skill_dir) if skill_dir else None,
        'source': None,
        'source_url': None,
        'skill_path_in_repo': None,
        'found': False,
    }

    # Try lock file first
    lock_info = find_repo_from_lock(args.skill_name, lock_data)
    if lock_info:
        result.update(lock_info)
        result['found'] = True
        result['method'] = 'lock-file'

    # Fall back to README
    if not result['found'] and skill_dir:
        url = find_repo_from_readme(skill_dir)
        if url:
            result['source_url'] = url
            result['found'] = True
            result['method'] = 'readme'

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if result['found']:
            print(f"Skill: {args.skill_name}")
            print(f"Directory: {result['skill_dir']}")
            print(f"Source: {result.get('source', 'unknown')}")
            print(f"URL: {result['source_url']}")
            print(f"Path in repo: {result.get('skill_path_in_repo', 'N/A')}")
            print(f"Found via: {result.get('method', 'unknown')}")
        else:
            print(f"Could not find GitHub repo for skill: {args.skill_name}", file=sys.stderr)
            print(f"Skill directory: {result['skill_dir'] or 'NOT FOUND'}", file=sys.stderr)
            sys.exit(1)


if __name__ == '__main__':
    main()
