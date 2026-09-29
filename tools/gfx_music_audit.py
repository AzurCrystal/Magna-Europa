#!/usr/bin/env python
"""Audit GFX + music references.

GFX defs: `name = "GFX_x"` in interface/*.gfx (spriteType/textSpriteType/
frameAnimatedSpriteType). Mod defs are merged with vanilla `interface/*.gfx`
when `--vanilla` is given — most GFX_* constants are engine resources defined
there, not mod sprites.
GFX refs: bare `GFX_x` tokens in common/, events/, history/ — NOT interface/
(interface files cross-reference their own sprites = noise).

Music defs: `name = "x"` in music/**/*.asset. `play_song = x` refs in
common/events. Mod has no .asset files → all refs must hit vanilla songs.

Dead GFX = broken icon (renders placeholder, spams error.log).
Dead song = error.log line, no crash.

Usage: python tools/gfx_music_audit.py --vanilla "<hoi4 dir>"
"""
import re, glob, os, sys, argparse

def load_gfx(*dirs):
    out = set()
    for d in dirs:
        for f in glob.glob(f'{d}/*.gfx'):
            txt = open(f, encoding='utf-8', errors='replace').read()
            for m in re.finditer(r'\bname\s*=\s*"(GFX_[^"]+)"', txt):
                out.add(m.group(1))
    return out

def load_songs(*dirs):
    out = set()
    for d in dirs:
        for f in glob.glob(f'{d}/**/*.asset', recursive=True):
            for m in re.finditer(r'\bname\s*=\s*"([^"]+)"',
                                 open(f, encoding='utf-8', errors='replace').read()):
                out.add(m.group(1))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--vanilla', default=None)
    a = ap.parse_args()
    gfx_dirs, song_dirs = ['interface'], ['music']
    if a.vanilla:
        gfx_dirs.append(os.path.join(a.vanilla, 'interface'))
        song_dirs.append(os.path.join(a.vanilla, 'music'))
    gfx, songs = load_gfx(*gfx_dirs), load_songs(*song_dirs)

    dead_gfx, dead_song = set(), set()
    gfx_pat = re.compile(r'\b(GFX_[A-Za-z0-9_]+)\b')
    song_pat = re.compile(r'\bplay_song\s*=\s*"?([A-Za-z0-9_. -]+)"?')
    scanned = (glob.glob('common/**/*.txt', recursive=True) +
               glob.glob('events/*.txt') + glob.glob('history/**/*.txt', recursive=True))
    for f in scanned:
        for i, line in enumerate(open(f, encoding='utf-8', errors='replace'), 1):
            code = line.split('#', 1)[0]
            for m in gfx_pat.finditer(code):
                if m.group(1) not in gfx:
                    dead_gfx.add((os.path.basename(f), i, m.group(1)))
            for m in song_pat.finditer(code):
                if m.group(1).strip() not in songs:
                    dead_song.add((os.path.basename(f), i, m.group(1).strip()))
    print(f'GFX defined: {len(gfx)} | songs defined: {len(songs)}')
    print(f'dead GFX refs: {len(dead_gfx)} | dead song refs: {len(dead_song)}')
    for d in sorted(dead_gfx)[:40]: print('  gfx ', d)
    for d in sorted(dead_song)[:40]: print('  song', d)
    return 1 if dead_song else 0

if __name__ == '__main__':
    sys.exit(main())
