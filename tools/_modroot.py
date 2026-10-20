#!/usr/bin/env python
"""Locate the mod payload root inside this monorepo.

The repo keeps mod products under mods/<Name>/ (Magna Europa, submods).
Mod-content audits run against exactly one mod dir:

  ME_MOD=<abs path to a mod dir>  — explicit override
  else                            — first mods/*/ dir containing descriptor.mod

cd_mod() chdirs into that root so tools can keep using relative globs
('common/...', 'history/...') unchanged, whether invoked from anywhere.
"""
import os, glob as _glob


def mod_root():
    env = os.environ.get('ME_MOD')
    if env:
        return os.path.abspath(env)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for cand in sorted(_glob.glob(os.path.join(repo, 'mods', '*'))):
        if os.path.isfile(os.path.join(cand, 'descriptor.mod')):
            return cand
    # fallback: pre-monorepo layout (payload at repo root)
    if os.path.isfile(os.path.join(repo, 'descriptor.mod')):
        return repo
    raise SystemExit('no mod root found under mods/*/ (set ME_MOD)')


def repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def cd_mod():
    os.chdir(mod_root())
