<div align="center">

<img src="thumbnail.png" alt="Toolpack for Magna Europa: Reforged">

# Toolpack for Magna Europa: Reforged

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Hearts of Iron IV](https://img.shields.io/badge/HoI4-1.19.*-orange)

[English](README.md) | **简体中文**

**一个最小化兼容补丁，让 [Toolpack without the Errors](https://steamcommunity.com/sharedfiles/filedetails/?id=2913150560) 能在 [Magna Europa: Reforged](https://steamcommunity.com/sharedfiles/filedetails/?id=3809580289) 下正常工作。**

</div>

---

## 为什么需要这个补丁

Magna Europa: Reforged 对整个脚本层声明了 `replace_path`（`common/scripted_guis`、`common/decisions`、`events` 等）——正好是 Toolpack 写入的所有目录。而 Toolpack 的 descriptor 依赖只指向旧作 *Magna Europa: Reloaded*，没有任何机制保证它排在 Reforged 之后；一旦先加载，**其脚本层会被静默整体丢弃**，mod 表现为"装了没反应"。

本补丁把 Toolpack 的脚本层文件（41 个，与上游 2026-08-10 修订逐字节一致，仅下述重映射行除外）原样重发，并声明对两个母 mod 的依赖以保证最后加载，使 Toolpack 的 UI 得以存活。

`interface`、`gfx`、本地化与 opinion_modifiers 文件**不复制**——仍由 Toolpack 本体提供，因此必须订阅 Toolpack。

## 必需模组

| 模组 | 链接 |
|---|---|
| Magna Europa: Reforged | [Workshop 3809580289](https://steamcommunity.com/sharedfiles/filedetails/?id=3809580289) |
| Toolpack without the Errors | [Workshop 2913150560](https://steamcommunity.com/sharedfiles/filedetails/?id=2913150560) |
| **本补丁**——与两者同时启用；`dependencies` 保证其最后加载 | |

## 排障

- **游戏内看不到面板/悬浮按钮**：先按 **Ctrl+Shift+H**（切换 `toolpack_hidden` 全局 flag），再按 **Ctrl+T** 展开面板。展开箭头在右下边缘（距右约 21px、距底约 260px），很小容易漏看。
- **激活 mod 列表里没有 Toolpack**：启动时 Steam 还在下载工坊文件——等下载完成后重启游戏即可。切勿在 `mod/` 里再放一份同名的本地副本（同名冲突）。
- error.log 里 `TPT_pos_*_tyes` 的 malformed token 警告是上游自带瑕疵（意见修改器名超过 token 长度上限），两条效果被无害跳过，按原样保留。

## 为 ME 地图所做的改动

仅 **`common/scripted_guis/tpt.txt` 中的 8 行**：阵营条约风味命名（`tpt_create_faction_click`）用 vanilla 首都州 ID 做 `owns_state` 判定，在 ME 地图上指向错误的州。已重映射到持有对应首都胜利点的 ME 州：

| Vanilla 州 | 含义 | → ME 州 |
|---|---|---|
| 3 | 日内瓦 | 1452 — Geneve |
| 16 | 巴黎 | 571 — Paris |
| 126 | 伦敦 | 2475 — London |
| 64 | 柏林 | 1650 — East Berlin |
| 2 | 罗马 | 876 — Roma Centro |
| 219 | 莫斯科 | 229 — Moscow |
| 4 | 维也纳 | 1817 — North Vienna |
| 10 | 华沙 | 508 — Warszawa |

其余内容已审计无需改动——所有其他州/省访问均为动态作用域，无字面量省份 ID，建筑/科技/意识形态 ID 在 ME 全部可解析。完整审计见 [PORTING.md](PORTING.md)。

## 已知限制

- 装饰性 tag 工具（`cct`）对没有 `TAG_ideology` 装饰性 tag 的国家静默无效——与 vanilla 表现一致。
- ME 科技树没有 `improved`/`advanced_battlecruiser` 船壳，对应两个生成选项静默无效。
- 请勿使用已停更的 *Toolpack for Magna Europa*（Workshop 2973258716，仅 1.13 的重打包）。

## 上游更新后如何同步

```bash
python tools/sync_toolpack.py <新下载的toolpack目录>
```

重新复制脚本层并自动复打 8 行重映射，同时清除上游已删除的文件；完成后用 `git diff` 检查。

## 版本

`v0.1.0` — 对应 Toolpack **2026-08-10** 修订、Magna Europa: Reforged **0.99.7**、HoI4 **1.19.\***。
