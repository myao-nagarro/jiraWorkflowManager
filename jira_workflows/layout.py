"""Layered (top-down) layout of a workflow graph: Start, then To Do, In Progress and Done statuses. Pure Python, no Qt."""
from collections import defaultdict, deque
from dataclasses import dataclass, field

START, ANY = "__start__", "__any__"
ROW_MAX = 4  # most statuses side by side in one row


@dataclass
class Node:
    id: str
    kind: str                 # status | start | any
    label: str
    sub: str = ""
    category: str = "TODO"
    layer: int = 0
    row: int = 0


@dataclass
class Edge:
    tid: str
    name: str
    src: str
    dst: str
    type: str                 # initial | global | directed
    back: bool = False
    also: list = field(default_factory=list)   # transitions running the opposite way, drawn on the same double-headed line


@dataclass
class Graph:
    nodes: dict = field(default_factory=dict)
    edges: list = field(default_factory=list)
    layer_sizes: dict = field(default_factory=dict)
    global_in: dict = field(default_factory=dict)   # status id -> global transitions ending there (drawn as tags)


def build_graph(wf: dict, merge: bool = True) -> Graph:
    g = Graph()
    status_names = {s["id"]: s for s in wf["statuses"]}

    def need(sid):
        if sid not in g.nodes:
            s = status_names.get(sid, {})
            g.nodes[sid] = Node(sid, "status", s.get("name", sid), f"ID {sid}", s.get("category", "TODO"))

    for s in wf["statuses"]:
        need(s["id"])
    for t in wf["transitions"]:
        need(t["to"])
        sources = t["from"]
        if not sources:
            sources = [START] if t["type"] == "initial" else [ANY]
        for src in sources:
            if src == START and START not in g.nodes:
                g.nodes[START] = Node(START, "start", "Start")
            elif src == ANY:
                g.global_in.setdefault(t["to"], []).append(Edge(t["id"], t["name"], ANY, t["to"], t["type"]))
                continue
            if src not in (START, ANY):
                need(src)
            g.edges.append(Edge(t["id"], t["name"], src, t["to"], t["type"]))
    if merge:
        _merge_opposites(g)
    _layout(g)
    return g


def _merge_opposites(g: Graph):
    """A -> B and B -> A become one double-headed line carrying both transitions."""
    first, kept = {}, []
    for e in g.edges:
        rev = first.get((e.dst, e.src))
        if rev is not None and e.src != e.dst and START not in (e.src, e.dst):
            rev.also.append(e)
            continue
        first.setdefault((e.src, e.dst), e)
        kept.append(e)
    g.edges = kept


def _layout(g: Graph):
    nodes, edges = g.nodes, g.edges
    out = defaultdict(list)
    has_in = set()
    for e in edges:
        if e.src != e.dst:
            out[e.src].append(e)
            has_in.add(e.dst)

    # depth-first walk from Start, then from statuses nobody enters; edges closing a cycle are "back" edges
    state, post, discovered = {}, [], []

    def visit(u):
        state[u] = 1
        discovered.append(u)
        for e in out[u]:
            if state.get(e.dst) == 1:
                e.back = True
            elif e.dst not in state:
                visit(e.dst)
        state[u] = 2
        post.append(u)

    statuses = [n for n in nodes if n != START]
    for root in ([START] if START in nodes else []) + [n for n in statuses if n not in has_in] + statuses:
        if root not in state:
            visit(root)

    # rows: Start, then the To Do, In Progress and Done statuses in that order. Inside a category the
    # statuses are ordered by distance from Start and wrapped so that no row gets too wide.
    starts = [START] if START in nodes else ([n for n in statuses if n not in has_in] or statuses[:1])
    dist = {n: 0 for n in starts}
    queue = deque(starts)
    while queue:
        u = queue.popleft()
        for e in out[u]:
            if e.dst not in dist:
                dist[e.dst] = dist[u] + 1
                queue.append(e.dst)
    nxt = 0
    if START in nodes:
        nodes[START].layer, nxt = 0, 1
    for cat in ("TODO", "IN_PROGRESS", "DONE"):
        members = [n for n in statuses if nodes[n].category == cat]
        for d in sorted({dist.get(n, 10 ** 6) for n in members}):
            row = [n for n in members if dist.get(n, 10 ** 6) == d]
            for i in range(0, len(row), ROW_MAX):
                for n in row[i:i + ROW_MAX]:
                    nodes[n].layer = nxt
                nxt += 1

    layers = defaultdict(list)
    for n in ([START] if START in nodes else []) + [n for n in discovered if n != START]:
        layers[nodes[n].layer].append(n)
    adj = defaultdict(set)
    for e in edges:
        adj[e.src].add(e.dst)
        adj[e.dst].add(e.src)
    pos = {n: i for L in layers.values() for i, n in enumerate(L)}
    top = max(layers)
    for sweep in range(6):
        rng = range(1, top + 1) if sweep % 2 == 0 else range(top - 1, -1, -1)
        ref = -1 if sweep % 2 == 0 else 1
        for L in rng:
            def bary(n, L=L, ref=ref):   # the layer and the direction are bound now, not when the sort calls it
                near = [pos[m] for m in adj[n] if nodes[m].layer == L + ref]
                return sum(near) / len(near) if near else pos[n]
            layers[L].sort(key=bary)
            pos.update({n: i for i, n in enumerate(layers[L])})
    for L, members in layers.items():
        g.layer_sizes[L] = len(members)
        for i, n in enumerate(members):
            nodes[n].row = i
