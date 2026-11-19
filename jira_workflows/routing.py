"""Placement of the statuses and routing of the transitions. Pure Python, no Qt.

Statuses sit in rows (one row per layer) and are nudged sideways so connected boxes line up. Every transition
is then routed on its own: it may leave and enter a box on any of its four sides, and the router picks the
route with the fewest turns that does not run through another box, preferring straight lines, then single
turns (L), then two turns (Z / C). Routes that cross or overlap earlier ones cost extra.
"""
import math
from collections import defaultdict

GAP_X, MIN_PORT_GAP, CLEAR, KEEP_AWAY = 110, 9, 8, 28   # CLEAR: never closer; KEEP_AWAY: preferred distance to other boxes
DIRS = {"top": (0, -1), "bottom": (0, 1), "left": (-1, 0), "right": (1, 0)}


def route(g, size: dict, label_size=None, gap_x=GAP_X, tags=None, row_boost=1.0, refine=True, wide=True, positions=None, forced=None, turn_labels=True):
    """size: node id -> (width, height). Returns (rects, routes, bad); `bad` counts the labels that could not be kept clear of
    other lines, boxes and labels (the caller can then ask for more room: wider gaps, `row_boost` > 1 for taller rows).

    rects:  node id -> (x, y, w, h);  routes: list of (edge, [(x, y), ...], (label x, label y)).
    """
    nodes = g.nodes
    layers = defaultdict(list)
    for n in nodes.values():
        layers[n.layer].append(n)
    last = max(layers)
    for members in layers.values():
        members.sort(key=lambda n: n.row)
    cx = {}
    for members in layers.values():
        total = sum(size[n.id][0] for n in members) + gap_x * (len(members) - 1)
        x = -total / 2
        for n in members:
            cx[n.id] = x + size[n.id][0] / 2
            x += size[n.id][0] + gap_x
    _align(g, layers, size, cx, gap_x)

    # ---- vertical placement: rows are spaced by how many transitions have to pass between them
    cross = defaultdict(int)
    for e in g.edges:
        a, b = sorted((nodes[e.src].layer, nodes[e.dst].layer))
        for k in range(a, b):
            cross[k] += 1
    layer_h = {l: max(size[n.id][1] for n in m) for l, m in layers.items()}
    sparse = len(g.edges) / max(1, len(nodes)) < 2   # few transitions per box: rows can sit closer together
    y, y_top = 44, {}
    for l in range(last + 1):
        y_top[l] = y
        y += layer_h[l] + ((70 if sparse else 96) + (7 if sparse else 9) * min(cross[l], 14)) * row_boost
    rects = {}
    for n in nodes.values():
        w, h = size[n.id]
        rects[n.id] = (cx[n.id] - w / 2, y_top[n.layer] + (layer_h[n.layer] - h) / 2, w, h)

    if positions:   # fixed places (the Jira editor's): the boxes are centred on them
        rects = {n.id: (positions[n.id][0] - size[n.id][0] / 2, positions[n.id][1] - size[n.id][1] / 2, *size[n.id]) for n in nodes.values()}
    routes = _Router(g, rects, refine, wide, forced).run()

    # labels last, so each can dodge the boxes and the labels placed before it
    placed = list(rects.values())
    for dst, pills in (tags or {}).items():  # the "Any" badge and its pills sit above the top-right corner of the box
        x, y, w, _ = rects[dst]
        right, cy_ = x + w - 30 - 23 - 8, y - 30
        for k, (pw, ph) in enumerate(pills):
            placed.append((right - pw, cy_ - k * (ph + 4) - ph / 2, pw, ph))
        placed.append((x + w - 30 - 23, cy_ - 11, 46, 22))
    out, bad = [], 0
    for k, (e, pts) in enumerate(routes):
        lw, lh = label_size[k] if label_size else (120, 20)
        others = [seg for j, (_, p2) in enumerate(routes) if j != k for seg in zip(p2, p2[1:])]   # every other line
        pos, turned, cost = _label_pos(pts, lw, lh, placed, others, turn_labels)
        bad += cost >= 5000
        placed.append(label_box(pos, lw, lh, turned))
        out.append((e, pts, pos, turned))
    return rects, out, bad


