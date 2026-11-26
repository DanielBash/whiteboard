from __future__ import annotations

import itertools
import math

import numpy as np

from .parser import ParseError, parse

MAX_LIST = 10000
MAX_TERMS = 5000
MAX_CURVES = 100
RESERVED = {"x", "y", "t", "theta", "r", "e", "pi", "tau", "inf"}
CONST = {"e": np.e, "pi": np.pi, "tau": 2 * np.pi, "inf": np.inf}
SPECIAL = {"sum", "prod", "int", "integral", "diff"}
_GAUSS = np.polynomial.legendre.leggauss(48)
_gamma = np.vectorize(
    lambda v: math.gamma(v) if v < 171 and not (v <= 0 and v == int(v)) else np.nan, otypes=[float]
)


class EvalError(ValueError):
    pass


def _f(a):
    return np.asarray(a, dtype=float)


def _ptop(op, A, B):
    pa, pb = isinstance(A, tuple), isinstance(B, tuple)
    if op in "+-" and pa and pb:
        return (A[0] + B[0], A[1] + B[1]) if op == "+" else (A[0] - B[0], A[1] - B[1])
    if op == "*" and pa != pb:
        P, s = (A, B) if pa else (B, A)
        return (P[0] * s, P[1] * s)
    if op == "/" and pa and not pb:
        return (A[0] / B, A[1] / B)
    raise EvalError("Эта операция недоступна для точек")


_BIN = {"+": np.add, "-": np.subtract, "*": np.multiply, "/": np.divide, "^": lambda a, b: np.power(_f(a), b)}
_CMP = {
    "<": np.less,
    ">": np.greater,
    "<=": np.less_equal,
    ">=": np.greater_equal,
    "=": lambda a, b: np.isclose(a, b, rtol=1e-9, atol=1e-9),
}


def _nthroot(x, n):
    r = np.power(np.abs(x), 1.0 / n)
    return np.where((x < 0) & (np.mod(n, 2) == 1), -r, np.where(x < 0, np.nan, r))


def _ints(f):
    def g(a, b):
        m = np.isfinite(a) & np.isfinite(b)
        out = f(np.where(m, np.round(a), 0).astype(np.int64), np.where(m, np.round(b), 0).astype(np.int64))
        return np.where(m, out, np.nan)

    return g


MATH = {
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
    "sec": lambda x: 1 / np.cos(x),
    "csc": lambda x: 1 / np.sin(x),
    "cot": lambda x: 1 / np.tan(x),
    "sinh": np.sinh,
    "cosh": np.cosh,
    "tanh": np.tanh,
    "sech": lambda x: 1 / np.cosh(x),
    "csch": lambda x: 1 / np.sinh(x),
    "coth": lambda x: 1 / np.tanh(x),
    "arcsin": np.arcsin,
    "arccos": np.arccos,
    "arctan": np.arctan,
    "arcsec": lambda x: np.arccos(1 / x),
    "arccsc": lambda x: np.arcsin(1 / x),
    "arccot": lambda x: np.arctan(1 / x),
    "arcsinh": np.arcsinh,
    "arccosh": np.arccosh,
    "arctanh": np.arctanh,
    "ln": np.log,
    "exp": np.exp,
    "sqrt": np.sqrt,
    "cbrt": np.cbrt,
    "nthroot": _nthroot,
    "abs": np.abs,
    "sign": np.sign,
    "floor": np.floor,
    "ceil": np.ceil,
    "mod": np.mod,
    "round": lambda x, n=0: np.round(x * 10.0**n) / 10.0**n,
    "gcd": _ints(np.gcd),
    "lcm": _ints(np.lcm),
    "nCr": lambda n, r: np.round(_gamma(n + 1) / (_gamma(r + 1) * _gamma(n - r + 1))),
    "nPr": lambda n, r: np.round(_gamma(n + 1) / _gamma(n - r + 1)),
}
ALIAS = {
    "asin": "arcsin",
    "acos": "arccos",
    "atan": "arctan",
    "arsinh": "arcsinh",
    "arcosh": "arccosh",
    "artanh": "arctanh",
    "sgn": "sign",
    "stddev": "stdev",
    "integral": "int",
}
TRIG = {"sin", "cos", "tan", "sec", "csc", "cot"}
ATRIG = {"arcsin", "arccos", "arctan", "arcsec", "arccsc", "arccot"}
LISTFN = {
    "mean": lambda L: np.mean(L, 0),
    "median": lambda L: np.median(L, 0),
    "total": lambda L: np.sum(L, 0),
    "length": lambda L: np.float64(len(L)),
    "stdev": lambda L: np.std(L, 0, ddof=1),
    "var": lambda L: np.var(L, 0, ddof=1),
    "sort": lambda L: np.sort(L, 0),
    "unique": lambda L: np.unique(L),
}


