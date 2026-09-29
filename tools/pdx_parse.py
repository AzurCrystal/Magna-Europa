"""Clausewitz/PDSX parser — string-aware, comment-aware, quote-aware.

Grammar:
    file   := stmt*
    stmt   := key '=' value | bare_literal
    value  := '{' stmt* '}' | literal | string
    key    := literal | string
    comment:= '#' ... EOL   (only outside string)
    string := '"' ... '"'

Block: name, children=[(key,value,line), ...], open_line, close_line.
"""
from __future__ import annotations
import re, sys, io

_TOK = re.compile(r'''
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>\#[^\n]*)
  | (?P<open>\{)
  | (?P<close>\})
  | (?P<eq>=)
  | (?P<less><)
  | (?P<more>>)
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<lit>[^\s{}<>=#"']+)
''', re.X)

class Node:
    __slots__ = ('key','val','line','children','is_block','modifier')
    def __init__(self, key, val, line, children=None):
        self.key=key; self.val=val; self.line=line
        self.children=children
        self.is_block = children is not None
        self.modifier = None
    def __repr__(self):
        if self.is_block: return f'{self.key} = {{...}} @{self.line} ({len(self.children)} children)'
        return f'{self.key} = {self.val} @{self.line}'

def tokenize(text):
    import bisect
    nl=[0]+[m.end() for m in re.finditer(r'\n',text)]
    for m in _TOK.finditer(text):
        k = m.lastgroup
        if k in ('ws','comment'): continue
        v = m.group(0)
        if k=='str': v=v[1:-1]
        yield (k, v, bisect.bisect_right(nl,m.start()))

class Parser:
    def __init__(self, text):
        self.toks = list(tokenize(text))
        self.errors=[]
        self.i=0
    def peek(self):
        return self.toks[self.i] if self.i<len(self.toks) else ('eof',None,-1)
    def pop(self):
        t=self.toks[self.i]; self.i+=1; return t
    def parse(self):
        return self._stmts(end='eof')
    def _stmts(self, end, depth=0):
        out=[]
        while True:
            k,v,ln=self.peek()
            if k=='eof':
                if end!='eof': self.errors.append(f'unclosed block at eof (depth {depth})')
                break
            if k==end: break
            if k=='close':
                if end=='eof': self.errors.append(f'stray }} at line {ln}'); self.pop(); continue
                break
            self.pop()
            if k in ('lit','str'):
                kk,vv,ll=self.peek()
                if kk in ('eq','less','more'):
                    op=vv; self.pop()
                    k3,v3,l3=self.peek()
                    if k3=='open':
                        self.pop()
                        kids=self._stmts(end='close', depth=depth+1)
                        # consume '}' if present
                        if self.peek()[0]=='close': self.pop()
                        else: self.errors.append(f'missing }} for block opened at line {ln} ({v})')
                        out.append(Node(v,None,ln,kids))
                    elif k3 in ('lit','str'):
                        self.pop()
                        # modifier-then-block: `key = rgb { ... }`
                        if self.peek()[0]=='open':
                            self.pop()
                            kids=self._stmts(end='close', depth=depth+1)
                            if self.peek()[0]=='close': self.pop()
                            else: self.errors.append(f'missing }} at {ln} ({v}={v3})')
                            n=Node(v,None,ln,kids); n.modifier=v3
                            out.append(n)
                        else:
                            val=f'{op}{v3}' if op in ('<','>') else v3
                            out.append(Node(v,val,ln))
                    else:
                        out.append(Node(v,None,ln))
                else:
                    out.append(Node(v,None,ln,[]))
            elif k in ('eq','less','more'):
                pass
        return out

def parse_file(path):
    return Parser(open(path,encoding='utf-8',errors='replace').read()).parse()

def iter_blocks(nodes, depth=0, parent_key=None):
    for n in nodes:
        if n.is_block:
            yield n, depth, parent_key
            yield from iter_blocks(n.children, depth+1, n.key)

if __name__=='__main__':
    import sys
    p=sys.argv[1]
    top=parse_file(p)
    print('top-level entries:',len(top))
    for n in top[:30]: print(' ',n)
    # report depth anomalies
    for n,d,pk in iter_blocks(top):
        if d==0 and n.key=='focus': print('ORPHAN focus at line',n.line)
        if d==0 and n.key=='country_event': print('ORPHAN country_event at',n.line)
