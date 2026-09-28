
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
