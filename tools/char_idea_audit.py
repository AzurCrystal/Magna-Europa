#!/usr/bin/env python
"""Audit character + idea reference integrity.

Defs
  characters: keys at relative depth 1 inside `characters = {}` blocks in
      common/characters/*.txt (depth tracking, NOT indent — attribute
      sub-blocks like `advisor = {`/`portraits = {` are deeper and excluded).
  ideas: keys at relative depths 1 and 2 inside `ideas = {}` blocks in
      common/ideas/*.txt (depth 1 = bucket e.g. `country`/`political_advisor`,
      depth 2 = real idea id e.g. `CZE_army_readiness_3`; flat depth-1
      defs are kept too — harmless, never ref'd by bucket name).
  Key regex `[^\\s={}#]+` is unicode-safe (IRE_Éamon_de_valera) and
  accepts column-0 keys.

Refs (comment-stripped, whole-file finditer — multiline-safe)
  char refs: recruit_character / retire_character / kill_character /
      promote_character / promote_to_character / set_character_name = X,
      plus bare `character = X` (catches corps_commander / navy_leader /
      field_marshal / country_leader / has_country_leader / promote_character
      block interiors). Resolved against character defs.
  idea refs: add_idea(s) / remove_idea(s) / swap_ideas / available_ideas
      — `= X` scalar AND `= { X Y }` list-block interiors (bare ids only;
      keys and `key = value` values inside blocks are not refs).
      Resolved against idea defs OR character defs (`add_ideas` also
      accepts character ids — vanilla hires character advisors this way).
  token refs: `idea_token = X` and `token = X` inside `advisor = {}` blocks
      — resolved against idea defs OR character defs (this mod points
      advisor idea_tokens at character ids); dead only if in neither.

Skipped
  Values: yes/no, SCOPES (ROOT/PREV/FROM/...), `var:`/`event_target:` refs
  (`:` in value), scope-chained `PREV.x`, ids containing `_random_`
  (intentional vanilla unnamed ideas, e.g. ENG_random_communist_minister).
  Dirs: common/characters skipped for char refs (self-defs) but scanned
  for idea refs (idea_token lives there); common/ideas skipped for all
  ref scanning (defs only).
  has_idea / has_character triggers are checks, not refs — never scanned.

Usage: python tools/char_idea_audit.py
Exit 1 on real dead refs else 0.
"""
import re, glob, os, sys, bisect

IDPAT = r'[^\s={}#]+'
SCOPES = {'ROOT','PREV','FROM','CAPITAL','OWNER','CONTROLLER','THIS','OVERLORD'}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def mask_comments(txt):
    # strip comment text, keep newlines so line numbers stay exact
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

def inner_keys(path, outer, depths):
    """Keys of `key = {` blocks sitting at the given relative depth(s)
    inside `outer = {` blocks. Depth is tracked on raw text (comments
    masked) — a key counts when the newline preceding its line was seen
    while the scan depth was in `depths`."""
    out = {}
    for f in glob.glob(os.path.join(ROOT, path, '*.txt')):
        txt = mask_comments(open(f, encoding='utf-8-sig', errors='replace').read())
        for m in re.finditer(r'\b' + outer + r'\s*=\s*\{', txt):
            d, i = 1, m.end()
            while i < len(txt) and d:
                c = txt[i]
                if c == '{': d += 1
                elif c == '}': d -= 1
                elif c == '\n' and d in depths:
                    j = i + 1
                    while j < len(txt) and txt[j] in ' \t': j += 1
                    km = re.match(r'(' + IDPAT + r')\s*=\s*\{', txt[j:j+200])
                    if km:
                        out[km.group(1)] = os.path.basename(f)
                i += 1
    return out

CHAR_VERB = re.compile(
    r'\b(?:recruit_character|retire_character|kill_character|promote_character|'
    r'promote_to_character|set_character_name)\s*=\s*(' + IDPAT + r')')
CHAR_ATTR = re.compile(r'\bcharacter\s*=\s*(' + IDPAT + r')')
IDEA_SCALAR = re.compile(
    r'\b(add_ideas?|remove_ideas?|available_ideas|swap_ideas|idea_token)\s*=\s*(' + IDPAT + r')')
