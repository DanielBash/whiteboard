from __future__ import annotations

import base64
import math
import time
import uuid

import numpy as np

from . import geom
from .qt import (
    QtCore,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainterPath,
    QPen,
    QPointF,
    QRectF,
    QTransform,
    Qt,
)
from .settings import Bool, Color, Choice, Float, HasSettings, Str


class Model(HasSettings):
    TYPES = {}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        Model.TYPES[cls.__name__] = cls

    def state(self) -> dict:
        return {}

    def set_state(self, d: dict):
        pass

    def snapshot(self) -> dict:
        return {**self.settings.dump(), **self.state()}

    def restore(self, d: dict):
        specs = type(self).specs()
        self.settings.load({k: v for k, v in d.items() if k in specs})
        self.set_state({k: v for k, v in d.items() if k not in specs})

    def to_dict(self) -> dict:
        return {"type": type(self).__name__, **self.snapshot()}

    @staticmethod
    def from_dict(d: dict):
        d = dict(d)
        cls = Model.TYPES[d.pop("type")]
        obj = cls(id=d.pop("id")) if "id" in d else cls()
        obj.restore(d)
        return obj


class Item(Model):
    layer = Str("main", ui=False)
    group = Str("", ui=False)
    erasable = True
    selectable = True
    zoom_dependent = False  # bounds() зависит от масштаба холста для текста постоянного экранного размера

    def __init__(self, id=None, **values):
        self.id = id or uuid.uuid4().hex[:12]
        self.canvas = None
        self._mute = 0
        super().__init__(**values)

    def to_dict(self):
        return {**super().to_dict(), "id": self.id}

    def _emit(self, key, old, new):
        super()._emit(key, old, new)
        if self.canvas is not None:
            self.canvas._on_item_set(self, key, old, new)

    def update(self, rect: QRectF = None):
        if self.canvas is not None:
            self.canvas._item_update(self, rect)

    # --- Методы для переопределения
    def bounds(self) -> QRectF:
        raise NotImplementedError

    def paint(self, p, zoom: float):
        raise NotImplementedError

    def hit(self, pos: QPointF, r=0.0) -> bool:
        return self.bounds().adjusted(-r, -r, r, r).contains(pos)

    def inside(self, path: QPainterPath) -> bool:
        return path.contains(self.bounds().center())

    def transform(self, t: QTransform):
        pass

    def erase(self, a: np.ndarray, b: np.ndarray, r: float):
        return None

    def reveal(self, dt):
        pass

    def refs(self) -> list:
        return []


class Layer(HasSettings):
    name = Str("main", label="Название")
    visible = Bool(True, label="Показывать")
    locked = Bool(False, label="Заблокировать")
    opacity = Float(1, min=0, max=1, label="Непрозрачность")


class Background(Model):
    color = Color("#ffffff", label="Цвет")

    def paint(self, p, rect: QRectF, zoom: float):
        p.fillRect(rect, QColor(self.color))


class Grid(Background):
    style = Choice(
        "grid",
        ["grid", "dots", "lines", "none"],
        label="Стиль",
        choice_labels={"grid": "Сетка", "dots": "Точки", "lines": "Линейка", "none": "Без узора"},
    )
    spacing = Float(40, min=4, max=1000, label="Шаг сетки")
    line = Color("#e4e7eb", label="Цвет линий")

    def paint(self, p, rect, zoom):
        super().paint(p, rect, zoom)
        s = self.spacing
        while s * zoom < 12:
            s *= 5
        if self.style == "none":
            return
        pen = QPen(QColor(self.line), 2.5 if self.style == "dots" else 1)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        xs = np.arange(math.floor(rect.left() / s), math.ceil(rect.right() / s) + 1) * s
        ys = np.arange(math.floor(rect.top() / s), math.ceil(rect.bottom() / s) + 1) * s
        L, T, R, B = rect.left(), rect.top(), rect.right(), rect.bottom()
        if self.style == "dots":
            p.drawPoints(geom.polygon(np.stack(np.meshgrid(xs, ys), -1).reshape(-1, 2)))
            return
        lines = [QtCore.QLineF(L, y, R, y) for y in ys.tolist()]
        if self.style == "grid":
            lines += [QtCore.QLineF(x, T, x, B) for x in xs.tolist()]
        p.drawLines(lines)


