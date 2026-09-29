#!/usr/bin/env python
"""Audit cross-entity reference integrity (8 classes).

Classes: focus / focus_tree, decision / decision_category, technology,
unit+equipment, OOB filename, random_events, ideology, idea.

Refs are regex-scanned over common/, events/, history/ — comment-stripped.
Directories that only DEFINE entities are skipped for ref scanning:
characters/, ideas/, units/, technologies/, ideologies/, countries/,
country_tags*/.

Default def source is the mod. --vanilla PATH merges vanilla defs and
re-scans vanilla refs; ids dead in vanilla itself are reported as parity
(documented), not flagged. Exit 1 iff any class has a non-parity dead ref.

Usage: python tools/entity_ref_audit.py [--vanilla <hoi4-root>]
"""
import re, glob, os, sys, bisect

IDPAT = r'[^\s={}#]+'
WORD = r'[A-Za-z_][A-Za-z0-9_]*'
SCOPES = {'ROOT', 'PREV', 'FROM', 'CAPITAL', 'OWNER', 'CONTROLLER', 'THIS',
          'OVERLORD'}
MODROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# common/<sub> dirs skipped for REF scanning only (still mined for defs)
REF_SKIP = {'characters', 'ideas', 'units', 'technologies', 'ideologies',
            'countries'}
REF_GLOBS = ('common/**/*.txt', 'events/**/*.txt', 'history/**/*.txt')

def mask_comments(txt):
    return re.sub(r'#[^\n]*', '', txt)

def block_end(txt, brace_pos):
    """txt[brace_pos] == '{' -> index of matching '}' (or len(txt))."""
    d, i = 0, brace_pos
    while i < len(txt):
        c = txt[i]
        if c == '{': d += 1
        elif c == '}':
            d -= 1
            if d == 0: return i
        i += 1
    return len(txt)

TOK = re.compile(r'[{}]|(' + IDPAT + r')\s*=\s*(\{?)')
VAL = re.compile(r'\s*("(?:\\.|[^"\\])*"|' + IDPAT + r')')

def kv_iter(txt, a=0, b=None):
    """Single pass over txt[a:b]. Yields:
      ('block', key, None, pos, brace_pos, depth, stack)
      ('kv',    key, val,  pos, -1,        depth, stack)
    depth = brace depth at the token (slice-relative). stack is the live
    list of (name|None, opened_depth) enclosing braces — read it while the
    generator is suspended; it is mutated on resume."""
    if b is None: b = len(txt)
    stack = []
    d = 0
    for m in TOK.finditer(txt, a, b):
        g2 = m.group(2)
        t = m.group(0)
        if g2 is None and (t == '{' or t == '}'):
            if t == '{':
                stack.append((None, d)); d += 1
            else:
                if stack: stack.pop()
                d -= 1
            continue
        if g2:  # `key = {` — the brace is part of the match
            yield 'block', m.group(1), None, m.start(), m.end() - 1, d, stack
            stack.append((m.group(1), d)); d += 1
        else:
            vm = VAL.match(txt, m.end())
            yield 'kv', m.group(1), vm.group(1) if vm else None, \
                  m.start(), -1, d, stack

BARE = re.compile(IDPAT)

def bare_ids(txt, a, b):
    """Bare ids in txt[a:b]: tokens that are neither `key =` nor `= value`."""
    for m in BARE.finditer(txt, a, b):
        if re.match(r'\s*=', txt[m.end():]): continue      # key
        prev = txt[:m.start()].rstrip()
        if prev.endswith('='): continue                     # value
        yield m.group(0), m.start()

def norm(v):
    return v.strip('"').strip("'")

def isnum(v):
    return re.fullmatch(r'[+\-]?[0-9.]+', norm(v)) is not None

def skip(v):
    # ':' = var:/event_target:/mio: namespaced refs; '[x]' = scripted-var
    # substitution resolved at runtime -> unverifiable.
    if not v or v in SCOPES or v in ('yes', 'no'): return True
    if ':' in v or '[' in v or '_random_' in v: return True
    if v.split('.')[0] in SCOPES: return True
    if re.fullmatch(r'[0-9.\-]+', v): return True
    return False

def read(root, rel):
    return mask_comments(open(os.path.join(root, rel), encoding='utf-8-sig',
                              errors='replace').read())

def load_texts(root):
    """{relpath: (comment-masked text, [newline positions])} for ref dirs."""
    texts = {}
    for g in REF_GLOBS:
        for f in glob.glob(os.path.join(root, g), recursive=True):
            rel = os.path.relpath(f, root).replace('\\', '/')
            if rel.startswith('common/'):
                sub = rel.split('/')[1]
                if sub in REF_SKIP or sub.startswith('country_tags'):
                    continue
            txt = read(root, rel)
            texts[rel] = (txt, [m.start() for m in re.finditer(r'\n', txt)])
    return texts