class Scope:
    def __init__(self, defs=None, funcs=None, params=None, fixed=None, degrees=False):
        self.defs, self.funcs = defs or {}, funcs or {}
        self.params, self.fixed = params or {}, fixed or {}
        self.degrees = degrees
        self.cache, self._busy = {}, set()

    def lookup(self, name):
        if name in self.params:
            return np.float64(self.params[name])
        if name in self.fixed:
            return self.fixed[name]
        if name in self.cache:
            return self.cache[name]
        if name in self.defs:
            if name in self._busy:
                raise EvalError(f"Определение «{name}» ссылается на себя")
            self._busy.add(name)
            try:
                v = self.cache[name] = Ev(self).ev(self.defs[name])
            finally:
                self._busy.discard(name)
            return v
        if name in CONST:
            return np.float64(CONST[name])
        raise EvalError(f"Переменная «{name}» не определена")


class Ev:
    def __init__(self, scope: Scope, local=None, nd=0):
        self.s, self.local, self.nd = scope, local or {}, nd

    def sub(self, **local):
        return Ev(self.s, {**self.local, **local}, self.nd)

    def ev(self, n):
        try:
            return getattr(self, "e_" + n[0])(*n[1:])
        except (EvalError, ParseError):
            raise
        except RecursionError:
            raise EvalError("Слишком много вложенных или рекурсивных вызовов") from None
        except Exception as ex:
            raise EvalError(error_message(ex)) from None

    def is_list(self, v):
        return not isinstance(v, tuple) and np.ndim(v) > self.nd

    def lift(self, v):
        if isinstance(v, tuple):
            return tuple(self.lift(a) for a in v)
        if self.nd and np.ndim(v) == 1:
            return np.reshape(v, v.shape + (1,) * self.nd)
        return v

    def as_list(self, vals):
        if vals and all(isinstance(v, tuple) for v in vals):
            return tuple(self.as_list([v[i] for v in vals]) for i in (0, 1))
        if any(isinstance(v, tuple) for v in vals):
            raise EvalError("Список не может содержать одновременно точки и числа")
        if any(self.is_list(v) for v in vals):
            raise EvalError("Вложенные списки не поддерживаются")
        if not vals:
            return np.zeros((0,) + (1,) * self.nd)
        A = np.stack(np.broadcast_arrays(*[_f(v) for v in vals]))
        return A.reshape(A.shape[:1] + (1,) * self.nd) if A.ndim == 1 else A

    def e_num(self, v):
        return np.float64(v)

    def e_var(self, name):
        if name in self.local:
            return self.local[name]
        return self.lift(self.s.lookup(name))

    def e_bin(self, op, a, b):
        A, B = self.ev(a), self.ev(b)
        if isinstance(A, tuple) or isinstance(B, tuple):
            return _ptop(op, A, B)
        return _BIN[op](_f(A), B)

    def e_neg(self, a):
        A = self.ev(a)
        return (-A[0], -A[1]) if isinstance(A, tuple) else -_f(A)

    def e_point(self, a, b):
        A, B = self.ev(a), self.ev(b)
        if isinstance(A, tuple) or isinstance(B, tuple):
            raise EvalError("Точка задаётся двумя числами")
        return np.broadcast_arrays(_f(A), _f(B))

    def e_fact(self, a):
        return _gamma(_f(self.ev(a)) + 1)

    def e_deg(self, a):
        return self.ev(a) * (np.pi / 180)

    def e_member(self, a, m):
        A = self.ev(a)
        if not isinstance(A, tuple):
            raise EvalError(f"Координату {m} можно получить только у точки")
        return A[0] if m == "x" else A[1]

    def e_list(self, items):
        return self.as_list([self.ev(i) for i in items])

    def e_range(self, a, a2, b):
        A, B = float(np.ravel(self.ev(a))[0]), float(np.ravel(self.ev(b))[0])
        step = float(np.ravel(self.ev(a2))[0]) - A if a2 is not None else (1.0 if B >= A else -1.0)
        if step == 0 or (B - A) / step < 0:
            raise EvalError("Некорректный диапазон")
        n = int(math.floor((B - A) / step + 1e-9)) + 1
        if n > MAX_LIST:
            raise EvalError(f"Список может содержать не более {MAX_LIST} элементов")
        return (A + step * np.arange(n)).reshape((n,) + (1,) * self.nd)

    def e_comp(self, body, gens):
        names = [v for v, _ in gens]
        pools = [np.ravel(self.ev(l)) for _, l in gens]
        if math.prod(len(p) for p in pools) > MAX_LIST:
            raise EvalError(f"Список может содержать не более {MAX_LIST} элементов")
        return self.as_list(
            [
                self.sub(**dict(zip(names, map(np.float64, combo)))).ev(body)
                for combo in itertools.product(*pools)
            ]
        )

    def e_index(self, a, items):
        A = self.ev(a)
        if not self.is_list(A) and not (isinstance(A, tuple) and self.is_list(A[0])):
            return _ptop("*", self.e_list(items), A) if isinstance(A, tuple) else A * self.e_list(items)
        idx = self.ev(items[0]) if len(items) == 1 else self.e_list(items)

        def take(L):
            if np.asarray(idx).dtype == bool:
                return L[np.ravel(idx)] if L.ndim == 1 else L[np.ravel(idx)[: len(L)]]
            ii = np.round(np.ravel(idx)).astype(int) - 1
            ok = (ii >= 0) & (ii < len(L))
            out = L[np.where(ok, ii, 0)].astype(float)
            out[~ok] = np.nan
            return out if np.ndim(idx) > self.nd else out[0]

        return (take(A[0]), take(A[1])) if isinstance(A, tuple) else take(A)

    def e_cmp(self, ops, terms):
        vals = [self.ev(t) for t in terms]
        m = True
        for op, a, b in zip(ops, vals, vals[1:]):
            m = m & _CMP[op](_f(a), _f(b))
        return np.asarray(m)

    def e_piece(self, items):
        conds = [(c, v) for c, v in items if v is not None]
        default = next((c for c, v in items if v is None and c[0] != "cmp"), None)
        restrict = [c for c, v in items if v is None and c[0] == "cmp"]
        val = self.ev(default) if default is not None else np.float64(np.nan if conds else 1.0)
        for c, v in reversed(conds):
            val = np.where(self.ev(c), self.ev(v), val)
        for c in restrict:
            val = np.where(self.ev(c), val, np.nan)
        return val

    def e_deriv(self, var, body, order=1):
        v = _f(self.e_var(var))
        h = 1e-5 * (1 + np.abs(v))
        f = lambda w: _f(self.sub(**{var: w}).ev(body))
        return (f(v + h) - f(v - h)) / (2 * h)

    def e_prime(self, name, n, args):
        if n > 2:
            raise EvalError("Поддерживаются только первая и вторая производные")
        a = _f(self.ev(args[0]))
        f = lambda w: _f(self.call(name, [w]))
        if n == 1:
            h = 1e-5 * (1 + np.abs(a))
            return (f(a + h) - f(a - h)) / (2 * h)
        h = 1e-3 * (1 + np.abs(a))
        return (f(a + h) - 2 * f(a) + f(a - h)) / (h * h)

    def call(self, name, vals):
        if name in self.s.funcs:
            params, body = self.s.funcs[name]
            if len(params) != len(vals):
                raise EvalError(f"Функция {name} ожидает аргументов: {len(params)}")
            return self.sub(**dict(zip(params, vals))).ev(body)
        return self.builtin(name, vals)

    def e_callish(self, name, args):
        if name in self.s.funcs:
            return self.call(name, [self.ev(a) for a in args])
        v = self.e_var(name)
        arg = self.ev(args[0]) if len(args) == 1 else self.e_point(*args[:2])
        return _ptop("*", arg, v) if isinstance(arg, tuple) or isinstance(v, tuple) else v * _f(arg)

    def e_call(self, name, args):
        name = ALIAS.get(name, name)
        if name in SPECIAL:
            return self.special(name, args)
        if name in self.s.funcs:
            return self.call(name, [self.ev(a) for a in args])
        return self.builtin(name, [self.ev(a) for a in args])

    def builtin(self, name, vals):
        ang = np.pi / 180 if self.s.degrees else 1.0
        if name in LISTFN or (name in ("min", "max") and len(vals) == 1):
            L = vals[0]
            if not self.is_list(L):
                L = np.reshape(_f(L), (1,) + np.shape(L))
            return {"min": lambda L: np.min(L, 0), "max": lambda L: np.max(L, 0)}.get(name, LISTFN.get(name))(
                L
            )
        if name in ("min", "max"):
            return (np.minimum if name == "min" else np.maximum).reduce(np.broadcast_arrays(*map(_f, vals)))
        if name == "join":
            return np.concatenate(
                [v if self.is_list(v) else np.reshape(_f(v), (1,) + np.shape(v)) for v in vals]
            )
        if name in ("distance", "midpoint"):
            (x1, y1), (x2, y2) = vals
            return np.hypot(x2 - x1, y2 - y1) if name == "distance" else ((x1 + x2) / 2, (y1 + y2) / 2)
        if name == "log":
            return np.log10(vals[0]) if len(vals) == 1 else np.log(vals[0]) / np.log(vals[1])
        if name == "arctan" and len(vals) == 2:
            return np.arctan2(vals[0], vals[1]) / ang
        if name not in MATH:
            raise EvalError(f"Неизвестная функция: {name}")
        if any(isinstance(v, tuple) for v in vals):
            raise EvalError(f"Функция {name} не принимает точки")
        vals = [_f(v) for v in vals]
        if name in TRIG:
            return MATH[name](vals[0] * ang)
        if name in ATRIG:
            return MATH[name](vals[0]) / ang
        return MATH[name](*vals)

    def special(self, name, args):
        if name == "diff":
            if len(args) != 2 or args[1][0] != "var":
                raise EvalError("Формат производной: diff(выражение, x)")
            return self.e_deriv(args[1][1], args[0])
        if len(args) != 4 or args[1][0] != "var":
            raise EvalError(f"Формат: {name}(выражение, переменная, начало, конец)")
        body, var, lo, hi = args[0], args[1][1], self.ev(args[2]), self.ev(args[3])
        if name == "int":
            mid, half = (_f(lo) + hi) / 2, (_f(hi) - lo) / 2
            xs, ws = _GAUSS
            return sum(w * _f(self.sub(**{var: mid + half * x}).ev(body)) for x, w in zip(xs, ws)) * half
        a, b = int(np.floor(np.nanmin(lo))), int(np.floor(np.nanmax(hi)))
        if b - a > MAX_TERMS:
            raise EvalError(
                f"Сумма или произведение может содержать не более {MAX_TERMS} слагаемых или множителей"
            )
        acc = np.float64(0.0 if name == "sum" else 1.0)
        for n in range(a, b + 1):
            m = (n >= _f(lo)) & (n <= _f(hi))
            acc = (
                acc + np.where(m, self.sub(**{var: np.float64(n)}).ev(body), 0)
                if name == "sum"
                else acc * np.where(m, self.sub(**{var: np.float64(n)}).ev(body), 1)
            )
        return acc


