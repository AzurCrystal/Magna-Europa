import re, collections, sys
sys.path.insert(0, r'D:/Projects/Magna-Europa')
from _gt_setup import *

# ============ verdict tables ============
# verdict per (block): 'MIG' already_ok | 'DEAD' dead_path | dict vid->(verdict,detail)
MIG_BLOCKS = {
 'GER_is_RKU_state':'RKU Ukraine zone: block fully re-authored to mod Ukraine/Belarus/south-Russia-border states; every listed id resolves to a mod state inside the zone (SOV-owned).',
 'GER_is_RKO_state':'RKO Ostland zone: block re-authored to mod Baltic states (LAT/LIT/EST owners); all listed ids are Baltic.',
 'GER_is_RKG_state':'RKG Norwegen zone: all 25 ids are mod Norwegian states (NOR owner).',
 'GER_is_RKN_state':'RKN Niederlande zone: all 21 ids are mod Dutch states (HOL owner).',
 'GER_is_RKB_state':'RKB Belgien zone: ids are mod Belgian + adjacent French-Flanders states (BEL/FRA).',
 'GER_is_RKK_state':'RKK Kaukasus zone: all 125 ids are mod Caucasus/Caucasus-front states (SOV owner).',
 'GER_is_RKT_state':'RKT Turkestan zone: all ids are mod Central Asian/Kazakh states (SOV owner).',
 'GER_is_soviet_greater_romania_state':'Greater-Romania-in-Soviets zone: all ids are mod Transnistria/Odessa-area states (SOV), matching vanilla Odessa/Balta/Vinnytsia scope.',
 'GER_is_GEN_state':'Generalgouvernement zone: all ids are mod Polish states (POL).',
 'GER_is_east_GEN_state':'East-GEN (Galicia) zone: all ids are mod Galician Polish states.',
 'GER_is_RKI_state':'RKI Iberien zone: ids are mod Spanish/Portuguese states (SPR/POR) + Gibraltar (in-region).',
 'GER_is_RGB_state':'RGB Grossbritannien zone: all ids are mod English/Scottish/Welsh states (ENG).',
 'GER_is_RGB_ireland_state':'RGB-Ireland zone: ids are mod Irish + N.Irish states (IRE/ENG).',
}

# vanilla-id -> (verdict, detail). verdict 'fix=ID' or 'dead' or 'ambig'
def F(mid, note): return ('fix', mid, note)
def D(note): return ('dead', None, note)
def A(mid, note): return ('ambig', mid, note)

