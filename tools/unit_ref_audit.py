#!/usr/bin/env python
"""Audit unit / equipment / name-group reference integrity.

DEF SET (union — sub-units, ships, air units, equipment, modules, variants):
  - keys inside container blocks `sub_units`/`equipments`/`equipment_modules`/
    `search_filters`/`duplicate_archetypes`/`upgrades`/`ship_hull_*`/`land_units`/
    `air_units` in common/units/**/*.txt
  - every other depth-1 `key = {` in common/units — in the name dirs
    (names/, names_ships/, names_divisions/, names_railway_guns/,
    codenames_operatives/) those keys ARE the name-group defs.

REFS (all resolve against the union def set):
  - keys under regiments|support inside division_template          -> units
  - keys under sub_unit_modifiers (unit_leader trait files, AI)    -> units
  - literals in enable_subunits / need(=sub-unit legacy)           -> units
  - literals in enable_equipments/enable_equipment_modules,
    keys+literals in need_equipment/need_equipment_modules         -> equipment
  - keys inside air_wings/air_wing blocks (equipment ids incl _N)  -> equipment
  - equipment = { X = { amount = N } } ship loadout keys           -> equipment
  - type = X scalar inside stockpile/production/variant/equipment/
    division/air_wing/ship blocks                                  -> both
  - definition = X scalar inside ship                              -> units
  - scalar values inside modules/upgrades (variant slot values)    -> equipment
  - name_group/for_names/ship_names/division_names_group = X and
    link_numbering_with literals                                   -> name groups

Usage: python tools/unit_ref_audit.py [--vanilla "<hoi4 dir>"] [--no-vanilla]
Vanilla common/units defs merge ON by default (auto-detects the standard
install) — the meaningful check, since mod files sit on top of vanilla defs.
Dead refs present verbatim in the vanilla counterpart file are tagged
[inherited] and documented, not counted.
"""
import re, glob, os, sys, bisect

IDCHAR = r'[A-Za-z0-9_.\-]+'
NUM = re.compile(r'^\d+$')
SKIP_VAL = {'yes', 'no', 'empty', 'none', 'any', 'all', 'empty_slot'}
TOKEN = re.compile(
    r'(' + IDCHAR + r'(?::' + IDCHAR + r')?)\s*=\s*(\{)?'   # key = | key = {
    r'|"[^"]*"'                                            # string literal
    r'|[{}]'                                               # braces
    r'|[A-Za-z0-9_.\-:@%]+'                                   # bare literal (incl. var: scopes)
    r'|[^\s]', re.M)                                         # stray char

CONTAINERS = {'sub_units', 'equipments', 'equipment_modules', 'search_filters',
              'duplicate_archetypes', 'upgrades', 'land_units', 'air_units',
              'critical_parts'}
D1_SKIP = CONTAINERS | {'if', 'limit', 'else_if', 'else', 'values', 'resources',
                        'NOT', 'OR', 'AND'}
NAME_DIRS = ('names', 'names_ships', 'names_divisions', 'names_railway_guns',
             'codenames_operatives')

REG_PARENTS = {'regiments', 'support'}
UKEY_PARENTS = {'sub_unit_modifiers'}
EKEY_PARENTS = {'need_equipment', 'need_equipment_modules', 'equipment'}
ULIT_PARENTS = {'enable_subunits'}
ELIT_PARENTS = {'enable_equipments', 'enable_equipment_modules', 'need_equipment',
                'need_equipment_modules'}
NLIT_PARENTS = {'link_numbering_with'}
AIR_ANCESTORS = {'air_wings', 'air_wing'}
TYPE_PARENTS = {'add_equipment_to_stockpile', 'add_equipment_production',
                'create_equipment_variant', 'equipment', 'division',
                'division_template', 'air_wing', 'ship', 'archtype',
                'ai_equipment_variant', 'set_equipment_fraction'}
DEFN_PARENTS = {'ship', 'task_force', 'fleet'}
MODULE_PARENTS = {'modules', 'upgrades'}
NAME_KEYS = {'name_group', 'for_names', 'ship_names', 'division_names_group'}
EQUIP_SCALAR_PARENTS = {'add_equipment_to_stockpile', 'add_equipment_production',
                        'ship'}


