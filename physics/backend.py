from __future__ import annotations

import math

import numpy as np

from . import Body, Force, Rope, Spring


def _f(v, default=0.0, lo=-1e9, hi=1e9):
    v = float(v)
    return min(max(v, lo), hi) if math.isfinite(v) else default


class PymunkBackend:
    def __init__(self):
        self.space = None
        self.bodies = {}  # Идентификатор объекта и пара из объекта Body и тела pymunk
        self.ropes = {}  # Идентификатор верёвки, объект Rope, тела узлов и крайние соединения
        self.forces = []
        self.grab_joints = {}
        self.contact, self.tension = {}, {}

    # Перевод пикселей в метры и обратно; физическая ось y направлена вверх
    def m(self, x, y):
        return x * self.k, -y * self.k

    def px(self, X, Y):
        return X / self.k, -Y / self.k

    def build(self, sim, items):
        try:
            import pymunk
        except ImportError:
            raise ImportError(
                "physics needs pymunk (pip install pymunk) or Sim(canvas, backend=...)"
            ) from None
        self.pm, self.k = pymunk, 1 / sim.scale
        S = self.space = pymunk.Space()
        S.gravity = (0, -sim.g)
        S.iterations = 30
        self.bodies, self.ropes, self.forces, self.grab_joints = {}, {}, [], {}
        for it in items:
            if isinstance(it, Body):
                self._body(it)
        for it in items:
            if isinstance(it, Rope):
                self._rope(it, sim)
            elif isinstance(it, Spring):
                self._spring(it, sim)
            elif isinstance(it, Force):
                self.forces.append(it)

    def _body(self, it):
        pm, k = self.pm, self.k
        b = pm.Body(
            body_type={"dynamic": pm.Body.DYNAMIC, "static": pm.Body.STATIC, "kinematic": pm.Body.KINEMATIC}[
                it.kind
            ]
        )
        b.position, b.angle = self.m(_f(it.x), _f(it.y)), -_f(it.angle)
        loc = [(x * k, -y * k) for x, y in it.points]
        rad = it.width / 2 * k
        masses, shapes = [], []
        if it.shape == "circle":
            shapes = [pm.Circle(b, it.r * k)]
            masses = [(1.0, pm.moment_for_circle(1.0, 0, it.r * k))]
        elif it.shape == "line" and len(loc) > 1:
            for p, q in zip(loc, loc[1:]):
                L = math.dist(p, q)
                shapes.append(pm.Segment(b, p, q, rad))
                masses.append((L, pm.moment_for_segment(1.0, p, q, rad) * L))
        elif it.shape == "poly" and len(loc) > 2:
            ring = loc if self._area(loc) > 0 else loc[::-1]
            try:
                parts = pm.autogeometry.convex_decomposition(ring + ring[:1], k)
            except Exception:
                parts = [ring]
            for part in parts:
                poly = pm.Poly(b, [tuple(v) for v in part], radius=rad / 2)
                a = abs(poly.area) or 1e-9
                shapes.append(poly)
                c = poly.center_of_gravity
                masses.append((a, (pm.moment_for_poly(1.0, [tuple(v) for v in part]) + c.dot(c)) * a))
        else:
            shapes = [pm.Circle(b, max(rad, 0.01))]
            masses = [(1.0, pm.moment_for_circle(1.0, 0, max(rad, 0.01)))]
        if it.kind == "dynamic":
            total = sum(w for w, _ in masses) or 1.0
            m = _f(it.mass, 1.0, 1e-6, 1e9)
            b.mass = m
            b.moment = _f(sum(I for _, I in masses) * m / total, m, 1e-9, 1e15)
        if it.kind != "static":
            b.velocity, b.angular_velocity = (
                (_f(it.vx, lo=-1e6, hi=1e6), _f(it.vy, lo=-1e6, hi=1e6)),
                _f(it.omega, lo=-1e4, hi=1e4),
            )
        for s in shapes:
            s.friction, s.elasticity = it.friction, it.elasticity
        self.space.add(b, *shapes)
        self.bodies[it.id] = (it, b)

    @staticmethod
    def _area(P):
        return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(P, P[1:] + P[:1])) / 2

    def _anchor(self, body_id, local, world):
        ent = self.bodies.get(body_id)
        if ent is not None:
            return ent[1], (local[0] * self.k, -local[1] * self.k)
        return self.space.static_body, self.m(world.x(), world.y())

    def _can_connect(self, a, b):
        return a is not b and self.pm.Body.DYNAMIC in (a.body_type, b.body_type)

    def _rope(self, it, sim):
        pm = self.pm
        A, B = it.ends()
        L = it.length * sim.scale or math.hypot(B.x() - A.x(), B.y() - A.y())
        n = it.segments
        if len(it.nodes) != n - 1:
            it.init_nodes(A, B, L)
        r = max(it.width / 2 * self.k, 0.005)
        mi = _f(it.mass, 0.2, 1e-6, 1e6) / (n - 1)
        nodes = []
        for x, y, vx, vy in it.nodes:
            nb = pm.Body(mi, pm.moment_for_circle(mi, 0, r))
            nb.position, nb.velocity = (
                self.m(_f(x), _f(y)),
                (_f(vx, lo=-1e6, hi=1e6), _f(vy, lo=-1e6, hi=1e6)),
            )
            sh = pm.Circle(nb, r)
            sh.sensor = not it.collide
            sh.filter = pm.ShapeFilter(group=hash(it.id) & 0xFFFF)
            sh.friction = 0.5
            self.space.add(nb, sh)
            nodes.append(nb)
        chain = (
            [self._anchor(it.a, it.pa, A)] + [(nb, (0, 0)) for nb in nodes] + [self._anchor(it.b, it.pb, B)]
        )
        seg = L * self.k / n
        joints = [pm.SlideJoint(b1, b2, a1, a2, 0, seg) for (b1, a1), (b2, a2) in zip(chain, chain[1:])]
        # Нагрузку несёт ограничение общей длины: цепочка лёгких узлов без него растягивается и дрожит под тяжёлыми телами
        (b1, a1), (b2, a2) = chain[0], chain[-1]
        direct = (
            pm.SlideJoint(b1, b2, a1, a2, 0, L * self.k) if self._can_connect(b1, b2) else None
        )  # Оба конца прикреплены к одному телу
        self.space.add(*joints, *([direct] if direct else []))
        self.ropes[it.id] = (it, nodes, (joints[0], joints[-1], direct))

    def _spring(self, it, sim):
        A, B = it.ends()
        rest = it.rest or math.hypot(B.x() - A.x(), B.y() - A.y()) * self.k
        (ba, aa), (bb, ab) = self._anchor(it.a, it.pa, A), self._anchor(it.b, it.pb, B)
        if not self._can_connect(ba, bb):
            return  # Оба конца на одном теле: сила отсутствует
        self.space.add(self.pm.DampedSpring(ba, bb, aa, ab, rest, it.k, it.damping))

    def _grabs(self, sim):
        pm = self.pm
        for key in [k for k in self.grab_joints if k not in sim.grabs]:
            self.space.remove(*self.grab_joints.pop(key))
        for key, (bid, local, target) in sim.grabs.items():
            ent = self.bodies.get(bid)
            if ent is None or ent[1].body_type != pm.Body.DYNAMIC:
                continue
            if key not in self.grab_joints:
                mb = pm.Body(body_type=pm.Body.KINEMATIC)
                mb.position = self.m(target.x(), target.y())
                j = pm.PivotJoint(mb, ent[1], (0, 0), (local[0] * self.k, -local[1] * self.k))
                j.max_force = 400 * ent[1].mass * max(sim.g, 9.81)
                j.error_bias = 0.15**60
                self.space.add(mb, j)
                self.grab_joints[key] = (mb, j)
            self.grab_joints[key][0].position = self.m(target.x(), target.y())

    def step(self, sim, dt, substeps):
        self._grabs(sim)
        for bid, jx, jy in sim._kicks:
            if bid in self.bodies:
                self.bodies[bid][1].apply_impulse_at_local_point(
                    (_f(jx, lo=-1e7, hi=1e7), _f(jy, lo=-1e7, hi=1e7))
                )
        h = dt / substeps
        acc = {}
        ten = {rid: [0.0, 0.0] for rid in self.ropes}
        for _ in range(substeps):
            for f in self.forces:
                ent = self.bodies.get(f.body)
                if ent is not None:
                    b = ent[1]
                    b.apply_force_at_world_point(
                        tuple(_f(v, lo=-1e8, hi=1e8) for v in f.vector()),
                        b.local_to_world((f.pa[0] * self.k, -f.pa[1] * self.k)),
                    )
            self.space.step(h)
            for bid, (it, b) in self.bodies.items():
                if it.kind == "dynamic":
                    a = acc.setdefault(bid, [0.0, 0.0])

                    def add(arb, a=a):
                        a[0] += arb.total_impulse.x
                        a[1] += arb.total_impulse.y

                    b.each_arbiter(add)
            for rid, (_, _, (ja, jb, jd)) in self.ropes.items():
                d = jd.impulse if jd is not None else 0.0
                ten[rid][0] += ja.impulse + d
                ten[rid][1] += jb.impulse + d
        self.contact = {bid: (a[0] / dt, a[1] / dt) for bid, a in acc.items()}
        self.tension = {rid: (t[0] / dt, t[1] / dt) for rid, t in ten.items()}

    LIMIT = 1e7  # Предел в пикселях: тела, выброшенные чрезмерными силами, останавливаются на этой границе

    def read(self, sim):
        s = {}
        for bid, (it, b) in self.bodies.items():
            if it.kind != "static":
                x, y = self.px(*b.position)
                st = np.array([x, y, -b.angle, b.velocity.x, b.velocity.y, b.angular_velocity])
                if not np.isfinite(st).all() or abs(x) > self.LIMIT or abs(y) > self.LIMIT:
                    st = np.nan_to_num(st, nan=0.0, posinf=self.LIMIT, neginf=-self.LIMIT)
                    st[:2] = np.clip(st[:2], -self.LIMIT, self.LIMIT)
                    st[3:] = 0.0
                    b.position, b.velocity, b.angular_velocity = self.m(*st[:2]), (0, 0), 0.0
                    b.angle = -st[2]
                s[bid] = st
        for rid, (it, nodes, _) in self.ropes.items():
            N = np.array([[*self.px(*nb.position), nb.velocity.x, nb.velocity.y] for nb in nodes]).reshape(
                -1, 4
            )
            s[rid] = np.clip(np.nan_to_num(N), -self.LIMIT, self.LIMIT)
        return {"s": s, "fc": self.contact, "T": self.tension}
