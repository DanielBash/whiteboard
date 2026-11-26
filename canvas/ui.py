from __future__ import annotations

from .qt import QtWidgets, QColor, Qt, QtCore
from .settings import Bool, Bound, Choice, Color, Float, Int, Str

W = QtWidgets


def _guard():
    busy = [False]

    def run(fn, *a):
        if not busy[0]:
            busy[0] = True
            try:
                fn(*a)
            finally:
                busy[0] = False

    return run


def _pick_color(widget, b):
    color = QColor(b.value)
    dialog = W.QDialog(widget)
    dialog.setWindowTitle(b.label)
    layout = W.QVBoxLayout(dialog)
    preview = W.QFrame()
    preview.setMinimumSize(320, 50)
    layout.addWidget(preview)

    def refresh():
        preview.setStyleSheet(f"background: {color.name(QColor.NameFormat.HexArgb)}; border-radius: 10px;")

    refresh()
    swatches = W.QHBoxLayout()
    layout.addLayout(swatches)
    channels = []
    for title, getter, setter in (
        ("Красный", color.red, color.setRed), ("Зелёный", color.green, color.setGreen),
        ("Синий", color.blue, color.setBlue), ("Непрозрачность", color.alpha, color.setAlpha),
    ):
        layout.addWidget(W.QLabel(title))
        slider = W.QSlider(Qt.Orientation.Horizontal)
        slider.setAccessibleName(title)
        slider.setRange(0, 255)
        slider.setValue(getter())
        channels.append(slider)

        def change(value, apply=setter):
            apply(value)
            refresh()

        slider.valueChanged.connect(change)
        layout.addWidget(slider)
    def use_swatch(rgb):
        chosen = QColor(rgb)
        for slider, value in zip(channels, (chosen.red(), chosen.green(), chosen.blue())):
            slider.setValue(value)

    for name, rgb in (("Чернила", "#202a29"), ("Красный", "#c64438"), ("Синий", "#3264af"),
                      ("Зелёный", "#277849"), ("Фиолетовый", "#7650a5"), ("Жёлтый", "#f6be3e")):
        button = W.QPushButton()
        button.setFixedSize(46, 46)
        button.setAccessibleName(name)
        button.setToolTip(name)
        button.setStyleSheet(f"background: {rgb}; border-radius: 23px;")
        button.clicked.connect(lambda checked, value=rgb: use_swatch(value))
        swatches.addWidget(button)
    actions = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok | W.QDialogButtonBox.StandardButton.Cancel)
    actions.accepted.connect(dialog.accept)
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    if dialog.exec():
        b.value = color
    dialog.deleteLater()


