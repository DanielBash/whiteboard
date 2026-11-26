from __future__ import annotations

import copy
import gzip
import json
import math
import os
import time
import uuid
from contextlib import contextmanager

from . import geom
from .items import Grid, Layer, Model
from .qt import (
    QtCore,
    QtGui,
    QtWidgets,
    QColor,
    QEvent,
    QImage,
    QPainter,
    QPointF,
    QRectF,
    QTimer,
    QTransform,
    Qt,
    Signal,
    svg_generator,
)
from .timeline import Timeline

_T = QEvent.Type
_TOUCH = {_T.TouchBegin, _T.TouchUpdate, _T.TouchEnd, _T.TouchCancel}
_MOUSE = {_T.MouseButtonPress, _T.MouseButtonDblClick, _T.MouseMove, _T.MouseButtonRelease}
_TABLET = {_T.TabletPress, _T.TabletMove, _T.TabletRelease}
_PS = QtGui.QEventPoint.State
_DEV = QtGui.QInputDevice.DeviceType


def _merge(rects):
    area = lambda r: r.width() * r.height()
    out = []
    for r in sorted(rects, key=area, reverse=True):
        for i, o in enumerate(out):
            if area(o.united(r)) <= (area(o) + area(r)) * 1.4:
                out[i] = o.united(r)
                break
        else:
            out.append(r)
    return out if len(out) <= 8 else [_union(out)]


def _union(rects):
    u = rects[0]
    for r in rects[1:]:
        u = u.united(r)
    return u


class Pointer:
    def __init__(
        self, id, kind, screen: QPointF, pos: QPointF, pressure=1.0, button=None, size=None, event=None
    ):
        self.id, self.kind = id, kind  # Тип контакта: мышь, касание, перо или обратный конец пера
        self.screen = self.start_screen = screen  # Координаты виджета
        self.pos = self.start = self.last = pos  # Мировые координаты
        self.pressure, self.button, self.size, self.event = pressure, button, size, event
        self.t0 = self.time = time.monotonic()
        self.moved = 0.0  # Максимальное расстояние от начала в экранных пикселях для распознавания касания
        self.tool = None
        self.nav = False  # Истина, пока указатель перемещает или масштабирует вид
        self.group = None  # Шаг отмены в истории для этого жеста
        self.nav_prev = screen

    def __repr__(self):
        return f"<Pointer {self.kind}#{self.id} {self.pos.x():.0f},{self.pos.y():.0f}>"


