from __future__ import annotations

import math

from canvas import geom
from canvas.qt import QColor, QPen, QPointF, QRectF, Qt
from canvas.settings import Bool, Choice, Color, Float, Int, Str
from canvas import Tool
from .drawing import Brush
from physics import Body, Force, Rope, Spring


def _sim(tool):
    sim = tool.canvas.ext.get("physics")
    if sim is None:
        raise RuntimeError("create physics.Sim(canvas) first")
    return sim


class BodyTool(Brush):
    name = "body"
    shape = Choice(
        "line",
        ["line", "poly", "circle", "box"],
        label="Форма",
        choice_labels={"line": "Линия", "poly": "Многоугольник", "circle": "Круг", "box": "Прямоугольник"},
    )
    kind = Choice(
        "dynamic",
        ["dynamic", "static", "kinematic"],
        label="Тип тела",
        choice_labels={"dynamic": "Подвижное", "static": "Неподвижное", "kinematic": "С заданным движением"},
    )
    mass = Float(1, min=0.01, max=1000, label="Масса (кг)")
    friction = Float(0.6, min=0, max=2, label="Трение")
    elasticity = Float(0.3, min=0, max=1, label="Упругость")
    filled = Bool(True, label="Заливка")
    color = Color("#2d70b3", label="Цвет")
    width = Float(6, min=1, max=60, label="Толщина (пикс.)")

    def body_settings(self) -> dict:
        fill = QColor(self.color)
        fill.setAlpha(70 if self.filled else 0)
        return dict(
            kind=self.kind,
            mass=self.mass,
            friction=self.friction,
            elasticity=self.elasticity,
            color=self.color,
            fill=fill,
            width=self.px(self.width),
        )

    def _prim(self, a, b):
        if self.shape == "circle":
            return Body.circle(
                a.x(), a.y(), max(math.dist((a.x(), a.y()), (b.x(), b.y())), 1e-3), **self.body_settings()
            )
        r = QRectF(a, b).normalized()
        return Body.box(
            r.center().x(),
            r.center().y(),
            max(r.width(), 1e-3),
            max(r.height(), 1e-3),
            **self.body_settings(),
        )

    def press(self, p):
        if self.shape in ("line", "poly"):
            super().press(p)
        else:
            p.obj = self.canvas.draft(self._prim(p.pos, p.pos))

    def move(self, p):
        if self.shape in ("line", "poly"):
            return super().move(p)
        self.canvas.discard(p.obj)
        p.obj = self.canvas.draft(self._prim(p.start, p.pos))

    def release(self, p):
        c = self.canvas
        c.discard(p.obj)
        if p.moved < 4:
            return
        if self.shape in ("line", "poly"):
            p.obj.append(p.pos)
            body = Body.from_points(p.obj.points, closed=self.shape == "poly", **self.body_settings())
        else:
            body = p.obj
        c.add(body)

    def cancel(self, p):
        self.canvas.discard(p.obj)


