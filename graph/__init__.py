from __future__ import annotations

import contextlib
import math

import numpy as np

from canvas.items import BoxItem
from canvas.qt import QColor, QImage, QLineF, QPainter, QPen, QPointF, QPolygonF, QRectF, Qt
from canvas.settings import Bool, Choice, Color, Float, HasSettings, Str
from . import engine
from .parser import parse

_STYLE = {"solid": Qt.PenStyle.SolidLine, "dashed": Qt.PenStyle.DashLine, "dotted": Qt.PenStyle.DotLine}


class Expr(HasSettings):
    text = Str("", label="Текст")
    color = Color("#c74440", label="Цвет")
    width = Float(2.5, min=0.5, max=12, label="Толщина")
    style = Choice(
        "solid",
        list(_STYLE),
        label="Стиль",
        choice_labels={"solid": "Сплошная", "dashed": "Штриховая", "dotted": "Пунктирная"},
    )
    visible = Bool(True, label="Показывать")
    fill = Float(0.25, min=0, max=1, label="Непрозрачность заливки")
    point_size = Float(8, min=2, max=30, label="Размер точек")
    domain = Str("", label="Диапазон параметра")

    def __init__(self, text="", table=None, **kw):
        super().__init__(text=text, **kw)
        self.table = table
        self.row = engine.Row()

    kind = property(lambda self: self.row.kind)
    error = property(lambda self: self.row.error)
    value = property(lambda self: self.row.value)
    fit = property(lambda self: self.row.fit)

    def to_dict(self):
        return {**self.settings.dump(), **({"table": self.table} if self.table else {})}


def _nice(v):
    e = math.floor(math.log10(v))
    f = v / 10**e
    return (1 if f <= 1 else 2 if f <= 2 else 5 if f <= 5 else 10) * 10**e


def _fmt(v):
    return f"{v:.10g}"


