[← Back to Overview](README.md) · [简体中文主页](README_zh-CN.md) · [Architecture](methodology.md) · [CLI Guide](guide.md)

# Offline Interactive Cartographic Review Atlas

In addition to static tables and CLI translators, the repository ships a **zero-server interactive HTML atlas** containing pre-rendered pixel-hit canvases (`me_hit.png` and `van_hit.png`) for all `2,961` European/MENA ME states and `464` European Vanilla states:

- Entry point: [`docs/states/eu_mapping/review/index.html`](../states/eu_mapping/review/index.html)

> [!NOTE]
> Because browsers block cross-file fetches over `file:///` in some configurations, launch a local Python HTTP server (or open after cloning) if interactive overlay loading is restricted in your browser.

---

## Launching Locally

From the repository root:

```bash
python -m http.server 8765 --directory docs/states/eu_mapping/review
```

Then open `http://localhost:8765/` in any modern browser.

---

## Four Inspection Modes

1. **Swipe Atlas**: Interactive split-screen curtain slider comparing Vanilla borders against Magna Europa borders pixel-for-pixel at any zoom level.
2. **Parent-Color Check**: Full 8-color graph-colored Vanilla canvas paired with Magna Europa states inheriting their assigned parent hue, plus a one-click toggle to overlay legacy `m2v_states.tsv` differences.
3. **Spotlight A / B / C / D**: Click any ME state to isolate its top candidate Vanilla parents with distinct candidate colors, overlay `rivers.bmp` hydrology, display **ME (`●`) & Vanilla (`◆`) Victory Point city pins**, inspect ME regional `add_core_of` signatures, compare Vanilla focus/event territory, look up historical geography, and review full reasons for all `59` entries in [`adjudications.tsv`](../states/eu_mapping/adjudications.tsv).
4. **Synced Side-by-Side**: Pan-and-zoom synchronized dual viewports locked to identical geographic coordinates.
