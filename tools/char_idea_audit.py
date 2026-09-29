#!/usr/bin/env python
"""Audit character + idea reference integrity.

Characters: depth-1 keys inside `characters = {}` blocks in common/characters/.
Ids may contain `-` (e.g. `AUS_arthur_seyss-inquart`).
Real refs: recruit_character / retire_character / kill_character /
promote_character / set_character_name = X, plus `character = X` inside
corps_commander / navy_leader / advisor / country_leader blocks.
(Skipped: `character = yes`, scope variables PREV/ROOT/FROM.)

Ideas: depth-1 keys inside `ideas = {}` blocks in common/ideas/.
Real refs: add_idea(s) / remove_idea(s) / swap_ideas / available_ideas /
idea_token = X. swap_ideas contains nested remove_idea/add_idea.
(Skipped: has_idea = X — trigger check, not a ref.)

Usage: python tools/char_idea_audit.py
"""
import re, glob, os, sys

IDCHAR = r'[A-Za-z0-9_.\-]+'
SCOPES = {'ROOT','PREV','FROM','CAPITAL','OWNER','CONTROLLER','THIS','OVERLORD'}

def inner_keys(path, outer):
    out = {}
    for f in glob.glob(f'{path}/*.txt'):
        txt = open(f, encoding='utf-8', errors='replace').read()
        for m in re.finditer(r'\b' + outer + r'\s*=\s*\{', txt):
            depth, i = 1, m.end()
            start = i
            while i < len(txt) and depth:
                if txt[i] == '{': depth += 1
                elif txt[i] == '}': depth -= 1
                i += 1
            body = txt[start:i]
            # ideas/characters are nested 1-2 levels deep — accept any tab indent
            for km in re.finditer(r'^[ \t]*(' + IDCHAR + r')\s*=\s*\{', body, re.M):
                out[km.group(1)] = os.path.basename(f)
    return out

CHAR_PAT = re.compile(
    r'\b(?:recruit_character|retire_character|kill_character|promote_character|set_character_name)\s*=\s*(' + IDCHAR + r')|'
    r'\b(?:corps_commander|navy_leader|field_marshal|advisor|theorist|country_leader|army_chief|navy_chief|air_chief|high_command|political_advisor)\s*=\s*\{[^}]*?character\s*=\s*(' + IDCHAR + r')'
)
IDEA_PAT = re.compile(r'\b(?:add_ideas?|remove_ideas?|available_ideas|idea_token)\s*=\s*(' + IDCHAR + r')')

def audit():
    chars = inner_keys('common/characters', 'characters')
    ideas = inner_keys('common/ideas', 'ideas')
    char_refs, idea_refs = [], []
    for f in glob.glob('common/**/*.txt', recursive=True) + glob.glob('events/*.txt') + glob.glob('history/**/*.txt', recursive=True):
        rel = f.replace('\\','/')
        if 'common/characters' in rel or 'common/ideas' in rel: continue
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in CHAR_PAT.finditer(code):
                v = m.group(1) or m.group(2)
                if v and v != 'yes' and v not in SCOPES and v not in chars:
                    char_refs.append((os.path.basename(f), i, v))
            for m in IDEA_PAT.finditer(code):
                v = m.group(1)
                if v != 'yes' and v not in ideas and not re.match(r'[A-Z]{3}_random_.*_\d+$', v):
                    idea_refs.append((os.path.basename(f), i, v))
    print(f'chars: {len(chars)} | ideas: {len(ideas)}')
    print(f'dead char refs: {len(char_refs)} | dead idea refs: {len(idea_refs)}')
    for r in char_refs[:15]: print('  char', r)
    for r in idea_refs[:15]: print('  idea', r)
    return 1 if (char_refs or idea_refs) else 0

if __name__ == '__main__':
    sys.exit(audit())
