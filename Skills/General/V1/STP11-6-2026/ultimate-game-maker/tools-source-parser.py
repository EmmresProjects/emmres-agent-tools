#!/usr/bin/env python3
"""Source normalizer for plain text, Markdown, and copied HTML capability catalogs.

It extracts one unified umbrella, category names, skill names, and descriptions while
ignoring tags, CSS, data attributes, numbering marks, page furniture, and extra spacing.
"""
from __future__ import annotations

import html
import re
from pathlib import Path

import ugm_common as common

HEADER_WORDS = {
    'ULTIMATE GAME MAKER SKILL CATALOG', 'ULTIMATE', 'GAME MAKER', 'SKILL CATALOG',
    'HOW TO USE THIS CATALOG', 'CATEGORY INDEX', 'RECOMMENDED CONTRACT FOR EVERY FUTURE SKILL',
    'CONTRACT AREA EXPECTED DESIGN',
}
STOPWORDS = {
    'and', 'the', 'for', 'with', 'game', 'games', 'skill', 'skills', 'capability',
    'capabilities', 'category', 'development', 'design', 'production', 'specialized',
    'using', 'into', 'from', 'across', 'complete', 'based', 'systems', 'system',
    'engine', 'engines', 'creator', 'platform', 'platforms', 'services', 'service',
}


def strip_markup(raw: str) -> tuple[str, dict]:
    tag_count = len(re.findall(r'<[^>]+>', raw))
    script_style = re.findall(r'(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>', raw)
    text = re.sub(r'(?is)<(?:script|style)\b[^>]*>.*?</(?:script|style)>', ' ', raw)
    text = re.sub(r'(?s)<!--.*?-->', ' ', text)
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'(?i)</?(?:p|div|li|ul|ol|h[1-6]|tr|td|th|section|article|table|header|footer)[^>]*>', '\n', text)
    text = re.sub(r'(?s)<[^>]+>', '', text)
    text = html.unescape(text)
    return text, {
        'html_tags_removed': tag_count,
        'css_or_script_blocks_removed': len(script_style),
        'html_entities_decoded': text != raw,
    }


def normalize_lines(raw: str) -> tuple[list[str], str | None, dict]:
    text, markup_stats = strip_markup(raw)
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\u00a0', ' ')
    lines: list[str] = []
    title: str | None = None
    numbering_removed = 0
    spacing_collapsed = 0
    for original in text.split('\n'):
        line = original.replace('\t', ' ').strip()
        if not line:
            continue
        if len(re.findall(r'\s{2,}', line)):
            spacing_collapsed += 1
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            heading_text = heading.group(2).strip()
            if title is None and re.search(r'(?i)(catalog|capability list|skill list)', heading_text):
                title = heading_text
                continue
            heading_text = re.sub(r'(?i)^Category\s+\d+\s*:\s*', '', heading_text).strip()
            line = f'Category: {heading_text}'
        line = re.sub(r'^>\s*', '', line)
        line = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', line)
        line = line.replace('**', '').replace('__', '').replace('`', '')
        if re.match(r'^(?:\d+[.)]|\(\d+\)|[-*•▪])\s+', line):
            numbering_removed += 1
            line = re.sub(r'^(?:\d+[.)]|\(\d+\)|[-*•▪])\s+', '', line)
        line = re.sub(r'(?i)^Skill\s+Tool\s+Name\s*:', 'Skill Name:', line)
        line = re.sub(r'(?i)^Capability\s+Name\s*:', 'Skill Name:', line)
        line = re.sub(r'(?i)^Category\s+(\d+)\s*:\s*', r'Category \1: ', line)
        line = re.sub(r'\s{2,}', ' ', line).strip()
        line = re.sub(r'\s*\(\d+\s+skills?\)\s*$', '', line, flags=re.I)
        if not line or re.fullmatch(r'\d{1,4}', line):
            continue
        if line.upper() in HEADER_WORDS:
            if title is None and 'CATALOG' in line.upper():
                title = ' '.join(word.capitalize() for word in line.lower().split())
            continue
        if re.match(r'(?i)^\d+\s+specialized capabilities', line):
            continue
        if re.match(r'(?i)^prepared for\b', line):
            continue
        if title is None and re.fullmatch(r'(?i)ultimate game maker skill catalog', line):
            title = line
            continue
        lines.append(line)
    stats = dict(markup_stats)
    stats.update({
        'numbering_marks_removed': numbering_removed,
        'lines_with_extra_spacing_collapsed': spacing_collapsed,
        'normalized_line_count': len(lines),
    })
    return lines, title, stats


