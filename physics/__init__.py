from __future__ import annotations

import math
import time

import numpy as np

from canvas import geom
from canvas.items import Item
from canvas.qt import (
    QColor,
    QFont,
    QFontMetricsF,
    QObject,
    QPainterPath,
    QPen,
    QPointF,
    QRectF,
    QTimer,
    QTransform,
    Qt,
    Signal,
)
from canvas.settings import Bool, Choice, Color, Float, HasSettings, Int, Str


class PhysicsItem(Item):
    DISPLAY = {"color", "fill", "label", "trace", "strobe", "vectors", "layer", "group"}

    def sim_state(self) -> dict:
        return {}

    def frame_data(self):
        return None

    def apply_frame(self, data):
        pass

    def _sim(self):
        return self.canvas.ext.get("physics") if self.canvas is not None else None

    def _end(self, body_id, local):
        body = self.canvas.find(body_id) if body_id and self.canvas is not None else None
        return body.to_world(QPointF(*local)) if body is not None else QPointF(*local)


class Body(PhysicsItem):
    kind = Choice(
        "dynamic",
        ["dynamic", "static", "kinematic"],
        label="Тип тела",
        choice_labels={"dynamic": "Подвижное", "static": "Неподвижное", "kinematic": "С заданным движением"},
    )
    color = Color("#2d70b3", label="Цвет")
    fill = Color("#00000000", label="Цвет заливки")
    width = Float(6, min=1, max=80, label="Толщина")
    mass = Float(1, min=0.01, max=1e4, label="Масса (кг)")
    friction = Float(0.6, min=0, max=2, label="Трение")
    elasticity = Float(0.3, min=0, max=1, label="Упругость")
    vx = Float(0, label="Скорость по горизонтали (м/с)")
    vy = Float(0, label="Скорость по вертикали (м/с)")
    omega = Float(0, label="Угловая скорость (рад/с)")
    label = Str("", label="Подпись")
    trace = Bool(False, label="Показывать след")
    strobe = Float(0, min=0, max=2, label="Интервал отметок следа (с)")
    vectors = Bool(True, label="Показывать векторы")

    def __init__(self, shape="line", points=(), r=20.0, x=0.0, y=0.0, angle=0.0, **kw):
        super().__init__(**kw)
        self.shape, self.points, self.r = shape, [tuple(map(float, q)) for q in points], float(r)
        self.x, self.y, self.angle = float(x), float(y), float(angle)
        self._ver, self._cache = 0, (None, None)

    @classmethod
    def from_points(cls, pts, closed=False, **kw):
        P = geom.rdp(np.asarray(pts, float), 0.6)
        if closed and len(P) >= 3:
            Q = np.vstack([P, P[:1]])
            cr = Q[:-1, 0] * Q[1:, 1] - Q[1:, 0] * Q[:-1, 1]
            A = cr.sum() / 2
            c = ((Q[:-1] + Q[1:]) * cr[:, None]).sum(0) / (6 * A) if abs(A) > 1e-6 else P.mean(0)
        elif len(P) > 1:
            L = np.hypot(*np.diff(P, axis=0).T)
            c = ((P[1:] + P[:-1]) / 2 * L[:, None]).sum(0) / L.sum() if L.sum() > 0 else P.mean(0)
        else:
            c = P.mean(0)
        return cls("poly" if closed and len(P) >= 3 else "line", P - c, x=c[0], y=c[1], **kw)

    @classmethod
    def circle(cls, x, y, r, **kw):
        return cls("circle", r=r, x=x, y=y, **kw)

    @classmethod
    def box(cls, x, y, w, h, angle=0.0, **kw):
        return cls(
            "poly",
            [(-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)],
            x=x,
            y=y,
            angle=angle,
            **kw,
        )

    def local(self) -> QTransform:
        return QTransform().translate(self.x, self.y).rotateRadians(self.angle)

    def to_world(self, local: QPointF) -> QPointF:
        return self.local().map(QPointF(local))

    def to_local(self, pos: QPointF):
        q = self.local().inverted()[0].map(QPointF(pos))
        return (q.x(), q.y())

    def path(self) -> QPainterPath:
        key = (self._ver, self.shape, self.r)
        if self._cache[0] != key:
            if self.shape == "circle":
                p = QPainterPath()
                p.addEllipse(QPointF(), self.r, self.r)
            elif len(self.points) == 1:
                p = QPainterPath(QPointF(*self.points[0]))
                p.lineTo(QPointF(*self.points[0]))
            else:
                p = geom.smooth_path(
                    self.points, 0.5 if self.shape == "line" else 0.0, closed=self.shape == "poly"
                )
            self._cache = (key, p)
        return self._cache[1]

    def bounds(self):
        r = self.width / 2 + 1
        return self.local().mapRect(self.path().boundingRect().adjusted(-r, -r, r, r))

    def paint(self, p, zoom):
        p.save()
        p.setTransform(self.local(), True)
        c = QColor(self.color)
        if self.shape == "line":
            p.setPen(
                QPen(c, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
        else:
            p.setPen(
                QPen(
                    c,
                    max(1.0, self.width / 2),
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                )
            )
            p.setBrush(QColor(self.fill))
        p.drawPath(self.path())
        if self.shape == "circle":
            p.drawLine(QPointF(), QPointF(self.r, 0))
        p.restore()

    def hit(self, pos, r=0.0):
        q = np.array(self.to_local(pos))
        if self.shape == "circle":
            return math.hypot(*q) <= self.r + r + self.width / 4
        if self.shape == "poly" and self.path().contains(QPointF(*q)):
            return True
        P = np.asarray(self.points + (self.points[:1] if self.shape == "poly" else []))
        return len(P) > 0 and geom.polyline_dist(q, P) <= r + self.width / 2

    def transform(self, t):
        c = t.map(QPointF(self.x, self.y))
        self.x, self.y = c.x(), c.y()
        self.angle += geom.tf_angle(t)
        s = geom.tf_scale(t)
        if abs(s - 1) > 1e-9:
            self.points = [(a * s, b * s) for a, b in self.points]
            self.r *= s
            self.width *= s
            self._ver += 1

    def erase(self, a, b, r):
        if self.shape != "line":
            mid = QPointF(*((a + b) / 2))
            return [] if any(self.hit(q, r) for q in (QPointF(*a), mid, QPointF(*b))) else None
        P = geom.tf_points(self.local(), self.points)
        pieces = geom.cut(P, np.zeros(len(P)), a, b, r + self.width / 2)
        if pieces is None:
            return None
        return [Body.from_points(Q, **self.settings.dump()) for Q, _ in pieces]

    def state(self):
        return {
            "shape": self.shape,
            "points": np.round(self.points, 2).tolist(),
            "r": self.r,
            "x": self.x,
            "y": self.y,
            "angle": self.angle,
        }

    def set_state(self, d):
        self.shape = d.get("shape", self.shape)
        self.points = [tuple(q) for q in d.get("points", self.points)]
        self.r, self.x, self.y, self.angle = (
            float(d.get(k, getattr(self, k))) for k in ("r", "x", "y", "angle")
        )
        self._ver += 1

    def sim_state(self):
        return {
            "x": self.x,
            "y": self.y,
            "angle": self.angle,
            "vx": self.vx,
            "vy": self.vy,
            "omega": self.omega,
        }

    def frame_data(self):
        return np.array([self.x, self.y, self.angle, self.vx, self.vy, self.omega])

    def apply_frame(self, d):
        self.x, self.y, self.angle = float(d[0]), float(d[1]), float(d[2])
        self._values["vx"], self._values["vy"], self._values["omega"] = float(d[3]), float(d[4]), float(d[5])


class _Link(PhysicsItem):
    a = Str("", ui=False)
    b = Str("", ui=False)

    def __init__(self, pa=(0.0, 0.0), pb=(0.0, 0.0), **kw):
        super().__init__(**kw)
        self.pa, self.pb = tuple(map(float, pa)), tuple(map(float, pb))

    def finalize(self, scale):
        name = "length" if isinstance(self, Rope) else "rest" if isinstance(self, Spring) else None
        if name and not self.get(name):
            A, B = self.ends()
            self._values[name] = math.hypot(B.x() - A.x(), B.y() - A.y()) / scale

    def refs(self):
        return [i for i in (self.a, self.b) if i]

    def ends(self):
        if self.canvas is None:
            return QPointF(*self.pa), QPointF(*self.pb)
        return self._end(self.a, self.pa), self._end(self.b, self.pb)

    def pts(self):
        A, B = self.ends()
        return np.array([[A.x(), A.y()], [B.x(), B.y()]])

    def bounds(self):
        P = self.pts()
        (x0, y0), (x1, y1) = P.min(0), P.max(0)
        r = getattr(self, "width", 2) * 3 + 6
        return QRectF(x0 - r, y0 - r, x1 - x0 + 2 * r, y1 - y0 + 2 * r)

    def hit(self, pos, r=0.0):
        return geom.polyline_dist(geom.arr(pos), self.pts()) <= r + self.width / 2 + 3

    def erase(self, a, b, r):
        P = self.pts()
        return [] if geom.cut(P, np.zeros(len(P)), a, b, r) is not None else None

    def transform(self, t):
        if not self.a:
            self.pa = tuple(geom.tf_points(t, [self.pa])[0])
        if not self.b:
            self.pb = tuple(geom.tf_points(t, [self.pb])[0])

    def state(self):
        return {"pa": list(self.pa), "pb": list(self.pb)}

    def set_state(self, d):
        self.pa, self.pb = tuple(d.get("pa", self.pa)), tuple(d.get("pb", self.pb))


class Rope(_Link):
    length = Float(0, min=0, label="Длина (м; 0 — текущая)")
    segments = Int(16, min=2, max=80, label="Количество звеньев")
    mass = Float(0.2, min=0.001, max=100, label="Масса (кг)")
    color = Color("#8a5a2b", label="Цвет")
    width = Float(3, min=0.5, max=30, label="Толщина")
    collide = Bool(False, label="Столкновения звеньев")

    def __init__(self, **kw):
        super().__init__(**kw)
        self.nodes = np.zeros((0, 4))  # Внутренние узлы: x, y в пикселях, vx, vy в м/с

    def pts(self):
        A, B = self.ends()
        return np.vstack([[A.x(), A.y()], self.nodes[:, :2], [B.x(), B.y()]])

    def init_nodes(self, A, B, L):
        n = self.segments
        A, B = geom.arr(A), geom.arr(B)
        s = np.linspace(0, 1, n + 1)[1:-1, None]
        d = B - A
        D = math.hypot(*d)
        sag = 0.0
        if L > D * 1.001:
            perp = np.array([-d[1], d[0]]) / (D or 1)
            if perp[1] < 0:
                perp = -perp
            lo, hi = 0.0, L
            for _ in range(40):
                sag = (lo + hi) / 2
                P = (
                    A
                    + np.linspace(0, 1, 33)[:, None] * d
                    + (4 * sag * np.linspace(0, 1, 33) * (1 - np.linspace(0, 1, 33)))[:, None] * perp
                )
                lo, hi = (sag, hi) if np.hypot(*np.diff(P, axis=0).T).sum() < L else (lo, sag)
            pos = A + s * d + 4 * sag * s * (1 - s) * perp
        else:
            pos = A + s * d
        self.nodes = np.hstack([pos, np.zeros((n - 1, 2))])

    def paint(self, p, zoom):
        p.setPen(
            QPen(
                QColor(self.color),
                self.width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(geom.smooth_path(self.pts(), 1.0))

    def transform(self, t):
        super().transform(t)
        if len(self.nodes):
            self.nodes[:, :2] = geom.tf_points(t, self.nodes[:, :2])

    def state(self):
        return {**super().state(), "nodes": np.round(self.nodes, 3).tolist()}

    def set_state(self, d):
        super().set_state(d)
        if "nodes" in d:
            self.nodes = np.asarray(d["nodes"], float).reshape(-1, 4)

    def sim_state(self):
        return {"nodes": np.round(self.nodes, 3).tolist()}

    def frame_data(self):
        return self.nodes.copy()

    def apply_frame(self, d):
        self.nodes = d


class Spring(_Link):
    rest = Float(0, min=0, label="Длина без нагрузки (м; 0 — текущая)")
    k = Float(50, min=0.1, max=1e5, label="Жёсткость (Н/м)")
    damping = Float(0.5, min=0, max=1000, label="Сопротивление (Н·с/м)")
    coils = Int(12, min=2, max=60, label="Количество витков")
    color = Color("#555b66", label="Цвет")
    width = Float(2, min=0.5, max=20, label="Толщина")

    def paint(self, p, zoom):
        A, B = self.ends()
        d = B - A
        L = math.hypot(d.x(), d.y())
        if L < 1e-6:
            return
        u, n = d / L, QPointF(-d.y() / L, d.x() / L)
        amp = 3 * self.width + 5
        k = self.coils * 2
        pts = [A, A + u * (L * 0.1)]
        for i in range(1, k):
            pts.append(A + u * (L * (0.1 + 0.8 * i / k)) + n * (amp if i % 2 else -amp))
        pts += [A + u * (L * 0.9), B]
        p.setPen(
            QPen(
                QColor(self.color),
                self.width,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPolyline(pts)


class Force(PhysicsItem):
    body = Str("", ui=False)
    fx = Float(0, label="Сила по горизонтали (Н)")
    fy = Float(0, label="Сила по вертикали (Н)")
    frame = Choice(
        "world",
        ["world", "body"],
        label="Система отсчёта",
        choice_labels={"world": "Относительно доски", "body": "Относительно тела"},
    )
    color = Color("#c74440", label="Цвет")
    label = Str("Сила: {F:.1f} Н", label="Подпись")
    zoom_dependent = True

    def __init__(self, pa=(0.0, 0.0), **kw):
        super().__init__(**kw)
        self.pa = tuple(map(float, pa))

    def refs(self):
        return [self.body] if self.body else []

    def vector(self):
        fx, fy = self.fx, self.fy
        b = self.canvas.find(self.body) if self.canvas is not None else None
        if self.frame == "body" and b is not None:
            c, s = math.cos(-b.angle), math.sin(-b.angle)
            fx, fy = fx * c - fy * s, fx * s + fy * c
        return fx, fy

    def arrow(self):
        sim = self._sim()
        A = self._end(self.body, self.pa) if self.canvas is not None else QPointF(*self.pa)
        fx, fy = self.vector()
        k = sim.f_scale if sim is not None else 4.0
        return A, A + QPointF(fx * k, -fy * k)

    def _label(self):
        try:
            return self.label.format(F=math.hypot(*self.vector()), fx=self.fx, fy=self.fy)
        except Exception as ex:
            return f"label error: {ex}"

    def bounds(self):
        A, B = self.arrow()
        r = QRectF(A, B).normalized()
        z = self.canvas.zoom if self.canvas is not None else 1.0
        r = r.adjusted(-14 / z, -14 / z, 14 / z, 14 / z)
        if self.label:  # Подпись постоянного экранного размера над наконечником стрелки; см. _text
            f = QFont()
            f.setPixelSize(14)
            text = self._label()
            w = QFontMetricsF(f).horizontalAdvance(max(text.split("\n"), key=len)) + 4
            h = 14 * 1.4 * (text.count("\n") + 1) + 4
            r = r.united(QRectF(B.x() + 6 / z - 2 / z, B.y() - 6 / z - h / z, w / z, h / z + 4 / z))
        return r

    def hit(self, pos, r=0.0):
        A, B = self.arrow()
        return geom.seg_dist(geom.arr(pos)[None], geom.arr(A), geom.arr(B))[0] <= r + 4

    def erase(self, a, b, r):
        A, B = self.arrow()
        P = np.array([geom.arr(A), geom.arr(B)])
        return [] if geom.cut(P, np.zeros(2), a, b, r) is not None else None

    def paint(self, p, zoom):
        if self.body and self.canvas is not None and self.canvas.find(self.body) is None:
            return  # Тело отсутствует, например скрыто отменой: не рисовать в случайной точке
        A, B = self.arrow()
        c = QColor(self.color)
        p.setPen(QPen(c, 2.5 / zoom))
        p.setBrush(c)
        geom.arrow(p, A, B, 12 / zoom)
        if self.label:
            _text(p, B + QPointF(6 / zoom, -6 / zoom), self._label(), c, zoom)

    def state(self):
        return {"pa": list(self.pa)}

    def set_state(self, d):
        self.pa = tuple(d.get("pa", self.pa))


def _text(p, pos, text, color, zoom, size=14):
    p.save()
    p.translate(pos)
    p.scale(1 / zoom, 1 / zoom)
    f = QFont()
    f.setPixelSize(size)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QRectF(0, -size * 1.4 * (text.count("\n") + 1), 600, 400), Qt.AlignmentFlag.AlignLeft, text)
    p.restore()


class _Signals(QObject):
    frameChanged = Signal(int)
    playingChanged = Signal(bool)


def _nice(v):
    e = math.floor(math.log10(v))
    f = v / 10**e
    return (1 if f < 2 else 2 if f < 5 else 5) * 10**e


class Sim(HasSettings):
    g = Float(9.81, min=0, max=100, label="Ускорение свободного падения (м/с²)")
    scale = Float(100, min=1, max=5000, label="Пикселей на метр")
    speed = Float(1, min=0.05, max=8, label="Скорость воспроизведения")
    substeps = Int(4, min=1, max=50, label="Шагов расчёта за кадр")
    dt = Float(1 / 60, ui=False)
    show_v = Bool(False, label="Вектор скорости")
    show_a = Bool(False, label="Вектор ускорения")
    show_g = Bool(False, label="Сила тяжести")
    show_n = Bool(False, label="Силы контакта")
    show_net = Bool(False, label="Равнодействующая сил")
    show_t = Bool(False, label="Натяжение верёвки")
    v_scale = Float(40, min=1, max=500, label="Масштаб скорости (пикс. на м/с)")
    a_scale = Float(10, min=0.1, max=200, label="Масштаб ускорения (пикс. на м/с²)")
    f_scale = Float(4, min=0.01, max=200, label="Масштаб силы (пикс. на Н)")
    ruler = Bool(True, label="Масштабная линейка")
    max_frames = Int(36000, ui=False)
    COLORS = {
        "v": "#388c46",
        "a": "#6042a6",
        "g": "#7a7f87",
        "n": "#fa7e19",
        "net": "#c74440",
        "t": "#8a5a2b",
    }

    def __init__(self, canvas, backend=None, **kw):
        super().__init__(**kw)
        if backend is None:
            from .backend import PymunkBackend

            backend = PymunkBackend()
        self.canvas, self.backend = canvas, backend
        self.frames, self.frame = [], 0
        self.grabs = {}  # Ключ и данные захвата: идентификатор тела, локальная точка, мировая цель
        self.on_build = []  # Обработчик после построения мира; здесь можно добавить собственные объекты движка
        self.signals = _Signals()
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._dirty, self._base, self._writing, self._acc, self._kicks = True, {}, False, 0.0, []
        self._moved = False
        self._traces = {}
        self._trail_img, self._trail_key, self._trail_drawn = None, None, {}
        canvas.ext["physics"] = self
        canvas.overlays.append(self.paint)
        canvas.timeline.hooks.append(self.sync)
        canvas.itemAdded.connect(self._edited)
        canvas.itemRemoved.connect(self._edited)
        canvas.itemChanged.connect(self._edited)
        canvas.reset.connect(self.reset)
        canvas.aboutToChange.connect(self._about_to_change)
        self.watch(self._setting)
        self.reset()

    # --- Учёт объектов
    def items(self) -> list:
        return [it for it in self.canvas.items if isinstance(it, PhysicsItem)]

    def _edited(self, it, key=""):
        if self._writing or not isinstance(it, PhysicsItem) or key in PhysicsItem.DISPLAY:
            return
        if isinstance(it, _Link) and it.canvas is self.canvas:
            it.finalize(self.scale)
        self.restart()
        if self.playing and it.canvas is self.canvas and getattr(it, "kind", "") != "static":
            self.canvas.set_live([it])
        if it.canvas is self.canvas:
            self._base[it.id] = it.sim_state()
        else:
            self._base.pop(it.id, None)

    def _about_to_change(self, items):
        if self.playing and any(isinstance(it, PhysicsItem) for it in items):
            self.pause()  # Прямое редактирование перемещением не должно конфликтовать с работающей симуляцией

    def _setting(self, obj, key, old, new):
        if key in ("g", "scale", "substeps", "dt"):
            self.restart()
        if key == "f_scale":
            for it in self.items():
                if isinstance(it, Force):
                    it.update()
        self.canvas.update()

    def cut(self):
        del self.frames[self.frame + 1 :]
        self._dirty = True
        self.signals.frameChanged.emit(self.frame)

    def restart(self):
        self.frames, self.frame, self._dirty = [], 0, True
        self.signals.frameChanged.emit(0)

    def reset(self):
        self._timer.stop()
        self.frames, self.frame, self._dirty, self._moved = [], 0, True, False
        self._base = {it.id: it.sim_state() for it in self.items()}
        self.signals.frameChanged.emit(0)

    def sync(self):
        if not self._moved:
            return
        self._moved = False
        changed = []
        for it in self.items():
            cur, old = it.sim_state(), self._base.get(it.id)
            if old is not None and cur != old:
                changed.append((it, old, cur))
            self._base[it.id] = cur
        if changed:
            with self.canvas.batch():
                for it, old, cur in changed:
                    self.canvas.timeline.record({"k": "state", "id": it.id, "before": old, "after": cur})

    def to_dict(self):
        return self.settings.dump()

    def load(self, d):
        self.settings.load(d)

    # --- Воспроизведение
    @property
    def playing(self) -> bool:
        return self._timer.isActive()

    @property
    def time(self) -> float:
        return self.frame * self.dt

    def capture(self) -> dict:
        return {"s": {it.id: it.frame_data() for it in self.items() if it.frame_data() is not None}}

    def play(self):
        if self.playing:
            return
        self.canvas.set_live([it for it in self.items() if getattr(it, "kind", "") != "static"])
        self._last, self._acc = time.monotonic(), 0.0
        self._timer.start(max(1, int(self.dt * 1000)))
        self.signals.playingChanged.emit(True)

    def pause(self):
        if not self.playing:
            return
        self._timer.stop()
        self.canvas.set_live(self.items(), False)
        self.sync()
        self.signals.playingChanged.emit(False)

    def toggle(self):
        self.pause() if self.playing else self.play()

    def stop(self):
        self.pause()
        self.seek(0)

    def seek(self, i: int):
        if not self.frames:
            return
        self.frame = max(0, min(int(i), len(self.frames) - 1))
        self._apply(self.frames[self.frame])
        self._dirty = True
        self.signals.frameChanged.emit(self.frame)

    def rewind(self):
        self.seek(0)

    def forward(self):
        self.seek(len(self.frames) - 1)

    def step(self, n=1):
        for i in range(n):
            self._advance(show=i == n - 1)
        self._refresh()

    def back(self, n=1):
        self.seek(self.frame - n)

    # --- Взаимодействие
    def grab(self, body, pos: QPointF):
        self.cut()  # При взаимодействии во время воспроизведения продолжить расчёт с текущего момента
        key = object()
        self.grabs[key] = [body.id, body.to_local(pos), QPointF(pos)]
        return key

    def drag(self, key, pos: QPointF):
        if key in self.grabs:
            self.grabs[key][2] = QPointF(pos)

    def release(self, key):
        self.grabs.pop(key, None)

    def kick(self, body, jx, jy):
        self.cut()
        self._kicks.append((body.id, jx, jy))

    # --- Расчёт шагов
    def _tick(self):
        now = time.monotonic()
        self._acc += min(now - self._last, 0.1) * self.speed / self.dt
        self._last = now
        n = int(self._acc)
        self._acc -= n
        n = min(n, 4)  # На медленном компьютере пропускать время, чтобы избежать накопления отставания
        for i in range(n):
            self._advance(
                show=i == n - 1
            )  # В объекты и на экран передаётся только последнее состояние текущего такта
        if n:
            self._refresh()

    def _advance(self, show=True):
        if not self.frames:
            self.frames, self.frame = [self.capture()], 0
        if self.frame + 1 < len(self.frames):
            self.frame += 1
            if show:
                self._apply(self.frames[self.frame])
            return
        if self._dirty:
            self._apply(
                self.frames[self.frame]
            )  # Объекты могут отставать от пропущенных кадров, которые не были показаны
            self.backend.build(self, self.items())
            for f in self.on_build:
                f(self)
            self._dirty = False
        self.backend.step(self, self.dt, self.substeps)
        self._kicks.clear()
        fr = self.backend.read(self)
        if show:
            self._apply(fr)
        else:
            self._moved = True
        self.frames.append(fr)
        self.frame += 1
        if len(self.frames) > self.max_frames:
            del self.frames[0]
            self.frame -= 1

    def _apply(self, fr):
        self._moved = True
        self._writing = True
        moved = []
        try:
            for id, d in fr["s"].items():
                it = self.canvas.find(id)
                if it is not None:
                    it.apply_frame(d)
                    moved.append(it)
        finally:
            self._writing = False
        if (
            self.playing
        ):  # Холст всё равно обновляется каждый такт; достаточно обновить границы для проверки попадания
            b, deps = self.canvas._bounds, self.canvas._deps
            for it in moved + [d for it in moved for d in deps.get(it.id, ())]:
                if it.id in b:
                    b[it.id] = it.bounds()
        else:
            for it in (
                moved
            ):  # Обновлять только движущиеся тела, иначе неподвижные заставят перестраивать кеш каждый кадр
                it.update()  # Прикреплённые силы и пружины следуют через зависимости холста

    def _refresh(self):
        self.canvas.update()
        self.signals.frameChanged.emit(self.frame)

    # --- Измерения и рисование
    def quantities(self, body) -> dict:
        d = {
            "m": body.mass,
            "x": body.x / self.scale,
            "y": -body.y / self.scale,
            "vx": body.vx,
            "vy": body.vy,
            "w": body.omega,
            "angle": -math.degrees(body.angle),
            "t": self.time,
        }
        d["v"] = math.hypot(body.vx, body.vy)
        ax = ay = 0.0
        if 0 < self.frame < len(self.frames):
            prev = self.frames[self.frame - 1]["s"].get(body.id)
            if prev is not None:
                ax, ay = (body.vx - prev[3]) / self.dt, (body.vy - prev[4]) / self.dt
        d.update(ax=ax, ay=ay, a=math.hypot(ax, ay), Fx=body.mass * ax, Fy=body.mass * ay)
        d["F"] = body.mass * d["a"]
        d.update(Ek=body.mass * d["v"] ** 2 / 2, Ep=body.mass * self.g * d["y"], p=body.mass * d["v"])
        return d

    def _paint_traces(self, p, canvas):
        traced = [b for b in self.items() if isinstance(b, Body) and b.trace and b.kind == "dynamic"]
        if not traced or not self.frames:
            return
        from canvas.qt import QImage, QPainter

        dpr = canvas.devicePixelRatioF()
        v = canvas.view
        key = (
            int(canvas.width() * dpr),
            int(canvas.height() * dpr),
            v.m11(),
            v.m12(),
            v.m21(),
            v.m22(),
            v.dx(),
            v.dy(),
        )
        z = canvas.zoom
        steps = {b.id: (1.5 / z if b.strobe <= 0 else 0.0) for b in traced}
        lists = {b.id: self._trace(b, steps[b.id]) for b in traced}
        if (
            self._trail_img is None
            or self._trail_key != key
            or any(self._trail_drawn.get(i, 0) > len(pts) for i, pts in lists.items())
        ):
            self._trail_img = QImage(
                max(key[0], 1), max(key[1], 1), QImage.Format.Format_ARGB32_Premultiplied
            )
            self._trail_img.setDevicePixelRatio(dpr)
            self._trail_img.fill(0)
            self._trail_key, self._trail_drawn = key, {}
        q = QPainter(self._trail_img)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.setTransform(v)
        for b in traced:
            pts, done = lists[b.id], self._trail_drawn.get(b.id, 0)
            c = QColor(b.color)
            c.setAlpha(150)
            if b.strobe > 0:
                k = max(1, round(b.strobe / self.dt))
                q.setPen(Qt.PenStyle.NoPen)
                q.setBrush(c)
                for i in range(done, len(pts)):
                    if i % k == 0:
                        q.drawEllipse(pts[i], 3.5 / z, 3.5 / z)
            elif len(pts) - max(done - 1, 0) > 1:
                q.setPen(
                    QPen(c, 2 / z, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                )
                q.setBrush(Qt.BrushStyle.NoBrush)
                q.drawPolyline(pts[max(done - 1, 0) :])
            self._trail_drawn[b.id] = len(pts)
        q.end()
        p.save()
        p.resetTransform()
        p.drawImage(QPointF(0, 0), self._trail_img)
        p.restore()

    def paint(self, p, canvas):
        self._paint_traces(p, canvas)
        z = canvas.zoom
        fr = self.frames[self.frame] if self.frames else {}
        for it in self.items():
            if isinstance(it, Body) and it.kind == "dynamic":
                self._paint_body(p, it, fr, z)
            elif isinstance(it, Rope) and self.show_t and it.id in fr.get("T", {}):
                P = it.pts()
                for end, nxt, T in ((P[0], P[1], fr["T"][it.id][0]), (P[-1], P[-2], fr["T"][it.id][1])):
                    d = (nxt - end) / (math.hypot(*(nxt - end)) or 1) * T * self.f_scale
                    self._vec(p, QPointF(*end), d[0], d[1], "t", z)
        if self.ruler:
            p.resetTransform()
            m = _nice(160 / (self.scale * z))
            L = m * self.scale * z
            x, y = 16, canvas.height() - 18
            p.setPen(QPen(QColor("#4a4f57"), 2))
            p.drawLine(QPointF(x, y), QPointF(x + L, y))
            p.drawLine(QPointF(x, y - 5), QPointF(x, y + 3))
            p.drawLine(QPointF(x + L, y - 5), QPointF(x + L, y + 3))
            p.drawText(QPointF(x, y - 8), f"{m:g} м")

    def _vec(self, p, at, dx, dy, kind, zoom):
        c = QColor(self.COLORS[kind])
        p.setPen(QPen(c, 2.5 / zoom))
        p.setBrush(c)
        geom.arrow(p, at, at + QPointF(dx, dy), 10 / zoom)

    def _trace(self, b, step):
        key = (b.id, step > 0)
        first = self.frames[0] if self.frames else None
        pts, upto, fr0 = self._traces.get(key, ([], 0, None))
        if fr0 is not first or upto > self.frame + 1:
            pts, upto = [], 0  # Запись перезапущена или перемотана: перестроить
        for f in self.frames[upto : self.frame + 1]:
            s = f["s"].get(b.id)
            if s is not None:
                q = QPointF(s[0], s[1])
                if not step or not pts or abs(q.x() - pts[-1].x()) + abs(q.y() - pts[-1].y()) >= step:
                    pts.append(q)
        self._traces[key] = (pts, self.frame + 1, first)
        return pts

    def _paint_body(self, p, b, fr, z):
        need = b.label or (b.vectors and (self.show_net or self.show_a))
        q = self.quantities(b) if need else None
        at = QPointF(b.x, b.y)
        if b.vectors:
            if self.show_g:
                self._vec(p, at, 0, b.mass * self.g * self.f_scale, "g", z)
            if self.show_n and b.id in fr.get("fc", {}):
                fx, fy = fr["fc"][b.id]
                self._vec(p, at, fx * self.f_scale, -fy * self.f_scale, "n", z)
            if self.show_net:
                self._vec(p, at, q["Fx"] * self.f_scale, -q["Fy"] * self.f_scale, "net", z)
            if self.show_v:
                self._vec(p, at, b.vx * self.v_scale, -b.vy * self.v_scale, "v", z)
            if self.show_a:
                self._vec(p, at, q["ax"] * self.a_scale, -q["ay"] * self.a_scale, "a", z)
        if b.label:
            try:
                text = b.label.format(**q)
            except Exception as ex:
                text = f"label error: {ex}"
            r = b.bounds()
            _text(p, QPointF(r.right(), r.top()), text, QColor("#1e1e1e"), z)