UNMIG = {}
UNMIG['GER_is_RKM_state'] = {
 219: F(195,'vanilla Moscow Area -> mod 195 Moscow Oblast (Moscow city also in 229)'),
 205: F(300,'vanilla Hrodna -> mod 300 Grodno (POL-owned, in region)'),
 242: F(2189,'vanilla Smolensk -> mod 2189 Smolensk'),
 243: F(2190,'vanilla Roslavl -> mod 2190 Roslavl'),
 241: F(2183,'vanilla Pochep -> mod 2183 Bryansk (Pochep inside vanilla Bryansk front)'),
 207: F(2168,'vanilla Viciebsk -> mod 2168 Vitebsk'),
 224: F(2183,'vanilla Bryansk -> mod 2183 Bryansk'),
 210: F(2183,'vanilla Bryansk -> mod 2183 Bryansk'),
 246: F(2452,'vanilla Rzhev -> mod 2452 Rzhev'),
 209: F(2189,'vanilla Smolensk -> mod 2189 Smolensk'),
 263: F(138,'vanilla Veliky Novgorod -> mod 138 Novgorod'),
 208: F(151,'vanilla Pskov -> mod 151 Pskov'),
 195: F(119,'vanilla Leningrad Area -> mod 119 Leningrad (city)'),
 244: F(2794,'vanilla Gatchina -> mod 2794 Gostilitsy (covers Gatchina; alt 127 Leningrad Oblast)'),
 264: F(2941,'vanilla Tikhvin -> mod 2941 Lodeynoye Pole (covers Tikhvin-Volkhov)'),
 247: F(154,'vanilla Tver -> mod 154 Tver'),
 223: F(265,'vanilla Tula -> mod 265 Tula'),
 222: F(318,'vanilla Orel -> mod 318 Oryol'),
 220: F(388,'vanilla Kursk Area -> mod 388 Kursk'),
 240: F(412,'vanilla Voronezh -> mod 412 Voronezh'),
 258: F(359,'vanilla Lipetsk -> mod 359 Lipetsk'),
 260: F(2420,'vanilla Borisoglebsk -> mod 2420 Borisoglebsk'),
 265: F(2418,'vanilla Mikhaylovka -> mod 2418 Mikhaylovka (mod 265 is Tula, not this)'),
 245: F(2392,'vanilla Donetsk (Millerovo/Don oblast) -> mod 2392 Millerovo'),
 218: F(511,'vanilla Rostov Area -> mod 511 Rostov'),
 217: F(457,'vanilla Stalingrad Area -> mod 457 Volgograd'),
 351: F(96,'vanilla Vologda -> mod 96 Vologda'),
 248: F(153,'vanilla Yaroslavl -> mod 153 Yaroslavl'),
 253: F(177,'vanilla Ivanovo -> mod 177 Ivanovo'),
 254: F(250,'vanilla Ryazan -> mod 250 Ryazan'),
 257: F(2449,'vanilla Livny -> mod 2449 Livny'),
 252: F(171,'vanilla Nizhny Novgorod -> mod 171 Nizhny Novgorod'),
 255: F(299,'vanilla Penza -> mod 299 Penza'),
 239: F(366,'vanilla Saratov -> mod 366 Saratov'),
 250: F(261,'vanilla Ulyanovsk -> mod 261 Ulyanovsk'),
 879: A(61,'vanilla Kargopol (southern onega-area lakes) -> nearest mod 61 Pudozh; alternatives 2451 Veliky Ustyug / 96 Vologda'),
 880: F(2456,'vanilla Kotlas -> mod 2456 Kotlas'),
 214: F(41,'vanilla Arkhangelsk -> mod 41 Arkhangelsk'),
}
UNMIG['GER_is_soviet_greater_finland_state'] = {
 216: F(57,'vanilla "Below Zero" (=Olonets per author notes) -> mod 57 Olonets (SOV). Sibling zone states: 24,74,59,2437'),
 215: F(30,'vanilla "Eastern Karelia" (=Onega coast) -> mod 30 Medvezhyegorsk; author Onega zone = 30,39,52,38,62'),
 213: F(19,'vanilla Murmansk -> mod 19 Murmansk (author Murmansk zone = 19,2785,2709)'),
}
UNMIG['GER_is_additional_soviet_greater_finland_state'] = {
 146: F(77,'vanilla Karelia (Karjala: Viipuri isthmus) -> mod 77 Viipuri (FIN; author Karjala = 77,113,2783,100)'),
 147: F(33,'vanilla Salla -> mod 33 Vanha Salla (FIN; author Salla = 33,2786)'),
 722: F(17,'vanilla Petsamo -> mod 17 Petsamo (FIN; author Petsamo = 17,22)'),
}
UNMIG['GER_is_RK_south_urals_state'] = {
 401: F(2589,'vanilla Engels -> mod 2589 Engels'),
 251: F(270,'vanilla Kuybyshev/Samara -> mod 270 Kuybyshev'),
 652: F(285,'vanilla Orenburg -> mod 285 Orenburg'),
 582: A(2629,'vanilla "Southern Urals" (Magnitogorsk area, commented Magnitogorsk) -> nearest mod 2629 Baymak; alternatives 2389 Beloretsk/2390 Orsk'),
 651: F(201,'vanilla Ufa/Bashkortostan -> mod 201 Bashkortostan'),
 249: F(200,'vanilla Kazan/Tatarstan -> mod 200 Tatarstan'),
 256: F(209,'vanilla Chuvashia -> mod 209 Chuvash'),
 833: F(186,'vanilla Mari El -> mod 186 Mari El'),
 399: F(162,'vanilla Udmurtia/Izhevsk -> mod 162 Udmurt'),
}
UNMIG['GER_is_RK_north_urals_state'] = {
 825: F(27,'vanilla Nenetsia -> mod 27 Nenets'),
 262: F(2377,'vanilla Pechora -> mod 2377 Pechora'),
 397: F(35,'vanilla Syktyvkar -> mod 35 Komi (Syktyvkar is Komi capital)'),
 400: F(108,'vanilla Kirov -> mod 108 Kirov (note: mod also has 2439 Kirov; 108 is the northern Kirov-oblast state)'),
 398: F(97,'vanilla Perm -> mod 97 Perm'),
 581: A(2380,'vanilla Northern Urals -> nearest mod 2380 Berezniki (north Perm); alt 97 Perm'),
 573: A(2389,'vanilla Zlatoust -> nearest mod 2389 Beloretsk (south Ural foothills); alt 2629 Baymak'),
}
UNMIG['GER_is_RK_west_yenisei_state'] = {
 569: D('vanilla Khakassia - no Siberian states exist on mod map'),
 570: D('vanilla Novosibirsk - no Siberian states exist on mod map'),
 571: D('vanilla Omsk - no Siberian states exist on mod map'),
 403: A(632,'vanilla 403 "Kharabali" (file comment says Tyumen, VP is Kharabali near Astrakhan) -> mod 632 Astrakhan if Kharabali; Tyumen itself is off-map'),
 572: A(2629,'vanilla Chelyabinsk -> nearest mod 2629 Baymak; alt 2390 Orsk'),
 653: F(175,'vanilla Sverdlovsk/Yekaterinburg -> mod 175 Sverdlovsk'),
 580: D('vanilla Tobolsk - off mod map'),
 577: D('vanilla Surgut - off mod map'),
 578: D('vanilla Tomsk - off mod map'),
 579: D('vanilla Salekhard - off mod map'),
 824: D('vanilla Yamalia - off mod map'),
}
UNMIG['GER_is_soviet_greater_romania_state_extra'] = {}
UNMIG['GER_is_additional_soviet_greater_romania_state'] = {
 766: F(794,'vanilla Southern Bessarabia -> mod 794 Ismail (ROM; Budjak). Siblings: 1601 Cetatea Alba'),
 78: F(2980,'vanilla Bessarabia -> mod 2980 Chelmenti (ROM; covers Chisinau/central Bessarabia). Author Bessarabia zone = 592,2980,600,1602-1606,2744,2745'),
}
UNMIG['GER_is_RKH_state'] = {
 9: F(513,'vanilla Bohemia -> mod 513 (canonical "# was vanilla 9" remap, e.g. Bohemian state); author Bohemia = 471,473,486,489,498,513,514,522,523,529,539,1425-1435'),
 75: F(532,'vanilla Moravia -> mod 532 Olomouc (canonical "# was vanilla 75" remap); author Moravia = 499,504,507,526,528,532,535,536,1428-1433,1977'),
}
UNMIG['GER_is_far_north_RKC_state'] = {
 73: F(2246,'vanilla Carpathian Ruthenia -> mod 2246 Korolevo (CZE; canonical "# was vanilla 73" in czech/poland files). Author Carpathia = 593,2930,1607,1608,2246,564,2247'),
 664: F(610,'vanilla Southern Slovakia -> mod 610 Komarom (CZE). Author Southern Slovakia = 610,580,1439,1438,1442'),
 71: F(2661,'vanilla East Slovakia -> mod 2661 Kezmarok (CZE; author Eastern Slovakia = 2661,569,550,2313,562,2929)'),
 70: F(2662,'vanilla West Slovakia -> mod 2662 Piestany (CZE; author Western Slovakia = 541,574,2660,577,586,2662,2978,1437,591,555,2663)'),
}
UNMIG['GER_is_RNA_state'] = {
 458: F(1109,'vanilla Tunisia -> mod 1109 Tunis (TUN)'),
 665: F(1215,'vanilla Gabes -> mod 1215 Gabes (TUN)'),
 460: F(1865,'vanilla Eastern Algeria -> mod 1865 Constantine (FRA)'),
 513: A(1237,'vanilla Southern Algeria -> mod 1237 Ghardaia or 1305 Tamanghasset (FRA Algerian Sahara)'),
 459: F(1161,'vanilla Western Algeria -> mod 1161 Oran (FRA)'),
 461: F(1855,'vanilla Northern Morocco -> mod 1855 Rabat (MOR)'),
 462: F(1829,'vanilla Southern Morocco -> mod 1829 Marrakech (MOR)'),
 290: F(1189,'vanilla Spanish Africa (Spanish Morocco/Rif) -> mod 1189 Rif (check owner; Melilla 1852 is SPR enclave)'),
 783: F(1976,'vanilla Sidi Ifni -> mod 1976 Sidi Ifni (SPR)'),
 699: F(1335,'vanilla Rio de Oro -> mod 1335 Rio de Oro (SPR)'),
 557: F(1331,'vanilla Mauritania -> mod 1331 Tiris Zemmour (MRT)'),
 448: F(1241,'vanilla Tripoli -> mod 1241 Tripoli (ITA)'),
 661: F(1242,'vanilla Tripolitania -> mod 1242 Jafara (ITA Tripolitania)'),
 449: F(1247,'vanilla Libyan Coast -> mod 1247 Tarhuna wa Msalata (ITA coast)'),
 662: F(1282,'vanilla Sirte -> mod 1282 Sirte (ITA)'),
 450: F(1239,'vanilla Benghasi -> mod 1239 Jabal al Akhdar (ITA; Benghazi region)'),
 663: F(1264,'vanilla Cyrenaica -> mod 1264 Butnan (ITA; Tobruk area)'),
 451: F(1240,'vanilla Derna -> mod 1240 Quba (ITA; Derna/Gulf of Bomba area)'),
 452: F(1893,'vanilla Marsa Matruh -> mod 1893 Matrouh (EGY)'),
 447: F(1895,'vanilla Alexandria -> mod 1895 Al Iksandariyah (EGY)'),
 907: F(1889,'vanilla Cairo -> mod 1889 Al Qahirah (EGY)'),
 456: F(1888,'vanilla Luxor -> mod 1888 Qina (EGY; covers Luxor)'),
 457: F(1334,'vanilla Aswan -> mod 1334 Aswan (EGY)'),
 883: D('vanilla Kassala (Sudan) - Sudan not on mod map'),
 551: D('vanilla Khartoum - Sudan not on mod map'),
 886: D('vanilla Blue Nile - Sudan not on mod map'),
 549: D('vanilla Sudan - Sudan not on mod map'),
 887: D('vanilla South Darfur - Sudan not on mod map'),
 774: D('vanilla Chad - not on mod map'),
 272: D('vanilla French Africa/Senegal - sub-Saharan Africa not on mod map'),
 701: D('vanilla Gambia - not on mod map'),
 899: D('vanilla Kayes-Koulikoro (Mali) - not on mod map'),
 898: D('vanilla Gao (Mali) - not on mod map (Timbuktu 1340 is MRT-owned, could cover but is only one mod state for a distinct region)'),
 781: D('vanilla Niger - not on mod map'),
 786: F(1884,'vanilla Mauritanian Desert -> mod 1884 Bir Moghrein (MRT)'),
 514: F(1305,'vanilla North Africa (Algerian Sahara impassable) -> mod 1305 Tamanghasset (FRA)'),
 273: F(1332,'vanilla Italian Africa (Fezzan) -> mod 1332 Murzuq (ITA)'),
 552: F(1325,'vanilla Western Egypt -> mod 1325 Al Wadi al Jadid (EGY)'),
 767: D('vanilla North Darfur - Sudan not on mod map'),
 782: F(1340,'vanilla Tombouctou -> mod 1340 Timbuktu (MRT)'),
 775: D('vanilla B.E.T. - Chad/Niger not on mod map'),
 515: D('vanilla Central Africa/Nigerian Sahara - not on mod map'),
}
UNMIG['GER_is_RUS_state'] = {
 359: F(47,'vanilla New Jersey -> mod 47 New Jersey'),
 360: F(21,'vanilla Pennsylvania -> mod 21 Pennsylvania'),
 361: F(26,'vanilla Maryland -> mod 26 Maryland'),
 362: F(28,'vanilla Virginia -> mod 28 Virginia'),
 816: F(32,'vanilla West Virginia -> mod 32 West Virginia'),
 363: F(31,'vanilla North Carolina -> mod 31 North Carolina'),
 364: D('vanilla South Carolina - south/east-US beyond mod map edge'),
 368: D('vanilla Tennessee - off mod map'),
 369: D('vanilla Kentucky - off mod map'),
 261: D('vanilla USA (central US catch-all) - off mod map'),
 396: D('vanilla Indiana - off mod map'),
 358: F(11,'vanilla New York -> mod 11 New York'),
 357: F(2,'vanilla New England -> mod 2 Massachusetts (largest New England mod state; siblings 10,12,18,55,7)'),
 393: D('vanilla Michigan - off mod map'),
 395: D('vanilla Illinois - off mod map'),
 394: D('vanilla Wisconsin - off mod map'),
 373: D('vanilla Missouri - off mod map'),
 392: D('vanilla Iowa - off mod map'),
 391: D('vanilla Minnesota - off mod map'),
 383: D('vanilla Kansas - off mod map'),
 384: D('vanilla Nebraska - off mod map'),
 390: D('vanilla South Dakota - off mod map'),
 389: D('vanilla North Dakota - off mod map'),
 382: D('vanilla Colorado - off mod map'),
 381: D('vanilla Wyoming - off mod map'),
 388: D('vanilla Montana - off mod map (mod 1565 "Montana" is a Bulgarian decoy name)'),
 380: D('vanilla Utah - off mod map'),
 470: D('vanilla Alberta - western Canada off mod map'),
 472: D('vanilla Northwest Territories - off mod map'),
 867: D('vanilla Northern Manitoba - off mod map'),
 683: F(1,'vanilla Northeastern Canada (N.Quebec/Labrador plateau) -> mod 1 Quebec (CAN)'),
 865: D('vanilla Northern Saskatchewan - off mod map'),
 469: D('vanilla Saskatchewan - off mod map'),
 467: D('vanilla Manitoba - off mod map'),
 682: F(9,'vanilla Northern Ontario -> mod 9 Ontario (CAN)'),
 866: F(9,'vanilla Districts of Ontario -> mod 9 Ontario (CAN)'),
 276: F(9,'vanilla Canada catch-all -> mod 9 Ontario (capital region; mod Canada = 1,6,8,9,60,64)'),
 468: D('vanilla Nunavut - off mod map'),
 465: F(6,'vanilla New Brunswick -> mod 6 New Brunswick'),
 464: F(8,'vanilla Nova Scotia -> mod 8 Nova Scotia'),
 862: F(1,'vanilla ouest du quebec -> mod 1 Quebec'),
 863: F(1,'vanilla Maurice -> mod 1 Quebec'),
 466: F(1,'vanilla Quebec -> mod 1 Quebec'),
 861: F(1,'vanilla Saguenay -> mod 1 Quebec'),
 860: F(1,'vanilla Cote-Nord -> mod 1 Quebec'),
 332: F(64,'vanilla Labrador -> mod 64 Newfoundland (CAN; Labrador folded in)'),
}
UNMIG['GER_is_RAR_state'] = {
 291: F(2210,'vanilla Iraq -> mod 2210 Baghdad (IRQ)'),
 676: F(1114,'vanilla Mosul -> mod 1114 Mosul (IRQ)'),
 675: F(2198,'vanilla Al Hajara (W.Iraq desert) -> mod 2198 Al-Anbar (IRQ)'),
 656: F(1302,'vanilla Kuwait -> mod 1302 Kuwait City (KUW)'),
 455: F(1911,'vanilla Jordan -> mod 1911 Amman (JOR)'),
 454: F(1903,'vanilla Israel -> mod 1903 Lydda (PAL; core Israel)'),
 553: F(1920,'vanilla Lebanon -> mod 1920 Beyrouth (LEB)'),
 554: F(1930,'vanilla Syria -> mod 1930 Damascus (SYR)'),
 680: F(1141,'vanilla Deir-az-Zur -> mod 1141 Deir ez-Zor (SYR)'),
 677: F(1124,'vanilla Aleppo -> mod 1124 Aleppo (SYR)'),
 799: F(1123,'vanilla Hatay -> mod 1123 Hatay (SYR owner in mod)'),
 453: F(1286,'vanilla Sinai -> mod 1286 Shamal Sina (EGY; siblings 1303 Janub Sina)'),
 854: F(1273,'vanilla Jawf -> mod 1273 Al-Jawf (SAU)'),
 855: F(1313,'vanilla Tabuk -> mod 1313 Tabuk (SAU)'),
 679: F(1327,'vanilla Hejaz -> mod 1327 Al-Madinah (SAU)'),
 857: F(1315,"vanilla Ha'il -> mod 1315 Ha'il (SAU)"),
 859: F(2304,'vanilla Dammam -> mod 2304 Ad-Dammam (SAU)'),
 292: F(1326,'vanilla Saudi Arabia -> mod 1326 Ar-Riyad (SAU)'),
 856: A(2326,'vanilla Asir-Makkah (SW Saudi) -> nearest mod 2326 Al Hinakiyah; Mecca/Jeddah not separate mod states'),
 858: D('vanilla Najiran - SW Saudi/Yemen border off mod map'),
 678: F(2238,'vanilla Rub al Khali -> mod 2238 Samah (SAU Empty Quarter)'),
 765: F(1337,'vanilla Qatar -> mod 1337 Doha (QAT)'),
 658: F(1338,'vanilla Arab UK 1 (Trucial) -> mod 1338 Abu Dhabi (UAE)'),
 659: A(2300,'vanilla Arabian UK 2 -> mod 2300 Musandam (OMA) or 1336 Ras Al-Khaimah'),
 293: D('vanilla Yemen - Yemen not on mod map'),
 294: F(1341,'vanilla Oman -> mod 1341 Sohar (OMA)'),
 1010: F(2198,'vanilla Al Anbar -> mod 2198 Al-Anbar (IRQ)'),
 1011: F(2206,'vanilla Al Basrah -> mod 2206 Basra (IRQ)'),
 1013: F(2300,'vanilla Musandam -> mod 2300 Musandam (OMA)'),
 1014: F(2204,'vanilla Bahrain -> mod 2204 Al Awal (BHR)'),
 1015: A(1341,'vanilla Interior Oman -> mod 1341 Sohar (only north-Oman mod state)'),
 1016: D('vanilla Dhofar - south Oman off mod map'),
 992: D('vanilla Province of Aden - Yemen not on mod map'),
}
UNMIG['GER_is_additional_RAR_turkey_state'] = {
 344: F(1066,'vanilla Adana -> mod 1066 Adana (TUR)'),
 350: F(1059,'vanilla Diyarbekir -> mod 1059 Diyarbakir (TUR)'),
 352: F(1092,'vanilla Hakkari -> mod 1092 Hakkari (TUR)'),
 348: F(1036,'vanilla Kayseri -> mod 1036 Kayseri (TUR)'),
 353: F(973,'vanilla Erzurum -> mod 973 Erzurum (TUR)'),
 800: F(1039,'vanilla Van -> mod 1039 Van (TUR)'),
 354: F(958,'vanilla Trabzon -> mod 958 Trabzon (TUR)'),
 349: F(1011,'vanilla Sivas -> mod 1011 Sivas (TUR)'),
 355: F(924,'vanilla Samsun -> mod 924 Samsun (TUR)'),
 798: F(963,'vanilla Amasya -> mod 963 Amasya (TUR)'),
 356: F(907,'vanilla Sinop -> mod 907 Sinop (TUR)'),
 345: F(1098,'vanilla Mersin -> mod 1098 Mersin (TUR)'),
 346: F(1046,'vanilla Konya -> mod 1046 Konya (TUR)'),
 49: F(979,'vanilla Turkey (Ankara core) -> mod 979 Ankara (TUR)'),
 347: F(1939,'vanilla Izmit -> mod 1939 Kocaeli (TUR)'),
 343: F(1043,'vanilla Afyon -> mod 1043 Afyonkarahisar (TUR)'),
 339: F(1032,'vanilla Izmir -> mod 1032 Izmir (TUR)'),
 342: F(1097,'vanilla Antalya -> mod 1097 Antalya (TUR)'),
}
UNMIG['GER_is_additional_RAR_iran_state'] = {
 419: F(1040,'vanilla Azerbaijan (Iranian) -> mod 1040 East Azerbaijan (PER)'),
 421: F(1138,'vanilla Kurdistan -> mod 1138 Iranian Kurdistan (PER)'),
 420: F(1069,'vanilla Gilan -> mod 1069 Gilan (PER)'),
 417: F(1082,'vanilla Golestan -> mod 1082 Golestan (PER)'),
 413: F(1238,'vanilla Khuzestan -> mod 1238 Khuzestan (PER)'),
 266: F(1155,'vanilla Persia -> mod 1155 Tehran (PER capital)'),
 411: F(1314,'vanilla Hormozgan -> mod 1314 Hormozgan (PER)'),
 412: F(1274,'vanilla Fars -> mod 1274 Fars (PER)'),
 418: F(1100,'vanilla Semnan -> mod 1100 Semnan (PER)'),
 416: F(1087,'vanilla Razavi Khorasan -> mod 1087 Razavi Khorasan (PER)'),
 414: F(1267,'vanilla Kerman -> mod 1267 Kerman (PER)'),
 410: A(1267,'vanilla Sistan -> nearest mod 1267 Kerman; Sistan not a separate mod state'),
 1000: F(1040,'vanilla East Azerbaijan -> mod 1040 East Azerbaijan'),
 1001: F(1138,'vanilla Kurdistan -> mod 1138 Iranian Kurdistan'),
 1004: F(1076,'vanilla North Khorasan -> mod 1076 North Khorasan'),
 1002: F(1235,'vanilla Yazd -> mod 1235 Yazd'),
 1003: F(1193,'vanilla South Khorasan -> mod 1193 South Khorasan'),
}

