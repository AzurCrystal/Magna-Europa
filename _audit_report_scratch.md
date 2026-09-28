
## S_ITA (完成)
- REGRESSION: italy.txt:4554 cat_ship_medium_battery 被删 → 已修 (f95fbc5)
- REGRESSION: italy.txt:13973-74 810→170 Madona 错 remap → 已修 (9c2514d)
- suspicious: italy.txt:13510 is_subject=no 新增 (刻意?)

## S_AUS (完成)
- 无确认回归
- 待判: AUS_seize_galicia create_wargoal 从 every_state 提升 → 语义变 (作者死代码修复?)
- 待判: austria.txt 3 处 include_locked 删除 — 自写海军 focus 跟进 vanilla? 

## S_PLANS (完成)
- REGRESSION: SOV_alternate:346 SOV_victory_over_devastation 注释 → SOV_socialist_humanism 断链
- REGRESSION: NOR_alternate:63 NOR_compromise_with_the_nkp 注释 → norway:521 双前置断链
- 注意: COG_alternate 文件名空格 (vanilla 同 bug, 不动)

## S_NORDIC 回归列表 (需要逐个修)
- denmark.txt: 7 处 limit/scope split-brain (253,909,943,4488,4647,4999,5031)
- norway.txt:7315 available 110 vs bypass 118
- norway.txt:7321 coastal_bunker→bunker 类型改错
- sweden.txt:9009 SWE_narvik_crisis bypass 语义改写 (is_subject→AND)
- finland.txt:4795/9828/9892/9752/10102 多处 limit/scope split + Olonets remap 错
- estonia.txt:2472 scope 811(Sibenik!) 该是 146 Saaremaa
- lithuania.txt:428 scope 784(Avar) vs owns 241(Wilno) split
- nordic_shared.txt 914 死 Jan-Mayen

## S_GER / S_AUS / S_EAST 已清
## 全局: include_locked 误删 ~890 处 (我误当 vanilla-sync) — vanilla 1.19 仍有效

## S_MISC (完成)
- congo.txt 恢复 1.18 stub (e1781b8)
- congo_shared.txt vanilla 非洲 id 死引用是 1.18 遗留, COG tag 存在但无玩家树 → 不可达, 记录不修
- uk/usa/canada/generic/00_titlebar 全干净 (只 include_locked 差, 已修)

## S_BALKAN fix 完成
- yugoslavia.txt 2264 106→1530 Kumanovo ✓ (51ab275)
- yugoslavia.txt 5458/5471/5487 776→1499 Sarajevo ✓ (117cc5a)
- turkey.txt 187→1554 Dodecaneso ×4 ✓ (79eb9fb)

## S_CZEHUN HUN split-brain 修了一部分
- owns/controls_state 全部已在上游 remap ✓
- add_state_claim/add_state_core/generator: 25处已 remap ✓ (此 commit)
- proclaim_restoration scope add_core_of 8处已 remap (152→1800 等) ✓
- march_to_the_shore bypass 的 102/853/103/109/45/163/764/804: **1.18 也原样未改 → 非回归, 保留**
  (vanilla Slovenia/Croatia 在 mod 地图分裂为多个州, 需作者语义决策)
- hungary_wuw 2145-2730 OR 链、3516-3528 every_country 重复/丢弃: 待单独审查

## S_OOB 复核 — 零回归
- USA_air 28-Virginia: 1.18 相同 (off-map 落点) — 非回归
- CZE_mun `location = N` 是 province id (非 state id) — 非回归
- GER_Gar_01/BEL_Mnt_01 大小写与 vanilla 一致 — 非回归
- ARM loads AZR_1936: vanilla 同 (ARM 共享 AZR OOB) — 非回归
- 余下 suspect: ITA_aviazione_legionaria 无后缀 oob 引用 (无 mod 文件 → vanilla fallback, 1.18 同)