# --- Анализ
def free_names(n, bound=frozenset()) -> set:
    if not isinstance(n, tuple) or not n:
        return set()
    k = n[0]
    if k == "var":
        return set() if n[1] in bound else {n[1]}
    if k == "callish":
        own = set() if n[1] in bound else {n[1]}
        return own | set().union(*(free_names(a, bound) for a in n[2]))
    if k == "comp":
        b = bound | {v for v, _ in n[2]}
        return free_names(n[1], b) | set().union(*(free_names(l, bound) for _, l in n[2]))
    if k == "deriv":
        return free_names(n[2], bound)
    if k == "call" and n[1] in SPECIAL and len(n[2]) >= 2 and n[2][1][0] == "var":
        v = n[2][1][1]
        return free_names(n[2][0], bound | {v}) | set().union(*(free_names(a, bound) for a in n[2][2:]))
    out = set()
    for a in n[1:]:
        if isinstance(a, tuple):
            out |= free_names(a, bound)
        elif isinstance(a, list):
            for x in a:
                out |= free_names(x, bound)
    return out


def _has(n, names):
    return bool(free_names(n) & set(names))


def error_message(error):
    if isinstance(error, (EvalError, ParseError)):
        return str(error)
    return "Не удалось вычислить выражение. Проверьте аргументы и размеры списков."


