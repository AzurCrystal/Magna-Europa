#!/usr/bin/env python
"""Build the vanilla 1.19.4 focus-tree state-reference corpus -> Magna Europa
member tables: docs/states/focus_state_refs.tsv + focus_state_map.tsv.

Walks every national-focus file on both sides with a brace-aware Clausewitz
parser and extracts true state references only:

  * scalar state keys        state = N / owns_state = N / transfer_state_to ...
  * numeric state scopes     N = { ... }          (with namespace traps below)
  * state lists              states = { N ... }, prioritize = { N ... } beneath
                             *_state quantifier scopes, numeric entries under
                             states_filter = { ... }
  * scope chains             has_war_with = 762.owner  (leading numeric root)
  * nested state args        state = N inside set_capital / capital_scope /
                             has_resources_rights / add_resource blocks

Excluded namespaces: province/path/region/strategic_region/victory_points keys,
province ids under set_province_name/add_victory_points, numeric weight blocks
under random/random_list, and numeric scopes whose first key is a modifier-ish
key (modifier/factor/add_attack/add_defense/add_logistics/add_planning).
Numbers are never classified by magnitude: a literal is a state ref only when
the grammar context demands one.

Ref rows resolve against the canonical docs/states tables. `m2v`/`v2m` are
the primary compatibility views; when `docs/states/state_edges.tsv` exists
the generated relation-evidence ledger is loaded as well. Only edges whose
`relation_scope` is `compatibility` (the adjudicated m2v/v2m primary edge —
a compatibility assertion, not a complete-subset proof) or `asserted`
(explicit comment/manual relation declarations) answer "is this a real
relation": they supply each live literal's asserted ancestor set
(`edge_ancestors`, canonical first) and each vanilla state's asserted member
set (`edge_members`, primary family first). `observation`-scope edges —
raster/candidate/name/pixel/vp heuristics recording sampled overlap, never
a relation claim — are reported separately in `observed_ancestors` /
`observed_members` and never enter attribution or collision logic.
  vanilla side  literal = vanilla id; NEVER a collision — literal_is_live_me
                is informational only: sharing a number with a live ME state
                is a namespace overlap, not a conflict.
  mod side      literal = ME id; vanilla_id = canonical m2v ancestor;
                collision ONLY when an explicit `# was vanilla ...` comment
                cites a vanilla-id set disjoint from every asserted ancestor
                (canonical plus compatibility/asserted secondary edges when
                the edge table is loaded). A comment matching only an
                asserted secondary edge resolves ok|comment_secondary_edge,
                not comment_mismatch; matching only an observation edge does
                NOT suppress the collision. Bare numeric overlap with the
                vanilla namespace is namespace_overlap, not a collision; a
                literal that is a vanilla id but not a live ME id stays
                stale_vanilla_id.
On mod rows covers/current_me_refs are per-occurrence: the set of literals
emitted by the same enclosing list block (a scalar is just itself); `full`
means that occurrence alone covers the ancestor's whole PRIMARY partition
cell — secondary edge members never extend coverage. Vanilla rows and the
map table's mod_coverage use corpus-union scope.

Usage: python tools/build_focus_state_refs.py   (HOI4_DIR / ME_MOD overridable)
"""
import csv
import glob
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _modroot import mod_root, repo_root  # noqa: E402

HOI4 = os.environ.get("HOI4_DIR") or r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
MOD = os.environ.get("ME_MOD") or mod_root()
OUT = os.path.join(repo_root(), "docs", "states")

# ---------------------------------------------------------------- corpus I/O

def fail(msg):
    print("ERROR:", msg)
    sys.exit(1)


def load_tsv(path, key):
    if not os.path.isfile(path):
        fail("missing canonical table %s" % path)
    rows = list(csv.DictReader(open(path, encoding="utf-8"), delimiter="\t"))
    return {int(r[key]): r for r in rows}, rows


v2m_rows, v2m_list = load_tsv(os.path.join(OUT, "v2m_states.tsv"), "vanilla_id")
m2v_rows, m2v_list = load_tsv(os.path.join(OUT, "m2v_states.tsv"), "me_id")

me_ancestor = {int(r["me_id"]): int(r["v_ancestor"]) for r in m2v_list}
live_me = set(me_ancestor)
live_vanilla = set(v2m_rows)