def _align(g, layers, size, cx, gap_x):
    """Line the statuses up: every box is centred under / over the boxes it connects to (the median of them), so a chain
    of statuses shares one centre line and its transitions can be straight. Boxes in a row never overlap."""
    nodes = g.nodes
    nbrs = defaultdict(list)
    for e in g.edges:
        if nodes[e.src].layer != nodes[e.dst].layer:
            nbrs[e.src].append(e.dst)
            nbrs[e.dst].append(e.src)
    top = max(layers)
    for sweep in range(10):
        order = range(1, top + 1) if sweep % 2 == 0 else range(top - 1, -1, -1)
        ref = -1 if sweep % 2 == 0 else 1
        for L in order:
            members = layers[L]
            want = []
            for n in members:
                # weighted median of every connected box: the ones in the neighbouring row count three times, the others once, so a
                # status joined to boxes several rows away (for example by a "cancel" transition) is still drawn next to them
                near = sorted((cx[m], 3 if nodes[m].layer == L + ref else 1) for m in nbrs[n.id])
                if not near:
                    want.append(cx[n.id])
                    continue
                half, run = sum(wt for _, wt in near) / 2, 0
                for i, (x, wt) in enumerate(near):
                    run += wt
                    if run > half:
                        want.append(x); break
                    if run == half:
                        want.append((x + near[i + 1][0]) / 2); break
            for n, x in zip(members, _place(members, want, size, gap_x)):
                cx[n.id] = x


def _place(members, want, size, gap):
    """Positions as close as possible to `want` (in order, no overlaps): one box stays exactly where it wants to be and the
    others are pushed outwards from it. The box that keeps its spot is the one giving the smallest total displacement."""
    n = len(members)
    w = [size[m.id][0] for m in members]
    best = None
    for p in range(n):
        xs = [0.0] * n
        xs[p] = want[p]
        for i in range(p - 1, -1, -1):
            xs[i] = min(want[i], xs[i + 1] - w[i + 1] / 2 - gap - w[i] / 2)
        for i in range(p + 1, n):
            xs[i] = max(want[i], xs[i - 1] + w[i - 1] / 2 + gap + w[i] / 2)
        cost = sum(abs(x - wx) for x, wx in zip(xs, want))
        if best is None or cost < best[0] - 1e-6:  # pylint: disable=unsubscriptable-object
            best = (cost, xs)
    return best[1]


# ---------------------------------------------------------------- geometry helpers
def _hits(p, q, rect, pad=CLEAR):
    """Does the axis-parallel segment p-q cross the rectangle (grown by pad)?"""
    x, y, w, h = rect
    l, t, r, b = x - pad, y - pad, x + w + pad, y + h + pad
    x0, x1 = sorted((p[0], q[0]))
    y0, y1 = sorted((p[1], q[1]))
    return x0 < r and x1 > l and y0 < b and y1 > t


C_SIDE, C_TOP = (40, 74, 108, 142, 176), (30, 58, 86, 114)   # distances of the outer lane of a C-shaped route
CROSS_COST = 45   # a crossing costs about two thirds of an extra turn
PARALLEL_SEP = 16   # parallel transition lines that run side by side keep at least this distance


