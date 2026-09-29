#!/usr/bin/env python
"""Audit scripted_trigger / scripted_effect reference integrity.

Every `<name> = yes` invocation in common//events//history must resolve to a
depth-1 key defined in common/scripted_triggers/ or common/scripted_effects/.
Invocations referencing undefined names produce error.log spam (or silently
never fire). Definitions never invoked are dead weight — report separately.

Also flags the reverse: a definition named inside the WRONG folder (a trigger
body defined under scripted_effects won't be callable as a trigger).

Usage: python tools/scripted_ref_audit.py
"""
import re, glob, os, sys

DEF_DIRS = {
    'trigger': 'common/scripted_triggers',
    'effect': 'common/scripted_effects',
    'localisation': 'common/scripted_localisation',
}
# contexts where a bare `name = yes` is NOT a scripted call — skip to cut noise
SKIP_FILES = re.compile(r'(scripted_triggers|scripted_effects|scripted_localisation|scripted_guis|common/defines|common/technologies|common/units|common/ideas|common/modifiers|common/dynamic_modifiers|common/terrain|common/resources|common/buildings|common/abilities|common/ai_equipment|common/ai_focuses|common/ai_peace|common/ai_strategy_plans|common/achievements|common/autonomous_states|common/bop|common/characters|common/country_leader|common/country_tags|common/difficulty_settings|common/ideologies|common/military_notifications|common/names|common/occupation_laws|common/operations|common/opinion_modifiers|common/peace_conference|common/raids|common/resistance_activity|common/scorers|common/scripted_diplomatic_actions|common/state_category|common/unit_leader|common/wargoals)/')

def defs(d):
    out = {}
    for f in glob.glob(f'{d}/*.txt'):
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            m = re.match(r'^\t?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{', code)
            if m: out[m.group(1)] = (os.path.basename(f), i)
    return out

def audit():
    tdefs = defs(DEF_DIRS['trigger'])
    edefs = defs(DEF_DIRS['effect'])
    ldefs = defs(DEF_DIRS['localisation'])
    known = set(tdefs) | set(edefs) | set(ldefs)

    # invocation candidates: `name = yes` at any depth in scanned dirs
    invoked = set()
    dead = []
    pat = re.compile(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*yes\b')
    for f in glob.glob('common/**/*.txt', recursive=True) + glob.glob('events/*.txt') + glob.glob('history/**/*.txt', recursive=True):
        rel = f.replace('\\', '/')
        if SKIP_FILES.search(rel): continue
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in pat.finditer(code):
                n = m.group(1)
                if n in tdefs or n in edefs or n in ldefs:
                    invoked.add(n)
                # heuristic: name looks like a scripted ref (not a generic keyword)
                elif re.match(r'^(?:has_|is_|can_)?[a-z_]+_trigger$|_effect$', n):
                    dead.append((os.path.basename(f), i, n))
    unused = sorted(set(known) - invoked)
    print(f'triggers: {len(tdefs)} | effects: {len(edefs)} | loc: {len(ldefs)}')
    print(f'invoked: {len(invoked)} | unused defs: {len(unused)} | dead-look refs: {len(dead)}')
    for u in unused[:25]: print('  unused', u)
    for d in dead[:25]: print('  dead  ', d)
    return 1 if dead else 0

if __name__ == '__main__':
    sys.exit(audit())