class Canvas(QtWidgets.QWidget):
    itemAdded = Signal(object)
    itemRemoved = Signal(object)
    itemChanged = Signal(object, str)  # Пустой ключ означает изменение геометрии или состояния
    selectionChanged = Signal()
    toolChanged = Signal(object)
    viewChanged = Signal()
    timelineChanged = Signal()
    reset = Signal()  # Доска заменена при загрузке или восстановлении
    aboutToChange = Signal(list)  # Начало прямого редактирования объектов через begin_change()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tool, self.pointers = None, {}
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMouseTracking(True)
        self.setTabletTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.items, self.drafts, self.selection = [], [], []
        self.live = set()  # Объекты, которые перерисовываются каждый кадр вместо кеширования
        self.layers = []
        self.filters = []  # Фильтры завершения: функция принимает объект и возвращает объект, список объектов или None
        self.overlays = []  # Функция от рисовальщика и холста; вызывается каждый кадр в мировых координатах
        self.ext = {}  # Расширения, сохраняемые с доской: имя и объект с методами to_dict()/load(d)
        self.meta = {}  # Произвольные данные приложения, сохраняемые с доской
        self.pen_eraser = None  # Инструмент для обратного конца пера
        self.view = QTransform()  # Преобразование мировых координат в координаты виджета
        self._inv = QTransform()
        self.min_zoom, self.max_zoom = 0.02, 50.0
        self.limits = None  # Прямоугольник QRectF, ограничивающий перемещение вида в мировых координатах
        self.nav_rotate = False  # Жест двумя пальцами может поворачивать вид
        self.nav_debounce = (
            150  # Время растяжения кеша при навигации в мс; ноль означает перестроение каждого кадра
        )
        self._by_id, self._bounds, self._draft_t = {}, {}, {}
        self._deps = {}  # Идентификатор и прикреплённые объекты через Item.refs для совместной перерисовки и удаления
        self._silent = 0
        self._bulk = False  # При загрузке пропускать обновление кеша для отдельных объектов
        self._cache, self._cache_view, self._stale = None, QTransform(), False
        self._dirty, self._full = [], True  # Мировые области для перестроения или признак полной перерисовки
        self._nav_timer = QTimer(self)
        self._nav_timer.setSingleShot(True)
        self._nav_timer.timeout.connect(self._nav_settled)
        self.timeline = Timeline(self)
        self._background = None
        self.background = Grid()
        self.layer("main")

    # --- Объекты
    def find(self, id):
        return self._by_id.get(id)

    def layer(self, name) -> Layer:
        for l in self.layers:
            if l.name == name:
                return l
        l = Layer(name=name)
        l.watch(lambda *a: self.invalidate())
        self.layers.append(l)
        return l

    def editable(self, it) -> bool:
        l = self.layer(it.layer)
        return l.visible and not l.locked

    def draft(self, it):
        it.canvas = self
        self.drafts.append(it)
        self._draft_t[it.id] = self.timeline.now()
        self._item_update(it)
        return it

    def discard(self, it):
        if it in self.drafts:
            self.drafts.remove(it)
            self._draft_t.pop(it.id, None)
            self._screen_update(self._bounds.pop(it.id, None) or it.bounds())
            it.canvas = None

    def commit(self, it) -> list:
        t0 = self._draft_t.pop(it.id, None)
        if it in self.drafts:
            self.drafts.remove(it)
            self._screen_update(self._bounds.pop(it.id, None) or it.bounds())
        out = [it]
        for f in self.filters:
            nxt = []
            for x in out:
                r = f(x)
                nxt += [] if r is None else list(r) if isinstance(r, (list, tuple)) else [r]
            out = nxt
        with self.batch():
            for x in out:
                self._insert(x, t0=t0)
        return out

    def add(self, *items):
        with self.batch():
            for it in items:
                self._insert(it)
        return items[0] if len(items) == 1 else list(items)

    def remove(self, *items):
        with self.batch():
            todo = list(items)
            while todo:
                it = todo.pop(0)
                if it.id in self._by_id:
                    i = self._remove_raw(it)
                    self.timeline.record({"k": "remove", "id": it.id, "i": i})
                    todo += list(self._deps.get(it.id, ()))

    def replace(self, old, new: list):
        if self.find(old.id) is not old:
            return
        i = self.items.index(old)
        with self.batch():
            self.remove(old)
            for k, it in enumerate(new):
                self._insert(it, index=i + k)

    def clear(self):
        self.remove(*self.items)

    def reorder(self, it, index):
        old = self.items.index(it)
        index = max(0, min(index, len(self.items) - 1))
        if old != index:
            self._reorder_raw(it, index)
            self.timeline.record({"k": "order", "id": it.id, "old": old, "new": index})

    def raise_(self, *items):
        with self.batch():
            for it in items:
                self.reorder(it, len(self.items) - 1)

    def lower(self, *items):
        with self.batch():
            for it in reversed(items):
                self.reorder(it, 0)

    def group(self, items):
        gid = uuid.uuid4().hex[:8]
        with self.batch():
            for it in items:
                it.group = gid

    def ungroup(self, items):
        with self.batch():
            for it in items:
                it.group = ""

    def grouped(self, items) -> list:
        gids = {it.group for it in items if it.group}
        return list(items) + [it for it in self.items if it.group in gids and it not in items]

    def select(self, items):
        items = self.grouped(items)
        if items != self.selection:
            self.selection = items
            self.selectionChanged.emit()
            self.update()

    # --- Запись изменений
    @contextmanager
    def batch(self):
        tl = self.timeline
        outer = tl._group
        if outer is None:
            tl._group = tl.new_group()
        try:
            yield
        finally:
            if outer is None:
                tl._group = None

    @contextmanager
    def silent(self):
        self._silent += 1
        try:
            yield
        finally:
            self._silent -= 1

    def begin_change(self, *items):
        self.aboutToChange.emit(list(items))
        self.timeline.prepare()
        for it in items:
            it._mute += 1
        return [(it, it.snapshot()) for it in items]

    def end_change(self, token):
        with self.batch():
            for it, before in token:
                it._mute -= 1
                after = it.snapshot()
                if after != before and it.id in self._by_id:
                    self.timeline.record({"k": "state", "id": it.id, "before": before, "after": after})
                    self.itemChanged.emit(it, "")
                it.update()

    @contextmanager
    def change(self, *items):
        tok = self.begin_change(*items)
        try:
            yield
        finally:
            self.end_change(tok)

    def undo(self):
        self.timeline.undo()

    def redo(self):
        self.timeline.redo()

    # --- Прямые изменения без записи в историю
    def _insert(self, it, index=None, t0=None):
        self._add_raw(it, index)
        op = {"k": "add", "id": it.id, "i": self.items.index(it), "d": it.to_dict()}
        if t0 is not None:
            op["t"], op["dur"] = t0, round(self.timeline.now() - t0, 3)
        self.timeline.record(op)

    def _add_raw(self, it, index=None):
        it.canvas = self
        layer = self.layer(it.layer)
        self.items.insert(len(self.items) if index is None else index, it)
        self._by_id[it.id] = it
        for r in it.refs():
            self._deps.setdefault(r, []).append(it)
        b = self._bounds[it.id] = it.bounds()
        if self._bulk:
            pass
        elif (
            self.items[-1] is it
            and self.layers[-1] is layer
            and layer.visible
            and it not in self.live
            and self._cache is not None
            and not self._stale
            and not self._full
        ):
            p = QPainter(self._cache)  # Верхний объект: рисовать прямо в кеш без полного перестроения
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setTransform(self.view)
            p.setOpacity(layer.opacity)
            self._paint_item(p, it, self.zoom)
            p.end()
            self._screen_update(b)
        else:
            self._invalidate_world(b)
        self.itemAdded.emit(it)

    def _remove_raw(self, it) -> int:
        i = self.items.index(it)
        del self.items[i]
        self._by_id.pop(it.id, None)
        for r in it.refs():
            if it in self._deps.get(r, ()):
                self._deps[r].remove(it)
        self._invalidate_world(self._bounds.pop(it.id, None) or it.bounds())
        self.live.discard(it)
        if it in self.selection:
            self.selection.remove(it)
            self.selectionChanged.emit()
        it.canvas = None
        self.itemRemoved.emit(it)
        return i

    def _reorder_raw(self, it, index):
        self.items.remove(it)
        self.items.insert(index, it)
        self._invalidate_world(it.bounds())

    def _on_item_set(self, it, key, old, new):
        if it.id in self._by_id:
            if not it._mute:
                self.timeline.record({"k": "set", "id": it.id, "key": key, "old": old, "new": new})
            if key == "layer":
                self.layer(new)
                self.invalidate()
            if key in ("body", "a", "b"):  # Привязка изменилась: обновить индекс зависимых объектов
                for lst in self._deps.values():
                    if it in lst:
                        lst.remove(it)
                for r in it.refs():
                    self._deps.setdefault(r, []).append(it)
            self.itemChanged.emit(it, key)
        it.update()

    def _item_update(self, it, rect=None, _seen=None):
        if it.id not in self._by_id and it not in self.drafts:
            return
        for d in self._deps.get(
            it.id, ()
        ):  # Прикреплённые объекты, например силы и концы верёвок, перемещаются вместе с основным
            if _seen is None or d not in _seen:
                self._item_update(d, None, (_seen or set()) | {it})
        old, new = self._bounds.get(it.id), it.bounds()
        self._bounds[it.id] = new
        r = rect if rect is not None else new.united(old) if old is not None else new
        if it.id in self._by_id and it not in self.live:
            self._invalidate_world(r)
        else:
            self._screen_update(r)

    def set_live(self, items, on=True):
        for it in items:
            if (it in self.live) != on:
                (self.live.add if on else self.live.discard)(it)
                self._invalidate_world(self._bounds.get(it.id))
                self._item_update(it)

    # --- Запросы
    def items_at(self, pos: QPointF, r=0.0) -> list:
        vis = {l.name for l in self.layers if l.visible}
        bb = self._bounds
        return [
            it
            for it in reversed(self.items)
            if it.layer in vis and bb[it.id].adjusted(-r, -r, r, r).contains(pos) and it.hit(pos, r)
        ]

    def item_at(self, pos, r=0.0, cls=None):
        return next((it for it in self.items_at(pos, r) if cls is None or isinstance(it, cls)), None)

    def items_in(self, rect: QRectF) -> list:
        return [it for it in self.items if self._bounds[it.id].intersects(rect)]

    def content_rect(self) -> QRectF:
        r = QRectF()
        for it in self.items:
            r = r.united(self._bounds[it.id])
        return r

    # --- Вид
    @property
    def zoom(self) -> float:
        return geom.tf_scale(self.view)

    def to_world(self, spos) -> QPointF:
        return self._inv.map(QPointF(spos))

    def to_screen(self, pos) -> QPointF:
        return self.view.map(QPointF(pos))

    def visible_rect(self) -> QRectF:
        return self._inv.mapRect(QRectF(self.rect()))

    def set_view(self, t: QTransform):
        z = geom.tf_scale(t)
        zc = min(max(z, self.min_zoom), self.max_zoom)
        if z and abs(zc - z) > 1e-12:
            c = QPointF(self.width() / 2, self.height() / 2)
            t = t * QTransform().translate(c.x(), c.y()).scale(zc / z, zc / z).translate(-c.x(), -c.y())
        if self.limits is not None:
            vis = t.inverted()[0].mapRect(QRectF(self.rect()))
            lim = self.limits
            d = []
            for a0, a1, b0, b1 in (
                (vis.left(), vis.right(), lim.left(), lim.right()),
                (vis.top(), vis.bottom(), lim.top(), lim.bottom()),
            ):
                d.append((b0 + b1 - a0 - a1) / 2 if a1 - a0 > b1 - b0 else max(0, b0 - a0) + min(0, b1 - a1))
            t = QTransform.fromTranslate(-d[0], -d[1]) * t
        if t == self.view:
            return
        self.view, self._inv = QTransform(t), t.inverted()[0]
        if self._cache is not None:
            if self.nav_debounce > 0:
                self._stale = True
                self._nav_timer.start(self.nav_debounce)
            else:
                self._full = True
        self.update()
        self.viewChanged.emit()

    def pan(self, dx, dy):
        self.set_view(self.view * QTransform.fromTranslate(dx, dy))

    def zoom_at(self, f, spos=None):
        c = QPointF(spos) if spos is not None else QPointF(self.width() / 2, self.height() / 2)
        self.set_view(self.view * QTransform().translate(c.x(), c.y()).scale(f, f).translate(-c.x(), -c.y()))

    def rotate_at(self, degrees, spos=None):
        c = QPointF(spos) if spos is not None else QPointF(self.width() / 2, self.height() / 2)
        self.set_view(
            self.view * QTransform().translate(c.x(), c.y()).rotate(degrees).translate(-c.x(), -c.y())
        )

    def fit(self, rect: QRectF = None, margin=40):
        rect = rect or self.content_rect()
        if rect.isEmpty():
            return self.set_view(QTransform())
        s = min((self.width() - 2 * margin) / rect.width(), (self.height() - 2 * margin) / rect.height())
        c = rect.center()
        self.set_view(
            QTransform().translate(self.width() / 2, self.height() / 2).scale(s, s).translate(-c.x(), -c.y())
        )

    def _nav_settled(self):
        if self._stale:
            self._stale = False
            self._full = True
            self.update()

    def _shift_cache(self):
        v, c = self.view, self._cache_view
        if (v.m11(), v.m12(), v.m21(), v.m22()) != (c.m11(), c.m12(), c.m21(), c.m22()):
            return None
        dpr = self._cache.devicePixelRatio()
        dx, dy = round((v.dx() - c.dx()) * dpr) / dpr, round((v.dy() - c.dy()) * dpr) / dpr
        v = QTransform(v.m11(), v.m12(), v.m21(), v.m22(), c.dx() + dx, c.dy() + dy)
        self.view, self._inv = (
            v,
            v.inverted()[0],
        )  # Округлить до целых пикселей устройства; погрешность меньше половины пикселя
        img = (
            self._spare
            if getattr(self, "_spare", None) is not None and self._spare.size() == self._cache.size()
            else QImage(self._cache.size(), self._cache.format())
        )
        img.setDevicePixelRatio(dpr)
        p = QPainter(img)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        p.drawImage(QPointF(dx, dy), self._cache)
        p.end()
        self._cache, self._spare = img, self._cache
        self._cache_view = QTransform(v)
        self._stale = False
        W, H = self.width(), self.height()
        dx, dy = math.copysign(math.ceil(abs(dx)), dx), math.copysign(math.ceil(abs(dy)), dy)
        dx, dy = int(dx), int(dy)
        QR = QtCore.QRect
        return [
            r
            for r in (
                QR(0, 0, dx, H) if dx > 0 else QR(W + dx, 0, -dx, H),
                QR(0, 0, W, dy) if dy > 0 else QR(0, H + dy, W, -dy),
            )
            if not r.isEmpty()
        ]

    def _nav_step(self, kind):
        navs = [q for q in self.pointers.values() if q.nav and q.kind == kind]
        if not navs:
            return
        if len(navs) == 1:
            d = navs[0].screen - navs[0].nav_prev
            t = QTransform.fromTranslate(d.x(), d.y())
        else:
            a, b = navs[:2]
            t = geom.similarity(a.nav_prev, b.nav_prev, a.screen, b.screen, rotate=self.nav_rotate)
        for q in navs:
            q.nav_prev = QPointF(q.screen)
        self.set_view(self.view * t)

    # --- Инструменты и ввод
    @property
    def tool(self):
        return self._tool

    @tool.setter
    def tool(self, t):
        if self._tool is not None:
            self._tool.deactivate()
        self._tool = t
        if t is not None:
            t.canvas = self
            t.activate()
            self.setCursor(t.cursor)
        self.toolChanged.emit(t)
        self.update()

    def tool_for(self, p: Pointer):
        if p.kind == "eraser" and self.pen_eraser is not None:
            self.pen_eraser.canvas = self
            return self.pen_eraser
        return self._tool

    def _call(self, p, name):
        tl = self.timeline
        prev, tl._group = tl._group, p.group
        try:
            getattr(p.tool, name)(p)
        finally:
            tl._group = prev

    def _down(self, pid, kind, spos, ev, pressure=1.0, button=None, size=None, nav=False):
        if pid in self.pointers:
            self._up(pid, cancel=True)
        p = Pointer(pid, kind, QPointF(spos), self.to_world(spos), pressure, button, size, ev)
        p.tool = self.tool_for(p)
        p.group = self.timeline.new_group()
        self.pointers[pid] = p
        peers = [q for q in self.pointers.values() if q.kind == kind]
        n = p.tool.nav_fingers if p.tool else 1
        if nav or (n and len(peers) >= n):
            for q in peers:
                if not q.nav:
                    if q is not p:
                        self._call(q, "cancel")
                    q.nav, q.nav_prev = True, QPointF(q.screen)
            return
        self._call(p, "press")

    def _move(self, pid, spos, ev, pressure=None):
        p = self.pointers.get(pid)
        if p is None:
            return
        p.screen, p.last, p.pos, p.event, p.time = (
            QPointF(spos),
            p.pos,
            self.to_world(spos),
            ev,
            time.monotonic(),
        )
        if pressure is not None:
            p.pressure = pressure
        d = p.screen - p.start_screen
        p.moved = max(p.moved, math.hypot(d.x(), d.y()))
        if p.nav:
            self._nav_step(p.kind)
        else:
            self._call(p, "move")

    def _up(self, pid, spos=None, ev=None, cancel=False):
        p = self.pointers.pop(pid, None)
        if p is None or p.nav:
            return
        if spos is not None:
            p.screen, p.last, p.pos, p.event = QPointF(spos), p.pos, self.to_world(spos), ev
        self._call(p, "cancel" if cancel else "release")

    def _hover(self, spos, ev, kind="mouse"):
        if self._tool is not None:
            p = Pointer(-1, kind, QPointF(spos), self.to_world(spos), 0.0, None, None, ev)
            self._tool.hover(p)

    def event(self, ev):
        t = ev.type()
        if self._tool is not None and t not in (_T.Paint, _T.Resize) and self._tool.event(ev):
            return True
        if t in _TOUCH:
            dev = ev.device()
            if dev is not None and dev.type() == _DEV.TouchPad:
                return False
            if t == _T.TouchCancel:
                for pid in [k for k, q in self.pointers.items() if q.kind == "touch"]:
                    self._up(pid, cancel=True)
            else:
                points = ev.points()
                threshold = self.tool.nav_fingers if self.tool else 1
                active = sum(pt.state() != _PS.Released for pt in points)
                navigate = bool(threshold and active >= threshold)
                for pt in sorted(points, key=lambda pt: pt.state() != _PS.Pressed):
                    st = pt.state()
                    if st == _PS.Pressed:
                        self._down(
                            pt.id(), "touch", pt.position(), ev, pt.pressure(), size=pt.ellipseDiameters(), nav=navigate
                        )
                    elif st == _PS.Updated:
                        pointer = self.pointers.get(pt.id())
                        if pointer and pointer.nav:
                            pointer.screen = QPointF(pt.position())
                        else:
                            self._move(pt.id(), pt.position(), ev, pt.pressure())
                    elif st == _PS.Released:
                        self._up(pt.id(), pt.position(), ev)
                self._nav_step("touch")
            ev.accept()
            return True
        if t in _MOUSE:
            if ev.device() is not None and ev.device().type() == _DEV.TouchScreen:
                return True  # Событие синтезировано из касания и уже обработано
            if t in (_T.MouseButtonPress, _T.MouseButtonDblClick):
                if -1 not in self.pointers:
                    self._down(
                        -1,
                        "mouse",
                        ev.position(),
                        ev,
                        button=ev.button(),
                        nav=ev.button() == Qt.MouseButton.MiddleButton,
                    )
            elif t == _T.MouseMove:
                if -1 in self.pointers:
                    self._move(-1, ev.position(), ev)
                else:
                    self._hover(ev.position(), ev)
            elif ev.buttons() == Qt.MouseButton.NoButton:
                self._up(-1, ev.position(), ev)
            return True
        if t in _TABLET:
            kind = "eraser" if ev.pointerType() == QtGui.QPointingDevice.PointerType.Eraser else "pen"
            if t == _T.TabletPress:
                self._down(-2, kind, ev.position(), ev, ev.pressure())
            elif t == _T.TabletMove:
                if -2 in self.pointers:
                    self._move(-2, ev.position(), ev, ev.pressure())
                else:
                    self._hover(ev.position(), ev, kind)
            else:
                self._up(-2, ev.position(), ev)
            ev.accept()
            return True
        if t == _T.NativeGesture and ev.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.zoom_at(1 + ev.value(), ev.position())
            return True
        return super().event(ev)

    def wheelEvent(self, ev):
        if self._tool is not None and self._tool.wheel(ev):
            return
        pd, ad = ev.pixelDelta(), ev.angleDelta()
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier or pd.isNull():
            self.zoom_at(1.0015 ** ad.y(), ev.position())
        else:
            self.pan(pd.x(), pd.y())

    def keyPressEvent(self, ev):
        if self._tool is None or not self._tool.key(ev):
            super().keyPressEvent(ev)

    # --- Рисование
    @property
    def background(self):
        return self._background

    @background.setter
    def background(self, bg):
        if self._background is not None:
            self._background.unwatch(self._bg_changed)
        self._background = bg
        bg.watch(self._bg_changed)
        self.invalidate()

    def _bg_changed(self, *a):
        self.invalidate()

    def invalidate(self):
        self._full = True
        self.update()

    def update_world(self, rect: QRectF):
        self._screen_update(rect)

    def _screen_update(self, rect):
        if rect is not None and not rect.isNull():
            self.update(self.view.mapRect(rect).toAlignedRect().adjusted(-3, -3, 3, 3))

    def _invalidate_world(self, rect):
        if self._bulk or rect is None or rect.isNull():
            return
        r = self.view.mapRect(rect).toAlignedRect().adjusted(-3, -3, 3, 3).intersected(self.rect())
        if r.isEmpty():
            return
        self._dirty.append(rect)
        self.update(r)

    def _paint_item(self, p, it, zoom):
        it.paint(p, zoom)

    def _paint_items(self, p, rect: QRectF, zoom, skip=()):
        by = {}
        for it in self.items:
            by.setdefault(it.layer, []).append(it)
        base = p.transform()
        bb = self._bounds
        for l in self.layers:
            if not l.visible:
                continue
            p.setOpacity(l.opacity)
            for it in by.get(l.name, ()):
                if it not in skip and bb[it.id].intersects(rect):
                    self._paint_item(p, it, zoom)
                    p.setTransform(base)
        p.setOpacity(1)

    def _render_cache(self, rects=None):
        if rects is None:  # Масштаб изменился: обновить границы объектов постоянного экранного размера
            for it in self.items:
                if it.zoom_dependent:
                    self._bounds[it.id] = it.bounds()
        p = QPainter(self._cache)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for r in rects if rects is not None else [self.rect()]:
            p.resetTransform()
            p.setClipRect(r)
            p.setTransform(self.view)
            world = self._inv.mapRect(QRectF(r))
            self.background.paint(p, world, self.zoom)
            self._paint_items(p, world, self.zoom, self.live)
        p.end()
        self._cache_view = QTransform(self.view)
        self._dirty, self._full = [], False

    def paintEvent(self, ev):
        dpr = self.devicePixelRatioF()
        w, h = int(self.width() * dpr), int(self.height() * dpr)
        if self._cache is None or (self._cache.width(), self._cache.height()) != (w, h):
            self._cache = QImage(max(w, 1), max(h, 1), QImage.Format.Format_ARGB32_Premultiplied)
            self._cache.setDevicePixelRatio(dpr)
            self._stale, self._full = False, True
        exposed = self._shift_cache() if self._stale and not self._full else None
        if not self._stale:
            if self._full:
                self._render_cache()
            elif self._dirty or exposed:
                rects = [
                    self.view.mapRect(r).toAlignedRect().adjusted(-3, -3, 3, 3).intersected(self.rect())
                    for r in self._dirty
                ]
                self._render_cache(_merge([r for r in rects if not r.isEmpty()]) + (exposed or []))
        p = QPainter(self)
        r = QRectF(ev.rect())
        if self._stale:
            p.fillRect(r, QColor(self.background.color))
            p.setTransform(self._cache_view.inverted()[0] * self.view)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawImage(QPointF(0, 0), self._cache)
        else:
            p.drawImage(r, self._cache, QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr))
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setTransform(self.view)
        z = self.zoom
        if self.live:
            vis = {l.name for l in self.layers if l.visible}
            for it in self.items:
                if it in self.live and it.layer in vis:
                    self._paint_item(p, it, z)
                    p.setTransform(self.view)
        for it in self.drafts:
            self._paint_item(p, it, z)
            p.setTransform(self.view)
        tools = ({q.tool for q in self.pointers.values()} | {self._tool}) - {None}
        for f in [t.paint for t in tools] + [lambda p, f=f: f(p, self) for f in self.overlays]:
            f(p)
            p.setTransform(self.view)
        p.end()

    # --- Экспорт
    def paint_to(self, p, rect: QRectF, scale=1.0, background=True):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(scale, scale)
        p.translate(-rect.left(), -rect.top())
        if background:
            self.background.paint(p, rect, scale)
        self._paint_items(p, rect, scale)

    def render_image(self, rect: QRectF = None, scale=1.0, background=True) -> QImage:
        rect = rect or self.content_rect().adjusted(-20, -20, 20, 20)
        img = QImage(
            max(1, math.ceil(rect.width() * scale)),
            max(1, math.ceil(rect.height() * scale)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.fill(0)
        p = QPainter(img)
        self.paint_to(p, rect, scale, background)
        p.end()
        return img

    def export(self, path, rect: QRectF = None, scale=2.0, background=True):
        rect = rect or self.content_rect().adjusted(-20, -20, 20, 20)
        if rect.isEmpty():
            rect = self.visible_rect()
        ext = str(path).rsplit(".", 1)[-1].lower()
        if ext == "svg":
            gen = svg_generator()()
            gen.setFileName(str(path))
            gen.setSize(QtCore.QSize(math.ceil(rect.width()), math.ceil(rect.height())))
            gen.setViewBox(QRectF(0, 0, rect.width(), rect.height()))
            p = QPainter(gen)
            self.paint_to(p, rect, 1.0, background)
            p.end()
        elif ext == "pdf":
            pdf = QtGui.QPdfWriter(str(path))
            pdf.setResolution(72)
            pdf.setPageMargins(QtCore.QMarginsF(0, 0, 0, 0))
            pdf.setPageSize(
                QtGui.QPageSize(QtCore.QSizeF(rect.width(), rect.height()), QtGui.QPageSize.Unit.Point)
            )
            p = QPainter(pdf)
            self.paint_to(p, rect, 1.0, background)
            p.end()
        else:
            self.render_image(rect, scale, background).save(str(path))

    # --- Сохранение и загрузка
    def to_dict(self, history=True) -> dict:
        v = self.view
        d = {
            "format": "canvas",
            "version": 1,
            "view": [v.m11(), v.m12(), v.m21(), v.m22(), v.dx(), v.dy()],
            "layers": [l.settings.dump() for l in self.layers],
            "background": self.background.to_dict(),
            "items": [it.to_dict() for it in self.items],
            "meta": copy.deepcopy(self.meta),
            "ext": {k: e.to_dict() for k, e in self.ext.items()},
        }
        if history:
            d["timeline"] = self.timeline.to_dict()
        return d

    def load_dict(self, d: dict):
        for pid in list(self.pointers):
            self._up(pid, cancel=True)
        for it in list(self.drafts):
            self.discard(it)
        self.timeline.stop()
        self._bulk = True
        with self.silent():
            for it in list(self.items):
                self._remove_raw(it)
            tl = self.timeline
            tl.ops, tl.pos, tl.base, tl.objs, tl._revealed = [], 0, [], {}, set()
            self.layers = []
            for ld in d.get("layers") or [{}]:
                self.layer(ld.get("name", "main")).settings.load(ld)
            if "background" in d:
                self.background = Model.from_dict(d["background"])
            if "timeline" in d:
                tl.load(d["timeline"])
                for saved in d.get("items", ()):
                    item = self.find(saved["id"])
                    if item is not None:
                        item.restore(saved)
                self._bounds.update((item.id, item.bounds()) for item in self.items)
            else:
                for it in d.get("items", []):
                    self._add_raw(Model.from_dict(it))
                tl.clear()
        self._bulk = False
        self.meta = copy.deepcopy(d.get("meta", {}))
        for k, e in d.get("ext", {}).items():
            if k in self.ext:
                self.ext[k].load(e)
        if "view" in d:
            self.set_view(QTransform(*d["view"]))
        self.reset.emit()
        self.invalidate()
        self.timelineChanged.emit()

    def snapshot(self, history=True) -> dict:
        return self.to_dict(history)

    def restore(self, snap: dict):
        self.load_dict(snap)

    def save(self, path, history=True):
        data = json.dumps(self.to_dict(history), separators=(",", ":")).encode()
        if str(path).endswith(".gz"):
            data = gzip.compress(data, 6)
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def load(self, path):
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        self.load_dict(json.loads(data))

    def autosave(self, path, interval=30.0, history=True) -> QTimer:
        state = {"dirty": False}
        self.timelineChanged.connect(lambda: state.update(dirty=True))

        def tick():
            if state["dirty"]:
                state["dirty"] = False
                self.save(path, history)

        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(int(interval * 1000))
        return timer