def walk(path):
    """Emit events from comment-stripped text:
    ('block', key, parent, ancestors, line) on `key = {`
    ('scalar', key, val, parent, ancestors, line) on `key = <tok>`
    ('lit', val, parent, ancestors, line) on bare literal inside a block."""
    txt = open(path, encoding='utf-8', errors='replace').read()
    code = '\n'.join(l.split('#', 1)[0] for l in txt.split('\n'))
    nl = [0] + [m.end() for m in re.finditer(r'\n', code)]
    stack = []          # ancestor keys; '' = anonymous block
    pending = None      # (key, line) awaiting scalar value or '{'
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
    """root = mod dir ('.') or vanilla install dir. Return (defs, counts)."""
    defs, counts = {}, {'unit': 0, 'equip': 0, 'name': 0, 'other': 0}
    for f in glob.glob(os.path.join(root, 'common/units/**/*.txt'), recursive=True):
        rel = f.replace('\\', '/')
        in_names = any(f'/{d}/' in rel for d in NAME_DIRS)
        for ev in walk(f):
            if ev[0] != 'block':
                continue
            _, key, parent, ancestors, _ = ev
            if NUM.match(key):
                continue
            depth = len(ancestors) + 1
            if depth == 1:
                if key in D1_SKIP:
                    continue
                defs.setdefault(key, rel)
                counts['name' if in_names else 'other'] += 1
            elif depth == 2 and (parent in CONTAINERS or parent.startswith('ship_hull')):
                defs.setdefault(key, rel)
                cls = 'unit' if parent == 'sub_units' or parent.startswith(('ship_hull', 'land_units', 'air_units')) else 'equip'
                counts[cls] += 1
        # second pass: derived_variant_name / variant_name scalars are usable
        # equipment ids (x_plane_airframes documents the alias attributes)
        for ev in walk(f):
            if ev[0] == 'scalar' and ev[1] in ('derived_variant_name', 'variant_name', 'alias'):
                if ev[2] and ev[2] not in SKIP_VAL and not NUM.match(ev[2]):
                    defs.setdefault(ev[2], rel)
                    counts['equip'] += 1
    return defs, counts


def ref_scan(files, defs):
    """Return dict {'unit':[(file,line,id)], 'equip':[...], 'name':[...]}"""
    dead = {'unit': [], 'equip': [], 'name': []}
    for f in files:
        rel = f.replace('\\', '/')
        if rel.startswith('common/units/'):
            continue
        for ev in walk(f):
            if ev[0] == 'block':
                _, key, parent, anc, line = ev
                if NUM.match(key):
                    continue
                if parent in REG_PARENTS or parent in UKEY_PARENTS:
                    if key not in defs:
                        dead['unit'].append((rel, line, key))
                elif parent in EKEY_PARENTS:
                    if key not in defs:
                        dead['equip'].append((rel, line, key))
                elif AIR_ANCESTORS & set(anc) or parent in AIR_ANCESTORS:
                    # keys under air_wings/state-id blocks are equipment ids;
                    # ignore obvious non-equipment sub-blocks
                    if key not in defs and key not in {'wing', 'air_wing'}:
                        dead['equip'].append((rel, line, key))
            elif ev[0] == 'lit':
                _, val, parent, anc, line = ev
                if val in SKIP_VAL or NUM.match(val):
                    continue
                if parent in ULIT_PARENTS:
                    if val not in defs:
                        dead['unit'].append((rel, line, val))
                elif parent in ELIT_PARENTS:
                    if val not in defs:
                        dead['equip'].append((rel, line, val))
                elif parent in NLIT_PARENTS:
                    if val not in defs:
                        dead['name'].append((rel, line, val))
            else:
                _, key, val, parent, anc, line = ev
                if parent == 'upgrades' and \
                        {'create_equipment_variant', 'ai_equipment_variant', 'ship'} & set(anc):
                    # upgrades = { <upgrade_id> = 1 } — the KEY is the ref
                    if not NUM.match(key) and key not in defs:
                        dead['equip'].append((rel, line, key))
                    continue
                if val in ('', '{') or val in SKIP_VAL or NUM.match(val):
                    continue
                if key in NAME_KEYS:
                    if val not in defs:
                        dead['name'].append((rel, line, val))
                elif ':' in val:  # var:/scope indirection — not a literal id
                    continue
                elif key == 'type' and parent in TYPE_PARENTS:
                    if val not in defs:
                        dead['equip'].append((rel, line, val))
                elif key == 'definition' and parent in DEFN_PARENTS:
                    if val not in defs:
                        dead['unit'].append((rel, line, val))
                elif key == 'equipment' and parent in EQUIP_SCALAR_PARENTS:
                    if val not in defs:
                        dead['equip'].append((rel, line, val))
                elif parent in MODULE_PARENTS and (key.endswith('_slot') or
                                                 'create_equipment_variant' in anc or 'ship' in anc):
                    if val not in defs:
                        dead['equip'].append((rel, line, val))
    return dead


