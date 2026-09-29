#!/usr/bin/env python
"""Audit technology + leader-trait reference integrity.

DEFS:
  techs      depth-1 keys inside `technologies = {}` in common/technologies/**/*.txt
             and common/technology/*.txt (depth-2 = per-tech grammar, kept per
             sweep spec — keys like `path`/`folder` can never be mistaken for a
             real tech id by the ref rules below).
  traits     depth-1 keys inside `leader_traits = {}` (common/unit_leader/*.txt)
             and `country_leader_traits`/`leader_traits` (common/country_leader/*.txt).
             Union set — operative traits live in unit_leader too.
  categories literals inside `technology_categories` in common/technology_tags/*.txt
             (only checked with --vanilla unless the mod ships its own file).

REFS:
  set_technology = { X = 1 }  keys                          -> tech
  has_tech / has_technology = X                             -> tech
  technology = X inside add_tech_bonus                      -> tech
  leads_to_tech = X (tech-tree path edges)                  -> tech
  category = X inside add_tech_bonus                        -> category
  research_bonus = { X = N } keys (ideas)                   -> category
  has_trait / add_*_trait / remove_*_trait / award_trait = X-> trait
  add_random_trait = { X ... } literals                     -> trait
  traits = { X } literals under leader-def contexts
    (country_leader/corps_commander/navy_leader/field_marshal/
     operative/create_operative_leader + trait parent lists) -> trait
  trait = X scalar under leader defs                        -> trait
  mutually_exclusive = X, keys under trait_xp_factor        -> trait

Dead tech refs silently no-op bonuses; dead trait refs log to error.log.
Usage: python tools/tech_trait_audit.py [--vanilla "<hoi4 dir>"]
--vanilla merges vanilla technologies/unit_leader/country_leader/technology_tags —
the meaningful check: the mod ships no common/country_leader, so country-leader
trait refs only resolve against vanilla (like loc_audit).
"""
import re, glob, os, sys, bisect

IDCHAR = r'[A-Za-z0-9_.\-]+'
NUM = re.compile(r'^\d+$')
SKIP_VAL = {'yes', 'no', 'empty', 'none', 'any', 'all'}
TOKEN = re.compile(
    r'(' + IDCHAR + r'(?::' + IDCHAR + r')?)\s*=\s*(\{)?'
    r'|"[^"]*"'
    r'|[{}]'
    r'|[A-Za-z0-9_.\-:@%]+'
    r'|[^\s]', re.M)

TECH_CONTAINERS = {'technologies'}
TRAIT_CONTAINERS = {'leader_traits', 'country_leader_traits'}
CAT_CONTAINERS = {'technology_categories'}
TRAIT_SCALARS = re.compile(
    r'^(?:has_trait|trait|award_trait|add_\w*trait|remove_\w*trait)$')
TRAIT_LIT_PARENTS = {'add_random_trait'}
# ancestors that make `traits = { ... }` a leader-trait list (idea `traits`
# under advisor/ideas are idea_tags, not leader traits — excluded)
LEADER_ANCESTORS = {'country_leader', 'corps_commander', 'navy_leader',
                    'field_marshal', 'unit_leader', 'operative', 'operatives',
                    'create_operative_leader', 'add_country_leader_role',
                    'leader_traits', 'country_leader_traits', 'parent',
                    'any_parent', 'all_parents'}
TRAIT_KEY_PARENTS = {'trait_xp_factor'}
TECH_KEY_PARENTS = {'set_technology'}
TECH_SCALARS = {'has_tech', 'has_technology', 'leads_to_tech'}
TECH_UNDER_BONUS = {'add_tech_bonus'}
CAT_KEYS = {'category'}
CAT_KEY_PARENTS = {'research_bonus'}
COUNTRY_TAGS = re.compile(r'^[A-Z][A-Z0-9]{2}$')


def walk(path):
    """('block',key,parent,anc,line) / ('scalar',key,val,parent,anc,line) /
    ('lit',val,parent,anc,line) from comment-stripped text."""
    txt = open(path, encoding='utf-8', errors='replace').read()
    code = '\n'.join(l.split('#', 1)[0] for l in txt.split('\n'))
    nl = [0] + [m.end() for m in re.finditer(r'\n', code)]
    stack, pending = [], None
    for m in TOKEN.finditer(code):
        line = bisect.bisect_right(nl, m.start())
        if m.group(1):
            if pending:
                yield ('scalar', pending[0], '', stack[-1] if stack else '',
                       tuple(stack), pending[1])
            if m.group(2):
                yield ('block', m.group(1), stack[-1] if stack else '',
                       tuple(stack), line)
                stack.append(m.group(1))
                pending = None
            else:
                pending = (m.group(1), line)
        else:
            t = m.group(0)
            if t == '{':
                if pending:
                    yield ('scalar', pending[0], '{', stack[-1] if stack else '',
                           tuple(stack), pending[1])
                    stack.append(pending[0])
                    pending = None
                else:
                    stack.append('')
            elif t == '}':
                if pending:
                    yield ('scalar', pending[0], '', stack[-1] if stack else '',
                           tuple(stack), pending[1])
                    pending = None
                if stack:
                    stack.pop()
            else:
                val = t.strip('"')
                if pending:
                    yield ('scalar', pending[0], val, stack[-1] if stack else '',
                           tuple(stack), pending[1])
                    pending = None
                elif re.match(r'^[A-Za-z0-9_.\-]+$', val):
                    yield ('lit', val, stack[-1] if stack else '', tuple(stack), line)


