#!/usr/bin/env python
"""Audit GFX + music references.

GFX: `name = "GFX_x"` defined in interface/*.gfx (spriteType/frameAnimatedSpriteType).
     Bare `GFX_x` tokens in common/events/interface/history must resolve — dead
     refs render as default texture, not a crash.

Music: `name = "x"` defined in music/*.asset. `play_song = x` must resolve —
       dead refs log errors but don't crash.

Usage: python tools/gfx_music_audit.py
"""
import re, glob, os, sys

def load_gfx():
    out = set()
    for f in glob.glob('interface/*.gfx'):
        txt = open(f, encoding='utf-8', errors='replace').read()
        # name = "GFX_x" spriteType definitions
        for m in re.finditer(r'\bname\s*=\s*"(GFX_[^"]+)"', txt):
            out.add(m.group(1))
        # texturefile = "gfx/foo/bar.dds" — the basename is also a usable GFX key
        for m in re.finditer(r'\btexturefile\s*=\s*"([^"]+)"', txt):
            out.add(m.group(1))
    return out

def load_songs():
    out = set()
    for f in glob.glob('music/**/*.asset', recursive=True) + glob.glob('music/*.txt'):
        for line in open(f, encoding='utf-8', errors='replace'):
            m = re.search(r'\bname\s*=\s*"([^"]+)"', line)
            if m: out.add(m.group(1))
    return out

def audit():
    gfx = load_gfx()
    songs = load_songs()
    dead_gfx, dead_song = [], []
    gfx_pat = re.compile(r'\b(GFX_[A-Za-z0-9_]+)\b')
    song_pat = re.compile(r'\bplay_song\s*=\s*([A-Za-z0-9_:". -]+)')
    for f in glob.glob('common/**/*.txt', recursive=True) + glob.glob('events/*.txt') + glob.glob('interface/*.gui'):
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in gfx_pat.finditer(code):
                if m.group(1) not in gfx:
                    dead_gfx.append((os.path.basename(f), i, m.group(1)))
            for m in song_pat.finditer(code):
                s = m.group(1).strip(' "')
                if s not in songs:
                    dead_song.append((os.path.basename(f), i, s))
    print(f'GFX defined: {len(gfx)} | songs defined: {len(songs)}')
    print(f'dead GFX refs: {len(dead_gfx)} | dead song refs: {len(dead_song)}')
    for d in dead_gfx[:20]: print('  gfx ', d)
    for d in dead_song[:20]: print('  song', d)
    return 1 if (dead_gfx or dead_song) else 0

if __name__ == '__main__':
    sys.exit(audit())