# ---------------------------------------------------------------- defs

def _ids_in(txt, a, b, depths):
    """`key = {` ids at relative depth(s) inside txt[a:b] (0 = children)."""
    for kind, key, _v, pos, _bp, d, _st in kv_iter(txt, a, b):
        if kind == 'block' and d in depths:
            yield key

def collect_defs(root):
    D = {}
    # 1. focus ids / focus_tree ids (scalar `id` inside focus/focus_tree blocks)
    foci, trees = set(), set()
    for f in glob.glob(os.path.join(root, 'common/national_focus/*.txt')) + \
             glob.glob(os.path.join(root, 'common/continuous_focus/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, bp, d, _st in kv_iter(txt):
            if kind == 'block' and key in ('focus', 'shared_focus',
                                           'focus_tree',
                                           'continuous_focus_palette'):
                a, b = bp + 1, block_end(txt, bp)
                for k2, v2, _p2, _d2, _st2 in _scalars(txt, a, b, {0, 1}):
                    if k2 != 'id': continue
                    (trees if key == 'focus_tree' else foci).add(norm(v2))
    D['focus'], D['focus_tree'] = foci, trees
    # 2. decision ids = depth-1 keys in common/decisions/*.txt;
    #    category ids = depth-0 keys in categories/*.txt + file stems
    decs, cats = set(), set()
    for f in glob.glob(os.path.join(root, 'common/decisions/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, _bp, d, _st in kv_iter(txt):
            if kind == 'block' and d == 1:
                decs.add(key)
    for f in glob.glob(os.path.join(root, 'common/decisions/categories/*.txt')):
        cats.add(os.path.splitext(os.path.basename(f))[0])
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, _bp, d, _st in kv_iter(txt):
            if kind == 'block' and d == 0:
                cats.add(key)
    D['decision'], D['decision_category'] = decs, cats
    # 3. tech ids = depth-0/1 keys inside `technologies = {}` blocks
    techs = set()
    for f in glob.glob(os.path.join(root, 'common/technologies/**/*.txt'),
                       recursive=True) + \
             glob.glob(os.path.join(root, 'common/technology/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, bp, _d, _st in kv_iter(txt):
            if kind == 'block' and key == 'technologies':
                techs.update(_ids_in(txt, bp + 1, block_end(txt, bp), {0, 1}))
    D['technology'] = techs
    # 4. unit + equipment ids = depth-0/1 keys in common/units/**
    units = set()
    for f in glob.glob(os.path.join(root, 'common/units/**/*.txt'),
                       recursive=True):
        txt = read(root, os.path.relpath(f, root))
        units.update(_ids_in(txt, 0, len(txt), {0, 1}))
        # duplicate_archetypes: variant_name = { equip_id = display_key } —
        # the LHS ids are real equipment defs created by the alias engine
        for kind, key, val, _p, bp, _d, _st in kv_iter(txt):
            if key not in ('variant_name', 'derived_variant_name'): continue
            if kind == 'block':
                units.update(k for k, _v, _p, _d, _s in _scalars(
                    txt, bp + 1, block_end(txt, bp), {0}))
            elif val:
                units.add(norm(val))
    D['unit_equip'] = units
    # 5. oob stems
    D['oob'] = {os.path.splitext(os.path.basename(f))[0]
                for f in glob.glob(os.path.join(root, 'history/units/*.txt'))}
    # 6. event ids (plus bare numeric tail)
    evs = set()
    for f in glob.glob(os.path.join(root, 'events/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for m in re.finditer(r'^\s*id\s*=\s*([A-Za-z0-9_.]+)', txt, re.M):
            evs.add(m.group(1))
            evs.add(m.group(1).split('.')[-1])
    D['event'] = evs
    # 7. ideology ids = depth-0 keys in `ideologies` + `types` children
    ideos = set()
    for f in glob.glob(os.path.join(root, 'common/ideologies/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, bp, _d, _st in kv_iter(txt):
            if kind == 'block' and key == 'ideologies':
                ideos.update(_ids_in(txt, bp + 1, block_end(txt, bp), {0}))
            elif kind == 'block' and key == 'types':
                ideos.update(_ids_in(txt, bp + 1, block_end(txt, bp), {0}))
    D['ideology'] = ideos
    # 8. idea ids = depth-0/1 keys in `ideas` blocks + idea_token defs
    ideas = set()
    for f in glob.glob(os.path.join(root, 'common/ideas/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for kind, key, _v, _p, bp, _d, _st in kv_iter(txt):
            if kind == 'block' and key == 'ideas':
                ideas.update(_ids_in(txt, bp + 1, block_end(txt, bp), {0, 1}))
    for f in glob.glob(os.path.join(root, 'common/characters/*.txt')):
        txt = read(root, os.path.relpath(f, root))
        for m in re.finditer(r'\bidea_token\s*=\s*(' + IDPAT + ')', txt):
            ideas.add(norm(m.group(1)))
    D['idea'] = ideas
    return D

def _scalars(txt, a, b, depths):
    """`key = value` pairs at relative depth(s); yields (key, val, pos, d, stack)."""
    for kind, key, val, pos, _bp, d, st in kv_iter(txt, a, b):
        if kind == 'kv' and d in depths:
            yield key, val, pos, d, st

def merge_defs(dst, src):
    for k, v in src.items():
        dst.setdefault(k, set()).update(v)

# ---------------------------------------------------------------- refs

# scalar `verb = X` -> ref class
VERBS = {
    'focus': 'focus', 'complete_national_focus': 'focus',
    'uncomplete_national_focus': 'focus', 'unlock_national_focus': 'focus',
    'select_focus': 'focus', 'has_completed_focus': 'focus',
    'has_selected_focus': 'focus', 'has_focus': 'focus',
    'has_focus_tree': 'focus_tree', 'load_focus_tree': 'focus_tree',
    'activate_mission': 'decision', 'has_decision': 'decision',
    'has_active_mission': 'decision', 'add_days_mission_timeout': 'decision',
    'unlock_decision_tooltip': 'decision',
    'has_tech': 'technology',
    'has_government': 'ideology', 'ruling_party': 'ideology',
    'ideology': 'ideology',
    'has_idea': 'idea', 'add_idea': 'idea', 'remove_idea': 'idea',
    'swap_ideas': 'idea', 'has_ideas': 'idea', 'add_ideas': 'idea',
    'remove_ideas': 'idea', 'idea_token': 'idea', 'add_timed_idea': 'idea',
    'available_ideas': 'idea',
    'has_equipment': 'unit_equip',
    'load_oob': 'oob', 'oob': 'oob',
}
# `key = val` inside this block whose value is a ref: {block: {key: class}}
INNER_SCALARS = {
    'load_focus_tree': {'tree': 'focus_tree'},
    'load_oob': {'id': 'oob'},
    'unlock_decision_tooltip': {'decision': 'decision'},
    'swap_ideas': {'add_idea': 'idea', 'remove_idea': 'idea'},
    'add_timed_idea': {'idea': 'idea', 'timed_idea': 'idea'},
    'add_tech_bonus': {'technology': 'technology'},
    'research_bonus': {'technology': 'technology'},
    'ship': {'definition': 'unit_equip'},
}
# `type = X` inside these blocks -> unit_equip ref
TYPE_BLOCKS = {'sub_units', 'regiments', 'support', 'air_wing', 'air_wings',
               'ship', 'create_equipment_variant',
               'add_equipment_to_stockpile', 'add_equipment_production',
               'equipment', 'has_equipment', 'units', 'division'}
# container block -> (class, rel kv depths, rel block depths).
# `key = <number>` and `key = {` items at those depths are id refs.
KV_CONTAINERS = {
    'set_technology': ('technology', {0}, set(), None),
    'set_politics': ('ideology', {0, 1}, {1}, None),
    'set_popularity': ('ideology', {0}, set(), None),
    'add_popularity': ('ideology', {0}, set(), None),
    'equipment': ('unit_equip', {0}, {0, 1}, None),
    'has_equipment': ('unit_equip', {0}, {0}, None),
    'ship': ('unit_equip', {0}, {0, 1}, None),
    'air_wing': ('unit_equip', {0}, {0, 1}, None),
    'air_wings': ('unit_equip', set(), {1}, None),
    'sub_units': ('unit_equip', {0}, {0}, None),
    'regiments': ('unit_equip', {0}, {0}, None),
    'support': ('unit_equip', {0}, {0}, None),
    'units': ('unit_equip', set(), {0}, 'history/'),
    'division': ('unit_equip', {0}, {0}, 'history/'),
    'add_equipment_to_stockpile': ('unit_equip', {0}, {0}, None),
    'add_equipment_production': ('unit_equip', {0}, {0}, None),
    'add_idea': ('idea', {0, 1}, {0, 1}, None),
    'remove_idea': ('idea', {0, 1}, {0, 1}, None),
    'add_ideas': ('idea', {0, 1}, {0, 1}, None),
    'remove_ideas': ('idea', {0, 1}, {0, 1}, None),
    'swap_ideas': ('idea', {0, 1}, {0, 1}, None),
    'add_timed_idea': ('idea', {0, 1}, {0, 1}, None),
    'has_idea': ('idea', {0}, {0}, None),
    'available_ideas': ('idea', {0, 1}, {0, 1}, None),
}
# keys inside item-blocks that are never entity ids
ITEM_BAN = {
    'type', 'amount', 'owner', 'creator', 'version_name', 'name', 'location',
    'x', 'y', 'experience', 'start_experience_factor', 'division_name',
    'is_name_ordered', 'name_order', 'requested_factories', 'progress',
    'efficiency', 'industrial_manufacturer', 'popularity', 'days',
    'days_re_add', 'is_default', 'modifier', 'factor', 'add', 'equipment',
    'start_manpower_factor', 'election_frequency', 'ordered_name',
    'start_equipment_factor', 'fleet', 'officer', 'civilian_factories',
    'instant_effect', 'version', 'producer', 'count', 'naval_base', 'base',
    'allow_spawning_on_enemy_provs', 'prioritize_location', 'portraits',
    'large', 'small', 'months',
    'units', 'division', 'sub_units', 'regiments', 'support', 'task_force',
    'navy', 'air_wings', 'air_wing', 'ship', 'parties', 'elections_allowed',
    'last_election', 'ruling_party', 'event', 'effect', 'priority',
    'days_mission_timeout', 'selectable_mission', 'fire_only_once',
    'timeout_effect', 'cancel_if_not_visible', 'activation', 'visible',
    'available', 'allowed', 'icon', 'picture', 'scripted_gui',
    'visible_when_empty', 'name_group', 'parent_version', 'modules',
    'upgrades', 'limit', 'if', 'else', 'hidden_effect',
    'custom_effect_tooltip', 'custom_trigger_tooltip', 'division_template',
    'air_equipment', 'mapicon', 'active', 'lock', 'force_equipment_variants',
    'can_be_boosted', 'ace', 'air_experience', 'experience_loss_factor',
    'oob', 'set_oob', 'load_oob', 'capital', 'set_capital', 'id', 'hours',
    'random', 'days', 'offset', 'technology', 'bonus', 'uses', 'value',
    'var', 'limit_to_airfields', 'size', 'wing', 'template', 'keep_completed',
    'completion_reward', 'available', 'cancel', 'prerequisite',
    'mutually_exclusive', 'text', 'cost', 'remove_cost', 'fire_only_once',
    'ai_will_do', 'allowed_trigger', 'dismantle_cost', 'is_good',
}
# idea-list blocks that take bare ids
IDEA_BARE = ('add_idea', 'remove_idea', 'add_ideas', 'remove_ideas',
             'swap_ideas', 'add_timed_idea', 'has_idea', 'available_ideas')

DOTTED = re.compile(r'\b(' + WORD + r'\.\d+)\b')
GT_KEY = re.compile(r'\b(' + WORD + r')\s*[<>]')
DIVSTR = re.compile(r'\bdivision\s*=\s*"((?:\\.|[^"#])*)"')
DIVKV = re.compile(r'(' + WORD + r')\s*=')
BLOCKPAT = {kw: re.compile(r'\b' + kw + r'\s*=\s*\{')
            for kw in set(IDEA_BARE) | {'random_events', 'has_equipment'}}

def blocks(txt, kw):
    pat = BLOCKPAT.get(kw) or re.compile(r'\b' + kw + r'\s*=\s*\{')
    for m in pat.finditer(txt):
        yield m.end(), block_end(txt, m.end() - 1)

def scan_refs(texts):
    """-> {class: [(file, line, id)]}"""
    refs = {}
    seen = set()

    def add(cls, rel, nl, pos, vid):
        v = norm(vid)
        if skip(v): return
        ln = bisect.bisect_left(nl, pos) + 1
        key = (cls, rel, ln, v)
        if key in seen: return
        seen.add(key)
        refs.setdefault(cls, []).append((rel, ln, v))

    for rel, (txt, nl) in texts.items():
        is_decfile = (rel.startswith('common/decisions/')
                      and rel.count('/') == 2)
        for kind, key, val, pos, _bp, d, stack in kv_iter(txt):
            top = next((s for s in reversed(stack) if s[0]), None)
            if kind == 'kv':
                cls = VERBS.get(key)
                if cls and val is not None:
                    add(cls, rel, nl, pos, val)
                if val is not None and (key == 'oob' or key == 'load_oob'
                        or (key.startswith('set_') and key.endswith('oob'))):
                    add('oob', rel, nl, pos, val)
                if top:
                    kw, od = top
                    isc = INNER_SCALARS.get(kw)
                    if isc and d == od + 1 and key in isc and val is not None:
                        add(isc[key], rel, nl, pos, val)
                    if key == 'type' and kw in TYPE_BLOCKS and val is not None:
                        add('unit_equip', rel, nl, pos, val)
                # ancestor containers: X = <number> items at rel depths
                for kw, od in reversed(stack):
                    if not kw: continue
                    cont = KV_CONTAINERS.get(kw)
                    if not cont: continue
                    if cont[3] and not rel.startswith(cont[3]): continue
                    rd = d - (od + 1)
                    if rd in cont[1] and val is not None and isnum(val) \
                            and key not in ITEM_BAN and not key.isdigit():
                        add(cont[0], rel, nl, pos, key)
            else:  # block
                if is_decfile and d == 0:
                    add('decision_category', rel, nl, pos, key)
                for kw, od in reversed(stack):
                    if not kw: continue
                    cont = KV_CONTAINERS.get(kw)
                    if not cont: continue
                    if cont[3] and not rel.startswith(cont[3]): continue
                    if (d - (od + 1)) in cont[2] \
                            and key not in ITEM_BAN and not key.isdigit():
                        add(cont[0], rel, nl, pos, key)
        # bare idea lists: add_ideas = { id1 id2 }
        for kw in IDEA_BARE:
            for a, b in blocks(txt, kw):
                for t, p in bare_ids(txt, a, b):
                    add('idea', rel, nl, p, t)
        # has_equipment = { id > 5 }
        for a, b in blocks(txt, 'has_equipment'):
            for m in GT_KEY.finditer(txt, a, b):
                add('unit_equip', rel, nl, a + m.start(), m.group(1))
        # random_events = { N = ns.id }
        for a, b in blocks(txt, 'random_events'):
            for m in DOTTED.finditer(txt, a, b):
                add('event', rel, nl, a + m.start(), m.group(1))
        # division = "..." legacy strings: inner `x = N` unit ids
        for m in DIVSTR.finditer(txt):
            inner = m.group(1).replace('\\"', '"')
            for km in DIVKV.finditer(inner):
                k = km.group(1)
                if k not in ITEM_BAN and not k.isdigit():
                    add('unit_equip', rel, nl, m.start(), k)
    return refs

# ---------------------------------------------------------------- driver

def audit(vanilla=None):
    defs = collect_defs(MODROOT)
    refs = scan_refs(load_texts(MODROOT))
    vdead_ids = set()
    if vanilla:
        vdefs = collect_defs(vanilla)
        vrefs = scan_refs(load_texts(vanilla))
        vdead_ids = {v for cls, rows in vrefs.items() for _, _, v in rows
                     if v not in vdefs.get(cls, set())}
        merge_defs(defs, vdefs)
    worst = 0
    order = ['focus', 'focus_tree', 'decision', 'decision_category',
             'technology', 'unit_equip', 'oob', 'event', 'ideology', 'idea']
    for cls in order:
        defined = defs.get(cls, set())
        dead, parity = [], []
        for rel, ln, v in refs.get(cls, []):
            if v in defined: continue
            (parity if v in vdead_ids else dead).append((rel, ln, v))
        # >80% vanilla-parity => documented-not-flagged: demote to WARN
        demoted = bool(dead) and len(parity) / (len(dead) + len(parity)) > 0.8
        if demoted:
            print(f'{cls:<18} defined={len(defined):<5} dead=0'
                  f'    parity={len(parity)} demoted={len(dead)} [WARN]')
        else:
            status = 'FAIL' if dead else ('WARN' if parity else 'OK')
            print(f'{cls:<18} defined={len(defined):<5} dead={len(dead):<4}'
                  f'{f" parity={len(parity)}" if parity else ""} [{status}]')
        for rel, ln, v in dead[:20]:
            tag = '(demoted suspect) ' if demoted else ''
            print(f'    {tag}{rel}:{ln}: {v}')
        if len(dead) > 20:
            print(f'    ... +{len(dead) - 20} more')
        for rel, ln, v in parity[:8]:
            print(f'    (vanilla-dead) {rel}:{ln}: {v}')
        if len(parity) > 8:
            print(f'    ... +{len(parity) - 8} more parity ids')
        if dead and not demoted: worst = 1
    return worst

if __name__ == '__main__':
    van = None
    if '--vanilla' in sys.argv:
        van = sys.argv[sys.argv.index('--vanilla') + 1]
    sys.exit(audit(van))
