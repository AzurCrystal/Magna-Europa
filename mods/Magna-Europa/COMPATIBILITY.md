# Compatibility List / 兼容性列表

Status of DLC content and third-party mods with **Magna Europa: Reforged**.
本模组与各 DLC 内容及第三方模组的兼容状况。

## DLC content / DLC 内容

No DLC is required — all DLC-specific content is gated by `has_dlc` checks and enables automatically when you own it.
不强制要求任何 DLC——DLC 专属内容通过 `has_dlc` 检查控制，拥有后自动启用。

| DLC | Status / 状态 |
|---|---|
| Together for Victory | ✅ Ported / 已移植 |
| Death or Dishonor | ✅ Ported / 已移植 |
| Waking the Tiger | ✅ Ported / 已移植 |
| Man the Guns | ✅ Ported / 已移植 |
| La Résistance | ✅ Ported / 已移植 |
| Battle for the Bosporus | ✅ Ported / 已移植 |
| No Step Back | ✅ Ported / 已移植 |
| By Blood Alone | ✅ Ported / 已移植 |
| Arms Against Tyranny | ✅ Ported / 已移植 |
| Trial of Allegiance | ✅ Ported / 已移植 |
| Götterdämmerung | ✅ Ported / 已移植 |
| Graveyard of Empires | ✅ Ported / 已移植 |
| Thunder at Our Gates | ✅ Ported / 已移植 |
| No Compromise, No Surrender | ✅ Ported / 已移植 |
| To The Bitter End | ✅ Ported (`has_dlc` gated; engine-parsed) / 已移植（含 `has_dlc` 检查；引擎解析验证） |
| Peace For Our Time | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |
| Poland: United and Ready | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |
| Axis Armor Pack | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |

### 1.20.0 gameplay adaptation / 1.20.0 玩法适配

- **To The Bitter End & Northern Dobruja (`v971`):** full port of the `has_dlc = "To The Bitter End"` Romania focus tree, events, decisions, characters, ideas, MIOs, portraits, unit medals, river-fleet tech, Dacia/Balkan formables, achievements, and `[state.GetName]` localisation overrides (`localisation/<lang>/replace/mer_ttbe_state_refs_l_<lang>.yml`), plus `v971` (Northern Dobruja -> ME `793` / `v971` group) state mapping across Romania, Bulgaria, Turkey, Hungary, and achievements.
  **《To The Bitter End》与北多布罗加（`v971`）完整适配：**完整移植受 `has_dlc = "To The Bitter End"` 控制的罗马尼亚国策树、事件、决议、人物、民族精神、军工组织、肖像、部队勋章、内河舰队科技、达契亚/巴尔干建国决议、成就及 `[state.GetName]` 本地化覆写（`localisation/<lang>/replace/mer_ttbe_state_refs_l_<lang>.yml`），并按 `v971` 映射同步北多布罗加在罗马尼亚、保加利亚、土耳其、匈牙利及成就中的州引用。
- **EU mapping remediation & 1.20 fixes:** cleaned legacy European state-collapse / wrong-remap bugs across scripts, fixed off-map European hijacks, synced 1.20.0 non-map localisation gaps (without importing `BRN`/`ETH` collisions or vanilla `ROM_STATE_*`/`ROM_VICTORY_POINTS_*` overrides), and merged 1.20 European bugfixes (`ENG_vickers_experimental_facility_focus`, `FIN` motti / Wolfgang von Hesse, `GRE`, `HUN`, `SOV`).
  **欧洲映射清理与 1.20 修复：**修复历史遗留的欧洲州塌缩/误映射与图外欧陆劫持问题，补齐 1.20.0 非地图类缺失本地化（跳过与 ME 冲突的 `BRN`/`ETH` 及原版地名改写键），并合入 1.20 欧洲国策/事件修复（英国维克斯实验设施、芬兰莫蒂与黑森王子、希腊、匈牙利、苏联等）。
- **Preserved ME policies:** half-scale `ai_templates` & tech balance, BEL/AUS/HOL European Federation diplomatic route, `GER` Rhineland/Anschluss/Sudetenland/Danzig gates, ME Vichy (`france.14`) and Molotov–Ribbentrop core transfer semantics, Yugoslav coup `BOS`/`BOS+HRZ` transfers, Bulgaria `-0.03` & nuclear breakthrough, 2x `send_volunteer_size`, and repurposed tags (`BRN/TNE/SAM/SIC/SIL`).
  **保留的 ME 机制：**半规模编制与科技平衡、BEL/AUS/HOL 欧洲联邦外交路线、德国莱茵兰/吞并/苏台德/但泽门槛、ME 维希法国与苏德互不侵犯条约核心转移逻辑、南斯拉夫政变 `BOS`/`BOS+HRZ` 转移、保加利亚 `-0.03` 与核突破参数、双倍志愿军规模及重用国家标签（`BRN/TNE/SAM/SIC/SIL`）。

### 1.19.4 gameplay adaptation / 1.19.4 玩法适配