class Row:
    kind = "empty"
    error = None
    value = None
    fit = None
    r2 = None

    def __init__(self, **kw):
        self.__dict__.update(kw)


def analyse(texts, tables=None):
    tables = tables or [None] * len(texts)
    rows, defs, funcs, data = [], {}, {}, {}
    for text, table in zip(texts, tables):
        if table:
            cols = {k: np.asarray(v, float) for k, v in table.items()}
            data.update(cols)
            rows.append(Row(kind="table", cols=list(cols)))
            continue
        try:
            st = parse(text)
        except ParseError as ex:
            rows.append(Row(kind="error", error=str(ex)))
            continue
        if st is None:
            rows.append(Row())
            continue
        rows.append(_classify(st, defs, funcs))
    used, bound_fit = set(), set()
    for r in rows:
        if r.kind == "func":
            used |= free_names(r.body, frozenset(r.params))
        elif r.kind == "reg":
            bound_fit |= free_names(r.rhs) - set(defs) - set(funcs) - set(data) - RESERVED
            used |= free_names(r.lhs)
        else:
            for n in getattr(r, "nodes", ()):
                used |= free_names(n)
    sliders = {r.name: r.value for r in rows if r.kind == "slider"}
    defined = set(defs) | set(funcs) | set(data) | bound_fit | RESERVED | set(sliders)
    for r in rows:
        if r.kind == "reg":
            r.params = sorted(
                free_names(r.rhs) - set(defs) - set(funcs) - set(data) - RESERVED - set(sliders)
            )
    return rows, defs, funcs, data, {**sliders, **{n: 1.0 for n in sorted(used - defined)}}