# --------------------------------------------------- optional relation edges
# docs/states/state_edges.tsv is the generated relation-evidence ledger —
# one row per (me_id, vanilla_id) edge; the m2v/v2m tables remain the
# primary compatibility views this tool's coverage columns are built on.
# Every row carries relation_scope: `compatibility` (the canonical m2v/v2m
# primary edge — an inherited compatibility assertion, NOT a complete-subset
# proof), `asserted` (explicit comment/manual relation declarations),
# `observation` (raster/candidate/name/pixel/vp heuristics — sampled overlap
# evidence only, never a relation claim), `excluded` (synthetic MAP edges).
# Only compatibility+asserted scopes may drive relation reasoning: they feed
# comment attribution (a comment matching only an asserted secondary edge is
# a real relation, not a collision) and the edge_ancestors/edge_members
# report columns. Observation edges are reported separately in
# observed_ancestors/observed_members for explicit query and NEVER enter
# attribution — a comment matching only an observed (geometry-candidate)
# edge still counts as a real collision. When the ledger is absent the tool
# resolves against the primary views only, exactly as before.

_SCOPES_ASSERTED = ("compatibility", "asserted")
_SCOPES_OBSERVED = ("observation",)


def _relation_scope(row):
    """relation_scope of one edge row; derive it with the generator's
    precedence (excluded > is_canonical > comment|manual families) when a
    legacy table without the column is read through the local fallback."""
    scope = (row.get("relation_scope") or "").strip()
    if scope:
        return scope
    if (row.get("edge_class") or "").strip() == "excluded":
        return "excluded"
    if (row.get("is_canonical") or "").strip() == "1":
        return "compatibility"
    families = set((row.get("evidence_families") or "").split("|"))
    if "comment" in families or "manual" in families:
        return "asserted"
    return "observation"


class _EdgeView:
    """Minimal row accessor for state_edges.tsv, used only when the shared
    state_relations loader is unavailable. Rows are normalised to carry the
    same _v/_m integer keys the loader sets so the scope index below is
    identical for both paths. No schema validation is repeated here."""

    def __init__(self, path):
        rows = []
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                r["_v"], r["_m"] = int(r["vanilla_id"]), int(r["me_id"])
                rows.append(r)
        self.edges = rows


EDGES = None
_edges_path = os.path.join(OUT, "state_edges.tsv")
if os.path.isfile(_edges_path):
    try:
        import state_relations
        EDGES = state_relations.load(OUT)
        print("edges: state_edges.tsv loaded via state_relations")
    except ImportError:
        EDGES = _EdgeView(_edges_path)
        print("edges: state_edges.tsv loaded directly (no state_relations)")
    except ValueError as exc:
        fail("state_edges.tsv present but invalid: %s" % exc)

# scope-partitioned indexes over the edge ledger + canonical/primary flags
# for ordering (canonical first, primary member first, then ascending id).
_edge_anc = defaultdict(lambda: defaultdict(set))  # me -> scope -> vanilla ids
_edge_mem = defaultdict(lambda: defaultdict(set))  # v  -> scope -> me ids
_canon_of = {}                                     # me -> canonical vanilla
_prim_of = {}                                      # v  -> primary me
if EDGES is not None:
    for _r in EDGES.edges:
        _scope = _relation_scope(_r)
        _v, _m = _r["_v"], _r["_m"]
        _edge_anc[_m][_scope].add(_v)
        _edge_mem[_v][_scope].add(_m)
        if (_r.get("is_canonical") or "").strip() == "1":
            _canon_of[_m] = _v
        if (_r.get("canonical_primary_of_vanilla") or "").strip() == "1":
            _prim_of[_v] = _m


def _scoped(buckets, scopes, first):
    """Ids in `buckets` (scope -> id set) restricted to `scopes`, `first`
    promoted to the front when present, the rest ascending."""
    ids = sorted(i for s in scopes for i in buckets.get(s, ()))
    if first in ids:
        ids.remove(first)
        ids.insert(0, first)
    return ids


def edge_ancestors_of(me_id):
    """Asserted-scope vanilla ancestors of a live ME id (compatibility +
    asserted edges), canonical first — just the canonical m2v ancestor when
    the edge table is absent or has no asserted rows for the state."""
    if EDGES is not None:
        anc = _scoped(_edge_anc.get(me_id, {}), _SCOPES_ASSERTED,
                      _canon_of.get(me_id))
        if anc:
            return anc
    return [me_ancestor[me_id]]