class RopeTool(Tool):
    name = "rope"
    mode = Choice(
        "rope",
        ["rope", "spring"],
        label="Тип соединения",
        choice_labels={"rope": "Верёвка", "spring": "Пружина"},
    )
    segments = Int(16, min=2, max=80, label="Количество звеньев")
    mass = Float(0.2, min=0.001, max=100, label="Масса верёвки (кг)")
    k = Float(50, min=0.1, max=1e5, label="Жёсткость пружины (Н/м)")
    damping = Float(0.5, min=0, max=1000, label="Затухание пружины")
    slack = Float(0, min=0, max=2, label="Запас длины (м)")
    color = Color("#8a5a2b", label="Цвет")
    width = Float(3, min=0.5, max=30, label="Толщина (пикс.)")

    def settings_panel(self, parent=None):
        panel = super().settings_panel(parent)
        form = panel.layout()
        rows = {bound.name: index for index, bound in enumerate(b for b in self.settings if b.spec.ui)}

        def show_mode(mode):
            for name in ("segments", "mass", "color"):
                form.setRowVisible(rows[name], mode == "rope")
            for name in ("k", "damping"):
                form.setRowVisible(rows[name], mode == "spring")

        unbind = self.settings.mode.connect(show_mode)
        panel.destroyed.connect(lambda *_: unbind())
        show_mode(self.mode)
        return panel

    def anchor(self, pos):
        b = self.canvas.item_at(pos, self.px(8), Body)
        return (b.id, b.to_local(pos)) if b is not None else ("", (pos.x(), pos.y()))

    def press(self, p):
        p.end_a = self.anchor(p.pos)

    def move(self, p):
        self.canvas.update()

    def release(self, p):
        self.canvas.update()
        if p.moved < 10:
            return
        sim, (ida, pa), (idb, pb) = _sim(self), p.end_a, self.anchor(p.pos)
        d = math.dist((p.start.x(), p.start.y()), (p.pos.x(), p.pos.y())) / sim.scale
        if self.mode == "rope":
            it = Rope(
                a=ida,
                b=idb,
                pa=pa,
                pb=pb,
                length=d + self.slack,
                segments=self.segments,
                mass=self.mass,
                color=self.color,
                width=self.px(self.width),
            )
        else:
            it = Spring(
                a=ida,
                b=idb,
                pa=pa,
                pb=pb,
                rest=d + self.slack,
                k=self.k,
                damping=self.damping,
                width=self.px(self.width) * 0.7,
            )
        self.canvas.add(it)

    def paint(self, p):
        pen = QPen(QColor(self.color), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        for q in self.active():
            p.drawLine(q.start, q.pos)


class ForceTool(Tool):
    name = "force"
    frame = Choice(
        "world",
        ["world", "body"],
        label="Система отсчёта",
        choice_labels={"world": "Относительно доски", "body": "Относительно тела"},
    )
    color = Color("#c74440", label="Цвет")
    label = Str("Сила: {F:.1f} Н", label="Подпись")
    snap = Float(0.5, min=0, max=100, label="Шаг округления силы (Н)")

    def press(self, p):
        p.body = self.canvas.item_at(p.pos, self.px(8), Body)

    def move(self, p):
        self.canvas.update()

    def vector(self, p):
        k = _sim(self).f_scale
        fx, fy = (p.pos.x() - p.start.x()) / k, -(p.pos.y() - p.start.y()) / k
        if self.snap:
            fx, fy = round(fx / self.snap) * self.snap, round(fy / self.snap) * self.snap
        return fx, fy

    def release(self, p):
        self.canvas.update()
        if p.body is None or p.moved < 8:
            return
        fx, fy = self.vector(p)
        if self.frame == "body":
            c, s = math.cos(p.body.angle), math.sin(p.body.angle)
            fx, fy = fx * c - fy * s, fx * s + fy * c
        self.canvas.add(
            Force(
                body=p.body.id,
                pa=p.body.to_local(p.start),
                fx=fx,
                fy=fy,
                frame=self.frame,
                color=self.color,
                label=self.label,
            )
        )

    def paint(self, p):
        for q in self.active():
            if q.body is not None:
                c = QColor(self.color)
                p.setPen(QPen(c, self.px(2.5)))
                p.setBrush(c)
                geom.arrow(p, q.start, q.pos, self.px(12))


class GrabTool(Tool):
    name = "grab"
    cursor = Qt.CursorShape.PointingHandCursor
    autoplay = Bool(True, label="Запускать при захвате")

    def press(self, p):
        sim = _sim(self)
        b = self.canvas.item_at(p.pos, self.px(6), Body)
        p.key = None
        if b is not None and b.kind == "dynamic":
            if not sim.playing and self.autoplay:
                sim.play()
            p.body, p.key = b, sim.grab(b, p.pos)

    def move(self, p):
        if p.key is not None:
            _sim(self).drag(p.key, p.pos)

    def release(self, p):
        if p.key is not None:
            _sim(self).release(p.key)

    cancel = release

    def paint(self, p):
        pen = QPen(QColor("#1e1e1e"), 1.5, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        for q in self.active():
            if q.key is not None:
                p.drawLine(q.body.to_world(QPointF(*_sim(self).grabs[q.key][1])), q.pos)
