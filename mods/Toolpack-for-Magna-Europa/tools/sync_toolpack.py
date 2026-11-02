#!/usr/bin/env python3
"""sync_toolpack.py — re-sync Toolpack for Magna Europa: Reforged's script layer against a
freshly-downloaded "Toolpack without the Errors" mod folder.

Usage:  python tools/sync_toolpack.py <path-to-toolpack-root>
        e.g. python tools/sync_toolpack.py "C:/.../workshop/content/394360/2913150560"

Copies every toolpack file living under a directory that Magna Europa: Reforged
`replace_path`s (the whole script layer), then re-applies the ME capital-state
remap in common/scripted_guis/tpt.txt. Prints a summary; review with git diff.
"""
import os, shutil, sys

REPLACED_DIRS = (
    "common/decisions",
    "common/ideas",
    "common/on_actions",
    "common/scripted_effects",
    "common/scripted_guis",
    "common/scripted_localisation",
    "common/scripted_triggers",
    "common/units",
    "events",
)

# vanilla state id -> ME state id (capital-city VP holder, see PORTING.md)
TPT_REMAP = {3: 1452, 16: 571, 126: 2475, 64: 1650, 2: 876, 219: 229, 4: 1817, 10: 508}
CITY = {3: "Geneva", 16: "Paris", 126: "London", 64: "Berlin",
        2: "Rome", 219: "Moscow", 4: "Vienna", 10: "Warsaw"}

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main(src_root: str) -> int:
    if not os.path.isdir(src_root):
        sys.exit(f"no such dir: {src_root}")
    copied, removed = 0, 0
    wanted = set()
    for rel_dir in REPLACED_DIRS:
        src_dir = os.path.join(src_root, rel_dir.replace("/", os.sep))
        if not os.path.isdir(src_dir):
            continue
        for root, _dirs, files in os.walk(src_dir):
            for fn in files:
                src = os.path.join(root, fn)
                rel = os.path.relpath(src, src_root).replace(os.sep, "/")
                wanted.add(rel)
                dst = os.path.join(HERE, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                copied += 1
    # drop stale copies (files removed upstream)
    for rel_dir in REPLACED_DIRS:
        dst_dir = os.path.join(HERE, rel_dir.replace("/", os.sep))
        if not os.path.isdir(dst_dir):
            continue
        for root, _dirs, files in os.walk(dst_dir):
            for fn in files:
                dst = os.path.join(root, fn)
                rel = os.path.relpath(dst, HERE).replace(os.sep, "/")
                if rel not in wanted:
                    os.remove(dst); removed += 1
                    print(f"removed stale: {rel}")
    # re-apply tpt.txt remap
    tpt = os.path.join(HERE, "common/scripted_guis/tpt.txt".replace("/", os.sep))
    raw = open(tpt, "rb").read()
    enc = "utf-8-sig" if raw[:3] == b"\xef\xbb\xbf" else "cp1252"
    txt = raw.decode(enc)
    nl = "\r\n" if "\r\n" in txt else "\n"
    n = 0
    for v_id, me_id in TPT_REMAP.items():
        old = f"owns_state = {v_id}" + nl
        new = f"owns_state = {me_id} #ME patch: {CITY[v_id]} (vanilla {v_id})" + nl
        if old in txt:
            txt = txt.replace(old, new, 1); n += 1
        elif new in txt:
            n += 1  # already applied
        else:
            print(f"WARN: owns_state = {v_id} not found in tpt.txt")
    open(tpt, "wb").write(txt.encode(enc))
    print(f"copied {copied} files, removed {removed} stale, tpt remap {n}/8")
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