def observed_ancestors_of(me_id):
    """Observation-scope vanilla ids related to a live ME id: heuristic
    (raster/candidate/name/pixel/vp) edges only — sampled overlap evidence,
    never a relation claim. Empty when the edge table is absent."""
    if EDGES is not None:
        return _scoped(_edge_anc.get(me_id, {}), _SCOPES_OBSERVED, None)
    return []


def edge_members_of(vid):
    """ME states related to a vanilla id by an asserted-scope edge,
    primary member first. Empty when the edge table is absent."""
    if EDGES is not None:
        return _scoped(_edge_mem.get(vid, {}), _SCOPES_ASSERTED,
                       _prim_of.get(vid))
    return []


def observed_members_of(vid):
    """ME states related to a vanilla id only by observation-scope
    (heuristic) edges. Empty when the edge table is absent."""
    if EDGES is not None:
        return _scoped(_edge_mem.get(vid, {}), _SCOPES_OBSERVED, None)
    return []

# ordered members: primary first, then the rest ascending (canonical
# me_members cells are already ordered that way; enforce regardless)
members_ordered = {}
for vid, r in v2m_rows.items():
    mem = [int(x) for x in r["me_members"].split("|") if x]
    prim = r["me_primary"]
    if mem:
        rest = sorted(x for x in mem if str(x) != prim)
        members_ordered[vid] = ([int(prim)] if prim else []) + rest
    else:
        members_ordered[vid] = []

# ---- partition invariants: v2m members must be an exact partition of live ME
_bad = []
_flat = []
for vid, mem in members_ordered.items():
    for x in mem:
        _flat.append(x)
        if x not in live_me:
            _bad.append("member %d of vanilla %d is not a live ME state" % (x, vid))
        elif me_ancestor[x] != vid:
            _bad.append("member %d of vanilla %d has m2v ancestor %d" % (x, vid, me_ancestor[x]))
    prim = v2m_rows[vid]["me_primary"]
    if mem and (not prim or int(prim) not in mem):
        _bad.append("vanilla %d primary %r not inside members" % (vid, prim))
if len(_flat) != len(set(_flat)):
    fail("canonical partition broken: duplicate v2m members")
if set(_flat) != live_me:
    fail("canonical partition broken: v2m members != live ME set (%d missing, %d extra)"
         % (len(live_me - set(_flat)), len(set(_flat) - live_me)))
if _bad:
    fail("canonical partition broken: " + "; ".join(_bad[:5]))

# ------------------------------------------------------- is_europe (vanilla)
# definition.csv last column = continent id; 1 = europe (continent.txt order).
def _is_europe_map():
    p2c = {}
    dcsv = os.path.join(HOI4, "map", "definition.csv")
    if not os.path.isfile(dcsv):
        fail("missing %s" % dcsv)
    for row in csv.reader(open(dcsv, encoding="utf-8", errors="replace"), delimiter=";"):
        if len(row) > 7 and row[0].lstrip("-").isdigit():
            p2c[int(row[0])] = row[7].strip()
    out = {}
    # history filenames are "<id> - <Name>.txt" or "<id>-<Name>.txt"
    state_files = {}
    for p in glob.glob(os.path.join(HOI4, "history", "states", "*.txt")):
        mm = re.match(r"(\d+)", os.path.basename(p))
        if mm:
            state_files[int(mm.group(1))] = p
    for vid in sorted(live_vanilla):
        hits = [state_files[vid]] if vid in state_files else []
        if not hits:
            continue
        txt = open(hits[0], encoding="utf-8", errors="replace").read()
        m = re.search(r"provinces\s*=\s*\{([^}]*)\}", txt)
        if not m:
            continue
        votes = defaultdict(int)
        for p in re.findall(r"\d+", m.group(1)):
            votes[p2c.get(int(p), "?")] += 1
        votes.pop("?", None)
        if votes:
            out[vid] = 1 if max(sorted(votes), key=lambda c: votes[c]) == "1" else 0
    return out


IS_EUROPE = _is_europe_map()

# ------------------------------------------------------------- parser
_TOK = re.compile(r"""
    (?P<ws>[ \t\r\n]+)
  | (?P<comment>\#[^\n]*)
  | (?P<open>\{)
  | (?P<close>\})
  | (?P<eq>[<>=])
  | (?P<str>"(?:[^"\\]|\\.)*")
  | (?P<lit>[^\s{}<>=#"']+)
""", re.X)


