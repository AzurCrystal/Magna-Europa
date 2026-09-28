<div align="center">

<img src="thumbnail.png" alt="Magna Europa: Reforged">

# Magna Europa: Reforged

![Version](https://img.shields.io/badge/version-0.99.2-blue)
![Hearts of Iron IV](https://img.shields.io/badge/HoI4-1.19.*-orange)
[![Steam Workshop](https://img.shields.io/badge/Steam%20Workshop-3809580289-1b2838)](https://steamcommunity.com/sharedfiles/filedetails/?id=3809580289)

**[English](README.md)** | 简体中文

**《钢铁雄心4》大型地图重制模组：更细致的欧洲，以及更多内容。**

**在密度大幅提升的省份地图上重建世界，并将原版与 DLC 的全部游戏内容移植到新地图上。**

</div>

> [!NOTE]
> **Reforged** 是 **Edouard_Saladier** 原作 [Magna Europa: Reloaded](https://steamcommunity.com/sharedfiles/filedetails/?id=3150495839) 项目的社区续作，已迁移至 HoI4 **1.19.\***，目前处于活跃开发中。

> [!CAUTION]
> 本模组基于 [Magna Europa Alpha](https://steamcommunity.com/sharedfiles/filedetails/?id=3586596093)（Alpha 本身也是对原作的社区重制）开发，但对其内容做了大幅精简与修改，因此可视为**不兼容任何为 Magna Europa Alpha 或 Reloaded 制作的子模组**，以及任何其他修改游戏内容的模组。具体兼容情况请查阅[兼容性列表](COMPATIBILITY.md)。

---

## 特性

- **重制地图** —— 约 21,600 个省份、约 2,980 个地区，战略区域、铁路、补给节点、河流与相邻关系全部重绘
- **440+ 国家** —— 遍及欧洲的新 tag、可释放国家与装饰性国家
- **全内容移植** —— 所有主要国家与 DLC 国家的国策树、事件、决议、人物与 AI 规划均已重映射到新地图（Götterdämmerung、Arms Against Tyranny、By Blood Alone、Graveyard of Empires、No Step Back、La Résistance、Man the Guns、Waking the Tiger、Trial of Allegiance、Thunder at Our Gates 等）
- **两个开局剧本** —— *The Gathering Storm*（1936 年 1 月 1 日）与 *Blitzkrieg*（1939 年 8 月 14 日）
- **新增机制** —— 可成立国家决议、动态地区改名、scripted GUI、自定义游戏规则
- **本地化** —— 英文（完整）、俄文、简体中文（进行中）

## 安装

1. 在 Steam 创意工坊订阅：[Magna Europa: Reforged](https://steamcommunity.com/sharedfiles/filedetails/?id=3809580289)
2. 在启动器播放集中启用 **Magna Europa: Reforged**，开始游戏。

## 兼容性

本模组对 `history/`、`events/`、`map/strategicregions` 及 `common/` 的大部分目录使用 `replace_path`，并以同名路径下的自带地图位图覆盖原版文件——实际上替换了整个游戏状态。**请默认它与其他所有模组不兼容**，包括子模组，除非另有说明。

- 需求游戏版本：**1.19.\***
- 不强制要求任何 DLC；DLC 专属内容均通过 `has_dlc` 检查做了门槛处理。
- 详细的 DLC 与模组支持情况见[兼容性列表](COMPATIBILITY.md)。

## 仓库结构

| 路径 | 内容 |
|---|---|
| `map/` | 省份/地区位图、`definition.csv`、相邻关系、铁路、战略区域 |
| `common/` | 游戏数据库：国家、国策树、决议、精神、人物、scripted effect/trigger、科技、部队、AI |
| `events/` | 事件脚本（原版 + DLC 移植，以及 MER 新增） |
| `history/` | 国家、地区、部队与将领历史——完全替换 |
| `localisation/` | `english/`、`russian/`、`simp_chinese/` |
| `interface/`、`gfx/` | UI 脚本、旗帜、头像、字体 |
| `music/` | 模组音乐与播放列表 |
| `descriptor.mod` | 模组内部描述文件（名称、版本、`replace_path` 列表） |
| `Magna Europa.mod` | 启动器用描述文件——复制到 `mod/` 目录并添加 `path=` |

以 `_` 开头的文件和目录（如 `_dev/`、`_audit_*.tsv`、`_118_base/`）是 1.19 移植期间的迁移与审计产物，已被 `.gitignore` 忽略，**不属于发布的模组内容**。


## 参与贡献

### 开发环境搭建


1. **先退订 / 卸载创意工坊的 Magna Europa。** Git 版使用相同的 workshop ID，二者会冲突。
2. 将本仓库克隆到任意位置：

   ```bash
   git clone https://github.com/AzurCrystal/Magna-Europa.git
   ```

3. 把仓库中的 `Magna Europa.mod` 复制到启动器的 mod 目录：

   ```
   Documents/Paradox Interactive/Hearts of Iron IV/mod/
   ```

4. 打开 **复制到 `mod/` 目录的那份**文件，把 `path=` 行改成你的克隆路径，例如：

   ```
   path="D:/Projects/Magna-Europa"
   ```

   > **注意**：仓库内的 `descriptor.mod` 是模组内部描述文件——**不要**修改它。只有放进 `mod/` 目录的 `Magna Europa.mod` 副本才带 `path=` 行。

5. 在启动器播放集中启用 **Magna Europa: Reforged**（克隆版在启动器中显示为"硬盘上的"本地模组），开始游戏。

### 反馈问题与提交贡献

- 反馈 bug：请在 [GitHub issue](https://github.com/AzurCrystal/Magna-Europa/issues) 中报告，并附上游戏版本、模组版本以及相关 `error.log` 片段。
- 欢迎提交 PR——fork 仓库、开分支，向 [`AzurCrystal/Magna-Europa`](https://github.com/AzurCrystal/Magna-Europa) 发起 pull request。
- 原项目上游：[`Cyberbace-company/Magna-Europa`](https://github.com/Cyberbace-company/Magna-Europa)

## 致谢

- **[Magna Europa](https://steamcommunity.com/sharedfiles/filedetails/?id=2152140768)** —— 已停更的原版项目，谱系源头
- **[Magna Europa: Reloaded](https://steamcommunity.com/sharedfiles/filedetails/?id=3150495839)** 团队 —— 官方续作，承载地图、剧本与内容基础：**Edouard_Saladier**（主导）、LolloBlue96、FBKong 及其他贡献者
- **Bacegun** —— [Magna Europa Alpha](https://steamcommunity.com/sharedfiles/filedetails/?id=3586596093) 作者，Reforged 直接基于该社区重制版开发
- Paradox Interactive —— 《钢铁雄心4》及全部移植的 DLC 内容