def _score_vs(segs, placed):
    """(crossings, overlap, close) of a candidate polyline's segments against already routed ones: lines that cross, lines on
    top of each other, and parallel lines that run side by side closer than PARALLEL_SEP."""
    cross = overlap = close = 0
    for p, q in segs:
        for a, b in placed:
            if abs(p[0] - q[0]) < 0.01 and abs(a[0] - b[0]) < 0.01:      # both vertical
                run = min(max(p[1], q[1]), max(a[1], b[1])) - max(min(p[1], q[1]), min(a[1], b[1]))
                d = abs(p[0] - a[0])
                if d < 4 < run:
                    overlap += 1
                elif d < PARALLEL_SEP and run > 6:
                    close += 1
            elif abs(p[1] - q[1]) < 0.01 and abs(a[1] - b[1]) < 0.01:    # both horizontal
                run = min(max(p[0], q[0]), max(a[0], b[0])) - max(min(p[0], q[0]), min(a[0], b[0]))
                d = abs(p[1] - a[1])
                if d < 4 < run:
                    overlap += 1
                elif d < PARALLEL_SEP and run > 6:
                    close += 1
            else:
                v, h = ((p, q), (a, b)) if abs(p[0] - q[0]) < 0.01 else ((a, b), (p, q))
                if (min(h[0][0], h[1][0]) < v[0][0] < max(h[0][0], h[1][0])
                        and min(v[0][1], v[1][1]) < h[0][1] < max(v[0][1], v[1][1])):
                    cross += 1
    return cross, overlap, close