def tokenize(text):
    import bisect
    nl = [0] + [m.end() for m in re.finditer(r"\n", text)]
    for m in _TOK.finditer(text):
        k = m.lastgroup
        if k == "ws":
            continue
        v = m.group(0)
        if k == "str":
            v = v[1:-1]
        yield (k, v, bisect.bisect_right(nl, m.start()))


class Node:
    __slots__ = ("key", "val", "line", "end_line", "children", "kind",
                 "pre_comment", "post_comment")

    def __init__(self, key, val, line):
        self.key = key
        self.val = val
        self.line = line
        self.end_line = line
        self.children = None   # list => block; None => scalar/bare
        self.kind = "bare"
        self.pre_comment = ""
        self.post_comment = ""


class Parser:
    """Recursive-descent over the token stream with comment attachment.

    A comment on the same line a statement ends attaches to that statement as
    post_comment; comments on following lines attach to the next statement as
    pre_comment (list blocks propagate post comments to their last member).
    """

    def __init__(self, text):
        self.toks = list(tokenize(text))
        self.i = 0
        self.errors = []
        self.warnings = []

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else ("eof", None, -1)

    def pop(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def parse(self):
        return self._stmts("eof", 0)

    def _stmts(self, end, depth):
        out = []
        pending = []       # comment strings not yet attached
        last_end = -1      # line the previous statement in this block ended
        while True:
            k, v, ln = self.peek()
            if k == "eof":
                if end != "eof":
                    self.errors.append("unclosed block at eof (depth %d)" % depth)
                return out
            if k == "close":
                if end == "eof":
                    # game-tolerated stray '}' (TSR_lingguang... line 420)
                    self.warnings.append("stray } at line %d" % ln)
                    self.pop()
                    continue
                return out
            if k == "comment":
                self.pop()
                c = v.lstrip("#").strip()
                if ln == last_end and out:
                    out[-1].post_comment = (out[-1].post_comment + " | " + c
                                            if out[-1].post_comment else c)
                else:
                    pending.append(c)
                continue
            self.pop()
            if k in ("lit", "str"):
                node = Node(v, None, ln)
                k2, v2, l2 = self.peek()
                if k2 in ("eq",):
                    op = v2
                    self.pop()
                    k3, v3, l3 = self.peek()
                    if k3 == "open":
                        self.pop()
                        node.kind = "block"
                        node.children = self._stmts("close", depth + 1)
                        if self.peek()[0] == "close":
                            self.pop()
                            node.end_line = self.toks[self.i - 1][2]
                        else:
                            self.errors.append("missing } for block at line %d (%s)" % (ln, v))
                            node.end_line = ln
                    elif k3 in ("lit", "str"):
                        self.pop()
                        node.kind = "scalar"
                        node.val = (op + v3) if op in ("<", ">") else v3
                        node.end_line = l3
                        # modifier-then-block: key = rgb { ... }
                        if self.peek()[0] == "open":
                            self.pop()
                            node.kind = "block"
                            node.children = self._stmts("close", depth + 1)
                            if self.peek()[0] == "close":
                                self.pop()
                                node.end_line = self.toks[self.i - 1][2]
                    else:
                        node.kind = "scalar"
                        node.val = ""
                node.pre_comment = " | ".join(pending)
                pending = []
                out.append(node)
                last_end = node.end_line
            elif k == "eq":
                pass  # stray comparator: skip
        return out


def parse_file(path):
    p = Parser(open(path, encoding="utf-8", errors="replace").read())
    nodes = p.parse()
    if p.errors:
        fail("%s: %s" % (path, "; ".join(p.errors[:3])))
    for wmsg in p.warnings:
        print("WARN %s: %s" % (os.path.basename(path), wmsg))
    return nodes


# ------------------------------------------------------------- extraction
STATE_KEYS = {
    "state", "owns_state", "controls_state", "has_full_control_of_state",
    "transfer_state", "transfer_state_to", "add_state_claim",
    "remove_state_claim", "add_state_core", "remove_state_core",
    "set_state_owner", "set_state_controller", "add_state_modifier",
    "add_state_research_slot",
    # state-valued args beneath set_capital / capital_scope /
    # has_resources_rights / add_resource blocks are covered by `state`.
    "set_capital", "capital_scope", "has_resources_rights", "add_resource",
}
LIST_KEYS = {"states", "states_filter"}
# prioritize members are states only beneath *_state quantifier scopes
PRIORITIZE_PARENT = re.compile(
    r"^(?:every|random|ordered|any|count|for_each)_\w*state\w*$")
# non-state scalar/block namespaces (defence in depth; none are STATE_KEYS)
EXCLUDE_KEYS = {"province", "path", "region", "strategic_region",
                "victory_points", "id"}
# numeric-scope traps
RANDOM_WEIGHT_PARENTS = {"random", "random_list"}
MODIFIER_FIRST_KEYS = {"modifier", "factor", "add_attack", "add_defense",
                       "add_logistics", "add_planning"}
# logical/container wrappers: list context propagates through these
WRAPPERS = {"NOT", "AND", "OR", "if", "else", "else_if", "hidden_effect",
            "limit", "trigger", "effect", "modifier", "count_triggered_scope",
            "tooltip", "custom_effect_tooltip", "custom_trigger_tooltip",
            "option", "ai_chance", "count", "count_no_scopes"}
QUANTIFIER = re.compile(
    r"^(?:every|random|ordered|any|count|first|last|for_each|while|if)_")
FOCUS_NODE_KEYS = {"focus", "shared_focus", "joint_focus"}
CHAIN_RE = re.compile(r"^(\d+)\.[A-Za-z_]")
INT_RE = re.compile(r"^-?\d+$")

refs = []          # list of dict rows (unresolved)


class Ctx:
    __slots__ = ("side", "file", "tree", "focus", "path", "quant", "list_ctx",
                 "list_node")

    def __init__(self, side, file_):
        self.side = side
        self.file = file_
        self.tree = ""
        self.focus = ""
        self.path = []      # ancestor keys inside the focus node
        self.quant = "direct"
        self.list_ctx = None
        self.list_node = None    # enclosing list block node, for occurrence sets

    def child(self, node):
        c = Ctx(self.side, self.file)
        c.tree, c.focus = self.tree, self.focus
        c.path = self.path
        c.quant = self.quant
        c.list_ctx = self.list_ctx
        c.list_node = self.list_node
        if node.key == "focus_tree":
            c.path = []
            c.focus = ""
            c.tree = _child_id(node) or ""
            return c, True   # hard boundary: list_ctx/quant reset below
        if node.key in FOCUS_NODE_KEYS:
            c.path = []
            c.focus = _child_id(node) or ""
            return c, True
        c.path = self.path + [node.key]
        if QUANTIFIER.match(node.key) or node.key in ("random", "every", "random_list"):
            c.quant = node.key
        if node.key in LIST_KEYS or node.key == "prioritize":
            c.list_ctx = node.key
            c.list_node = node
        elif node.key not in WRAPPERS:
            c.list_ctx = None
            c.list_node = None
        return c, False


def _child_id(block):
    for ch in block.children or []:
        if ch.kind == "scalar" and ch.key == "id":
            return ch.val
    return ""


def _first_key(children):
    for ch in children or []:
        if ch.kind in ("scalar", "block"):
            return ch.key
    return ""


def _comment(node, parent):
    parts = []
    if node.pre_comment:
        parts.append(node.pre_comment)
    if node.post_comment:
        parts.append(node.post_comment)
    # list blocks: a comment trailing the closing brace documents the members
    elif parent is not None and parent.post_comment and node.end_line == parent.end_line:
        parts.append(parent.post_comment)
    return " | ".join(p for p in parts if p)


def emit(node, ctx, construct, literal, parent=None, raw=None):
    refs.append({
        "side": ctx.side, "file": ctx.file, "line": node.line,
        "focus_id": ctx.focus, "tree_id": ctx.tree,
        "block_path": ".".join(ctx.path),
        "construct": construct, "key": node.key,
        "raw_value": raw if raw is not None else literal, "literal": int(literal),
        "quantifier": ctx.quant,
        "comment": _comment(node, parent),
        "occ_key": (ctx.file, id(ctx.list_node)) if ctx.list_node else None,
    })


def walk(node, ctx, parent=None):
    key = node.key
    if node.kind == "scalar":
        if node.val is not None and CHAIN_RE.match(node.val):
            if key not in EXCLUDE_KEYS:
                emit(node, ctx, "scope_chain", CHAIN_RE.match(node.val).group(1),
                     parent, raw=node.val)
            return
        if node.val is not None and INT_RE.match(node.val):
            if key in STATE_KEYS or (ctx.list_ctx and key.lstrip("-").isdigit()):
                construct = ctx.list_ctx if ctx.list_ctx else "scalar_key"
                emit(node, ctx, construct, node.val, parent)
        return
    if node.kind == "bare":
        if ctx.list_ctx and INT_RE.match(key):
            emit(node, ctx, ctx.list_ctx, key, parent)
        return
    # block
    if key == "prioritize":
        if not PRIORITIZE_PARENT.match(ctx.path[-1] if ctx.path else ""):
            return  # prioritize outside *_state scopes is not a state list
    if key.lstrip("-").isdigit():
        first = _first_key(node.children)
        if first not in MODIFIER_FIRST_KEYS and not (
                ctx.path and ctx.path[-1] in RANDOM_WEIGHT_PARENTS):
            emit(node, ctx, ctx.list_ctx or "state_scope", key, parent)
    child, boundary = ctx.child(node)
    if boundary:
        child.list_node = None
        child.list_ctx = None
    for ch in node.children or []:
        walk(ch, child, node)


def collect(rootdir, side):
    d = os.path.join(rootdir, "common", "national_focus")
    if not os.path.isdir(d):
        fail("missing focus dir %s" % d)
    files = []
    for f in sorted(glob.glob(os.path.join(d, "*.txt"))):
        base = os.path.basename(f)
        nodes = parse_file(f)
        ctx = Ctx(side, base)
        for n in nodes:
            walk(n, ctx)
        files.append(base)
    return files


vanilla_files = collect(HOI4, "vanilla")
mod_files = collect(MOD, "mod")

# ------------------------------------------------------------- resolution
mod_literals = set()                 # live ME ids referenced by the mod corpus
per_member_mod_refs = defaultdict(int)
for r in refs:
    if r["side"] == "mod" and r["literal"] in live_me:
        mod_literals.add(r["literal"])
        per_member_mod_refs[r["literal"]] += 1


def _cov_label(cell, cur):
    if not cell:
        return "offmap"
    if len(cur) == len(cell):
        return "full"
    if not cur:
        return "none"
    if len(cur) == 1:
        return "single"
    return "partial"


def coverage(vid):
    """Corpus-union coverage: which ordered cell members appear anywhere in
    the mod corpus (used by vanilla rows and the map table)."""
    mem = members_ordered.get(vid, [])
    cur = [x for x in mem if x in mod_literals]
    missing = [x for x in mem if x not in mod_literals]
    return _cov_label(mem, cur), "|".join(str(x) for x in missing)


def comment_vids(text):
    """All vanilla ids cited by a `was vanilla` comment, e.g.
    `# was vanilla 42/51/978` -> [42, 51, 978]."""
    m = re.search(r"was vanilla\s+([0-9]+(?:[\s,/]+[0-9]+)*)",
                  text, re.IGNORECASE)
    if not m:
        return []
    return list(dict.fromkeys(int(x) for x in re.findall(r"\d+", m.group(1))))


# per-occurrence literal sets: refs emitted inside one enclosing list block
# (states = { ... } / prioritize / states_filter) share the block's members.
occ_literals = defaultdict(set)          # (file, list node id) -> literal set
for r in refs:
    if r["occ_key"] is not None:
        occ_literals[r["occ_key"]].add(r["literal"])


rows = []
collision_map = defaultdict(int)     # vid -> real mod collision row count
collision_claims = defaultdict(set)  # vid -> vanilla ids claimed by those rows
for r in sorted(refs, key=lambda r: (0 if r["side"] == "vanilla" else 1,
                                     r["file"], r["line"], r["raw_value"],
                                     r["construct"], r["block_path"])):
    lit = r["literal"]
    side = r["side"]
    collision = ""
    edge_anc = []
    comment_set = comment_vids(r["comment"])
    comment_vid = "|".join(str(x) for x in comment_set)
    if side == "vanilla":
        # a vanilla literal lives in the vanilla namespace: sharing a number
        # with a live ME state is informational (literal_is_live_me), never a
        # collision.
        vid = lit if lit in live_vanilla else ""
        if vid == "":
            resolution = "unknown_vanilla_state"
        elif comment_set and vid not in comment_set:
            resolution = "comment_mismatch"
        else:
            resolution = "ok"
    else:
        if lit in live_me:
            vid = me_ancestor[lit]
            edge_anc = edge_ancestors_of(lit)
            if comment_set and vid not in comment_set:
                if set(comment_set) & set(edge_anc):
                    # the comment cites an asserted-scope ancestor from the
                    # relation ledger (multi-id groups, cross-state comment
                    # edges) — a real relation, not a collision. Observation
                    # edges are excluded: matching a raster/candidate
                    # heuristic alone does not suppress the collision.
                    resolution = "ok|comment_secondary_edge"
                else:
                    # the occurrence is explicitly attributed to a vanilla
                    # id set disjoint from every asserted ancestor.
                    collision = "live_me_state"
                    collision_map[vid] += 1
                    collision_claims[vid].update(comment_set)
                    resolution = "ok|comment_mismatch"
            else:
                resolution = "ok"
            if not collision and lit in live_vanilla and vid != lit:
                # the number is also a vanilla id for a different state,
                # but nothing attributes this occurrence to it.
                resolution += "|namespace_overlap"
        elif lit in live_vanilla:
            vid = lit
            resolution = "stale_vanilla_id"
            if comment_set and vid not in comment_set:
                resolution += "|comment_mismatch"
        else:
            vid = ""
            resolution = "unknown_literal"
    if vid != "" and vid in members_ordered:
        cell = members_ordered[vid]
        mcnt = len(cell)
        member_str = "|".join(str(x) for x in cell)
        if side == "mod":
            # per-occurrence scope: only the literals emitted by the same
            # enclosing list block (a scalar occurrence is just itself).
            occ = occ_literals.get(r["occ_key"], {lit})
            cur = [x for x in cell if x in occ]
            cov = _cov_label(cell, cur)
            missing = "|".join(str(x) for x in cell if x not in occ)
            coverage_scope = "occurrence"
        else:
            cur = [x for x in cell if x in mod_literals]
            cov, missing = coverage(vid)
            coverage_scope = "corpus_union"
    else:
        cur, cov, missing, mcnt, member_str = [], "", "", "", ""
        coverage_scope = ""
    comment = r["comment"]
    if resolution.startswith("stale_vanilla_id"):
        comment = ("stale literal: not a live ME state"
                   + (" | " + comment if comment else ""))
    elif resolution == "unknown_vanilla_state" or resolution == "unknown_literal":
        comment = ("unknown %s id" % ("vanilla" if side == "vanilla" else "mod")
                   + (" | " + comment if comment else ""))
    rows.append({
        "side": side, "file": r["file"], "line": r["line"],
        "focus_id": r["focus_id"], "tree_id": r["tree_id"],
        "block_path": r["block_path"], "construct": r["construct"],
        "key": r["key"], "raw_value": r["raw_value"],
        "vanilla_id": vid, "quantifier": r["quantifier"],
        "literal": lit, "literal_is_live_me": 1 if lit in live_me else 0,
        "resolution": resolution,
        "current_me_refs": "|".join(str(x) for x in cur),
        "me_member_count": mcnt, "me_members_ordered": member_str,
        "covers": cov, "missing_members": missing,
        "coverage_scope": coverage_scope,
        "collision_live_me": collision,
        "collision_me_ancestor": me_ancestor[lit] if collision else "",
        "comment_vanilla_id": comment_vid,
        "edge_ancestors": ("|".join(str(x) for x in edge_anc)
                           if EDGES is not None else ""),
        "observed_ancestors": ("|".join(str(x) for x in
                                       observed_ancestors_of(lit))
                               if EDGES is not None else ""),
        "migration_comment": comment,
    })

REFS_COLS = ["side", "file", "line", "focus_id", "tree_id", "block_path",
             "construct", "key", "raw_value", "vanilla_id", "quantifier",
             "literal", "literal_is_live_me", "resolution", "current_me_refs",
             "me_member_count", "me_members_ordered", "covers",
             "missing_members", "coverage_scope",
             "collision_live_me", "collision_me_ancestor",
             "comment_vanilla_id", "edge_ancestors", "observed_ancestors",
             "migration_comment"]

# ------------------------------------------------------------- map table
v_refs = defaultdict(list)
m_files = defaultdict(set)
m_ref_count = defaultdict(int)
for row in rows:
    if row["vanilla_id"] == "":
        continue
    vid = row["vanilla_id"]
    if row["side"] == "vanilla":
        v_refs[vid].append(row)
    else:
        m_files[vid].add(row["file"])
        m_ref_count[vid] += 1

mod_basenames = set(mod_files)
map_rows = []
for vid in sorted(set(v_refs) | set(collision_map)):
    r = v2m_rows[vid]
    mem = members_ordered[vid]
    cov, missing = coverage(vid)
    files = sorted({x["file"] for x in v_refs[vid]})
    constructs = sorted({x["construct"] for x in v_refs[vid]})
    quants = sorted({x["quantifier"] for x in v_refs[vid]})
    shadowed = sorted(set(files) & mod_basenames)
    notes = []
    if shadowed:
        notes.append("shadowed_by_mod:" + "|".join(shadowed))
    if missing:
        notes.append("missing_members:" + missing)
    map_rows.append({
        "vanilla_id": vid,
        "vanilla_name": r["vanilla_name"],
        "owner": r["owner"],
        "region": r["region"],
        "is_europe": IS_EUROPE.get(vid, ""),
        "me_primary": r["me_primary"],
        "me_members_ordered": "|".join(str(x) for x in mem),
        "me_member_count": len(mem),
        "mapping_basis": r["basis"],
        "mapping_status": r["status"],
        "off_map": 1 if not mem else 0,
        "focus_files": "|".join(files),
        "focus_ref_count": len(v_refs[vid]),
        "ref_constructs": "|".join(constructs),
        "ref_quantifiers": "|".join(quants),
        "mod_covered_members": "|".join(str(x) for x in mem if x in mod_literals),
        "mod_coverage": cov,
        "mod_coverage_scope": "corpus_union",
        "mod_ref_count": m_ref_count.get(vid, 0),
        "mod_files": "|".join(sorted(m_files.get(vid, ()))),
        "collision_live_me": collision_map.get(vid, 0),
        "collision_me_state": 1 if vid in collision_map else 0,
        "collision_comment_claims":
            "|".join(str(x) for x in sorted(collision_claims.get(vid, ()))),
        "edge_members": "|".join(str(x) for x in edge_members_of(vid)),
        "observed_members": "|".join(str(x) for x in observed_members_of(vid)),
        "notes": " | ".join(notes),
    })

MAP_COLS = ["vanilla_id", "vanilla_name", "owner", "region", "is_europe",
            "me_primary", "me_members_ordered", "me_member_count",
            "mapping_basis", "mapping_status", "off_map", "focus_files",
            "focus_ref_count", "ref_constructs", "ref_quantifiers",
            "mod_covered_members", "mod_coverage", "mod_coverage_scope",
            "mod_ref_count",
            "mod_files", "collision_live_me", "collision_me_state",
            "collision_comment_claims", "edge_members", "observed_members",
            "notes"]

os.makedirs(OUT, exist_ok=True)
with open(os.path.join(OUT, "focus_state_refs.tsv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=REFS_COLS, delimiter="\t")
    w.writeheader()
    w.writerows(rows)
with open(os.path.join(OUT, "focus_state_map.tsv"), "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=MAP_COLS, delimiter="\t")
    w.writeheader()
    w.writerows(map_rows)

from collections import Counter  # noqa: E402

vc = [r for r in rows if r["side"] == "vanilla"]
mc = [r for r in rows if r["side"] == "mod"]
print("refs: vanilla %d (%d files) | mod %d (%d files)"
      % (len(vc), len(vanilla_files), len(mc), len(mod_files)))
print("constructs:", dict(Counter(r["construct"] for r in rows)))
print("coverage:", dict(Counter(r["covers"] for r in rows if r["covers"])))
print("collisions:", dict(Counter(r["collision_live_me"] for r in rows if r["collision_live_me"])))
print("resolutions:", dict(Counter(r["resolution"] for r in rows)))
print("map rows: %d | off_map %d | collision_me_state %d"
      % (len(map_rows),
         sum(1 for r in map_rows if r["off_map"]),
         sum(1 for r in map_rows if r["collision_me_state"])))
print("written: docs/states/{focus_state_refs,focus_state_map}.tsv")