DEAD_REASONS = {
 'GER_is_ROA_state':'RK Ostasien (East Asia): mod map has no East Asian states; every id resolves to an unrelated European mod state. Dead for live tags.',
 'GER_is_RAA_state':'RK Australasien: mod map has no Oceania/SE-Asia states; ids resolve to unrelated European mod states. Dead for live tags.',
 'GER_is_RHD_state':'RK Grosshindustan: mod map has no Indian-subcontinent states. Dead for live tags.',
 'GER_is_RKA_state':'RK Mittelafrika: mod map has no sub-Saharan African states. Dead for live tags.',
 'GER_is_additional_RKA_state':'RKA South-Africa add-on: no southern-African states on mod map. Dead.',
 'GER_is_RKV_state':'RK Klein-Venedig (South America): no South-American states on mod map. Dead.',
 'GER_is_RAN_state':'RK Anden: no Andean/South-American states on mod map. Dead.',
 'GER_is_RCO_state':'RK Kolumbus (Central America/Caribbean/south-US): none of those states exist on mod map. Dead.',
 'GER_is_west_coast_north_america_state':'West-coast NA add-on: mod map has no US/Canadian west coast; mod "Baja"(2857,HUN) and "Montana"(1565,BUL) are decoy names. Dead.',
 'GER_is_additional_RKM_siberia_state':'RKM east-Siberia add-on: no Siberian states on mod map. Dead.',
 'GER_is_additional_RKT_xinjang_state':'RKT Xinjiang add-on: no Sinkiang/Mongolian states on mod map. Dead.',
}

