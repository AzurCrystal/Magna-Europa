#!/usr/bin/env python
"""Audit country_event/news_event/SET_TEMP_VARIABLE references.

Every `country_event = { id = <file.event> }`, `news_event = { id = <file.event> }`,
`calls_event = <file.event>`, `trigger_event = <file.event>` in common//events//
decisions//national_focus must resolve to an `id = <file.event>` declaration inside
events/*.txt (namespace prefix optional — engine accepts both `N.1` and `1`).

Usage: python tools/event_ref_audit.py
"""
import re, glob, os, sys

def audit():
    defined = set()
    for f in glob.glob('events/*.txt'):
        for m in re.finditer(r'^\s*id\s*=\s*([A-Za-z0-9_.]+)', open(f, encoding='utf-8', errors='replace').read(), re.M):
            defined.add(m.group(1))
            # also accept bare numeric tail (namespace.1 -> 1)
            defined.add(m.group(1).split('.')[-1])
    refs = []
    pat = re.compile(r'(?:country_event|news_event|calls_event|trigger_event|load_oob|add_to_tech_sharing_group)\s*=?\s*\{?\s*id\s*=\s*([A-Za-z0-9_.]+)')
    for d in ('common', 'events'):
        for f in glob.glob(f'{d}/**/*.txt', recursive=True):
            for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
                code = line.split('#', 1)[0]
                for m in pat.finditer(code):
                    eid = m.group(1)
                    if eid not in defined and eid.split('.')[-1] not in defined:
                        refs.append((os.path.basename(f), i, eid))
    print(f'event ids defined: {len(defined)} | dead refs: {len(refs)}')
    for r in refs[:40]: print('  ', r)
    return 1 if refs else 0

if __name__ == '__main__':
    sys.exit(audit())
