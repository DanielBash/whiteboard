from __future__ import annotations

import math

import numpy as np

from .qt import QPainterPath, QPointF, QTransform, QPolygonF


def arr(p) -> np.ndarray:
    return np.array([p.x(), p.y()]) if isinstance(p, QPointF) else np.asarray(p, float)


def smooth_path(pts, s=1.0, closed=False) -> QPainterPath:
    P = np.asarray(pts, float)
    if closed and len(P) > 2:
        P = np.vstack([P, P[:1]])
    path = QPainterPath(QPointF(*P[0]))
    if len(P) < 3 or s <= 0:
        for x, y in P[1:].tolist():
            path.lineTo(x, y)
    else:
        if closed:
            prev = np.vstack([P[-2:-1], P[:-2]])
            nxt = np.vstack([P[2:], P[1:2]])
        else:
            prev = np.vstack([P[:1], P[:-2]])
            nxt = np.vstack([P[2:], P[-1:]])
        c1 = P[:-1] + (P[1:] - prev) * (s / 6)
        c2 = P[1:] - (nxt - P[:-1]) * (s / 6)
        for (a, b), (c, d), (e, f) in zip(c1.tolist(), c2.tolist(), P[1:].tolist()):
            path.cubicTo(a, b, c, d, e, f)
    if closed:
        path.closeSubpath()
    return path


def polygon(pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in np.asarray(pts, float).tolist()])


def seg_dist(P, a, b) -> np.ndarray:
    ab = b - a
    L = ab @ ab
    t = np.clip(((P - a) @ ab) / L, 0, 1) if L > 0 else np.zeros(len(P))
    return np.hypot(*(P - (a + t[:, None] * ab)).T)


def polyline_dist(q, P) -> float:
    if len(P) == 1:
        return float(np.hypot(*(P[0] - q)))
    A, AB = P[:-1], P[1:] - P[:-1]
    L = (AB * AB).sum(1)
    t = np.clip(((q - A) * AB).sum(1) / np.where(L > 0, L, 1), 0, 1)
    return float(np.hypot(*(A + t[:, None] * AB - q).T).min())


def densify(P, T, step):
    if len(P) < 2:
        return P, T
    L = np.nan_to_num(np.hypot(*np.diff(P, axis=0).T), nan=0.0, posinf=0.0)
    k = np.clip(np.ceil(L / step), 1, 4096).astype(
        int
    )  # Ограничение: чрезмерные координаты не должны приводить к исчерпанию памяти
    if (k == 1).all():
        return P, T
    seg = np.repeat(np.arange(len(P) - 1), k)
    f = (np.arange(k.sum()) - np.repeat(np.cumsum(k) - k, k)) / np.repeat(k, k)
    P2 = np.vstack([P[seg] + (P[seg + 1] - P[seg]) * f[:, None], P[-1:]])
    T2 = np.append(T[seg] + (T[seg + 1] - T[seg]) * f, T[-1])
    return P2, T2


def cut(P, T, a, b, r):
    P, T = densify(P, T, max(r * 0.5, 1e-6))
    inside = seg_dist(P, a, b) <= r
    if not inside.any():
        return None
    keep = np.flatnonzero(np.diff(np.concatenate([[1], inside.view(np.int8), [1]])))
    return [(P[i:j], T[i:j]) for i, j in zip(keep[::2], keep[1::2]) if j - i >= 2]


def rdp(P, eps):
    P = np.asarray(P, float)
    if len(P) < 3:
        return P
    keep = np.zeros(len(P), bool)
    keep[[0, -1]] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        d = seg_dist(P[i + 1 : j], P[i], P[j])
        k = int(d.argmax())
        if d[k] > eps:
            keep[i + 1 + k] = True
            stack += [(i, i + 1 + k), (i + 1 + k, j)]
    return P[keep]


def similarity(a0: QPointF, b0: QPointF, a1: QPointF, b1: QPointF, rotate=True, scale=True) -> QTransform:
    m0, m1 = (a0 + b0) / 2, (a1 + b1) / 2
    v0, v1 = b0 - a0, b1 - a1
    l0 = math.hypot(v0.x(), v0.y())
    l1 = math.hypot(v1.x(), v1.y())
    s = l1 / l0 if scale and min(l0, l1) > 1e-9 else 1.0
    ang = math.atan2(v1.y(), v1.x()) - math.atan2(v0.y(), v0.x()) if rotate and l0 > 1e-9 else 0.0
    t = QTransform()
    t.translate(m1.x(), m1.y())
    t.rotateRadians(ang)
    t.scale(s, s)
    t.translate(-m0.x(), -m0.y())
    return t


def tf_scale(t: QTransform) -> float:
    return math.sqrt(abs(t.determinant()))


def tf_angle(t: QTransform) -> float:
    return math.atan2(t.m12(), t.m11())


def tf_points(t: QTransform, P) -> np.ndarray:
    P = np.asarray(P, float).reshape(-1, 2)
    m = np.array([[t.m11(), t.m12()], [t.m21(), t.m22()]])
    return P @ m + [t.dx(), t.dy()]


def arrow(painter, a: QPointF, b: QPointF, head=10.0):
    d = b - a
    L = math.hypot(d.x(), d.y())
    if L < 1e-9:
        return
    painter.drawLine(a, b)
    u = d / L
    n = QPointF(-u.y(), u.x())
    h = min(head, L * 0.6)
    painter.drawPolygon(QPolygonF([b, b - u * h + n * h * 0.45, b - u * h - n * h * 0.45]))
