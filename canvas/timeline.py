from __future__ import annotations

import bisect
import time

from .items import Model
from .qt import QTimer
from .settings import set_path


class Timeline:
    def __init__(self, canvas):
        self.canvas = canvas
        self.ops, self.pos, self.base = [], 0, []
        self.objs = {}  # Все когда-либо записанные объекты по идентификатору, включая удалённые
        self.hooks = []  # Вызывается перед записью, например для сохранения ключевых кадров физики
        self.merge = 1.0  # Изменения одной настройки за этот интервал объединяются в одну операцию
        self.max_gap = 1.5  # При воспроизведении пропускаются паузы длиннее этого значения
        self._t0 = time.monotonic()
        self._time = 0.0
        self._gid = 0
        self._group = None
        self._hooking = False
        self._revealed = set()
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self.speed = 1.0

    # --- Отсчёт времени
    def now(self) -> float:
        return time.monotonic() - self._t0

    @property
    def time(self) -> float:
        return self._time

    @property
    def end(self) -> float:
        return max((o["t"] + o.get("dur", 0) for o in self.ops), default=0.0)

    def new_group(self) -> int:
        self._gid += 1
        return self._gid

    # --- Запись
    def prepare(self):
        if not self._hooking and self.hooks:
            self._hooking = True
            try:
                for h in self.hooks:
                    h()
            finally:
                self._hooking = False

    def record(self, op: dict):
        if self.canvas._silent:
            return
        self.stop()
        if self.pos < len(self.ops):
            self._truncate()
        self._unreveal()
        self.prepare()
        now = self.now()
        last = self.ops[-1] if self.ops else None
        # Только добавление, время монотонно: порядок причин и следствий важнее точного времени начала
        # Штрих, начатый до отмены, не должен оказаться раньше операций, записанных после неё
        op["t"] = max(min(op.get("t", now), now), last["t"] if last else 0.0)
        if "dur" in op:
            op["dur"] = round(max(now - op["t"], 0.0), 3)
        op["g"] = self._group if self._group is not None else self.new_group()
        if (
            op["k"] == "set"
            and last
            and last["k"] == "set"
            and last["id"] == op["id"]
            and last["key"] == op["key"]
            and now - last["t"] < self.merge
        ):
            last["new"] = op["new"]
        else:
            self.ops.append(op)
            self.pos = len(self.ops)
        self._time = max(self._time, now)
        if op["k"] == "add":
            self.objs[op["id"]] = self.canvas.find(op["id"])
        self.canvas.timelineChanged.emit()

    def _truncate(self):
        del self.ops[self.pos :]
        alive = (
            {o["id"] for o in self.ops} | {d["id"] for d in self.base} | {it.id for it in self.canvas.items}
        )
        self.objs = {k: v for k, v in self.objs.items() if k in alive}

    # --- Перемещение по истории
    def _obj(self, op):
        obj = self.objs.get(op["id"])
        if obj is None:
            obj = self.objs[op["id"]] = Model.from_dict(op["d"])
        return obj

    def _do(self, op, forward):
        c, k = self.canvas, op["k"]
        if k == "add" or k == "remove":
            if (k == "add") == forward:
                c._add_raw(self._obj(op), op["i"])
            else:
                c._remove_raw(self._obj(op))
            return
        obj = self.objs.get(op["id"]) or c.find(op["id"])
        if obj is None:
            return
        if k == "set":
            set_path(obj, op["key"], op["new"] if forward else op["old"])
        elif k == "state":
            obj.restore(op["after"] if forward else op["before"])
            obj.update()
            c.itemChanged.emit(obj, "")
        elif k == "order":
            c._reorder_raw(obj, op["new"] if forward else op["old"])

    def seek_index(self, i: int, *, at_time=None):
        i = max(0, min(i, len(self.ops)))
        with self.canvas.silent():
            while self.pos < i:
                self._do(self.ops[self.pos], True)
                self.pos += 1
            while self.pos > i:
                self.pos -= 1
                self._do(self.ops[self.pos], False)
        self._time = self.ops[i - 1]["t"] + self.ops[i - 1].get("dur", 0) if i else 0.0
        if at_time is not None:
            self._time = at_time
        self._t0 = time.monotonic() - self._time
        self.canvas.timelineChanged.emit()

    def seek(self, t: float):
        self._unreveal()
        self.seek_index(bisect.bisect_right(self.ops, t, key=lambda o: o["t"]), at_time=t)
        for op in self.ops[max(0, self.pos - 64) : self.pos]:
            if op["k"] == "add" and op["t"] + op.get("dur", 0) > t:
                obj = self.objs.get(op["id"])
                if obj is not None:
                    obj.reveal(t - op["t"])
                    self._revealed.add(obj)
        self.canvas.update()

    def _unreveal(self):
        for obj in self._revealed:
            obj.reveal(None)
        self._revealed.clear()

    def undo(self):
        if self.pos:
            g, i = self.ops[self.pos - 1]["g"], self.pos - 1
            while i and self.ops[i - 1]["g"] == g:
                i -= 1
            self._unreveal()
            self.seek_index(i)

    def redo(self):
        if self.pos < len(self.ops):
            g, i = self.ops[self.pos]["g"], self.pos + 1
            while i < len(self.ops) and self.ops[i]["g"] == g:
                i += 1
            self._unreveal()
            self.seek_index(i)

    can_undo = property(lambda self: self.pos > 0)
    can_redo = property(lambda self: self.pos < len(self.ops))

    # --- Воспроизведение
    def play(self, speed=None, from_start=True):
        if speed is not None:
            self.speed = speed
        if from_start or self._time >= self.end:
            self.seek(0)
        self._last = time.monotonic()
        self._timer.start(16)

    def stop(self):
        self._timer.stop()

    playing = property(lambda self: self._timer.isActive())

    def _tick(self):
        now = time.monotonic()
        t = self._time + (now - self._last) * self.speed
        self._last = now
        if self.pos < len(self.ops) and not self._revealed and self.ops[self.pos]["t"] - t > self.max_gap:
            t = self.ops[self.pos]["t"] - 0.3
        if t >= self.end:
            self.stop()
            self._unreveal()
            self.seek_index(len(self.ops))
        else:
            self.seek(t)

    # --- Сохранение и загрузка
    def clear(self):
        self.stop()
        self._unreveal()
        self.base = [it.to_dict() for it in self.canvas.items]
        self.ops, self.pos = [], 0
        self.objs = {it.id: it for it in self.canvas.items}
        self._time, self._t0 = 0.0, time.monotonic()
        self.canvas.timelineChanged.emit()

    def to_dict(self) -> dict:
        # Данные записанных операций не меняются; меняются только словари операций при объединении настроек
        return {
            "ops": [dict(o) for o in self.ops],
            "pos": self.pos,
            "base": list(self.base),
            "time": self._time,
        }

    def load(self, d: dict):
        self.ops, self.base, self.pos = [dict(o) for o in d["ops"]], list(d["base"]), 0
        self.objs = {}
        with self.canvas.silent():
            for bd in self.base:
                obj = self.objs[bd["id"]] = Model.from_dict(bd)
                self.canvas._add_raw(obj)
        self._gid = max((o["g"] for o in self.ops), default=0)
        self.seek_index(d["pos"])
        self._time = d.get("time", self._time)
        self._t0 = time.monotonic() - self._time
