#!/usr/bin/env python3
"""Audit province-level references in common/ + events/ against mod map.
Read-only. Produces _prov_audit_report.json + tsv.
"""
import json, os, re, sys, collections

ROOT = r'D:/Projects/Magna-Europa'
SCAN_DIRS = ['common', 'events']

mp = json.load(open(os.path.join(ROOT, '_mp.json')))
MOD_P = set(mp['mod_provinces'])          # ints present in mod province space
P2S = {int(k): v for k, v in mp['p2s'].items()}

# definition.csv types
PTYPE = {}
with open(os.path.join(ROOT, 'map', 'definition.csv'), encoding='utf-8', errors='replace') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        p = line.split(';')
        if len(p) < 5:
            continue
        try:
            pid = int(p[0])
        except ValueError:
            continue
        PTYPE[pid] = p[4]

def land_status(pid):
    """return 'land'|'sea'|'lake'|None(not in definition.csv)"""
    return PTYPE.get(pid)

# ---------- parser ----------
NUM = re.compile(r'^\d+$')
ASSIGN = re.compile(r'^\s*([A-Za-z_][\w:.]*)\s*(=|<|>|<=|>=)\s*(.*)$')

# keys whose DIRECT numeric value is a province id
DIRECT_PROV_KEYS = {'start_province', 'target_province', 'province',
                    'prioritize_location', 'location', 'reset_province_name',
                    'controls_province', 'add_province_modifier', 'move_unit'}
# keys that open a block containing `id = <prov>` or `province = <prov>` or `value`
BLOCK_PROV_KEYS = {'set_province_name', 'add_victory_points', 'set_vp'}
# keys that open a building block possibly containing province =
BUILDING_KEYS = {'add_building_construction', 'add_targeted_building_construction',
                 'free_building_slots', 'bunker', 'add_bunker',
                 'add_extra_state_shared_building_slots'}
PATH_KEYS = {'path'}  # inside build_railway
RAILWAY_KEYS = {'build_railway'}

# generic: any key name containing 'province' taking a numeric value
GENERIC_PROV_RE = re.compile(r'province', re.I)

violations = []   # dicts
checked = collections.Counter()

def add(file, line, key_chain, value, vtype, fix=None, conf=''):
    violations.append(dict(file=file, line=line, context=key_chain,
                           value=value, type=vtype, fix=fix, confidence=conf))

def classify(pid):
    """(in_mod, ptype) -> violation type or None"""
    if pid not in MOD_P:
        return 'NOT_IN_MOD'
    t = land_status(pid)
    if t is None:
        return 'NO_DEFCSV'      # in p2s but absent from definition.csv (shouldn't happen)
    if t != 'land':
        return f'NON_LAND({t})'
    return None

def scan_file(path):
    rel = os.path.relpath(path, ROOT).replace('/', '\\')
    try:
        text = open(path, encoding='utf-8-sig', errors='replace').read()
    except OSError:
        return
    # strip comments for brace tracking but keep line numbers
    lines = text.split('\n')
    # line -> list of (kind, content) tokens
    stack = []  # list of (keyname, line) enclosing named blocks; None = anonymous
    pending_key = None   # key that just got '=' and may open a block
    pending_line = None
    depth_ctx = []       # context names aligned with stack

    for ln, raw in enumerate(lines, 1):
        # remove comment
        code = raw.split('#', 1)[0]
        if not code.strip():
            continue
        i = 0
        # tokenize: braces and assignments
        for m in re.finditer(r'([{}])|([A-Za-z_][\w:.]*\s*(?:=|!=|<|>|<=|>=)[^={}\n]*)|([A-Za-z_][\w:.]*)|(-?\d+\.?\d*)', code):
            brace, assign, bareword, num = m.group(1), m.group(2), m.group(3), m.group(4)
            if brace == '{':
                # open block: attach pending key
                stack.append((pending_key or '<anon>', pending_line or ln))
                pending_key = None
            elif brace == '}':
                if stack:
                    stack.pop()
                pending_key = None
            elif assign is not None:
                mm = ASSIGN.match(assign)
                if not mm:
                    continue
                key, op, val = mm.group(1), mm.group(2), mm.group(3).strip()
                ctx = '/'.join(k for k, _ in stack[-4:])
                parent = stack[-1][0] if stack else None
                nums = re.findall(r'-?\d+', val)
                # value may open with '{' handled by brace token next loop; but
                # assignments like key = { a b } on one line need brace tokens:
                # our regex consumed them into `assign` group... check:
                if '{' in val:
                    # split: treat as block opener + inline contents
                    inner = val.split('{', 1)[1]
                    stack.append((key, ln))
                    inner_nums = re.findall(r'-?\d+', inner)
                    closed = '}' in inner
                    handle_value(rel, ln, key, parent, ctx, inner_nums, stack)
                    if closed:
                        stack.pop()
                    continue
                handle_value(rel, ln, key, parent, ctx, nums, stack)
                pending_key = key
                pending_line = ln
            elif bareword is not None:
                pending_key = None
            elif num is not None:
                # bare number inside a list block, e.g. path = { 1 2 3 }
                if stack and stack[-1][0] == 'path':
                    pid = int(num)
                    ctx = '/'.join(k for k, _ in stack[-4:])
                    in_rail = any(k == 'build_railway' for k, _ in stack)
                    checked['path_member'] += 1
                    v = classify(pid)
                    if v:
                        add(rel, ln, ctx, pid, v + ('' if in_rail else ':NONRAIL'))
                elif stack and stack[-1][0] in ('provinces', 'province_list') :
                    pid = int(num)
                    ctx = '/'.join(k for k, _ in stack[-4:])
                    checked['provinces_list_member'] += 1
                    v = classify(pid)
                    if v:
                        add(rel, ln, ctx, pid, v)