def _classify(st, defs, funcs) -> Row:
    k = st[0]
    if k == "expr":
        e = st[1]
        if e[0] == "point" and _has(e, ["t"]):
            return Row(kind="param", node=e, nodes=[e])
        if _has(e, ["x"]) and not _has(e, ["y"]):
            return Row(kind="y", node=e, nodes=[e])
        return Row(kind="value", node=e, nodes=[e])
    if k == "reg":
        return Row(kind="reg", lhs=st[1], rhs=st[2])
    if k == "chain":
        return Row(kind="region", ops=st[1], terms=st[2], nodes=st[2])
    lhs, rhs = st[-2], st[-1]
    if k == "eq":
        num = rhs[1] if rhs[0] == "num" else -rhs[1][1] if rhs[0] == "neg" and rhs[1][0] == "num" else None
        if lhs[0] == "var" and lhs[1] not in RESERVED and num is not None:
            return Row(kind="slider", name=lhs[1], value=num)
        if lhs[0] == "var" and lhs[1] not in ("x", "y", "r") and not _has(rhs, ["x", "y"]):
            defs[lhs[1]] = rhs
            return Row(kind="define", name=lhs[1], node=rhs, nodes=[rhs])
        if lhs[0] == "callish" and lhs[1] not in ("x", "y") and all(a[0] == "var" for a in lhs[2]):
            params = [a[1] for a in lhs[2]]
            funcs[lhs[1]] = (params, rhs)
            return Row(kind="func", name=lhs[1], params=params, body=rhs, plot=params in (["x"], ["y"]))
    for var, other in (("y", "x"), ("x", "y"), ("r", None)):
        for a, b, flip in ((lhs, rhs, False), (rhs, lhs, True)):
            if a == ("var", var) and not _has(b, [var]):
                if var == "r" and _has(b, ["x", "y"]):
                    continue
                if k == "eq":
                    kind = {"y": "y", "x": "x", "r": "polar"}[var]
                    return Row(kind=kind, node=b, nodes=[b])
                if var != "r":
                    op = st[1]
                    if flip:
                        op = {"<": ">", ">": "<", "<=": ">=", ">=": "<="}[op]
                    return Row(kind="ineq_" + var, op=op, node=b, nodes=[b])
    if k == "eq":
        return Row(kind="implicit", lhs=lhs, rhs=rhs, nodes=[lhs, rhs])
    return Row(kind="region", ops=[st[1]], terms=[lhs, rhs], nodes=[lhs, rhs])


