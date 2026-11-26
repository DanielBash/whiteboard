from __future__ import annotations

import re

FUNCS = {
    "sin",
    "cos",
    "tan",
    "sec",
    "csc",
    "cot",
    "arcsin",
    "arccos",
    "arctan",
    "arcsec",
    "arccsc",
    "arccot",
    "asin",
    "acos",
    "atan",
    "sinh",
    "cosh",
    "tanh",
    "sech",
    "csch",
    "coth",
    "arcsinh",
    "arccosh",
    "arctanh",
    "arsinh",
    "arcosh",
    "artanh",
    "ln",
    "log",
    "exp",
    "sqrt",
    "cbrt",
    "nthroot",
    "abs",
    "sign",
    "sgn",
    "floor",
    "ceil",
    "round",
    "mod",
    "min",
    "max",
    "gcd",
    "lcm",
    "nCr",
    "nPr",
    "mean",
    "median",
    "total",
    "length",
    "stdev",
    "stddev",
    "var",
    "sort",
    "unique",
    "join",
    "distance",
    "midpoint",
    "sum",
    "prod",
    "int",
    "integral",
    "diff",
}
WORDS = sorted(FUNCS | {"pi", "tau", "theta", "infinity", "inf", "for"}, key=len, reverse=True)
INVERSE = {
    "sin": "arcsin",
    "cos": "arccos",
    "tan": "arctan",
    "sec": "arcsec",
    "csc": "arccsc",
    "cot": "arccot",
    "sinh": "arcsinh",
    "cosh": "arccosh",
    "tanh": "arctanh",
}
UNICODE = {
    "·": "*",
    "×": "*",
    "÷": "/",
    "−": "-",
    "≤": "<=",
    "≥": ">=",
    "π": "pi",
    "θ": "theta",
    "τ": "tau",
    "√": "sqrt",
    "…": "...",
    "²": "^2",
    "³": "^3",
    "∞": "inf",
    "∑": "sum",
    "∏": "prod",
    "∫": "int",
    "\\left": "",
    "\\right": "",
    "\\cdot": "*",
    "\\pi": "pi",
    "\\theta": "theta",
}
RELATIONS = {"=", "<", ">", "<=", ">=", "~"}
_NUM = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_DERIV = re.compile(r"d/d([a-zA-Z])")
_SUB = re.compile(r"_\{?([A-Za-z0-9]+)\}?|(\d+)")


class ParseError(ValueError):
    pass


def tokenize(s: str) -> list:
    for k, v in UNICODE.items():
        s = s.replace(k, v)
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
        elif s.startswith("...", i):
            out.append(("op", "..."))
            i += 3
        elif m := _NUM.match(s, i):
            out.append(("num", float(m.group())))
            i = m.end()
        elif m := _DERIV.match(s, i):
            out.append(("deriv", m.group(1)))
            i = m.end()
        elif c.isalpha():
            w = next((w for w in WORDS if s.startswith(w, i)), None)
            if w:
                kind = "fn" if w in FUNCS else "kw" if w == "for" else "id"
                out.append((kind, {"infinity": "inf"}.get(w, w)))
                i += len(w)
            else:
                name, i = c, i + 1
                if (m := _SUB.match(s, i)) and not (s[i] == "_" and m.group(1) is None):
                    name += "_" + (m.group(1) or m.group(2))
                    i = m.end()
                out.append(("id", name))
        elif s.startswith(("<=", ">="), i):
            out.append(("op", s[i : i + 2]))
            i += 2
        elif c in "+-*/^(),[]{}:=<>~|!'._°":
            out.append(("op", c))
            i += 1
        else:
            raise ParseError(f"Неожиданный символ «{c}»")
    return out