_vcache = {}
def _vanilla_contains(vroot, rel, vid):
    """True when the vanilla counterpart file references this id — i.e. the
    dead ref was inherited verbatim from vanilla (parity rule: documented,
    not counted against the mod)."""
    if rel not in _vcache:
        vf = os.path.join(vroot, rel)
        _vcache[rel] = open(vf, encoding='utf-8', errors='replace').read() \
            if os.path.isfile(vf) else None
    txt = _vcache[rel]
    return txt is not None and re.search(r'\b' + re.escape(vid) + r'\b', txt) is not None


def audit(vanilla=None):
    defs, counts = collect_defs('.')
    if vanilla:
        vdefs, _ = collect_defs(vanilla)
        for k, v in vdefs.items():
            defs.setdefault(k, 'vanilla:' + v)
    files = (glob.glob('common/**/*.txt', recursive=True)
             + glob.glob('events/*.txt')
             + glob.glob('history/**/*.txt', recursive=True))
    dead = ref_scan(files, defs)
    mine, inherited = {'unit': [], 'equip': [], 'name': []}, {'unit': [], 'equip': [], 'name': []}
    for cls, hits in dead.items():
        for h in hits:
            (inherited if vanilla and _vanilla_contains(vanilla, h[0], h[2]) else mine)[cls].append(h)
    total = sum(len(v) for v in mine.values())
    inh = sum(len(v) for v in inherited.values())
    print(f'defined: {len(defs)} (units {counts["unit"]} | equip {counts["equip"]} | '
          f'names {counts["name"]} | other {counts["other"]}'
          + (' + vanilla' if vanilla else '')
          + f') | dead refs: {total} '
          f'(units {len(mine["unit"])} | equip {len(mine["equip"])} | names {len(mine["name"])})'
          + (f' | vanilla-inherited (documented, uncounted): {inh}' if vanilla else ''))
    for cls in ('unit', 'equip', 'name'):
        for rel, line, vid in mine[cls][:40]:
            print(f'  {cls:5} {rel}:{line} {vid}')
        for rel, line, vid in inherited[cls][:20]:
            print(f'  [{cls}-inherited] {rel}:{line} {vid}')
    return 1 if total else 0


DEFAULT_VANILLA = 'C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV'

if __name__ == '__main__':
    # vanilla merge is ON by default when the standard install exists — mod
    # files sit ON TOP of vanilla defs, so a mod-only def set over-reports.
    van = DEFAULT_VANILLA if os.path.isdir(os.path.join(DEFAULT_VANILLA, 'common')) else None
    if '--no-vanilla' in sys.argv:
        van = None
    if '--vanilla' in sys.argv:
        i = sys.argv.index('--vanilla')
        van = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        if not van or not os.path.isdir(os.path.join(van or '', 'common')):
            print('--vanilla needs the HOI4 install dir')
            sys.exit(2)
    elif not van:
        print('note: vanilla dir not found — mod-only run (vanilla-resolvable ids will be flagged)')
    sys.exit(audit(van))