def _compact(value: str) -> str:
    return ' '.join(value.split())


def _is_boundary(line: str) -> bool:
    return bool(re.match(r'(?i)^(?:Category(?:\s+\d+)?\s*:|Skill Name\s*:|Description\s*:)', line))


def _purpose_slug(categories: list[dict]) -> str:
    verb_stopwords = {
        'defines', 'define', 'creates', 'create', 'builds', 'build', 'turns', 'turn',
        'implements', 'implement', 'finds', 'find', 'applies', 'apply', 'uses', 'use',
        'recovers', 'recover', 'enables', 'enable', 'protects', 'protect', 'measures',
        'measure', 'produces', 'produce', 'adapts', 'adapt', 'models', 'model',
        'observable', 'testable', 'editable', 'version-correct', 'production-ready',
    }
    first = categories[0]['skills'][0]
    words = [
        word for word in re.findall(r'[a-z0-9]+', first['description'].lower())
        if word not in STOPWORDS and word not in verb_stopwords and len(word) > 2
    ]
    purpose = words[:2] + ['production']
    candidate = common.slugify('-'.join(purpose))
    return candidate[:60].rstrip('-') or common.slugify(first['name'])[:60].rstrip('-')


def _shared_slug(categories: list[dict], title: str | None) -> str:
    if title:
        cleaned = re.sub(r'(?i)\b(?:skill|capability)?\s*catalog\b', '', title)
        candidate = common.slugify(cleaned)
        if candidate:
            return candidate[:60].rstrip('-')
    if len(categories) == 1 and not categories[0].get('implicit'):
        return common.slugify(categories[0]['name'])[:60].rstrip('-')
    if len(categories) == 1 and categories[0].get('implicit'):
        return _purpose_slug(categories)
    return 'game-production-orchestration'


def _humanize(slug: str) -> str:
    return ' '.join(part.capitalize() for part in slug.split('-') if part)