def bind(widget, b: Bound):
    s, g = b.spec, _guard()
    if isinstance(widget, W.QAbstractSlider):
        lower, upper = s.meta.get("min"), s.meta.get("max")
        lo = lower if lower is not None else min(-100, b.value, (upper or 0) - 100)
        hi = upper if upper is not None else max(100, b.value, lo + 100)
        integer = isinstance(s, Int)

        def to_w(value):
            nonlocal lo, hi
            if lower is None:
                lo = min(lo, value)
            if upper is None:
                hi = max(hi, value)
            steps = int(hi - lo) if integer else 1000
            widget.setRange(0, steps)
            widget.setValue(round((value - lo) / (hi - lo) * steps) if hi > lo else 0)

        def from_slider(index):
            value = lo + index if integer else lo + (hi - lo) * index / widget.maximum()
            b.value = value

        def expand_range():
            nonlocal lo, hi
            if lower is None and widget.value() == widget.minimum():
                lo -= hi - lo
            elif upper is None and widget.value() == widget.maximum():
                hi += hi - lo
            g(to_w, b.value)

        widget.valueChanged.connect(lambda index: g(from_slider, index))
        widget.sliderReleased.connect(expand_range)
    elif isinstance(widget, (W.QDoubleSpinBox, W.QSpinBox)):
        lo, hi = s.meta.get("min"), s.meta.get("max")
        lo, hi = -1e9 if lo is None else lo, 1e9 if hi is None else hi
        if isinstance(widget, W.QSpinBox):
            lo, hi = int(max(lo, -(2**31) + 1)), int(min(hi, 2**31 - 1))
        widget.setRange(lo, hi)
        if isinstance(widget, W.QDoubleSpinBox):
            widget.setDecimals(3)
            widget.setSingleStep(s.meta.get("step") or 0.1)
        to_w = widget.setValue
        widget.valueChanged.connect(lambda v: g(setattr, b, "value", v))
    elif isinstance(widget, W.QComboBox):
        if not widget.count():
            labels = s.meta.get("choice_labels", {})
            widget.addItems([labels.get(c, str(c)) for c in s.choices])
        to_w = lambda v: widget.setCurrentIndex(s.choices.index(v))
        widget.currentIndexChanged.connect(lambda i: g(setattr, b, "value", s.choices[i]) if i >= 0 else None)
    elif isinstance(widget, W.QPushButton) and isinstance(s, Color):

        def to_w(v):
            widget.setStyleSheet(f"background:{QColor(v).name()};min-width:40px;min-height:44px;")
            widget.setToolTip(v)

        widget.clicked.connect(lambda: _pick_color(widget, b))
    elif isinstance(widget, W.QAbstractButton):
        widget.setCheckable(True)
        to_w = widget.setChecked
        widget.toggled.connect(lambda v: g(setattr, b, "value", v))
    elif isinstance(widget, W.QLineEdit):
        to_w = lambda v: widget.setText(str(v)) if widget.text() != str(v) else None
        widget.editingFinished.connect(lambda: g(setattr, b, "value", widget.text()))
    elif isinstance(widget, W.QPlainTextEdit):
        to_w = lambda v: widget.setPlainText(v) if widget.toPlainText() != v else None
        widget.textChanged.connect(lambda: g(setattr, b, "value", widget.toPlainText()))
    elif isinstance(widget, W.QLabel):
        to_w = lambda v: widget.setText(
            QtCore.QLocale().toString(v, "g", 3) if isinstance(v, float) else str(v)
        )
    else:
        raise TypeError(f"don't know how to bind {type(widget).__name__}")
    g(
        to_w, b.value
    )  # Защита от обратной записи: отображение значения не должно менять его из-за округления ползунка
    unbind = b.connect(lambda v: g(to_w, v))
    widget.destroyed.connect(lambda *a: unbind())
    return unbind


def _number_editor(b, parent):
    w = W.QWidget(parent)
    lay = W.QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    sl, lab = W.QSlider(Qt.Orientation.Horizontal, w), W.QLabel(w)
    sl.setAccessibleName(b.label)
    lab.setMinimumWidth(42)
    lay.addWidget(sl, 1)
    lay.addWidget(lab)
    bind(sl, b)
    bind(lab, b)
    return w


def _choice_editor(b, parent):
    widget = W.QWidget(parent)
    layout = W.QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    group = W.QButtonGroup(widget)
    choices = b.spec.choices
    labels = b.spec.meta.get("choice_labels", {})
    for index, value in enumerate(choices):
        button = W.QRadioButton(labels.get(value, str(value)))
        button.setMinimumHeight(44)
        group.addButton(button, index)
        layout.addWidget(button)

    def select(value):
        group.button(choices.index(value)).setChecked(True)

    select(b.value)
    group.idClicked.connect(lambda index: setattr(b, "value", choices[index]))
    unbind = b.connect(select)
    widget.destroyed.connect(lambda *_: unbind())
    return widget


def _simple(cls):
    def make(b, parent):
        w = cls(parent)
        bind(w, b)
        return w

    return make


EDITORS = {
    Float: _number_editor,
    Int: _number_editor,
    Bool: _simple(W.QCheckBox),
    Choice: _choice_editor,
    Color: _simple(W.QPushButton),
    Str: lambda b, parent: _simple(W.QPlainTextEdit if b.spec.multiline else W.QLineEdit)(b, parent),
}


def editor(b: Bound, parent=None):
    for cls in type(b.spec).__mro__:
        if cls in EDITORS:
            return EDITORS[cls](b, parent)
    return None


def auto_panel(obj, names=None, parent=None) -> W.QWidget:
    w = W.QWidget(parent)
    form = W.QFormLayout(w)
    form.setRowWrapPolicy(W.QFormLayout.RowWrapPolicy.WrapAllRows)
    for b in obj.settings:
        if (names is None and b.spec.ui) or (names is not None and b.name in names):
            e = editor(b, w)
            if e is not None:
                form.addRow(b.label, e)
    return w
