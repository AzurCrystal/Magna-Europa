#!/usr/bin/env python
"""Audit country TAG references.

Every `tag = X` / `original_tag = X` / `country_exists = X` / `is_subject_of = X` /
`is_neighbor_of = X` / `is_in_faction_with = X` / `overlord = X` reference must
match a tag defined in `common/country_tags/*.txt`,
`common/country_tag_aliases/*.txt`, or `common/countries/*.txt` (top-level
cosmetic-tag keys such as `OZAV = { color = ... }`), or be a `D00`-`D99`
dynamic tag which is always valid. Dead references mean the trigger/effect
silently never fires.

Optionally `--vanilla <dir>` merges vanilla tag definitions to suppress refs
the mod inherits unchanged.

Usage: python tools/tag_ref_audit.py [--vanilla <hoi4 dir>]
"""
import re, glob, os, sys, argparse

REF_KEYS = ('tag','original_tag','country_exists','is_subject_of','is_neighbor_of',
            'is_in_faction_with','overlord','has_war_with','has_capitulated_to',
            'is_subject','is_puppet_of','is_ally_with','is_enemy_with','is_guaranteed_by')
DYN = re.compile(r'^D\d{2}$')
SCOPES = {'ROOT','PREV','FROM','CAPITAL','OWNER','CONTROLLER','THIS','OVERLORD','ORIGINAL_OWNER'}
PAT = re.compile(r'\b(' + '|'.join(REF_KEYS) + r')\s*=\s*([A-Z]{3,4})\b')

def tags(d):
    out = set()
    for f in glob.glob(f'{d}/*.txt'):
        for line in open(f, encoding='utf-8', errors='replace'):
            m = re.match(r'\s*([A-Z0-9]{2,4})\s*=', line.split('#', 1)[0])
            if m: out.add(m.group(1))
    return out

def country_keys(d):
    """Top-level `TAG = { ... }` keys from common/countries/*.txt (cosmetic tags)."""
    out = set()
    for f in glob.glob(f'{d}/*.txt'):
        depth = 0
        for line in open(f, encoding='utf-8', errors='replace'):
            code = line.split('#', 1)[0]
            if depth == 0:
                m = re.match(r'\s*([A-Z]{3,4})\s*=', code)
                if m: out.add(m.group(1))
            depth += code.count('{') - code.count('}')
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla', default=None)
    a = ap.parse_args()
    defined = (tags('common/country_tags') | tags('common/country_tag_aliases')
               | country_keys('common/countries'))
    if a.vanilla:
        defined |= tags(os.path.join(a.vanilla, 'common/country_tags'))
        defined |= tags(os.path.join(a.vanilla, 'common/country_tag_aliases'))
        defined |= country_keys(os.path.join(a.vanilla, 'common/countries'))
    refs = []
    for f in glob.glob('common/**/*.txt', recursive=True) + glob.glob('events/*.txt') + glob.glob('history/**/*.txt', recursive=True):
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in PAT.finditer(code):
                t = m.group(2)
                if t not in defined and not DYN.match(t) and t not in SCOPES:
                    refs.append((os.path.basename(f), i, m.group(1), t))
    print(f'tags defined: {len(defined)} | dead refs: {len(refs)}')
    for r in refs[:40]: print('  ', r)
    return 1 if refs else 0

if __name__ == '__main__':
    sys.exit(main())
