from __future__ import annotations

import copy

from .qt import QColor


class Setting:
    def __init__(self, default=None, label=None, ui=True, **meta):
        self.default, self.label, self.ui, self.meta = default, label, ui, meta
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name
        if self.label is None:
            self.label = name.replace("_", " ").capitalize()

    def __getattr__(self, key):
        try:
            return self.__dict__["meta"][key]
        except KeyError:
            raise AttributeError(key) from None

    def __get__(self, obj, cls=None):
        if obj is None:
            return self
        v = obj._values
        return v[self.name] if self.name in v else obj.get(self.name)

    def __set__(self, obj, value):
        obj.set(self.name, value)

    def coerce(self, v):
        return v


class Float(Setting):
    def __init__(self, default=0.0, min=None, max=None, step=None, **kw):
        super().__init__(float(default), min=min, max=max, step=step, **kw)

    def coerce(self, v):
        return float(v)


class Int(Setting):
    def __init__(self, default=0, min=None, max=None, **kw):
        super().__init__(int(default), min=min, max=max, **kw)

    def coerce(self, v):
        return int(v)


class Bool(Setting):
    def __init__(self, default=False, **kw):
        super().__init__(bool(default), **kw)

    def coerce(self, v):
        return bool(v)


class Str(Setting):
    def __init__(self, default="", multiline=False, **kw):
        super().__init__(str(default), multiline=multiline, **kw)

    def coerce(self, v):
        return str(v)


class Color(Setting):
    def __init__(self, default="#000000", **kw):
        super().__init__(default, **kw)

    def coerce(self, v):
        c = QColor(v)
        if not c.isValid():
            raise ValueError(f"bad color {v!r}")
        return c.name(QColor.NameFormat.HexArgb) if c.alpha() < 255 else c.name()


class Choice(Setting):
    def __init__(self, default, choices, **kw):
        super().__init__(default, choices=list(choices), **kw)

    def coerce(self, v):
        if v not in self.choices:
            raise ValueError(f"{v!r} not in {self.choices}")
        return v


_specs_cache = {}


class HasSettings:
    def __init__(self, **values):
        self._values, self._extra, self._watchers = {}, {}, []
        for k, v in values.items():
            self.set(k, v)

    @classmethod
    def specs(cls) -> dict:
        s = _specs_cache.get(cls)
        if s is None:
            s = {}
            for klass in reversed(cls.__mro__):
                s.update({k: v for k, v in vars(klass).items() if isinstance(v, Setting)})
            _specs_cache[cls] = s
        return s

    def spec(self, name) -> Setting:
        return self._extra.get(name) or type(self).specs()[name]

    def get(self, name):
        try:
            return self._values[name]
        except KeyError:
            d = self.spec(name).default
            v = self._values[name] = copy.deepcopy(d) if isinstance(d, (list, dict)) else d
            return v

    def set(self, name, value):
        value = self.spec(name).coerce(value)
        old = self.get(name)
        if old == value:
            return
        self._values[name] = value
        self._emit(name, old, value)

    def _emit(self, key, old, new):
        for w in list(self._watchers):
            w(self, key, old, new)

    def watch(self, fn):
        self._watchers.append(fn)
        return fn

    def unwatch(self, fn):
        if fn in self._watchers:
            self._watchers.remove(fn)

    @property
    def settings(self) -> Settings:
        return Settings(self)


class Bound:
    __slots__ = ("obj", "name")

    def __init__(self, obj, name):
        self.obj, self.name = obj, name

    spec = property(lambda self: self.obj.spec(self.name))
    label = property(lambda self: self.spec.label)

    @property
    def value(self):
        return self.obj.get(self.name)

    @value.setter
    def value(self, v):
        self.obj.set(self.name, v)

    def connect(self, fn):
        def w(obj, key, old, new):
            if key == self.name:
                fn(new)

        self.obj.watch(w)
        return lambda: self.obj.unwatch(w)

    def reset(self):
        self.value = self.spec.default

    def __repr__(self):
        return f"<{self.name}={self.value!r}>"


class Settings:
    __slots__ = ("_obj",)

    def __init__(self, obj):
        self._obj = obj

    def _names(self):
        return [*type(self._obj).specs(), *self._obj._extra]

    def __getattr__(self, name) -> Bound:
        if name.startswith("_") or name not in self:
            raise AttributeError(name)
        return Bound(self._obj, name)

    __getitem__ = __getattr__

    def __contains__(self, name):
        return name in self._obj._extra or name in type(self._obj).specs()

    def __iter__(self):
        return (Bound(self._obj, n) for n in self._names())

    def __len__(self):
        return len(self._names())

    def add(self, name, spec: Setting) -> Bound:
        spec.__set_name__(type(self._obj), name)
        self._obj._extra[name] = spec
        return Bound(self._obj, name)

    def remove(self, name):
        self._obj._extra.pop(name, None)
        self._obj._values.pop(name, None)

    def dump(self) -> dict:
        return {n: copy.deepcopy(self._obj.get(n)) for n in type(self._obj).specs()}

    def load(self, d: dict):
        for k, v in d.items():
            if k in self:
                self._obj.set(k, v)


def set_path(obj, path: str, value):
    *parts, last = path.split(".")
    for part in parts:
        obj = obj[int(part)] if part.isdigit() else getattr(obj, part)
    obj.set(last, value)
