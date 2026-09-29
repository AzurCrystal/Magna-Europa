# tools/

Mod development utilities. These are **not shipped** to the workshop — release packaging only copies game content dirs (common/events/gfx/history/interface/localisation/map/music/tutorial).

| Script | Purpose |
|---|---|
| `pdx_parse.py` | Clausewitz script parser — syntax-check `.txt` files after edits |
| `prov_adjacency.py` | Rebuild province adjacency from `provinces.bmp` + `adjacencies.csv` → JSON |
| `railway_audit.py` | Verify `railways.txt`/`supply_nodes.txt` integrity (chain breaks, sea hops, connectivity) |
| `vp_loc_audit.py` | Verify every mod VP has loc coverage; flag name collisions between loc files |
| `unAccenternator.py` | Legacy utility that strips diacritics from `history/states` filenames |

Run map audits after any `map/` edit:

```bash
python tools/railway_audit.py      # must print 0 breaks / 0 sea / 0 bad nodes
python tools/vp_loc_audit.py       # must print 0 missing / 0 collisions
```
