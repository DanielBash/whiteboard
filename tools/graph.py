import math

from canvas import Tool, Str
from canvas.qt import QColor, QPen, QRectF, Qt
from graph import Graph


class GraphTool(Tool):
    name = "graph"
    formula = Str("y = x^2", label="Формула нового графика")

    def __init__(self, **kw):
        super().__init__(**kw)
        self._edits = {}

    def press(self, p):
        c = self.canvas
        p.g = c.item_at(p.pos, 0, Graph)
        p.gp = p.pos
        if p.g is not None and c.editable(p.g) and p.g.id not in self._edits:
            self._edits[p.g.id] = c.begin_change(p.g)

    def move(self, p):
        g = p.g
        if g is None or g.id not in self._edits:
            self.canvas.update()
            return
        pts = [q for q in self.active() if q.g is g]
        if len(pts) >= 2:
            a, b = pts[:2]
            A0, B0, A1, B1 = (g.to_local(v) for v in (a.gp, b.gp, a.pos, b.pos))
            d0, d1 = B0 - A0, B1 - A1
            f = math.hypot(d1.x(), d1.y()) / max(math.hypot(d0.x(), d0.y()), 1e-9)
            m0, m1 = (A0 + B0) / 2, (A1 + B1) / 2
            g.zoom_at(f, m0)
            g.pan_px(m1.x() - m0.x(), m1.y() - m0.y())
        else:
            d = g.to_local(p.pos) - g.to_local(p.gp)
            g.pan_px(d.x(), d.y())
        for q in pts:
            q.gp = q.pos
        g.update()

    def release(self, p):
        c = self.canvas
        if p.g is None:
            r = QRectF(p.start, p.pos).normalized()
            if p.moved > 20 and r.width() > 1e-6 and r.height() > 1e-6:
                g = Graph(x=r.center().x(), y=r.center().y(), w=r.width(), h=r.height(), unit=r.width() / 16)
                if self.formula:
                    g.add(self.formula)
                c.add(g)
            c.update()
        elif not [q for q in self.active() if q.g is p.g]:
            tok = self._edits.pop(p.g.id, None)
            if tok is not None:
                c.end_change(tok)

    cancel = release

    def wheel(self, ev):
        c = self.canvas
        g = c.item_at(c.to_world(ev.position()), 0, Graph)
        if g is None or not c.editable(g):
            return False
        with c.change(g):
            g.zoom_at(1.0015 ** ev.angleDelta().y(), g.to_local(c.to_world(ev.position())))
        return True

    def paint(self, p):
        for q in self.active():
            if q.g is None and q.moved > 20:
                pen = QPen(QColor("#2d70b3"), 1.2, Qt.PenStyle.DashLine)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(QRectF(q.start, q.pos).normalized())
