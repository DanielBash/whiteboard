from __future__ import annotations

import math


from canvas import geom
from canvas.items import Stroke
from canvas.qt import QColor, QPainterPath, QPen, QPointF, QRectF, Qt
from canvas.settings import Choice, Color, Float


from canvas import Tool


class Pan(Tool):
    name = "pan"
    cursor = Qt.CursorShape.OpenHandCursor
    nav_fingers = 1


class Brush(Tool):
    name = "brush"
    cursor = Qt.CursorShape.CrossCursor
    color = Color("#1e1e1e", label="Цвет")
    width = Float(4, min=0.5, max=80, label="Толщина (пикс.)")
    opacity = Float(1, min=0.05, max=1, label="Непрозрачность")
    smoothing = Float(1, min=0, max=1, label="Сглаживание")
    spacing = Float(2.5, min=0, max=30, ui=False, label="Расстояние между точками (пикс.)")

    def make(self, p) -> Stroke:
        return Stroke(
            color=self.color, width=self.px(self.width), opacity=self.opacity, smoothing=self.smoothing
        )

    def press(self, p):
        p.obj = self.canvas.draft(self.make(p))
        p.obj.append(p.pos)
        p.added = p.screen

    def move(self, p):
        d = p.screen - p.added
        if math.hypot(d.x(), d.y()) >= self.spacing:
            p.obj.append(p.pos)
            p.added = p.screen

    def release(self, p):
        if p.screen != p.added:
            p.obj.append(p.pos)
        self.canvas.commit(p.obj)

    def cancel(self, p):
        self.canvas.discard(p.obj)