def parse_source_text(raw: str, umbrella_override: str | None = None) -> dict:
    if not isinstance(raw, str) or not raw.strip():
        raise common.ToolError('EMPTY_SOURCE', 'The source is empty.', [], ['Provide a capability, a numbered list, or one or more categories.'])
    lines, title, stats = normalize_lines(raw)
    if not lines:
        raise common.ToolError('EMPTY_SOURCE_AFTER_NORMALIZATION', 'No usable capability text remained after normalization.', [stats], ['Include at least one Skill Name and Description pair.'])
    source_format = 'html' if stats['html_tags_removed'] else ('markdown' if re.search(r'(?m)^#{1,6}\s|\*\*|\[[^\]]+\]\(', raw) else 'plain-text')

    categories: list[dict] = []
    current: dict | None = None
    pending: dict | None = None
    pending_description: list[str] = []
    section_counter = 0

    def close_pending():
        nonlocal pending, pending_description
        if pending is None:
            return
        description = _compact(' '.join(pending_description))
        if not description:
            description = f'{pending["name"]} capability. No description was supplied in the source.'
        pending['description'] = description
        pending['slug'] = common.slugify(pending['name'])
        if current is None:
            raise common.ToolError('PARSER_STATE', 'A skill was found before any category could be created.')
        current['skills'].append(pending)
        pending = None
        pending_description = []

    def add_category(name: str, explicit_number: int | None = None, implicit: bool = False):
        nonlocal current, section_counter
        close_pending()
        section_counter += 1
        current = {
            'number': explicit_number or section_counter,
            'name': _compact(name),
            'slug': common.slugify(name),
            'implicit': implicit,
            'skills': [],
        }
        categories.append(current)

    index = 0
    while index < len(lines):
        line = lines[index]
        category_match = re.match(r'(?i)^Category(?:\s+(\d+))?\s*:\s*(.+)$', line)
        if category_match:
            number = int(category_match.group(1)) if category_match.group(1) else None
            name_parts = [category_match.group(2)]
            lookahead = index + 1
            # Join wrapped category names, but never cross a skill or description boundary.
            while lookahead < len(lines) and len(name_parts) < 4 and not _is_boundary(lines[lookahead]):
                if re.match(r'(?i)^Category\s+\d+\s*\)\s*$', lines[lookahead]):
                    break
                name_parts.append(lines[lookahead])
                lookahead += 1
            add_category(' '.join(name_parts), number)
            index = lookahead
            continue
        skill_match = re.match(r'(?i)^Skill Name\s*:\s*(.+)$', line)
        if skill_match:
            close_pending()
            if current is None:
                add_category(title or 'Unified Capability', implicit=True)
            body = skill_match.group(1).strip()
            inline = re.match(r'^(.+?)\s+[-–:]\s+(.+)$', body)
            name = _compact(inline.group(1) if inline else body)
            pending = {'name': name}
            pending_description = [_compact(inline.group(2))] if inline else []
            index += 1
            continue
        description_match = re.match(r'(?i)^Description\s*:\s*(.*)$', line)
        if description_match:
            if pending is None:
                # A description may follow a skill whose name was on the previous non-labeled line.
                if current and current['skills']:
                    pending = current['skills'].pop()
                    pending_description = []
                else:
                    if current is None:
                        add_category(title or 'Unified Capability', implicit=True)
                    pending = {'name': f'Capability {len(current["skills"]) + 1}'}
                    pending_description = []
            if description_match.group(1):
                pending_description.append(description_match.group(1))
            index += 1
            continue
        if pending is not None:
            pending_description.append(line)
        elif current and current['skills']:
            # Continuation of the most recently completed description.
            current['skills'][-1]['description'] = _compact(current['skills'][-1]['description'] + ' ' + line)
        index += 1
    close_pending()

    # Fallback for a single unlabeled capability such as "Name: Description".
    if not any(category['skills'] for category in categories):
        fallback_skills = []
        for line in lines:
            match = re.match(r'^([A-Za-z0-9][A-Za-z0-9 ,+/&#().-]{2,80})\s*[-–:]\s*(.{15,})$', line)
            if not match:
                continue
            name = _compact(match.group(1))
            if name.lower() in {'goal', 'genre', 'engine', 'language', 'description', 'category'}:
                continue
            fallback_skills.append({
                'name': name,
                'slug': common.slugify(name),
                'description': _compact(match.group(2)),
            })
        if fallback_skills:
            categories = [{
                'number': 1,
                'name': title or 'Unified Capability',
                'slug': common.slugify(title or 'Unified Capability'),
                'implicit': True,
                'skills': fallback_skills,
            }]

    categories = [category for category in categories if category['skills']]
    if not categories:
        raise common.ToolError('NO_CAPABILITIES_FOUND', 'No category, Skill Name, or Description pairs were found.', [source_format], ['Use "Skill Name: X" followed by "Description: Y", or include numbered category sections.'])

    names = [skill['name'] for category in categories for skill in category['skills']]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    warnings: list[str] = []
    if duplicates:
        warnings.append(f'Duplicate skill names were preserved: {", ".join(duplicates[:5])}.')
    if any(skill['description'].startswith('No description was supplied') for category in categories for skill in category['skills']):
        warnings.append('At least one skill had no source description; a neutral fallback description was recorded.')
    if not title and len(categories) > 1:
        warnings.append('No source title was found; the umbrella name was derived from the shared purpose.')

    umbrella_slug = common.slugify(umbrella_override) if umbrella_override else _shared_slug(categories, title)
    if not umbrella_slug or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', umbrella_slug):
        raise common.ToolError('INVALID_UMBRELLA', 'The umbrella name must produce a lowercase hyphenated slug.', [umbrella_override or umbrella_slug], ['Use letters, digits, and single hyphens only.'])
    for category in categories:
        first_skill = category['skills'][0]
        category['description'] = first_skill['description']
        prefix = f'{first_skill["name"]} capability. '
        category['outcome'] = first_skill['description'][len(prefix):] if first_skill['description'].startswith(prefix) else first_skill['description']

    return {
        'schema_id': 'ugm-parsed-source-1.0',
        'source_format': source_format,
        'umbrella': {
            'slug': umbrella_slug,
            'name': title or _humanize(umbrella_slug),
            'unification': 'All related listed capabilities are represented as one unified source manifest.',
        },
        'category_count': len(categories),
        'capability_count': sum(len(category['skills']) for category in categories),
        'categories': categories,
        'normalization': stats,
        'warnings': warnings,
    }


def parse_source_file(path: Path, config: dict, deadline: common.Deadline, umbrella_override: str | None = None) -> dict:
    deadline.check('parse source')
    max_bytes = int(config['limits']['max_source_bytes'])
    raw = common.read_text_limited(path, max_bytes, 'source file', config)
    return parse_source_text(raw, umbrella_override)