class _Router:
    def __init__(self, g, rects, refine=True, wide=True, forced=None):
        self.g, self.rects, self.refine = g, rects, refine
        self.mirror = {}   # (source, target) -> (points, source port, target port, forced ports) of the lines routed with Jira's ports
        self.forced, self._cur = forced or {}, None   # (transition, source, target) -> the ports Jira used: ((side, share) | None, (side, share) | None)
        self.c_side, self.c_top = (C_SIDE, C_TOP) if wide else (C_SIDE[:2], C_TOP[:2])   # outer lanes of C-shaped routes
        self.used = defaultdict(list)      # (node, side) -> port offsets already taken
        self.placed = []                   # routed segments
        xs = [r[0] for r in rects.values()] + [r[0] + r[2] for r in rects.values()]
        self.min_x, self.max_x = min(xs), max(xs)
        self.lanes = {"L": 0, "R": 0}
        self.loops = defaultdict(int)

    # -- ports ------------------------------------------------------
    def free(self, nid, side, off):
        return all(abs(off - u) >= MIN_PORT_GAP for u in self.used[(nid, side)])

    def choices(self, nid, side, which):
        """The port offsets to try on a side: only Jira's own port when the line has one, else the next free one."""
        if self._cur and self._cur[which]:
            half = (self.rects[nid][2] if side in ("top", "bottom") else self.rects[nid][3]) / 2 - 9
            return [self._cur[which][1] * half]
        return self.slots(nid, side, 1)

    def fits(self, nid, side, off, which):
        """May a straight line use this offset? Jira's port, or the centre when the side is still free."""
        if self._cur and self._cur[which]:
            half = (self.rects[nid][2] if side in ("top", "bottom") else self.rects[nid][3]) / 2 - 9
            return abs(off - self._cur[which][1] * half) < 1
        return self.centred(nid, side, off)

    def centred(self, nid, side, off):
        """A port that is free, and the first line on a side leaves or enters at the centre of that side."""
        return self.free(nid, side, off) and (bool(self.used[(nid, side)]) or abs(off) < 1)

    def slots(self, nid, side, count):
        """The next free port(s) on a side, in order: the middle, then half way to either end, then ever finer."""
        _, _, w, h = self.rects[nid]
        half = (w if side in ("top", "bottom") else h) / 2 - 9
        out = []
        for off in [0.0] + [sgn * f * half for f in (0.5, 0.75, 0.25, 0.875, 0.625, 0.375, 0.125) for sgn in (1, -1)]:
            if self.free(nid, side, off):
                out.append(off)
                if len(out) == count:
                    break
        return out

    def point(self, nid, side, off):
        x, y, w, h = self.rects[nid]
        cx, cy = x + w / 2, y + h / 2
        return {"top": (cx + off, y), "bottom": (cx + off, y + h), "left": (x, cy + off), "right": (x + w, cy + off)}[side]

    # -- candidates -------------------------------------------------
    def candidates(self, a, b):
        """Yield (points, (side a, offset a), (side b, offset b)) for routes with at most two turns."""
        A, B = self.rects[a], self.rects[b]
        acx, bcx = A[0] + A[2] / 2, B[0] + B[2] / 2
        acy, bcy = A[1] + A[3] / 2, B[1] + B[3] / 2
        for sa, da in DIRS.items():
            for sb, nb in DIRS.items():
                if self._cur and ((self._cur[0] and sa != self._cur[0][0]) or (self._cur[1] and sb != self._cur[1][0])):
                    continue
                arrive = (-nb[0], -nb[1])
                va, vb = sa in ("top", "bottom"), sb in ("top", "bottom")
                if va and vb:
                    ya, yb = self.point(a, sa, 0)[1], self.point(b, sb, 0)[1]
                    if da == arrive:                                   # leave and arrive going the same way
                        if (yb - ya) * da[1] < 14:
                            continue
                        lo, hi = max(A[0], B[0]) + 9, min(A[0] + A[2], B[0] + B[2]) - 9
                        for x in dict.fromkeys([acx, bcx, (lo + hi) / 2]):   # a straight line fits where the boxes overlap
                            if lo <= x <= hi and self.fits(a, sa, x - acx, 0) and self.fits(b, sb, x - bcx, 1):
                                yield [(x, ya), (x, yb)], (sa, x - acx), (sb, x - bcx)
                        mids = [(ya + yb) / 2, ya + 36 * da[1], yb - 36 * da[1], ya + 64 * da[1], yb - 64 * da[1]]
                        mids += [mids[0] + k * PARALLEL_SEP for k in (-3, -2, -1, 1, 2, 3)]   # alternative channels, so parallel runs can keep apart
                        for oa in self.choices(a, sa, 0):
                            for ob in self.choices(b, sb, 1):
                                xa, xb = acx + oa, bcx + ob
                                if abs(xa - xb) < 1:                   # ports line up: a straight line
                                    yield [(xa, ya), (xa, yb)], (sa, oa), (sb, ob)
                                    continue
                                for m in mids:
                                    if (m - ya) * da[1] > 8 and (yb - m) * da[1] > 8:
                                        yield [(xa, ya), (xa, m), (xb, m), (xb, yb)], (sa, oa), (sb, ob)
                    elif sa == sb:                                     # both on the same side: go round (C shape)
                        for d in self.c_top:
                            m = (max(ya, yb) + d) if da[1] > 0 else (min(ya, yb) - d)
                            for oa in self.choices(a, sa, 0):
                                for ob in self.choices(b, sb, 1):
                                    yield [(acx + oa, ya), (acx + oa, m), (bcx + ob, m), (bcx + ob, yb)], (sa, oa), (sb, ob)
                elif not va and not vb:
                    xa, xb = self.point(a, sa, 0)[0], self.point(b, sb, 0)[0]
                    if da == arrive:
                        if (xb - xa) * da[0] < 14:
                            continue
                        lo, hi = max(A[1], B[1]) + 9, min(A[1] + A[3], B[1] + B[3]) - 9
                        for y in dict.fromkeys([acy, bcy, (lo + hi) / 2]):
                            if lo <= y <= hi and self.fits(a, sa, y - acy, 0) and self.fits(b, sb, y - bcy, 1):
                                yield [(xa, y), (xb, y)], (sa, y - acy), (sb, y - bcy)
                        mids = [(xa + xb) / 2, xa + 40 * da[0], xb - 40 * da[0], xa + 70 * da[0], xb - 70 * da[0]]
                        mids += [mids[0] + k * PARALLEL_SEP for k in (-3, -2, -1, 1, 2, 3)]
                        for oa in self.choices(a, sa, 0):
                            for ob in self.choices(b, sb, 1):
                                ya_, yb_ = acy + oa, bcy + ob
                                if abs(ya_ - yb_) < 1:
                                    yield [(xa, ya_), (xb, ya_)], (sa, oa), (sb, ob)
                                    continue
                                for m in mids:
                                    if (m - xa) * da[0] > 8 and (xb - m) * da[0] > 8:
                                        yield [(xa, ya_), (m, ya_), (m, yb_), (xb, yb_)], (sa, oa), (sb, ob)
                    elif sa == sb:
                        for d in self.c_side:
                            m = (max(xa, xb) + d) if da[0] > 0 else (min(xa, xb) - d)
                            for oa in self.choices(a, sa, 0):
                                for ob in self.choices(b, sb, 1):
                                    yield [(xa, acy + oa), (m, acy + oa), (m, bcy + ob), (xb, bcy + ob)], (sa, oa), (sb, ob)
                elif va:                                               # leave vertically, arrive sideways: one turn
                    ya, xb = self.point(a, sa, 0)[1], self.point(b, sb, 0)[0]
                    for oa in self.choices(a, sa, 0):
                        for ob in self.choices(b, sb, 1):
                            xa, yb = acx + oa, bcy + ob
                            if (yb - ya) * da[1] > 14 and (xb - xa) * arrive[0] > 14:
                                yield [(xa, ya), (xa, yb), (xb, yb)], (sa, oa), (sb, ob)
                else:                                                  # leave sideways, arrive vertically: one turn
                    xa, yb = self.point(a, sa, 0)[0], self.point(b, sb, 0)[1]
                    for oa in self.choices(a, sa, 0):
                        for ob in self.choices(b, sb, 1):
                            ya, xb = acy + oa, bcx + ob
                            if (xb - xa) * da[0] > 14 and (yb - ya) * arrive[1] > 14:
                                yield [(xa, ya), (xb, ya), (xb, yb)], (sa, oa), (sb, ob)

    def blocked(self, pts, skip):
        """Does the route run through a box other than its own two ends? (Its own boxes may only be touched at the ports.)"""
        ends = {(pts[0], pts[1]), (pts[-2], pts[-1])}
        for seg in zip(pts, pts[1:]):
            for nid, r in self.rects.items():
                if nid in skip:
                    if seg not in ends and _hits(*seg, r, -1):
                        return True
                elif _hits(*seg, r):
                    return True
        return False

    # -- main loop --------------------------------------------------
    def run(self):
        edges = list(enumerate(self.g.edges))

        def span(item):
            a, b = self.rects[item[1].src], self.rects[item[1].dst]
            return abs(a[0] - b[0]) + abs(a[1] - b[1])

        done, self.rec = {}, {}
        for i, e in sorted((x for x in edges if x[1].src != x[1].dst), key=span):  # short links first: they get the straight lines
            done[i] = self.route_edge(e, i)
        if self.refine and not self.forced:   # Jira's ports fix the lines: a second pass would pull twin lines apart
            self.reroute(edges, done)
        for i, e in edges:
            if e.src == e.dst:
                done[i] = self.loop(e)
        return [(e, done[i]) for i, e in edges]

    def score(self, pts, sa, sb, e, placed=None):
        segs = list(zip(pts, pts[1:]))
        cross, overlap, close = _score_vs(segs, self.placed if placed is None else placed)
        near = sum(1 for nid, r in self.rects.items() if nid not in (e.src, e.dst)
                   and any(_hits(p, q, r, KEEP_AWAY) for p, q in segs))  # passes closer than KEEP_AWAY to another status
        length = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in segs)
        return (len(pts) - 2) * 70 + length * 0.05 + cross * CROSS_COST + overlap * 600 + close * 300 + near * 90 + (abs(sa[1]) + abs(sb[1])) * 0.12

    def route_edge(self, e, i=None):
        best = None
        self._cur = self.forced.get((e.tid, e.src, e.dst))
        twin = self.mirror.get((e.dst, e.src))
        if twin and self._cur and self._cur[0] and self._cur[1] and twin[3] == (self._cur[1], self._cur[0]):
            # the opposite transition left and entered through the same ports: Jira draws one line with a head at both ends
            pts = list(reversed(twin[0]))
            self.commit(i, e, pts, twin[2], twin[1])
            return _simplify(pts)
        for pts, sa, sb in self.candidates(e.src, e.dst):
            if self.blocked(pts, {e.src, e.dst}):
                continue
            score = self.score(pts, sa, sb, e)
            if best is None or score < best[0]:  # pylint: disable=unsubscriptable-object
                best = (score, pts, sa, sb)
        if best is None:
            return self.lane_route(e)
        _, pts, sa, sb = best
        self.commit(i, e, pts, sa, sb)
        if self._cur:
            self.mirror[(e.src, e.dst)] = (pts, sa, sb, self._cur)
        return _simplify(pts)

    def commit(self, i, e, pts, sa, sb):
        self.used[(e.src, sa[0])].append(sa[1])
        self.used[(e.dst, sb[0])].append(sb[1])
        self.placed += list(zip(pts, pts[1:]))
        if i is not None:
            self.rec[i] = (pts, sa, sb)

    def uncommit(self, i, e):
        pts, sa, sb = self.rec.pop(i)
        self.used[(e.src, sa[0])].remove(sa[1])
        self.used[(e.dst, sb[0])].remove(sb[1])
        for seg in zip(pts, pts[1:]):
            self.placed.remove(seg)
        return pts, sa, sb

    def reroute(self, edges, done, passes=4):
        """Rip-up and re-route: the first lines were routed before the later ones existed, so a line can end up crossing or hugging a
        line that came after it. Each line in turn is lifted off, routed again among all the others, and kept only when that
        is better than where it was."""
        for _ in range(passes):
            changed = False
            for i, e in edges:
                if i not in self.rec:
                    continue
                old = self.uncommit(i, e)
                old_score = self.score(*old[:1], old[1], old[2], e)
                best = None
                self._cur = self.forced.get((e.tid, e.src, e.dst))
                for pts, sa, sb in self.candidates(e.src, e.dst):
                    if self.blocked(pts, {e.src, e.dst}):
                        continue
                    sc = self.score(pts, sa, sb, e)
                    if best is None or sc < best[0]:  # pylint: disable=unsubscriptable-object
                        best = (sc, pts, sa, sb)
                if best and best[0] < old_score - 1:
                    self.commit(i, e, *best[1:]); done[i] = _simplify(best[1]); changed = True
                else:
                    self.commit(i, e, *old)
            if not changed:
                break

    def lane_route(self, e):
        """Last resort: up out of the source, round the outside of the diagram, down onto the target."""
        A, B = self.rects[e.src], self.rects[e.dst]
        side = "R" if (A[0] + B[0]) / 2 >= 0 else "L"
        k = self.lanes[side]; self.lanes[side] += 1
        lane = self.max_x + 56 + k * 30 if side == "R" else self.min_x - 56 - k * 30
        oa, ob = (self.slots(e.src, "top", 1) or [0.0])[0], (self.slots(e.dst, "top", 1) or [0.0])[0]
        self.used[(e.src, "top")].append(oa); self.used[(e.dst, "top")].append(ob)
        xa, xb = A[0] + A[2] / 2 + oa, B[0] + B[2] / 2 + ob
        pts = [(xa, A[1]), (xa, A[1] - 34), (lane, A[1] - 34), (lane, B[1] - 34), (xb, B[1] - 34), (xb, B[1])]
        self.placed += list(zip(pts, pts[1:]))
        return _simplify(pts)

    def loop(self, e):
        k = self.loops[e.src]; self.loops[e.src] += 1
        x, y, w, h = self.rects[e.src]
        top, xr = y - 34 - 24 * k, x + w + 30 + 14 * k
        return _simplify([(x + w * 0.7, y), (x + w * 0.7, top), (xr, top), (xr, y + h / 2), (x + w, y + h / 2)])


