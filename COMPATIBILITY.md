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
| Peace For Our Time | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |
| Poland: United and Ready | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |
| Axis Armor Pack | ✅ Supported (`has_dlc` checks) / 支持（含 `has_dlc` 检查） |

## Mods / 模组

*Expert AI 5.0 listed below; other mods: assume incompatible until verified.*
*Expert AI 5.0 见下表；其余模组验证前一律视为不兼容。*

| Mod | Status / 状态 | Notes / 备注 |
|---|---|---|
| Expert AI 5.0 | ✅ Via submod / 经子mod | Requires `Magna Europa: Expert AI` patch submod (D:/Projects/ME-ExpertAI), loaded after both parents. EAI's embedded vanilla map DB is regenerated for this map there; do NOT run EAI without it (its vanilla state IDs would fire on wrong states). / EAI 内嵌的原版地图数据库已在子mod中按本地图重建；不要脱离子mod裸跑。 |