IDEA_BLOCK = re.compile(
    r'\b(?:add_ideas?|remove_ideas?|available_ideas|swap_ideas)\s*=\s*\{')
ADVISOR = re.compile(r'\badvisor\s*=\s*\{')
ADV_TOKEN = re.compile(r'\btoken\s*=\s*(' + IDPAT + r')')
BARE = re.compile(IDPAT)

def norm(v):
    return v.strip('"').strip("'")

def skip(v):
    # ':' = var:/event_target: refs; '[x]' = scripted-variable substitution
    # (e.g. CZE_skoda_..._[SKODA_OPT]_idea — resolves at runtime, unverifiable)
    return (not v or v in SCOPES or v in ('yes','no') or ':' in v or '[' in v
            or v.split('.')[0] in SCOPES or '_random_' in v)

def bare_ids(txt, a, b):
    """Bare ids in txt[a:b]: tokens that are neither `key =` nor `= value`."""
    for m in BARE.finditer(txt, a, b):
        if re.match(r'\s*=', txt[m.end():]): continue      # key
        prev = txt[:m.start()].rstrip()
        if prev.endswith('='): continue                     # value
        yield m.group(0), m.start()

def audit():
    chars = inner_keys('common/characters', 'characters', {1})
    ideas = inner_keys('common/ideas', 'ideas', {1, 2})

    char_refs, idea_refs, token_refs = [], [], []
    seen = set()
    files = (glob.glob(os.path.join(ROOT, 'common/**/*.txt'), recursive=True)
             + glob.glob(os.path.join(ROOT, 'events/*.txt'))
             + glob.glob(os.path.join(ROOT, 'history/**/*.txt'), recursive=True))
    for f in files:
        rel = os.path.relpath(f, ROOT).replace('\\', '/')
        in_chars = rel.startswith('common/characters/')
        if rel.startswith('common/ideas/'): continue        # defs only
        txt = mask_comments(open(f, encoding='utf-8-sig', errors='replace').read())
        nl = [i for i, c in enumerate(txt) if c == '\n']    # sorted offsets

        def line_of(pos):
            return bisect.bisect_right(nl, pos) + 1

        if not in_chars:                                    # self-defs live here
            for pat in (CHAR_VERB, CHAR_ATTR):
                for m in pat.finditer(txt):
                    v = norm(m.group(1))
                    key = ('char', rel, line_of(m.start()), v)
                    if not skip(v) and v not in chars and key not in seen:
                        seen.add(key); char_refs.append(key[1:])
        for m in IDEA_SCALAR.finditer(txt):
            v = norm(m.group(2))
            kind = 'token' if m.group(1) == 'idea_token' else 'idea'
            key = (kind, rel, line_of(m.start()), v)
            if key in seen or skip(v): continue
            if v not in ideas and v not in chars:
                seen.add(key)
                (token_refs if kind == 'token' else idea_refs).append(key[1:])
        for m in IDEA_BLOCK.finditer(txt):
            b = block_end(txt, m.end() - 1)
            for v0, pos in bare_ids(txt, m.end(), b):
                v = norm(v0)
                key = ('idea', rel, line_of(pos), v)
                if not skip(v) and v not in ideas and v not in chars and key not in seen:
                    seen.add(key); idea_refs.append(key[1:])
        for m in ADVISOR.finditer(txt):
            b = block_end(txt, m.end() - 1)
            for tm in ADV_TOKEN.finditer(txt, m.end(), b):
                v = norm(tm.group(1))
                key = ('token', rel, line_of(tm.start()), v)
                if not skip(v) and v not in ideas and v not in chars and key not in seen:
                    seen.add(key); token_refs.append(key[1:])

    char_refs.sort(); idea_refs.sort(); token_refs.sort()
    print(f'chars defined: {len(chars)} | ideas defined: {len(ideas)}')
    print(f'dead refs: char={len(char_refs)} idea={len(idea_refs)} token={len(token_refs)}')
    for r in char_refs[:40]: print('  char ', r)
    for r in idea_refs[:60]: print('  idea ', r)   # full list, cap 60
    for r in token_refs[:40]: print('  token', r)
    return 1 if (char_refs or idea_refs or token_refs) else 0

if __name__ == '__main__':
    sys.exit(audit())
