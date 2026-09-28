# -*- coding: utf-8 -*-
import re, sys

WT = 'C:/Users/AzurCrystal/.omp/wt/tb005ab8d1/m/'

# (file, line, old_id, new_id, region_name) ; old_id=None -> comment out block
EDITS = []

def A(f,l,old,new,reg): EDITS.append((f,l,old,new,reg))
def OUT(f,l,old,reg): EDITS.append((f,l,old,None,reg))

G='common/national_focus/germany.txt'
A_FILE='common/national_focus/austria.txt'
C='common/national_focus/czechoslovakia_mu.txt'
S='common/national_focus/switzerland.txt'

# ---------- germany ----------
# GER_agricultural_reforms pairs (limit + bare twin)
for l in (678,682): A(G,l,909,1710,'South Schleswig')
for l in (686,690): A(G,l,61,1699,'Mecklenburg')
for l in (694,698): A(G,l,62,1631,'Pommern')
for l in (702,706): A(G,l,63,1625,'WestPrussen')
for l in (710,714): A(G,l,5,1611,'Germany')
# urbanization Baden pair
for l in (742,762,765): A(G,l,1771,568,'Baden')
# autobahn south
for l in (1559,1607,1612): A(G,l,1784,1790,'Oberbayern')
for l in (1562,1622,1627): A(G,l,1768,582,'Upper Austria')
for l in (1565,1637,1642): A(G,l,1771,1802,'Lower Austria')
for l in (1568,1652,1657): A(G,l,644,1811,'Lower Austria')
for l in (1571,1667,1672): A(G,l,1810,1812,'Lower Austria')
for l in (1574,1682,1687): A(G,l,1817,1813,'Lower Austria')
for l in (1577,1697,1702): A(G,l,582,1804,'Lower Austria')
# autobahn east
for l in (1725,1749,1754): A(G,l,280,122,'Ostmark')
for l in (1728,1764,1769): A(G,l,267,1625,'Hinterpommern')
for l in (1731,1779,1784): A(G,l,1625,267,'Gdynia')
for l in (1734,1794,1799): A(G,l,1611,280,'Danzig')
A(G,1810,63,1625,'Hinterpommern')
A(G,1813,807,267,'Gdynia')
A(G,1816,85,280,'Danzig')
# kammhuber line atlantic list
A(G,6096,925,4,'Finnmark')
A(G,6099,924,13,'Troms')
A(G,6102,144,25,'Nordland')
A(G,6105,923,25,'Helgeland')
A(G,6108,143,71,'Trondelag')
A(G,6111,142,111,'Vestlandet')
A(G,6114,922,147,'Agder')
A(G,6117,123,123,'Telemark')
A(G,6120,110,124,'Oslo')
A(G,6123,915,184,'Halland')
A(G,6126,140,148,'Vaster Gotland')
A(G,6129,99,34,'Jylland')
A(G,6132,912,139,'Sonderjylland')
A(G,6135,36,336,'Friesland')
A(G,6138,7,402,'Holland')
A(G,6141,35,383,'Brabant')
A(G,6144,6,455,'Vlaanderen')
A(G,6147,29,472,'Nord-Pas-de_calais')
A(G,6150,785,442,'Picardy')
A(G,6153,15,551,'Normandy')
A(G,6156,14,583,'Brittany')
# atlantic islands
A(G,7639,914,1822,'Jan Mayen')
A(G,7642,100,160,'Iceland')
OUT(G,7645,101,'Greenland')
A(G,7648,337,90,'Faroes')
A(G,7651,933,116,'Shetlands')
A(G,7654,134,288,'Ireland')
A(G,7657,119,248,'North Ireland')
A(G,7660,178,1824,'The Canaries')
OUT(G,7663,698,'The Azores')
OUT(G,7666,696,'Bermuda')
A(G,7669,697,1823,'Madeira')
OUT(G,7672,702,'Cape Verde')
# brandenburg / czech / sachsen
for l in (10058,10119,17553,17620,17681,17738,18965,18973):
    A(G,l,64,1650,'Brandenburg')