def fit(scope: Scope, row: Row):
    y = np.ravel(_f(Ev(scope).ev(row.lhs)))
    names = row.params
    p = np.ones(len(names))

    def resid(p):
        s = Scope(
            scope.defs,
            scope.funcs,
            scope.params,
            {**scope.fixed, **dict(zip(names, map(np.float64, p)))},
            scope.degrees,
        )
        return np.ravel(_f(Ev(s).ev(row.rhs))) - y

    lam = 1e-3
    r = resid(p)
    for _ in range(100):
        J = np.empty((len(r), len(p)))
        for i in range(len(p)):
            h = 1e-6 * (1 + abs(p[i]))
            d = np.zeros_like(p)
            d[i] = h
            J[:, i] = (resid(p + d) - r) / h
        A, g = J.T @ J, J.T @ r
        step = np.linalg.solve(A + lam * np.diag(np.diag(A) + 1e-12), -g)
        r2 = resid(p + step)
        if np.nansum(r2**2) < np.nansum(r**2):
            p, r, lam = p + step, r2, lam / 3
            if np.abs(step).max() < 1e-10 * (1 + np.abs(p).max()):
                break
        else:
            lam *= 4
    ss = np.nansum((y - y.mean()) ** 2)
    row.fit = {n: float(v) for n, v in zip(names, p)}
    row.r2 = float(1 - np.nansum(r**2) / ss) if ss else 1.0
    scope.fixed.update({n: np.float64(v) for n, v in zip(names, p)})


# --- Выборка точек
def _runs(ok):
    e = np.flatnonzero(np.diff(np.concatenate([[0], ok.view(np.int8), [0]])))
    return list(zip(e[::2], e[1::2]))


def _curve(t, X, Y, view, f=None):
    x0, x1, y0, y1 = view
    W, H = x1 - x0, y1 - y0
    X, Y = np.broadcast_arrays(_f(X), _f(Y))
    ok = np.isfinite(X) & np.isfinite(Y)
    step = np.maximum(np.abs(np.diff(X)) / W, np.abs(np.diff(Y)) / H)
    if f is None:
        jumps = np.flatnonzero(step > 1)
    else:
        fin = step[np.isfinite(step)]
        cand = np.flatnonzero(step > max(4 * (np.median(fin) if len(fin) else 0), 1e-3))
        a, b = t[cand], t[cand + 1]
        Pa, Pb = np.stack([X[cand], Y[cand]]), np.stack([X[cand + 1], Y[cand + 1]])
        d0 = step[cand]
        nrm = lambda P, Q: np.maximum(np.abs(P[0] - Q[0]) / W, np.abs(P[1] - Q[1]) / H)
        for _ in range(12 if len(cand) else 0):
            m = (a + b) / 2
            Pm = np.stack(np.broadcast_arrays(*map(_f, f(m)))).reshape(2, -1)
            left = ~(nrm(Pm, Pb) > nrm(Pa, Pm))
            b, Pb = np.where(left, m, b), np.where(left, Pm, Pb)
            a, Pa = np.where(left, a, m), np.where(left, Pa, Pm)
        d1 = nrm(Pa, Pb)
        jumps = cand[~(d1 < 0.5 * d0)]
    brk = np.zeros(len(X), bool)
    brk[jumps + 1] = True
    out = []
    for a, b in _runs(ok):
        cuts = [a] + [i for i in np.flatnonzero(brk[a:b]) + a if i > a] + [b]
        for i, j in zip(cuts, cuts[1:]):
            if j - i >= 2:
                out.append(
                    np.column_stack(
                        [np.clip(X[i:j], x0 - 4 * W, x1 + 4 * W), np.clip(Y[i:j], y0 - 4 * H, y1 + 4 * H)]
                    )
                )
    return out


_MS = {
    1: [(3, 0)],
    2: [(0, 1)],
    3: [(3, 1)],
    4: [(1, 2)],
    5: [(3, 0), (1, 2)],
    6: [(0, 2)],
    7: [(3, 2)],
    8: [(2, 3)],
    9: [(0, 2)],
    10: [(0, 1), (2, 3)],
    11: [(1, 2)],
    12: [(1, 3)],
    13: [(0, 1)],
    14: [(0, 3)],
}