class Parser:
    def __init__(self, text):
        self.toks = tokenize(text)
        self.i = 0
        self.abs = 0

    def peek(self, k=0):
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else ("eof", None)

    def next(self):
        t = self.peek()
        self.i += 1
        return t

    def is_op(self, *ops, k=0):
        t = self.peek(k)
        return t[0] == "op" and t[1] in ops

    def accept(self, op):
        if self.is_op(op):
            self.i += 1
            return True
        return False

    def expect(self, op):
        if not self.accept(op):
            t = self.peek()
            raise ParseError(
                f"Ожидался символ «{op}»"
                + (f" рядом с «{t[1]}»" if t[1] is not None else " в конце выражения")
            )

    # --- Утверждения
    def statement(self):
        if not self.toks:
            return None
        e = self.expr()
        rels = []
        while self.is_op(*RELATIONS):
            op = self.next()[1]
            rels.append((op, self.expr()))
        if self.peek()[0] != "eof":
            raise ParseError(f"Неожиданный символ «{self.peek()[1]}»")
        if not rels:
            return ("expr", e)
        if len(rels) == 1:
            op, rhs = rels[0]
            return ("eq", e, rhs) if op == "=" else ("reg", e, rhs) if op == "~" else ("ineq", op, e, rhs)
        if any(op in ("=", "~") for op, _ in rels):
            raise ParseError("Знаки «=» и «~» нельзя объединять в цепочку")
        return ("chain", [op for op, _ in rels], [e] + [r for _, r in rels])

    def condition(self):
        e = self.expr()
        ops, terms = [], [e]
        while self.is_op("=", "<", ">", "<=", ">="):
            ops.append(self.next()[1])
            terms.append(self.expr())
        return ("cmp", ops, terms) if ops else e

    # --- Выражения
    def expr(self):
        left = self.term()
        while self.is_op("+", "-"):
            op = self.next()[1]
            left = ("bin", op, left, self.term())
        return left

    def term(self):
        left = self.unary()
        while True:
            if self.is_op("*", "/"):
                op = self.next()[1]
                left = ("bin", op, left, self.unary())
            elif self.starts_primary():
                left = ("bin", "*", left, self.power())
            else:
                return left

    def starts_primary(self, allow_fn=True):
        k, v = self.peek()
        if k in ("num", "id", "deriv"):
            return True
        if k == "fn":
            return allow_fn
        return k == "op" and (v in ("(", "[", "{") or (v == "|" and self.abs == 0))

    def unary(self):
        if self.accept("-"):
            return ("neg", self.unary())
        if self.accept("+"):
            return self.unary()
        return self.power()

    def power(self):
        base = self.postfix()
        if self.accept("^"):
            return ("bin", "^", base, self.exponent())
        return base

    def exponent(self):
        if self.accept("{"):
            e = self.expr()
            self.expect("}")
            return e
        if self.accept("-"):
            return ("neg", self.exponent())
        return self.power()

    def postfix(self):
        e = self.primary()
        while True:
            if self.accept("!"):
                e = ("fact", e)
            elif self.accept("°"):
                e = ("deg", e)
            elif self.is_op("[") and e[0] in ("var", "list", "index", "call", "callish", "range", "comp"):
                self.next()
                e = ("index", e, self.items("]"))
            elif self.is_op(".") and self.peek(1)[0] == "id" and self.peek(1)[1] in ("x", "y"):
                self.next()
                e = ("member", e, self.next()[1])
            else:
                return e

    def items(self, close):
        out = [self.condition()]
        while self.accept(","):
            out.append(self.condition())
        self.expect(close)
        return out

    def primary(self):
        k, v = self.next()
        if k == "num":
            return ("num", v)
        if k == "id":
            if self.is_op("'"):
                n = 0
                while self.accept("'"):
                    n += 1
                self.expect("(")
                return ("prime", v, n, self.items(")"))
            if self.is_op("(") and v not in ("x", "y", "t", "theta", "r", "e", "pi", "tau"):
                self.next()
                return ("callish", v, self.items(")"))
            return ("var", v)
        if k == "fn":
            return self.function(v)
        if k == "deriv":
            return ("deriv", v, self.fn_arg())
        if k == "op":
            if v == "(":
                e = self.expr()
                if self.accept(","):
                    e = ("point", e, self.expr())
                self.expect(")")
                return e
            if v == "[":
                return self.list_literal()
            if v == "{":
                return self.piecewise()
            if v == "|":
                self.abs += 1
                e = self.expr()
                self.abs -= 1
                self.expect("|")
                return ("call", "abs", [e])
        raise ParseError(f"Неожиданный символ «{v}»" if v is not None else "Выражение не завершено")

    def fn_arg(self):
        if self.is_op("("):
            return self.power()
        e = self.power()
        while self.starts_primary(allow_fn=False) and not self.is_op("{"):
            e = ("bin", "*", e, self.power())
        return e

    def function(self, name):
        base = None
        if name == "log" and self.accept("_"):
            base = self.exponent()
        power = None
        if self.accept("^"):
            power = self.exponent()
        if self.accept("("):
            args = self.items(")") if self.peek() != ("op", ")") else []
            if not args:
                self.expect(")")
        else:
            args = [self.fn_arg()]
        if base is not None:
            args = args + [base]
        if power == ("neg", ("num", 1.0)) and name in INVERSE:
            return ("call", INVERSE[name], args)
        call = ("call", name, args)
        return ("bin", "^", call, power) if power is not None else call

    def list_literal(self):
        if self.accept("]"):
            return ("list", [])
        first = self.expr()
        if self.peek() == ("kw", "for"):
            self.next()
            gens = []
            while True:
                k, var = self.next()
                if k != "id":
                    raise ParseError("После «for» ожидается переменная")
                self.expect("=")
                gens.append((var, self.expr()))
                if not self.accept(","):
                    break
            self.expect("]")
            return ("comp", first, gens)
        items = [first]
        while self.accept(","):
            if self.is_op("..."):
                break
            items.append(self.expr())
        if self.accept("..."):
            self.accept(",")
            last = self.expr()
            self.expect("]")
            if len(items) > 2:
                raise ParseError("Задайте диапазон в виде [a...b] или [a, a2...b]")
            return ("range", items[0], items[1] if len(items) == 2 else None, last)
        self.expect("]")
        return ("list", items)

    def piecewise(self):
        items = []
        while True:
            c = self.condition()
            items.append((c, self.expr() if self.accept(":") else None))
            if not self.accept(","):
                break
        self.expect("}")
        return ("piece", items)


def parse(text: str):
    if len(text) > 5000:
        raise ParseError("Выражение слишком длинное")
    try:
        return Parser(text).statement()
    except RecursionError:
        raise ParseError("Слишком много уровней вложенности") from None
