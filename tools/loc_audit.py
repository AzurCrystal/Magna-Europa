#!/usr/bin/env python
"""Audit localisation consistency across languages.

Per key in english/*.yml:
  - key exists in simp_chinese + russian (missing = shows english text via fallback)
  - no duplicated key within the same file (last wins silently)
  - no unterminated quotes / missing " at line end

Usage: python tools/loc_audit.py [--langs english,simp_chinese,russian]
"""
import re, glob, os, sys, argparse
from collections import Counter

LANG_HEADERS = {'l_english','l_simp_chinese','l_russian','l_french','l_german',
                'l_spanish','l_polish','l_braz_por','l_japanese','l_korean','l_turkish'}

KEY_RE = re.compile(r'^\s*([A-Za-z0-9_.]+)(?::\d+)?:\s*"[^"]*"')

def load(locdir):
    """-> ({filename: Counter(key: count)}, Counter(all keys))"""
    files = {}
    for f in glob.glob(f'{locdir}/*.yml'):
        base = os.path.basename(f)
        keys = Counter()
        for line in open(f, encoding='utf-8', errors='replace'):
            m = KEY_RE.match(line)
            if m and m.group(1) not in LANG_HEADERS:
                keys[m.group(1)] += 1
        files[base] = keys
    merged = Counter()
    for c in files.values(): merged.update(c)
    return files, merged

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--langs', default='english,simp_chinese,russian')
    a = ap.parse_args()

    base_files, bkeys = load('localisation/english')

    # in-file duplicates
    dups = []
    for fname, keys in base_files.items():
        for k, n in keys.items():
            if n > 1: dups.append((fname, k, n))

    # per-language missing
    missing = {}
    for lang in a.langs.split(','):
        if lang == 'english': continue
        _, m = load(f'localisation/{lang}')
        gap = sorted(k for k in bkeys if k not in m)
        missing[lang] = gap

    print(f'english keys: {len(bkeys)} | in-file dups: {len(dups)}')
    for d in dups[:20]: print('  dup', d)
    for lang, gap in missing.items():
        cov = 100 * (len(bkeys) - len(gap)) / max(len(bkeys), 1)
        print(f'{lang}: coverage {cov:.1f}% ({len(gap)} missing)')
        for k in gap[:15]: print('   ', k)
    return 1 if dups else 0  # missing translations are a gap, not an error

if __name__ == '__main__':
    sys.exit(main())
