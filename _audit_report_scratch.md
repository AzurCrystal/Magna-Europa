
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

## 看海实跑残留 (待修, 用户跑完再动)
- has_stability = 60 → 0.6 (Elections_scripted_triggers.txt:16, 1行)
- GOE_IRQ_news.* 引用 → 整批注释 (13_goe_on_actions.txt)
- LAR_Spain random_list 0-chance → 待查 base

---

## 1941-42 Observer Run Fixes (Pending — game running, DO NOT EDIT common/ until stopped)

### Script Errors (fix when game stopped)
- [ ] `mexico.17` — `events/AAT_Norway.txt:254,337` MEX event file not in mod → comment `country_event` refs
- [ ] `IRE_free_state_idea` — `events/MER_events.txt:170` remove_ideas invalid idea → check/comment
- [ ] `ITA_prince_umberto` — `italy.txt:7316+7319` add_country_leader_trait on corps_commander-only char → delete line (mod-added, not vanilla)
- [ ] `expire = "1965.1.1.1"` — ITA characters + germany.txt ~30719 → 4-segment date → "1965.1.1"
- [ ] `TUR_scripted_effects.txt:180-183` — remove_dynamic_modifier separatist_fatigue/kurdish_agitation/kurdish_separatism/kurdish_rebellion → modifier defs exist in `0_dynamic_modifiers.txt`, scope/event_target issue → check if state scope vs country scope
- [ ] `BBA_ethiopia_exile_events.53` — event not defined → find callers, comment or stub
- [ ] `country_capitulated.0` — event not defined → find callers
- [ ] `GER_german_immigration` ×10 — `reichskommissariat.txt:141` add_ideas invalid → idea deleted/renamed, fix or comment
- [ ] `15_mun_on_actions.txt:392` — has_border_war_between 喀尔巴阡鲁塞尼亚↔东斯洛伐克 → state ids likely wrong (mod split), check vs base
- [ ] `BFTB_Turkey.txt:4578-4634` — add_compliance/add_resistance on 卡尔勒奥瓦 (Karliova?) — state has no resistance → wrong state target
- [ ] `05_lar_on_actions.txt:1364-1365` — remove_dynamic_modifier autonomous_state/semi_autonomous_state → defs missing
- [ ] `LAR_NewsEvents.txt:251` — remove_dynamic_modifier unplanned_offensive → def missing
- [ ] `austro_hungarian_releasable_shared.txt:841` — add_doctrine_cost_reduction Invalid tech → `technology = X` → `category = naval_doctrine` (1.19 milestone doctrine system, vanilla france.txt:963 pattern)
- [ ] `spain.txt:4626` — remove_dynamic_modifier autonomous_state ×16 → def missing
- [ ] `BBA_Italy.txt:7881` — add_compliance on 13 states w/o resistance → wrong state targets (mod split)
- [ ] `netherlands.txt:5893-5899` — create_unit on enemy province → OOB location pick
- [ ] `hungary_wuw.txt:18419,18427` — build_railway prov not neighbours → map layer, defer
- [ ] `ai_peace/*` — GER Königsberg 2484→252, Luxembourg 2369→512, SOV/00_misc/yalta/GER_peace split-brain — UNFIXED since S_AI2
- [ ] `S_NORDIC` split-brain leftovers — verify covered or still open
- [ ] `GER strategy plan` — 4 dead focus ids: GER_befriend_china, GER_befriend_japan, GER_minor_allies, GER_second_vienna_award → comment (same as COG fix)
- [ ] `has_border_war_between` trigger — possible wrong state ids

### Localization (already committed)
- [x] `zzz_missing_state_names_l_simp_chinese.yml` — 1065 STATE_ keys filled w/ EN fallback (d073f6e)
- [ ] Victory points (PROV_x) same gap — check for missing zh
- [ ] Decision popup tooltips state lists — same fallback issue (诺曼底/瓦隆尼亚 in Jutland decision)

### Vanilla-parity (confirmed no fix needed)
- `LAR_Spain.txt:4984` random_list all-0 — vanilla same
- `DEBUG_Manu` duplicate — vanilla same  
- `poland.txt:276` build_railway — vanilla same
- `ITA add_country_leader_trait` on VE3 — vanilla same
- `original_tag = MLD` in GER strategy — dead but harmless
- GER focus labels (GER_rhineland etc.) — cosmetic log= labels, not missing ids
- `on_ruling_party_change`/`unlock_trait_command`/`order_set_invasion_source`/`Dropped command` — engine noise

### Map Layer (defer — needs map tool)
- bunker provs #12165/12205/19891/19890
- rivers.bmp palette, X-crossing 2581,1460, prov-stack offsets
- poland.txt:276 railway path disconnected
- hungary_wuw.txt railways not neighbours

### Asset Layer (defer)
- Equipment variants: FRA R-35/Char B1/SOMUA/D2, ITA CV35/Ca.301/Ca.310/CR.42/ICR.42, CZE light_tank, USA ship hulls
- MIO: USA_baldwin_locomotive_works, ITA_crda_organization
- ITA anti_tank OOB, ~90 missing history files (fixed by Unused.txt)
- Failed to generate a name ×4 — empty name list

### Crash Analysis
- First run crash 01:12 — likely file-watcher hot-reloaded mid-edit germany.txt (bad intermediate state)
- Second run alive 1942.08+, no crashes
- Rule: DO NOT edit common/* while game is running