def handle_value(rel, ln, key, parent, ctx, nums, stack):
    chain = ctx + ('/' if ctx else '') + key
    if not nums:
        return
    in_railway = any(k == 'build_railway' for k, _ in stack)
    if key in PATH_KEYS and in_railway:
        for n in nums:
            pid = int(n)
            checked['path_member'] += 1
            v = classify(pid)
            if v:
                add(rel, ln, chain, pid, v)
        return
    if key in DIRECT_PROV_KEYS and len(nums) == 1:
        pid = int(nums[0])
        checked[f'{key}@{parent}'] += 1
        v = classify(pid)
        if v:
            add(rel, ln, chain, pid, v)
        return
    if parent in BLOCK_PROV_KEYS and key == 'id' and len(nums) == 1:
        pid = int(nums[0])
        checked[f'id@{parent}'] += 1
        v = classify(pid)
        if v:
            add(rel, ln, chain, pid, v)
        return
    if parent in BUILDING_KEYS and key == 'province' and len(nums) == 1:
        pid = int(nums[0])
        checked[f'province@{parent}'] += 1
        v = classify(pid)
        if v:
            add(rel, ln, chain, pid, v)
        return
    # generic province-ish key with numeric value
    if GENERIC_PROV_RE.search(key) and len(nums) == 1 and key not in DIRECT_PROV_KEYS:
        pid = int(nums[0])
        if pid > 5000 or pid not in MOD_P:   # only log suspicious generic hits
            checked[f'generic:{key}'] += 1
            v = classify(pid)
            if v:
                add(rel, ln, chain, pid, v, conf='generic-key')
        return
    # check-4: numeric literal >5000 in a province-position block
    if parent in BLOCK_PROV_KEYS | BUILDING_KEYS | {'build_railway'} or 'province' in (key or ''):
        for n in nums:
            pid = int(n)
            if pid > 5000 and pid not in MOD_P:
                add(rel, ln, chain, pid, 'NOT_IN_MOD>5000')

def main():
    files = []
    for d in SCAN_DIRS:
        for dirpath, _, names in os.walk(os.path.join(ROOT, d)):
            for n in names:
                if n.endswith('.txt'):
                    files.append(os.path.join(dirpath, n))
    for f in sorted(files):
        scan_file(f)
    # dedupe
    seen = set()
    out = []
    for v in violations:
        k = (v['file'], v['line'], v['value'], v['type'])
        if k in seen:
            continue
        seen.add(k)
        out.append(v)
    print('files scanned:', len(files))
    print('checked:', dict(checked))
    print('violations:', len(out))
    for v in sorted(out, key=lambda x: (x['file'], x['line'])):
        print(f"{v['file']}:{v['line']}\t{v['type']}\t{v['value']}\t{v['context']}")
    json.dump(out, open(os.path.join(ROOT, '_prov_audit_report.json'), 'w'), indent=1)

main()
