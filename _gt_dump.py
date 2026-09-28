import sys
sys.path.insert(0, r'D:/Projects/Magna-Europa')
from _gt_setup import *

tags = sys.argv[1:] if len(sys.argv)>1 else ['SOV']
for t in tags:
    ids = sorted(sid for sid,o in G_OWNER.items() if o==t)
    print(f'===== {t} ({len(ids)}) =====')
    print('; '.join(f'{i} {G_MOD.get(i)}' for i in ids))