def contour(F, xs, ys):
    a, b, c, d = F[:-1, :-1], F[:-1, 1:], F[1:, 1:], F[1:, :-1]
    case = (a > 0) * 1 + (b > 0) * 2 + (c > 0) * 4 + (d > 0) * 8
    case[~(np.isfinite(a) & np.isfinite(b) & np.isfinite(c) & np.isfinite(d))] = 0
    X0, Y0 = np.meshgrid(xs[:-1], ys[:-1])
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    with np.errstate(all="ignore"):
        lerp = lambda p, q: np.clip(p / (p - q), 0, 1)
        E = [
            (X0 + lerp(a, b) * dx, Y0),
            (X0 + dx, Y0 + lerp(b, c) * dy),
            (X0 + lerp(d, c) * dx, Y0 + dy),
            (X0, Y0 + lerp(a, d) * dy),
        ]
    segs = []
    for k, pairs in _MS.items():
        m = case == k
        if not m.any():
            continue
        for e1, e2 in pairs:
            segs.append(
                np.stack(
                    [
                        np.column_stack([E[e1][0][m], E[e1][1][m]]),
                        np.column_stack([E[e2][0][m], E[e2][1][m]]),
                    ],
                    1,
                )
            )
    return np.concatenate(segs) if segs else np.zeros((0, 2, 2))


def _domain(text, default, scope):
    if not text.strip():
        return default
    st = parse(f"[{text}]")
    v = np.ravel(Ev(scope).ev(st[1]))
    return float(v[0]), float(v[-1])