- **European Federation:** the BEL/AUS Götterdämmerung focuses and HOL Man the Guns focus unlock the diplomatic route: invitation, research sharing, industrial investment, federalization, proclamation and 13 regional expansions. It uses ME's regional state selectors and annexes qualifying subjects, not independent countries. The separate generic EU route is unchanged; its Waking the Tiger/Man the Guns gate is not imposed on the diplomatic category.
  **欧洲联邦：**比利时、奥地利通过 Götterdämmerung 国策，荷兰通过 Man the Guns 国策解锁外交路线，包含邀请、科研共享、工业投资、联邦化、成立联邦及 13 个地区扩张。使用 ME 地区选择器，只吞并符合条件的附属国，不直接吞并独立国家。原有通用欧盟路线不变，其额外 DLC 门槛不套用到外交决议类别。
- **ME behavior retained:** half-scale units and the existing Vichy-France model. Both Yugoslav coup entrypoints transfer BOS after `YUG_safeguard_bosnia`, and BOS plus HRZ after `YUG_divide_bosnia`, preserving original ME puppet semantics.
  **保留 ME 玩法：**半规模编制、现有维希法国实现；南斯拉夫两个政变入口均在“保全波斯尼亚”后转移 BOS，在“分割波斯尼亚”后转移 BOS 与 HRZ，沿用 ME 原有附庸处理逻辑。
- **Approved vanilla updates:** retain the new German cores from eligible Molotov–Ribbentrop transfers and Bulgaria's Balkan-duty coefficient `-0.03`. The Bulgarian uranium focus grants one `bonus = 1.0` nuclear-tech use plus `0.8` nuclear breakthrough before `atomic_research`, or `1.0` breakthrough afterward; its ME state target and logging remain unchanged. This does not add resource-prospecting or naval-treaty decisions.
  **采用的原版更新：**保留德苏分区中符合条件的新增德国核心、保加利亚巴尔干责任系数 `-0.03`。铀矿国策在未研究原子能时提供一次 `bonus = 1.0` 核科技加成与 `0.8` 核突破进度，研究后提供 `1.0` 突破进度；ME 州目标及日志不变。不因此加入资源勘探或海军条约决议。

Verified against HOI4 `Operation Postern v1.20.0.0.8873` (static audits: `audit_eu_script_refs.py` 0 European mismatches / 0 off-map EU hijacks across 19,134 annotated lines, `test_eu_mapping.py` 8/8 OK, `railway_audit.py` 0 chain breaks, `buildings_audit.py` 0 errors, ref/OOB/loc/VP audits; live `-debug -hands_off -start_speed=4` historical observer run to `1939.1.1.2` with 46 owned DLCs: 0 new non-vanilla-parity mod errors in `error.log`, Rhineland DMZ cleared 48/48, Anschluss 36/36 and Sudetenland 11/11 transferred to GER, ROM/majors advancing focuses with `v971` Northern Dobruja cleanly mapped). Note: `has_dlc = "To The Bitter End"` content is engine-parsed and script/loc-verified; active in-game execution of those branches awaits an environment with the TTBE DLC active.
已完成对 HOI4 `Operation Postern v1.20.0.0.8873` 的静态审计与实机 `-debug -hands_off -start_speed=4` 史实观察者验收（推进至 `1939.1.1.2`）：19,134 条州注解 0 欧陆错配与 0 图外劫持、`test_eu_mapping` 8/8 通过、铁路 0 断链、建筑 0 报错；实机 `error.log` 无新增非原版同源模组报错，莱茵兰 48/48 非军事区、德奥合并 36/36、苏台德 11/11 及主要/巴尔干各国（含罗马尼亚 16 项国策与 `v971` 北多布罗加）均正常推进。注：`has_dlc = "To The Bitter End"` 分支已完成全量引擎解析与脚本/本地化核验，其激活后局内实机表现待拥有并启用该 DLC 时进一步验证。

## Mods / 模组

*Expert AI 5.0 listed below; other mods: assume incompatible until verified.*
*Expert AI 5.0 见下表；其余模组验证前一律视为不兼容。*

| Mod | Status / 状态 | Notes / 备注 |
|---|---|---|
| Expert AI 5.0 | ✅ Via submod / 经子mod | Requires `Magna Europa: Expert AI` patch submod (D:/Projects/ME-ExpertAI), loaded after both parents. EAI's embedded vanilla map DB is regenerated for this map there; do NOT run EAI without it (its vanilla state IDs would fire on wrong states). / EAI 内嵌的原版地图数据库已在子mod中按本地图重建；不要脱离子mod裸跑。 |
| Toolpack without the Errors (2913150560) | ✅ Via submod / 经子mod | Requires `Magna Europa: Toolpack` patch submod (D:/Projects/ME-Toolpack), loaded after both parents. ME's `replace_path` eats toolpack's whole script layer unless it loads after ME — its descriptor only depends on "Magna Europa: Reloaded" (predecessor, never matches our name) — so the submod re-ships the script layer; `tpt.txt` faction-name `owns_state` ids remapped to ME capital states. Do NOT use the discontinued "Toolpack for Magna Europa" (2973258716, 1.13-only repack). / 需要 `Magna Europa: Toolpack` 补丁子mod（D:/Projects/ME-Toolpack），加载在两个母mod之后。ME 的 `replace_path` 会吞掉 toolpack 全部脚本层（其依赖只指向旧作 "Magna Europa: Reloaded"），故子mod重发脚本层；`tpt.txt` 的阵营命名 `owns_state` 已按 ME 首都州重映射。勿用已停更的 "Toolpack for Magna Europa"（2973258716，仅支持 1.13 的重新打包）。 |
