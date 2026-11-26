import importlib
import os

_order = ["PyQt6", "PySide6"] if os.environ.get("CANVAS_QT", "").lower() == "pyqt6" else ["PySide6", "PyQt6"]
for BINDING in _order:
    try:
        QtCore = importlib.import_module(BINDING + ".QtCore")
        QtGui = importlib.import_module(BINDING + ".QtGui")
        QtWidgets = importlib.import_module(BINDING + ".QtWidgets")
        break
    except ImportError:
        continue
else:
    raise ImportError("canvas needs PySide6 or PyQt6")

Signal = getattr(QtCore, "Signal", None) or QtCore.pyqtSignal
Qt = QtCore.Qt
QPointF, QRectF, QSizeF, QLineF, QTimer, QEvent, QObject = (
    QtCore.QPointF,
    QtCore.QRectF,
    QtCore.QSizeF,
    QtCore.QLineF,
    QtCore.QTimer,
    QtCore.QEvent,
    QtCore.QObject,
)
QPainter, QPen, QBrush, QColor, QImage, QPainterPath, QTransform, QFont, QFontMetricsF, QPolygonF, QRegion = (
    QtGui.QPainter,
    QtGui.QPen,
    QtGui.QBrush,
    QtGui.QColor,
    QtGui.QImage,
    QtGui.QPainterPath,
    QtGui.QTransform,
    QtGui.QFont,
    QtGui.QFontMetricsF,
    QtGui.QPolygonF,
    QtGui.QRegion,
)


def svg_generator():
    return importlib.import_module(BINDING + ".QtSvg").QSvgGenerator
