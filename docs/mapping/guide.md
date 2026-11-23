[← Back to Overview](README.md) · [简体中文主页](README_zh-CN.md) · [Architecture](methodology.md) · [Interactive Atlas](atlas.md)

# Submodder & Content-Porter CLI Guide

All translation and audit CLI utilities run purely on the **Python standard library** (`python 3.10+`, zero third-party packages required) directly against the versioned TSVs in [`docs/states/eu_mapping/`](../states/eu_mapping/).

---

## 1. Translating Territorial Lists (Layer 3 — `--mode territory`)

Use `--mode territory` whenever Vanilla scripts transfer, core, claim, demilitarize, or check ownership of a set of states. When you supply multiple Vanilla state IDs together (for example, `42` Moselland + `51` Rhineland + `978` Baden), the tool computes the **territory union**, automatically merges internal border straddlers, and annotates each emitted ID with `# was vanilla N`:

```bash
python tools/translate_vanilla_states.py --states 42 51 978 --mode territory --emit transfer_state
```

Verified output:

```text
# Magna Europa territory union for vanilla states [42, 51, 978] (37 ME states)
transfer_state = 537 # Saarland (was vanilla 42 Moselland)
...
transfer_state = 1762 # Waldshut (was vanilla 978 Baden) [outer straddler 72%]
transfer_state = 1765 # Mannheim und Heidelberg (was vanilla 978 Baden) [outer straddler 67%]
transfer_state = 1766 # Calw (was vanilla 978 Baden) [outer straddler 59%]
transfer_state = 2583 # Vaihingen (was vanilla 978 Baden) [outer straddler 49%]

--- Summary: 37 ME states | internal straddlers auto-cancelled: 1 | included outer straddlers: 4 | excluded outer straddlers (>=20%): 9 ---
  [outer advisory, NOT included] ME #1457 Schaffhausen (SWI): 100.0% inside union (partition parent = v3 Swiss Plateau)
  ...
```

---

## 2. Translating Single-Target City / Industry Effects (Layer 4 — `--mode city`)

Never emit 10× factories or building slots when porting a Vanilla state split into 8–21 ME sub-states. Use `--mode city` to resolve each Vanilla state to its unique `1→1` primary city state:

```bash
python tools/translate_vanilla_states.py --states 126 16 64 --mode city
```

Verified output:

```text
571 # Paris (was vanilla 16 Ile de France; basis=national_capital:FRA)
1650 # Ost-Berlin (was vanilla 64 Brandenburg; basis=national_capital:GER)
2475 # London (was vanilla 126 Greater London Area; basis=national_capital:ENG)
```

---

## 3. Translating Province IDs, Full Snippets & Vanilla Patch Diffs

```bash
# Province IDs (for set_province_controller, fortifications, canals, railway hops)
python tools/translate_vanilla_states.py --provinces 6521 11506

# Full Vanilla focus/decision/event snippet (auto-detects territory vs city vs province context)
python tools/translate_vanilla_states.py --snippet path/to/vanilla_focus.txt

# Diff two Vanilla HOI4 patches (e.g. 1.19 -> 1.20) and emit ONLY changed lines translated to ME
python tools/translate_vanilla_states.py --diff old_vanilla.txt new_vanilla.txt
```

---

## 4. Translating & Auditing Strategic Regions (Layer 5)

Use [`tools/translate_vanilla_regions.py`](../../tools/translate_vanilla_regions.py) for land and sea strategic region IDs (`strategic_region = N`, `region = N`, or `ai_strategy` region targets):

```bash
python tools/translate_vanilla_regions.py --regions 7 8 18
```

Verified output:

```text
v7 (Western Germany) [mapped cov=1.000]:
  ME -> 35 (Southern Germany), 36 (Rhineland), 70 (Westphalia)
  IDs: 35 36 70
v8 (Eastern Germany) [mapped cov=0.998]:
  ME -> 69 (Brandenburg), 11 (Silesia), 40 (Western Pomerania), 68 (Saxony), 95 (Eastern Pomerania)
  IDs: 69 11 40 68 95
v18 (English Channel) [mapped cov=0.741]:
  ME -> 209 (English Channel)
  IDs: 209
```

Audit all mod scripts for unmigrated or collision-prone Strategic Region references and European state annotations:

```bash
# Scan all ME-modified scripts for strategic_region / region = N references
python tools/translate_vanilla_regions.py --audit-scripts

# Verify all `# was vanilla N` state/province script annotations against eu_mapping
python tools/audit_eu_script_refs.py --audit-annotations
```

Current baseline from `--audit-scripts`: `1,278` `OK_ME` hits, `0` invalid IDs, `0` duplicate `ai_strategy` targets (only intentional NA inset / Congo proxy refs and reviewed vanilla-parity collision triggers flagged for inspection).

---

## 5. Regenerating Tables, Running Tests & Re-Rendering Visuals

```bash
# Rebuild registration + Layers 1-4 + offline interactive atlas (requires numpy, scipy, Pillow)
python tools/build_eu_mapping.py

# Rebuild Layer 5 Strategic Regions (pure stdlib)
python tools/build_region_mapping.py

# Re-render the 3 showcase PNGs under docs/mapping/assets/ (pure stdlib — ~4 seconds!)
python tools/render_mapping_showcase.py

# Run test suites & live SR map coverage audit
python -m unittest discover -s tools -p test_eu_mapping.py -v
python -m unittest discover -s tools -p test_region_mapping.py -v
python tools/sr_audit.py
```