def geometry(row: Row, scope: Scope, view, px, quality=1.0, domain=""):
    x0, x1, y0, y1 = view
    n = int(np.clip(px[0] * quality, 64, 4000))
    k = row.kind
    if k in ("y", "x") or (k == "func" and row.plot) or k == "reg":
        if k == "func":
            k, node, var = ("y" if row.params == ["x"] else "x"), row.body, row.params[0]
        elif k == "reg":
            data = [v for v in free_names(row.rhs) if v in scope.fixed and np.ndim(scope.fixed[v]) == 1]
            if not data:
                return []
            node, var, k = row.rhs, data[0], "y"
        else:
            node, var = row.node, "x" if k == "y" else "y"
        lo, hi = (x0, x1) if k == "y" else (y0, y1)
        t = np.linspace(lo, hi, n)
        f = lambda s: _f(Ev(scope, {var: s}, 1).ev(node))
        V = f(t)
        if isinstance(V, tuple):
            raise EvalError("Ожидалось число, получена точка")
        if np.size(V) > MAX_CURVES * n:
            raise EvalError(f"Количество кривых превышает {MAX_CURVES}")
        V = np.broadcast_to(V, np.broadcast_shapes(np.shape(V), t.shape)).reshape(-1, n)
        one = len(V) == 1
        out = []
        for vr in V:
            if k == "y":
                out += _curve(t, t, vr, view, (lambda s: (s, f(s))) if one else None)
            else:
                out += _curve(t, vr, t, view, (lambda s: (f(s), s)) if one else None)
        return [("lines", out)]
    if k in ("polar", "param"):
        lo, hi = _domain(domain, (0, 2 * np.pi) if k == "polar" else (0, 1), scope)
        t = np.linspace(lo, hi, int(np.clip(1000 * quality, 100, 6000)))
        if k == "polar":

            def f(s):
                R = _f(Ev(scope, {"theta": s}, 1).ev(row.node))
                return R * np.cos(s), R * np.sin(s)
        else:

            def f(s):
                P = Ev(scope, {"t": s}, 1).ev(row.node)
                if not isinstance(P, tuple):
                    raise EvalError("Параметрическая кривая задаётся парой (x(t), y(t))")
                return P

        X, Y = np.broadcast_arrays(*map(_f, f(t)))
        if X.size > MAX_CURVES * len(t):
            raise EvalError(f"Количество кривых превышает {MAX_CURVES}")
        out = []
        for xr, yr in zip(X.reshape(-1, len(t)), Y.reshape(-1, len(t))):
            out += _curve(t, xr, yr, view, f if X.ndim == 1 else None)
        return [("lines", out)]
    if k in ("value", "define"):
        v = Ev(scope).ev(row.node)
        if isinstance(v, tuple):
            return [("dots", np.column_stack([np.ravel(v[0]), np.ravel(v[1])]))]
        return []
    if k in ("ineq_y", "ineq_x"):
        is_y = k == "ineq_y"
        t = np.linspace(x0, x1, n) if is_y else np.linspace(y0, y1, n)
        V = np.broadcast_to(_f(Ev(scope, {"x" if is_y else "y": t}, 1).ev(row.node)), t.shape)
        edge = (y1 if row.op in (">", ">=") else y0) if is_y else (x1 if row.op in (">", ">=") else x0)
        lines, polys = [], []
        for a, b in _runs(np.isfinite(V)):
            if b - a < 2:
                continue
            vv = np.clip(V[a:b], (y0 if is_y else x0) - 1e3, (y1 if is_y else x1) + 1e3)
            P = np.column_stack([t[a:b], vv]) if is_y else np.column_stack([vv, t[a:b]])
            lines.append(P)
            E = [[t[b - 1], edge], [t[a], edge]] if is_y else [[edge, t[b - 1]], [edge, t[a]]]
            polys.append(np.vstack([P, E]))
        dashed = row.op in ("<", ">")
        return [("fill", polys), ("lines", lines, dashed)]
    if k in ("implicit", "region"):
        gw = int(np.clip(px[0] * quality / 4, 24, 400))
        gh = int(np.clip(px[1] * quality / 4, 24, 400))
        xs, ys = np.linspace(x0, x1, gw), np.linspace(y0, y1, gh)
        X, Y = np.meshgrid(xs, ys)
        ev = Ev(scope, {"x": X, "y": Y}, 2)
        if k == "implicit":
            L = _f(ev.ev(row.lhs))
            R = _f(ev.ev(row.rhs))
            if math.prod(np.broadcast_shapes(L.shape, R.shape)) > 20 * gw * gh:
                raise EvalError("Количество неявных кривых превышает 20")
            F = L - R
            F = F.reshape((-1, gh, gw)) if F.ndim >= 2 else np.full((1, gh, gw), F)
            return [("segs", np.concatenate([contour(f, xs, ys) for f in F]))]
        vals = [_f(ev.ev(t)) for t in row.terms]
        mask = np.ones((gh, gw), bool)
        for op, a, b in zip(row.ops, vals, vals[1:]):
            mask &= np.broadcast_to(_CMP[op](a, b), (gh, gw))
        near = mask.copy()
        near[1:] |= mask[:-1]
        near[:-1] |= mask[1:]
        near[:, 1:] |= near[:, :-1].copy()
        near[:, :-1] |= near[:, 1:].copy()
        prims = [("mask", mask, (x0, x1, y0, y1))]
        for op, a, b in zip(row.ops, vals, vals[1:]):
            S = contour(np.broadcast_to(a - b, (gh, gw)), xs, ys)
            if len(S):
                mid = S.mean(1)
                ci = np.clip(((mid[:, 0] - x0) / (x1 - x0) * (gw - 1)).astype(int), 0, gw - 1)
                ri = np.clip(((mid[:, 1] - y0) / (y1 - y0) * (gh - 1)).astype(int), 0, gh - 1)
                prims.append(("segs", S[near[ri, ci]], op in ("<", ">")))
        return prims
    if k == "table":
        X, Y = scope.fixed[row.cols[0]], [scope.fixed[c] for c in row.cols[1:]]
        return [("dots", np.column_stack([X[: len(y)], y[: len(X)]])) for y in Y]
    return []


def compute(rows, defs, funcs, data, params, degrees=False) -> Scope:
    scope = Scope(defs, funcs, params, dict(data), degrees)
    for r in rows:
        if r.kind == "reg":
            try:
                r.error = None
                fit(scope, r)
            except Exception as ex:
                r.error, r.fit = error_message(ex), None
    for r in rows:
        if r.kind in ("value", "define"):
            try:
                r.error = None
                v = Ev(scope).ev(r.node)
                r.value = v if isinstance(v, tuple) else (float(v) if np.ndim(v) == 0 else np.asarray(v))
            except EvalError as ex:
                r.error, r.value = str(ex), None
    return scope