class Eraser(Tool):
    name = "eraser"
    cursor = Qt.CursorShape.BlankCursor
    radius = Float(16, min=2, max=200, label="Радиус (пикс.)")

    def __init__(self, **kw):
        super().__init__(**kw)
        self._at = {}

    def erase(self, a: QPointF, b: QPointF):
        c, r = self.canvas, self.px(self.radius)
        A, B = geom.arr(a), geom.arr(b)
        for it in c.items_in(QRectF(a, b).normalized().adjusted(-r, -r, r, r)):
            if (
                it.erasable and c.find(it.id) is it and c.editable(it)
            ):  # Объект уже мог быть удалён вместе с телом
                pieces = it.erase(A, B, r)
                if pieces is not None:
                    c.replace(it, pieces)

    def _cursor(self, p, on=True):
        old = self._at.pop(p.id, None)
        r = self.px(self.radius) + self.px(2)
        for q in (old, p.pos if on else None):
            if q is not None:
                self.canvas.update_world(QRectF(q.x() - r, q.y() - r, 2 * r, 2 * r))
        if on:
            self._at[p.id] = p.pos

    def press(self, p):
        self.erase(p.pos, p.pos)
        self._cursor(p)

    def move(self, p):
        self.erase(p.last, p.pos)
        self._cursor(p)

    def release(self, p):
        self._cursor(p, on=p.kind == "mouse")

    hover = _cursor

    def deactivate(self):
        for pid in list(self._at):
            self._at.pop(pid)
        self.canvas.update()

    def paint(self, p):
        pen = QPen(QColor(90, 90, 90, 200), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(QColor(255, 255, 255, 90))
        r = self.px(self.radius)
        for q in self._at.values():
            p.drawEllipse(q, r, r)


class Select(Tool):
    name = "select"
    mode = Choice(
        "lasso",
        ["lasso", "rect"],
        label="Способ выделения",
        choice_labels={"lasso": "Лассо", "rect": "Прямоугольник"},
    )
    HANDLE = 14  # Пиксели

    def __init__(self, **kw):
        super().__init__(**kw)
        self._edit = None

    def bounds(self) -> QRectF:
        r = QRectF()
        for it in self.canvas.selection:
            r = r.united(it.bounds())
        return r

    def _handle(self):
        b = self.bounds()
        return None if b.isNull() else b.topRight() + QPointF(self.px(self.HANDLE), -self.px(self.HANDLE))

    def _delete_handle(self, handle):
        return handle + QPointF(self.px(2 * self.HANDLE), 0)

    def press(self, p):
        c = self.canvas
        h = self._handle()
        p.tp = p.pos
        if h is not None and (p.pos - self._delete_handle(h)).manhattanLength() <= self.px(self.HANDLE):
            p.mode = "delete"
            return
        if h is not None and math.hypot(*(geom.arr(c.to_screen(h)) - geom.arr(p.screen))) < self.HANDLE * 1.6:
            p.mode = "handle"
        elif c.selection and self.bounds().contains(p.pos):
            p.mode = "move"
        else:
            hit = next((it for it in c.items_at(p.pos, self.px(6)) if it.selectable and c.editable(it)), None)
            if hit is None:
                p.mode, p.path = "lasso", [p.pos]
                if not [q for q in self.active() if q is not p]:
                    c.select([])
                return
            c.select([hit])
            p.mode = "move"
        if self._edit is None:
            self._edit = c.begin_change(*c.selection)
            c.set_live(c.selection)

    def move(self, p):
        c = self.canvas
        if p.mode == "delete":
            return
        if p.mode == "lasso":
            p.path.append(p.pos)
            c.update()
            return
        if p.mode == "handle":
            ctr = self.bounds().center()
            t = geom.similarity(ctr, p.tp, ctr, p.pos)
        else:
            movers = [q for q in self.active() if getattr(q, "mode", None) == "move"]
            if len(movers) >= 2:
                a, b = movers[:2]
                t = geom.similarity(a.tp, b.tp, a.pos, b.pos)
            else:
                d = p.pos - p.tp
                t = geom.QTransform.fromTranslate(d.x(), d.y())
        for q in self.active():
            q.tp = q.pos
        p.tp = p.pos
        for it in c.selection:
            it.transform(t)
            it.update()
        c.update()

    def release(self, p):
        c = self.canvas
        if p.mode == "delete":
            if p.moved < 6:
                self.delete()
            return
        if p.mode == "lasso":
            if p.moved > 6:
                path = QPainterPath(p.path[0])
                if self.mode == "rect":
                    path.addRect(QRectF(p.start, p.pos).normalized())
                else:
                    for q in p.path[1:]:
                        path.lineTo(q)
                    path.closeSubpath()
                c.select([it for it in c.items if it.selectable and c.editable(it) and it.inside(path)])
            c.update()
        elif self._edit is not None and not [q for q in self.active() if q is not p and q.mode != "lasso"]:
            items = [it for it, _ in self._edit]
            c.set_live(items, False)
            c.end_change(self._edit)
            self._edit = None

    def cancel(self, p):
        if p.mode != "delete":
            self.release(p)

    def delete(self):
        c = self.canvas
        c.remove(*c.selection)

    def key(self, ev):
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete()
            return True
        return False

    def deactivate(self):
        self.canvas.select([])

    def paint(self, p):
        pen = QPen(QColor("#2d70b3"), 1.2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for q in self.active():
            if getattr(q, "mode", None) == "lasso" and len(q.path) > 1:
                if self.mode == "rect":
                    p.drawRect(QRectF(q.start, q.pos).normalized())
                else:
                    p.drawPolyline(q.path)
        b = self.bounds()
        if not b.isNull():
            p.drawRect(b)
            h = self._handle()
            p.setBrush(QColor("#2d70b3"))
            p.drawLine(b.topRight(), h)
            p.drawEllipse(h, self.px(self.HANDLE / 2), self.px(self.HANDLE / 2))
            center = self._delete_handle(h)
            radius = self.px(self.HANDLE / 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#ad3535"))
            p.drawEllipse(center, radius, radius)
            pen = QPen(QColor("white"))
            pen.setCosmetic(True)
            p.setPen(pen)
            for sign in (-1, 1):
                offset = QPointF(radius / 2, sign * radius / 2)
                p.drawLine(center - offset, center + offset)


class LineTool(Tool):
    name = "line"
    color = Color("#c74440", label="Цвет")
    width = Float(4, min=1, max=40, label="Толщина (пикс.)")

    def press(self, p):
        a = (p.pos.x(), p.pos.y())
        p.obj = self.canvas.draft(
            Stroke(points=[a, a], color=self.color, width=self.px(self.width), smoothing=0)
        )

    def move(self, p):
        p.obj.set_points([p.obj.points[0], (p.pos.x(), p.pos.y())])

    def release(self, p):
        self.canvas.commit(p.obj)

    def cancel(self, p):
        self.canvas.discard(p.obj)