A(G,10556,9,513,'Czechoslovakia')
A(G,10560,75,532,'Moravia')
A(G,21607,65,411,'Sachsen')
A(G,19408,1749,411,'Sachsen')
A(G,19445,440,411,'Sachsen')
# Greece
A(G,14494,185,995,'Epirus')
A(G,14523,185,995,'Epirus')
A(G,14503,731,941,'Central Macedonia')
A(G,14531,731,941,'Central Macedonia')
A(G,14512,184,910,'Thrace')
A(G,14539,184,910,'Thrace')
# misc german-region leftovers
A(G,33280,5,1611,'Germany')
A(G,33308,763,252,'Konigsberg')

# ---------- austria ----------
A(A_FILE,8334,539,None,'KEEP')  # placeholder removed below
EDITS.pop()
A(A_FILE,8457,102,1490,'North-Eastern Slovenia')
A(A_FILE,8470,109,2830,'Eastern Croatia')
A(A_FILE,8483,853,743,'Ljubljana')
A(A_FILE,8571,853,743,'Ljubljana')
A(A_FILE,8373,74,499,'Eastern Sudetenland')
A(A_FILE,8890,76,1582,'Northern Transylvania')
A(A_FILE,8903,80,590,'Bocovina')
A(A_FILE,8916,82,2668,'Banat')
A(A_FILE,8929,83,705,'crisana')
A(A_FILE,8942,84,713,'Transylvania')
for l in (9065,9072): A(A_FILE,l,76,1582,'Northern Transylvania')
for l in (9096,9103): A(A_FILE,l,80,590,'Bocovina')
for l in (9127,9134): A(A_FILE,l,82,2668,'Banat')
for l in (9158,9165): A(A_FILE,l,83,705,'crisana')
for l in (9189,9196): A(A_FILE,l,84,713,'Transylvania')
for l in (9247,9270,9311): A(A_FILE,l,69,1672,'Sudatenland')
A(A_FILE,9344,75,532,'Moravia')
A(A_FILE,9355,74,499,'Eastern Sudetenland')
A(A_FILE,9432,155,624,'Western Hungary')
A(A_FILE,9454,154,646,'Southern plain')
OUT(A_FILE,11241,2292,'Alaska')
OUT(A_FILE,11232,463,'Alaska')
OUT(A_FILE,11253,463,'Alaska')
OUT(A_FILE,11259,463,'Alaska')

# ---------- czechoslovakia_mu ----------
A(C,12390,155,621,'Western Hungary')

# ---------- switzerland ----------
A(S,5378,694,685,'Eastern Swiss Alps')
A(S,5395,685,694,'Western Swiss Alps')
A(S,2495,660,724,'Western Swiss Alps (St Maurice)')
A(S,2547,707,724,'Western Swiss Alps (St Maurice)')

hdr = re.compile(r'^(\s*)(\d+)(\s*=\s*\{)(.*)$')
byfile = {}
for e in EDITS: byfile.setdefault(e[0],[]).append(e)

for f, items in byfile.items():
    path = WT+f
    raw = open(path,'rb').read()
    text = raw.decode('utf-8')
    eol = '\r\n' if '\r\n' in text else '\n'
    lines = text.split(eol)
    for _,ln,old,new,reg in items:
        i = ln-1
        m = hdr.match(lines[i])
        if not m:
            print(f'MISS {f}:{ln} not-a-header: {lines[i][:60]}'); continue
        indent,cid,eq,rest = m.groups()
        if old is not None and int(cid)!=old:
            print(f'IDMISMATCH {f}:{ln} expected {old} got {cid}: {lines[i][:60]}'); continue
        if new is None:
            lines[i] = indent+'#'+cid+eq.strip()+' # 1.19-migration: off-map region '+reg
            # comment whole block: find matching close
            depth = 1; j = i+1
            while j < len(lines) and depth>0:
                depth += lines[j].count('{') - lines[j].count('}')
                j+=1
            # j is one past closing line
            for k in range(i+1,j):
                s = lines[k]
                if s.strip()=='' :
                    continue
                lines[k] = '#'+s
            print(f'OUT   {f}:{ln} {old} ({reg}) commented {j-i} lines')
        else:
            lines[i] = indent+str(new)+eq+' # was vanilla '+str(old)+' ('+reg+')'
            print(f'REW   {f}:{ln} {old}->{new} ({reg})')
    open(path,'wb').write(eol.join(lines).encode('utf-8'))
    print('WROTE',f)