# Build output rows
rows = []
# collect every `state = id` line grouped by block
for n,s,e in G_BLOCKS:
    ids = ids_in(G_LINES,s,e)
    if n in MIG_BLOCKS:
        note = MIG_BLOCKS[n]
        for i,sid in ids:
            rows.append((i, sid, 'already_ok', note + f' mod {sid}={G_MOD.get(sid)}.'))
    elif n in DEAD_REASONS:
        note = DEAD_REASONS[n]
        for i,sid in ids:
            rows.append((i, sid, 'dead_path', note))
    elif n in UNMIG:
        table = UNMIG[n]
        for i,sid in ids:
            if sid in table:
                kind, mid, note = table[sid]
                if kind=='fix':
                    rows.append((i, sid, f'fix_to={mid}', f'{n}: {note}'))
                elif kind=='dead':
                    rows.append((i, sid, 'dead_path', f'{n}: {note}'))
                else:
                    rows.append((i, sid, 'ambiguous', f'{n}: {note}'))
            else:
                # vanilla id not in table - check if maybe a mod id snuck in
                rows.append((i, sid, 'ambiguous', f'{n}: unmapped vanilla id {sid} ({G_VAN.get(sid)})'))
    else:
        # non-region blocks: suspects here get context check
        for i,sid in ids:
            rows.append((i, sid, 'UNHANDLED', n or '?'))

# map suspect dedup lines -> rows; ensure every suspect line covered
suslines = {s['line'] for s in G_SUS}
covered = {r[0] for r in rows}
missing = sorted(suslines - covered)
print('total rows:', len(rows), 'suspect lines:', len(suslines), 'missing:', len(missing))
if missing:
    for l in missing[:30]: print('MISSING L',l, G_LINES[l-1], g_block_of(l))
verd = collections.Counter(r[2].split('=')[0] for r in rows)
print(verd)
import io
with io.open(r'D:/Projects/Magna-Europa/_audit_gertrig.tsv','w',encoding='utf-8',newline='') as f:
    f.write('file\tline\told_id\tverdict\tdetail\n')
    for i,sid,verd_,det in sorted(rows):
        f.write(f'common/scripted_triggers/GER_scripted_triggers.txt\t{i}\t{sid}\t{verd_}\t{det}\n')
print('written')