def _simplify(pts):
    out = []
    for p in pts:
        if out and abs(out[-1][0] - p[0]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01:
            continue
        if len(out) >= 2 and ((abs(out[-2][0] - out[-1][0]) < 0.01 and abs(out[-1][0] - p[0]) < 0.01)
                              or (abs(out[-2][1] - out[-1][1]) < 0.01 and abs(out[-1][1] - p[1]) < 0.01)):
            out[-1] = p  # collinear: extend the previous segment
            continue
        out.append(p)
    return out


def label_box(c, w, h, turned):
    """(x, y, w, h) of a label centred on c; a turned label (reading bottom to top) has the box h x w."""
    bw, bh = (h, w) if turned else (w, h)
    return (c[0] - bw / 2, c[1] - bh / 2, bw, bh)


def _label_pos(pts, w, h, placed, others=(), turn=True):
    """Centre of a label of w x h sitting ON its own line, and whether it is turned (reads bottom to top).

    Rules, in order of importance:
      1. the label never overlaps a status box or another label, and where there is room it touches no other transition line;
      2. it is horizontal whenever that fits, and turned only when a turned label fits where a horizontal one does not;
      3. it sits on a straight stretch of its own line, clear of bends and arrowheads, near the middle of the longest stretches."""
    segs = list(zip(pts, pts[1:]))
    order = sorted(range(len(segs)), key=lambda i: -(abs(segs[i][0][0] - segs[i][1][0]) + abs(segs[i][0][1] - segs[i][1][1])))
    best = None
    for rank, i in enumerate(order):
        (x0, y0), (x1, y1) = segs[i]
        seg_vertical = abs(x1 - x0) < 0.01
        length = abs(y1 - y0) if seg_vertical else abs(x1 - x0)
        if length < 0.01:
            continue
        own = [sg for j, sg in enumerate(segs) if j != i]
        for turned in ((False, True) if turn else (False,)):   # a label is turned only when that is allowed
            bw, bh = (h, w) if turned else (w, h)
            along = (bh if seg_vertical else bw) / 2     # half the label's extent measured along this stretch
            for step in range(1, 40):
                f = step / 40
                c = (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f)
                box = (c[0] - bw / 2, c[1] - bh / 2, bw, bh)
                if any(box[0] - 4 < px + pw and px < box[0] + bw + 4 and box[1] - 3 < py + ph and py < box[1] + bh + 3
                       for px, py, pw, ph in placed):
                    continue
                cost = rank * 15 + abs(f - 0.5) * 40 + (60 if turned else 0)
                d0, d1 = f * length, (1 - f) * length    # distance to the two ends of this stretch
                if d0 < along + 4 or d1 < along + 4:
                    cost += 1000                         # sticks out past the bend / the end of the line
                if i == 0 and d0 < along + 14 or i == len(segs) - 1 and d1 < along + 14:
                    cost += 1000                         # would cover an arrowhead
                r = (box[0] - 2, box[1] - 2, bw + 4, bh + 4)
                if any(_hits(p, q, r, 0) for p, q in others):
                    cost += 5000                         # touches another transition
                if any(_hits(p, q, r, 0) for p, q in own):
                    cost += 200                          # hides a bend of its own line
                if best is None or cost < best[0]:  # pylint: disable=unsubscriptable-object
                    best = (cost, c, turned)
    if best:
        return best[1], best[2], best[0]
    # no free place on the line itself (many transitions share one stretch): the nearest free place beside it, joined to the line by a
    # dotted leader, so labels never lie on top of a box or of each other
    mid = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in segs] + [p for sg in segs for p in sg]
    for radius in range(14, 400, 14):
        for px0, py0 in mid:
            for k in range(16):
                ang = k * math.pi / 8
                c = (px0 + radius * math.cos(ang), py0 + radius * math.sin(ang))
                box = label_box(c, w, h, False)
                if not any(box[0] - 3 < px + pw and px < box[0] + box[2] + 3 and box[1] - 3 < py + ph and py < box[1] + box[3] + 3
                           for px, py, pw, ph in placed):
                    return c, False, 9000
    (x0, y0), (x1, y1) = segs[order[0]]
    return ((x0 + x1) / 2, (y0 + y1) / 2), False, 9999