## S_ONACT — 待处理 (events 之后, 同规则: 欧洲=remap, off-map=注释)
- on_actions/00: 运河 58/685 vanilla Kiel/Panama (off-map → 注释)
- on_actions/05_lar: SPR resistance 41/165/167/168/173/175/790/792/793/794 → 需查 vanilla 西班牙州对应 mod id
- on_actions/06_bftb: Bosporus flag 341/742 — vanilla Istanbul → mod id
- on_actions/09_aat: set_capital 110 → mod Oslo (124/118)
- on_actions/12_wuw: 塞尔维亚 107/108, BEL Force Publique 295→2974, Greenland GRN+101 双错
- on_actions/13_goe: Achaemenid 15 个 id 全 vanilla → 整数组注释
- scripted_effects/GER: 1169-1176 波兰工厂 280 重复+86=Satakunta+762=Istria; 1266-1290 Sudetenland 数组
- scripted_effects/ITA: Albania 44→?, Epirus 805→?, Dalmatia 103→?
- scripted_effects/SOV: 563 Chita→41, Vladivostok 408→off-map, Sakhalin 655→off-map
- scripted_effects/USA: Eagle Legion create_unit 31 — 需查 is_csa_state 是否存
- kongs_scripted_core_groups.txt:58 `state = 939i` 语法残值

## Debug-run error.log 分类 (post-fix)
- ~500 audio — bel/aus voice pack 缺文件, 1.18 同, 不修
- ~115 COG_* focus refs — 我回滚 congo.txt stub 后悬空; 1.18 同 stub 也悬空 → **非回归**
- ~90 history.cpp — air wing state id / OOB 类
- ~79 trigger.cpp — has_completed_focus 悬空 (大半 COG)
- ~64 effect + ~50 trigger impl — 同 COG
- MAP_ERROR: rivers.bmp palette + 2581,1460 X crossing + 丹麦海峡 fractioned (SR_167 加的 3 province 双 claim — 已 revert 52611a2) + 12165/12205/19891/19890 bunker + prov stack 偏移 — **均为地图/provinces.bmp 伤, 非本次改动**
- invalid ai area pacific — ai_areas/default.txt 里 pacific 区域作者注释留着 (off-map 占位), 语义 harmless

## 本轮新增 (post-debug)
- pacific ai_area 在我 `cacaa2b` 时误删 → 已补回 (Mid/Central/American Atlantic)
- COG_* focus refs ~150 处全注释 (congo.txt stub 无 focus)
- GER befriend_japan/china/heed 恢复注释 — focus 不在 mod 树里, 我误以为 vanilla parity
- SR_167 回退 (double-claim+fractioned)
- 剩的 MAP_ERROR (rivers.bmp palette, 2581,1460 X crossing, bunker 4个, prov stack) 都是 provinces.bmp 层伤 — 非本轮

## 终审分类 (user advisory 确认)
- GER_befriend_japan/china invalid focus: mod 树里完全没这俩 focus id — 作者遗留断链, 非回归, 记 suspect. 
  c4eebf3/6a77694 "uncomment" 没改变悬空状态 — 已回退到 1.18 相同形态.
- invalid database object ×31 (tech 当变量): _118_base 同代码 → 1.18 遗留, 不修
- pacific ai_area: 已补回 (commit b041646)
- 音/实体/地图层: 全是 vanilla + provinces.bmp 旧伤

## 会话净改动 (~18 commits)
- 7 大类 split-brain 修复: focus(3) / ai_areas(1) / events(2 批 14 文件) / on_actions(9) / scripted_effects(4) / decisions(15) / on_actions-canon(1)
- 2 个回退: congo.txt→stub, GER_befriend 恢复注释, SR_167 扩展撤销
- 1 个回写: include_locked×1025
- 1 个新加: pacific ai_area

## 剩余 suspect (不动)
- HUN march_to_the_shore 等 split-brain: vanilla 州 1:N 分裂, 1.18 同坏
- formable_nation_decisions 长 off-map id 清单: 已注释
- COG/SPR/ITA 等 1939 OOB 全缺: vanilla parity
- bunker 省号 12165/12205/19890/19891: provinces.bmp 层, 需地图工具
- 2581,1460 X crossing + rivers.bmp palette: provinces.bmp 层
- cl_tech→industry swap: 死类别, 语义漂移 unavoidable
- BEL prevent_auto_flip 等 build up: 1.18 遗留
