import re, collections, os, json, sys

ROOT = r'D:/Projects/Magna-Europa'
VROOT = r'C:/Program Files (x86)/Steam/steamapps/common/Hearts of Iron IV'
G_LINES = open(ROOT + r'/common/scripted_triggers/GER_scripted_triggers.txt', encoding='utf-8-sig').read().split('\n')

def load_idx(p):
    d = {}
    for ln in open(p, encoding='utf-8-sig'):
        parts = ln.rstrip('\n').split('\t')
        if len(parts) >= 2 and parts[0].strip().isdigit():
            d[int(parts[0])] = parts[1].strip()
    return d
G_MOD = load_idx(ROOT + '/_mod_state_index.txt')
G_VAN = load_idx(ROOT + '/_vanilla_state_index.txt')

G_SUS = []
for ln in open(ROOT + '/_dedup_suspects.txt', encoding='utf-8', errors='replace'):
    f, sid, lno, v, m, cnt = ln.rstrip('\n').split('\t')
    if 'GER_scripted_triggers' not in f: continue
    G_SUS.append(dict(line=int(lno[1:]), id=int(sid), van=v.replace('vanilla=',''), mod=m.replace('mod=',''), cnt=int(cnt[1:])))

def parse_blocks(lines):
    blocks=[]; depth=0; cur=None; st=None
    for idx,ln in enumerate(lines,1):
        code = ln.split('#')[0]
        if depth==0:
            mm = re.match(r'^([A-Za-z_][\w]*)\s*=\s*\{', ln.strip())
            if mm: cur,st = mm.group(1), idx
        depth += code.count('{')-code.count('}')
        if depth==0 and cur is not None:
            blocks.append((cur,st,idx)); cur=None
    return blocks

G_BLOCKS = parse_blocks(G_LINES)
V_LINES = open(VROOT + r'/common/scripted_triggers/GER_scripted_triggers.txt', encoding='utf-8-sig').read().split('\n')
V_BLOCKS = parse_blocks(V_LINES)

STATE_RE = re.compile(r'^\s*state\s*=\s*(\d+)\s*(?:#.*)?$')
SCOPE_RE = re.compile(r'^\s*(\d+)\s*=\s*\{')
def ids_in(lines_, s, e):
    out=[]
    for i in range(s,e+1):
        mm = STATE_RE.match(lines_[i-1]) or SCOPE_RE.match(lines_[i-1])
        if mm: out.append((i,int(mm.group(1))))
    return out
def v_ids(name):
    for n,s,e in V_BLOCKS:
        if n==name: return [i for _,i in ids_in(V_LINES,s,e)]
    return None
def m_ids(name):
    for n,s,e in G_BLOCKS:
        if n==name: return ids_in(G_LINES,s,e)
    return []
def g_block_of(line):
    for n,s,e in G_BLOCKS:
        if s<=line<=e: return n
    return None

# owners (cache to json for speed)
OCACHE = ROOT + '/_gt_owner_cache.json'
if os.path.exists(OCACHE):
    j = json.load(open(OCACHE))
    G_OWNER = {int(k):v for k,v in j['owner'].items()}
    G_CORES = {int(k):set(v) for k,v in j['cores'].items()}
else:
    G_OWNER={}; G_CORES={}
    own_re = re.compile(r'owner\s*=\s*(\w+)'); core_re = re.compile(r'add_core_of\s*=\s*(\w+)')
    SD = ROOT+'/history/states'
    for fn in os.listdir(SD):
        if fn.endswith('.txt'):
            try: sid=int(fn.split('-')[0])
            except: continue
            txt=open(os.path.join(SD,fn),encoding='utf-8',errors='replace').read()
            mo=own_re.search(txt); G_OWNER[sid]=mo.group(1) if mo else None
            G_CORES[sid]=set(core_re.findall(txt))
    json.dump({'owner':G_OWNER,'cores':{k:sorted(v) for k,v in G_CORES.items()}}, open(OCACHE,'w'))

V2M = collections.defaultdict(set)
for ln in open(ROOT+'/_tmp_wv.txt', encoding='utf-8', errors='replace'):
    mm = re.search(r'((?:\d+[\s,]*)+)\s*#\s*was vanilla\s*([\d\s,]+)', ln)
    if not mm: continue
    mods=[int(x) for x in re.findall(r'\d+', mm.group(1))]
    vans=[int(x) for x in re.findall(r'\d+', mm.group(2))]
    for a,b in zip(mods,vans): V2M[b].add(a)

occ = collections.defaultdict(list)
for i,ln in enumerate(G_LINES,1):
    mm = STATE_RE.match(ln)
    if mm: occ[int(mm.group(1))].append(i)

if __name__ == '__main__':
    print('sus=%d gblocks=%d vblocks=%d v2m=%d owners=%d' % (len(G_SUS),len(G_BLOCKS),len(V_BLOCKS),len(V2M),len(G_OWNER)))