def collect_defs(root):
    """Return (techs, traits, cats) dicts from root ('.' or vanilla dir)."""
    techs, traits, cats = {}, {}, {}
    for f in (glob.glob(os.path.join(root, 'common/technologies/**/*.txt'), recursive=True)
              + glob.glob(os.path.join(root, 'common/technology/*.txt'))):
        rel = f.replace('\\', '/')
        for ev in walk(f):
            if ev[0] == 'block' and ev[1] not in TECH_CONTAINERS and not NUM.match(ev[1]):
                _, key, parent, anc, _ = ev
                if parent in TECH_CONTAINERS or TECH_CONTAINERS & set(anc):
                    techs.setdefault(key, rel)
    for f in glob.glob(os.path.join(root, 'common/unit_leader/*.txt')) \
            + glob.glob(os.path.join(root, 'common/country_leader/*.txt')):
        rel = f.replace('\\', '/')
        for ev in walk(f):
            if ev[0] == 'block' and not NUM.match(ev[1]):
                _, key, parent, anc, _ = ev
                if parent in TRAIT_CONTAINERS or TRAIT_CONTAINERS & set(anc):
                    traits.setdefault(key, rel)
    for f in glob.glob(os.path.join(root, 'common/technology_tags/*.txt')):
        rel = f.replace('\\', '/')
        for ev in walk(f):
            if ev[0] == 'lit' and ev[2] in CAT_CONTAINERS:
                cats.setdefault(ev[1], rel)
    return techs, traits, cats


def audit(vanilla=None):
    techs, traits, cats = collect_defs('.')
    if vanilla:
        vt, vr, vc = collect_defs(vanilla)
        for src, dst in ((vt, techs), (vr, traits), (vc, cats)):
            for k, v in src.items():
                dst.setdefault(k, 'vanilla:' + v)
    dead = {'tech': [], 'trait': [], 'cat': []}
    files = (glob.glob('common/**/*.txt', recursive=True)
             + glob.glob('events/*.txt')
             + glob.glob('history/**/*.txt', recursive=True))
    for f in files:
        rel = f.replace('\\', '/')
        for ev in walk(f):
            if ev[0] == 'block':
                _, key, parent, anc, line = ev
                if NUM.match(key):
                    continue
                if parent in TECH_KEY_PARENTS and key not in techs:
                    dead['tech'].append((rel, line, key))
                elif parent in CAT_KEY_PARENTS and cats and key not in cats:
                    dead['cat'].append((rel, line, key))
                elif parent in TRAIT_KEY_PARENTS and key not in traits:
                    dead['trait'].append((rel, line, key))
            elif ev[0] == 'lit':
                _, val, parent, anc, line = ev
                if val in SKIP_VAL or NUM.match(val) or COUNTRY_TAGS.match(val):
                    continue
                if parent in TRAIT_LIT_PARENTS and val not in traits:
                    dead['trait'].append((rel, line, val))
                elif parent == 'traits' and LEADER_ANCESTORS & set(anc) \
                        and val not in traits:
                    dead['trait'].append((rel, line, val))
            else:
                _, key, val, parent, anc, line = ev
                if val in ('', '{') or val in SKIP_VAL or NUM.match(val):
                    continue
                if key in TECH_SCALARS:
                    if val not in techs:
                        dead['tech'].append((rel, line, val))
                elif key == 'technology' and TECH_UNDER_BONUS & set(anc):
                    if val not in techs:
                        dead['tech'].append((rel, line, val))
                elif key in CAT_KEYS and TECH_UNDER_BONUS & set(anc):
                    if cats and val not in cats:
                        dead['cat'].append((rel, line, val))
                elif TRAIT_SCALARS.match(key) or key == 'mutually_exclusive':
                    if key == 'trait' and not (LEADER_ANCESTORS & set(anc)):
                        continue
                    if val not in traits:
                        dead['trait'].append((rel, line, val))
    total = sum(len(v) for v in dead.values())
    cat_note = f'{len(cats)}' if cats else '0 (category refs unchecked — no technology_tags)'
    print(f'defined: techs {len(techs)} | traits {len(traits)} | categories {cat_note}'
          + (' + vanilla' if vanilla else '')
          + f' | dead refs: {total} '
          f'(tech {len(dead["tech"])} | trait {len(dead["trait"])} | cat {len(dead["cat"])})')
    for cls in ('tech', 'trait', 'cat'):
        for rel, line, vid in dead[cls][:40]:
            print(f'  {cls:5} {rel}:{line} {vid}')
    return 1 if total else 0


if __name__ == '__main__':
    van = None
    if '--vanilla' in sys.argv:
        i = sys.argv.index('--vanilla')
        van = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        if not van or not os.path.isdir(os.path.join(van or '', 'common')):
            print('--vanilla needs the HOI4 install dir')
            sys.exit(2)
    sys.exit(audit(van))