class Graph(BoxItem):
    w = Float(640, ui=False)
    h = Float(420, ui=False)
    ox = Float(0, ui=False)  # Математические координаты центра рамки
    oy = Float(0, ui=False)
    unit = Float(40, ui=False)  # Пикселей рамки на математическую единицу
    grid = Bool(True, label="Сетка")
    axes = Bool(True, label="Оси координат")
    labels = Bool(True, label="Подписи осей")
    degrees = Bool(False, label="Углы в градусах")
    bg = Color("#ffffff", label="Цвет фона")
    border = Color("#c9ced6", label="Цвет рамки")
    quality = Float(1, min=0.25, max=3, ui=False, label="Качество построения")
    erasable = False
    COLORS = ["#c74440", "#2d70b3", "#388c46", "#6042a6", "#fa7e19", "#000000"]

    def __init__(self, *texts, **kw):
        self.exprs = []
        self._ver, self._an, self._geo = 0, None, (None, None)
        super().__init__(**kw)
        for t in texts:
            self.add(t)

    # --- Выражения
    @contextlib.contextmanager
    def _edit(self):
        c = self.canvas
        with c.change(self) if c is not None and c.find(self.id) is self else contextlib.nullcontext():
            yield
        self.update()

    def add(self, text="", table=None, **kw) -> Expr:
        kw.setdefault("color", self.COLORS[len(self.exprs) % len(self.COLORS)])
        e = Expr(text, table, **kw)
        with self._edit():
            e.watch(self._on_expr)
            self.exprs.append(e)
            self._changed()
        return e

    def table(self, **cols) -> Expr:
        return self.add(table={k: [float(v) for v in vals] for k, vals in cols.items()})

    def remove(self, e: Expr):
        with self._edit():
            e.unwatch(self._on_expr)
            self.exprs.remove(e)
            self._changed()

    def set_texts(self, texts):
        with self._edit():
            for e, t in zip(self.exprs, texts):
                e._values["text"] = t
            for e in self.exprs[len(texts) :]:
                e.unwatch(self._on_expr)
            del self.exprs[len(texts) :]
            for t in texts[len(self.exprs) :]:
                e = Expr(t, color=self.COLORS[len(self.exprs) % len(self.COLORS)])
                e.watch(self._on_expr)
                self.exprs.append(e)
            self._changed()

    def _on_expr(self, e, key, old, new):
        if e in self.exprs:
            if key in ("text", "domain"):
                self._changed()
            self._geo = (None, None)
            self._emit(f"exprs.{self.exprs.index(e)}.{key}", old, new)

    def _changed(self):
        self._ver += 1
        self._an = None
        self._geo = (None, None)
        rows, *_, params = self.analysis()
        for name, v in params.items():
            if name not in self.settings:
                self.settings.add(name, Float(v, min=min(-10.0, v), max=max(10.0, v), step=0.1, label=name))
        for r in rows:
            if r.kind == "slider":
                self._values[r.name] = float(r.value)
        for name in [n for n in self._extra if n not in params]:
            self.settings.remove(name)

    def _emit(self, key, old, new):
        if key in self._extra:  # Ползунок изменён: синхронизировать текст соответствующей строки вида 'a = 3'
            for e in self.exprs:
                if e.row.kind == "slider" and e.row.name == key:
                    e._values["text"] = f"{key} = {_fmt(new)}"
                    e.row.value = new
        super()._emit(key, old, new)

    def analysis(self):
        if self._an is None:
            self._an = engine.analyse([e.text for e in self.exprs], [e.table for e in self.exprs])
            for e, r in zip(self.exprs, self._an[0]):
                e.row = r
        return self._an

    def params(self) -> dict:
        return {n: self.get(n) for n in self._extra}

    def scope(self) -> engine.Scope:
        rows, defs, funcs, data, _ = self.analysis()
        with np.errstate(all="ignore"):
            return engine.compute(rows, defs, funcs, data, self.params(), self.degrees)

    def evaluate(self, text: str):
        st = parse(text)
        with np.errstate(all="ignore"):
            v = engine.Ev(self.scope()).ev(st[1] if st[0] == "expr" else st[-1])
        return v if isinstance(v, tuple) else float(v) if np.ndim(v) == 0 else np.asarray(v)

    # --- Преобразование математических координат в локальные пиксели рамки
    def view_rect(self):
        hw, hh = self.w / 2 / self.unit, self.h / 2 / self.unit
        return self.ox - hw, self.ox + hw, self.oy - hh, self.oy + hh

    def to_math(self, local: QPointF):
        return self.ox + local.x() / self.unit, self.oy - local.y() / self.unit

    def to_local_xy(self, x, y) -> QPointF:
        return QPointF((x - self.ox) * self.unit, -(y - self.oy) * self.unit)

    def pan_px(self, dx, dy):
        self.ox -= dx / self.unit
        self.oy += dy / self.unit

    def zoom_at(self, f, local: QPointF = QPointF()):
        mx, my = self.to_math(local)
        self.unit = min(max(self.unit * f, 1e-6), 1e9)
        self.ox, self.oy = mx - local.x() / self.unit, my + local.y() / self.unit

    def fit_math(self, x0, x1, y0, y1):
        self.unit = min(self.w / (x1 - x0), self.h / (y1 - y0))
        self.ox, self.oy = (x0 + x1) / 2, (y0 + y1) / 2

    def scale_by(self, s):
        super().scale_by(s)
        self.unit *= s

    # --- Геометрия и рисование
    def geometry(self, zoom=1.0):
        zb = 2 ** (round(math.log2(max(zoom, 1e-9)) * 2) / 2)
        key = (
            self._ver,
            tuple(self.params().items()),
            self.ox,
            self.oy,
            self.unit,
            self.w,
            self.h,
            self.degrees,
            self.quality,
            zb,
        )
        if self._geo[0] == key:
            return self._geo[1]
        rows = self.analysis()[0]
        try:
            scope = self.scope()
        except Exception:
            scope = engine.Scope()
        view = self.view_rect()
        px = (self.w * zb, self.h * zb)
        u, ox, oy = self.unit, self.ox, self.oy
        loc = lambda P: QPolygonF(
            [QPointF(x, y) for x, y in np.column_stack([(P[:, 0] - ox) * u, -(P[:, 1] - oy) * u]).tolist()]
        )
        out = []
        for e, r in zip(self.exprs, rows):
            try:
                with np.errstate(all="ignore"):
                    prims = engine.geometry(r, scope, view, px, self.quality, e.domain)
            except Exception as ex:
                r.error = engine.error_message(ex)
                continue
            conv = []
            for pr in prims:
                k, data, flag = pr[0], pr[1], (pr[2] if len(pr) > 2 else False)
                if k in ("lines", "fill"):
                    conv.append((k, [loc(P) for P in data if len(P)], flag))
                elif k == "dots":
                    data = data[np.isfinite(data).all(1)]
                    conv.append((k, loc(data), flag))
                elif k == "segs":
                    S = np.stack([(data[..., 0] - ox) * u, -(data[..., 1] - oy) * u], -1).reshape(-1, 4)
                    conv.append((k, [QLineF(*s) for s in S.tolist()], flag))
                elif k == "mask":
                    x0, x1, y0, y1 = pr[2]
                    conv.append(
                        (k, data, QRectF((x0 - ox) * u, -(y1 - oy) * u, (x1 - x0) * u, (y1 - y0) * u))
                    )
            out.append((e, conv))
        self._geo = (key, out)
        return out

    def paint_local(self, p, zoom):
        box = self.box()
        p.fillRect(box, QColor(self.bg))
        p.save()
        p.setClipRect(box, Qt.ClipOperation.IntersectClip)
        self._paint_grid(p)
        for e, prims in self.geometry(zoom):
            if not e.visible:
                continue
            c = QColor(e.color)
            for k, data, flag in prims:
                if k == "fill" or k == "mask":
                    fc = QColor(c)
                    fc.setAlphaF(e.fill)
                    if k == "fill":
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(fc)
                        for poly in data:
                            p.drawPolygon(poly)
                    else:
                        m = np.ascontiguousarray(data[::-1])
                        rgba = np.zeros(m.shape + (4,), np.uint8)
                        rgba[m] = (fc.red(), fc.green(), fc.blue(), round(255 * e.fill))
                        img = QImage(
                            rgba.tobytes(),
                            m.shape[1],
                            m.shape[0],
                            m.shape[1] * 4,
                            QImage.Format.Format_RGBA8888,
                        ).copy()
                        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                        p.drawImage(flag, img)
                    continue
                pen = QPen(c, e.point_size if k == "dots" else e.width)
                pen.setStyle(Qt.PenStyle.DashLine if flag else _STYLE[e.style])
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if k == "lines":
                    for poly in data:
                        p.drawPolyline(poly)
                elif k == "segs":
                    p.drawLines(data)
                elif k == "dots":
                    p.drawPoints(data)
        p.restore()
        pen = QPen(QColor(self.border), 1)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(box)

    def _paint_grid(self, p):
        x0, x1, y0, y1 = self.view_rect()
        step = _nice(90 / self.unit)
        minor = step / (4 if round(step / 10 ** math.floor(math.log10(step))) == 2 else 5)
        hw, hh = self.w / 2, self.h / 2

        def lines(s, color):
            pen = QPen(QColor(color), 1)
            pen.setCosmetic(True)
            p.setPen(pen)
            xs = np.arange(math.ceil(x0 / s), math.floor(x1 / s) + 1) * s
            ys = np.arange(math.ceil(y0 / s), math.floor(y1 / s) + 1) * s
            if len(xs) + len(ys) < 2000:
                p.drawLines(
                    [QLineF((x - self.ox) * self.unit, -hh, (x - self.ox) * self.unit, hh) for x in xs]
                    + [QLineF(-hw, -(y - self.oy) * self.unit, hw, -(y - self.oy) * self.unit) for y in ys]
                )
            return xs, ys

        if self.grid:
            lines(minor, "#eef0f3")
            xs, ys = lines(step, "#d9dde3")
        else:
            xs = np.arange(math.ceil(x0 / step), math.floor(x1 / step) + 1) * step
            ys = np.arange(math.ceil(y0 / step), math.floor(y1 / step) + 1) * step
        if not self.axes:
            return
        o = self.to_local_xy(0, 0)
        ax, ay = min(max(o.x(), -hw), hw), min(max(o.y(), -hh), hh)
        pen = QPen(QColor("#4a4f57"), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QLineF(-hw, ay, hw, ay))
        p.drawLine(QLineF(ax, -hh, ax, hh))
        if not self.labels:
            return
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor("#4a4f57"))
        for x in xs:
            if abs(x) > step / 2:
                lx = (x - self.ox) * self.unit
                p.drawText(
                    QRectF(lx - 40, min(ay + 3, hh - 16), 80, 14),
                    Qt.AlignmentFlag.AlignHCenter,
                    _fmt(round(x / step) * step),
                )
        for y in ys:
            if abs(y) > step / 2:
                ly = -(y - self.oy) * self.unit
                p.drawText(
                    QRectF(max(ax - 84, -hw + 2), ly - 7, 80, 14),
                    Qt.AlignmentFlag.AlignRight,
                    _fmt(round(y / step) * step),
                )

    # --- Сохранение и загрузка
    def state(self):
        return {
            "exprs": [e.to_dict() for e in self.exprs],
            "params": {n: [self.get(n), s.min, s.max, s.step] for n, s in self._extra.items()},
        }

    def set_state(self, d):
        if "exprs" in d:
            for e in self.exprs:
                e.unwatch(self._on_expr)
            self.exprs = []
            for ed in d["exprs"]:
                ed = dict(ed)
                e = Expr(table=ed.pop("table", None))
                e.settings.load(ed)
                e.watch(self._on_expr)
                self.exprs.append(e)
        if "params" in d:
            for n in list(self._extra):
                self.settings.remove(n)
            for n, (v, lo, hi, st) in d["params"].items():
                self.settings.add(n, Float(v, min=lo, max=hi, step=st, label=n))
                self._values[n] = float(v)
        self._changed()