def _round(P, nd=2):
    return np.round(np.asarray(P, float), nd).tolist()


class Stroke(Item):
    color = Color("#1e1e1e", label="Цвет")
    width = Float(4, min=0.1, max=500, label="Толщина")
    opacity = Float(1, min=0.05, max=1, label="Непрозрачность")
    smoothing = Float(1, min=0, max=1, label="Сглаживание")

    def __init__(self, points=(), times=(), **kw):
        super().__init__(**kw)
        self.points = [tuple(map(float, q)) for q in points]
        self.times = list(map(float, times)) or [0.0] * len(self.points)
        self._t0 = None
        self._shown = None
        self._ver = 0
        self._cache = {}

    def append(self, pos: QPointF, t: float = None):
        if self._t0 is None:
            self._t0 = time.monotonic() - (self.times[-1] if self.times else 0)
        self.points.append((pos.x(), pos.y()))
        self.times.append(time.monotonic() - self._t0 if t is None else t)
        self._ver += 1
        tail = self.points[-4:]
        xs, ys = [q[0] for q in tail], [q[1] for q in tail]
        r = self.width / 2 + 1
        self.update(QRectF(min(xs) - r, min(ys) - r, max(xs) - min(xs) + 2 * r, max(ys) - min(ys) + 2 * r))

    def set_points(self, points, times=None):
        self.set_state({"points": points, "times": times})
        self.update()

    def _pts(self):
        return self.points if self._shown is None else self.points[: self._shown]

    def _cached(self, name, fn):
        key = (self._ver, self._shown, self.smoothing, self.width)
        hit = self._cache.get(name)
        if hit is None or hit[0] != key:
            hit = self._cache[name] = (key, fn())
        return hit[1]

    def path(self) -> QPainterPath:
        return self._cached("path", lambda: geom.smooth_path(self._pts(), self.smoothing))

    def bounds(self):
        def calc():
            if not self.points:
                return QRectF()
            P = np.asarray(self.points)
            (x0, y0), (x1, y1) = P.min(0), P.max(0)
            r = self.width / 2 + 1
            return QRectF(x0 - r, y0 - r, x1 - x0 + 2 * r, y1 - y0 + 2 * r)

        return self._cached("bounds", calc)

    def paint(self, p, zoom):
        pts = self._pts()
        if not pts:
            return
        if self.opacity < 1:
            p.setOpacity(p.opacity() * self.opacity)
        c = QColor(self.color)
        if len(pts) == 1:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawEllipse(QPointF(*pts[0]), self.width / 2, self.width / 2)
        else:
            p.setPen(
                QPen(c, self.width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            )
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(self.path())
        if self.opacity < 1:
            p.setOpacity(p.opacity() / self.opacity)

    def hit(self, pos, r=0.0):
        return (
            bool(self.points)
            and geom.polyline_dist(geom.arr(pos), np.asarray(self.points)) <= r + self.width / 2
        )

    def inside(self, path):
        n = sum(path.contains(QPointF(x, y)) for x, y in self.points[:: max(1, len(self.points) // 50)])
        return n * 2 > len(self.points[:: max(1, len(self.points) // 50)])

    def transform(self, t):
        self.points = [tuple(q) for q in geom.tf_points(t, self.points).tolist()]
        self._ver += 1
        self.width *= geom.tf_scale(t)

    def erase(self, a, b, r):
        if not self.points:
            return None
        pieces = geom.cut(np.asarray(self.points), np.asarray(self.times), a, b, r + self.width / 2)
        if pieces is None:
            return None
        return [type(self)(points=P, times=T, **self.settings.dump()) for P, T in pieces]

    def reveal(self, dt):
        shown = None if dt is None else int(np.searchsorted(self.times, dt, side="right"))
        if shown != self._shown:
            self._shown = shown
            self.update()

    def state(self):
        return {"points": _round(self.points), "times": _round(self.times, 3)}

    def set_state(self, d):
        if "points" in d:
            self.points = [tuple(q) for q in d["points"]]
            self.times = list(d.get("times") or [0.0] * len(self.points))
            self._ver += 1


class BoxItem(Item):
    x = Float(0, ui=False)
    y = Float(0, ui=False)
    w = Float(100, ui=False)
    h = Float(100, ui=False)
    angle = Float(0, ui=False)

    def local(self) -> QTransform:
        return QTransform().translate(self.x, self.y).rotate(self.angle)

    def box(self) -> QRectF:
        return QRectF(-self.w / 2, -self.h / 2, self.w, self.h)

    def bounds(self):
        return self.local().mapRect(self.box())

    def to_local(self, pos: QPointF) -> QPointF:
        return self.local().inverted()[0].map(pos)

    def hit(self, pos, r=0.0):
        return self.box().adjusted(-r, -r, r, r).contains(self.to_local(pos))

    def paint(self, p, zoom):
        p.save()
        p.setTransform(self.local(), True)
        self.paint_local(p, zoom)
        p.restore()

    def paint_local(self, p, zoom):
        p.drawRect(self.box())

    def scale_by(self, s):
        self.w, self.h = self.w * s, self.h * s

    def transform(self, t):
        c = t.map(QPointF(self.x, self.y))
        self.x, self.y = c.x(), c.y()
        self.angle = (self.angle + math.degrees(geom.tf_angle(t))) % 360
        self.scale_by(geom.tf_scale(t))


class Text(BoxItem):
    text = Str("", multiline=True, label="Текст")
    color = Color("#1e1e1e", label="Цвет")
    size = Float(32, min=4, max=400, label="Размер текста")
    font = Str("", label="Шрифт")
    bold = Bool(False, label="Полужирный")
    erasable = False
    BASE = 64  # Шрифт рассчитывается в этом размере, затем масштабируется независимо от разрешения

    def _font(self):
        f = QFont(self.font) if self.font else QFont()
        f.setPixelSize(self.BASE)
        f.setBold(self.bold)
        return f

    def box(self):
        r = QFontMetricsF(self._font()).boundingRect(QRectF(0, 0, 1e6, 1e6), 0, self.text or " ")
        k = self.size / self.BASE
        return QRectF(-r.width() * k / 2, -r.height() * k / 2, r.width() * k, r.height() * k)

    def paint_local(self, p, zoom):
        k = self.size / self.BASE
        b = self.box()
        p.scale(k, k)
        p.setFont(self._font())
        p.setPen(QColor(self.color))
        p.drawText(QRectF(b.x() / k, b.y() / k, b.width() / k, b.height() / k), 0, self.text)

    def scale_by(self, s):
        self.size *= s


class Image(BoxItem):
    erasable = False

    def __init__(self, image: QImage = None, **kw):
        super().__init__(**kw)
        self._img = image
        self._b64 = None
        if image is not None and "w" not in kw:
            self.w, self.h = image.width(), image.height()

    @classmethod
    def load(cls, path, **kw):
        return cls(QImage(path), **kw)

    def paint_local(self, p, zoom):
        if self._img is not None:
            p.drawImage(self.box(), self._img)

    def state(self):
        if self._b64 is None and self._img is not None:
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
            self._img.save(buf, "PNG")
            self._b64 = base64.b64encode(bytes(buf.data())).decode()
        return {"data": self._b64}

    def set_state(self, d):
        if d.get("data"):
            self._b64 = d["data"]
            self._img = QImage.fromData(base64.b64decode(self._b64))
