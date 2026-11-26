import copy
import math
import gzip
import json
import sys
from pathlib import Path

from canvas import Canvas, Image, auto_panel
from graph import Graph
from physics import Body, Rope, Sim, Spring
from canvas.qt import QPointF, Qt, QtCore, QtGui, QTimer, QtWidgets
from tools.registry import TOOL_GROUPS, TOOL_LABELS, create_tools

W = QtWidgets
ITEM_LABELS = {
    "Stroke": "Линия",
    "Text": "Текст",
    "Image": "Изображение",
    "Graph": "График",
    "Body": "Тело",
    "Rope": "Верёвка",
    "Spring": "Пружина",
    "Force": "Сила",
    "Background": "Фон",
}
BLANK = {"items": [], "view": [1, 0, 0, 1, 0, 0]}


class Board(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Доска")
        self.canvas = c = Canvas()
        self.sim = Sim(c)
        self.tools = create_tools()
        c.pen_eraser = self.tools["eraser"]
        self.pages, self.page = [None], 0
        self._build_ui()
        self._connect_signals()
        self.set_tool("brush")
        self.resize(1400, 900)

    def _connect_signals(self):
        c = self.canvas
        c.toolChanged.connect(lambda *_: self._panel())
        c.selectionChanged.connect(self._panel)
        c.timelineChanged.connect(self._timeline_ui)
        self.sim.signals.frameChanged.connect(self._sim_ui)
        self.sim.signals.playingChanged.connect(self._sim_playing_ui)

    def _build_ui(self):
        self._build_settings_panel()
        self._build_tool_bar()
        self._build_timeline_bar()
        self._build_simulation_bar()
        self._build_page_bar()
        self._build_menus()
        self.settings_dock.hide()
        self.sim_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Space"), self)
        self.sim_shortcut.activated.connect(self.sim.toggle)

    def _build_tool_bar(self):
        workspace = W.QWidget()
        layout = W.QHBoxLayout(workspace)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        rail = W.QFrame()
        rail.setObjectName("toolRail")
        rail.setFixedWidth(224)
        column = W.QVBoxLayout(rail)
        column.setContentsMargins(12, 12, 12, 12)
        column.setSpacing(8)
        settings = W.QToolButton()
        settings.setDefaultAction(self.settings_dock.toggleViewAction())
        settings.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        settings.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Fixed)
        column.addWidget(settings)

        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        palette = W.QWidget()
        palette.setObjectName("toolPalette")
        buttons = W.QVBoxLayout(palette)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(4)
        group = W.QButtonGroup(rail)
        self.tool_buttons = {}
        captions = {"select": "Выбор", "pan": "Сдвиг", "line": "Линия", "body": "Тело",
                    "rope": "Связь", "grab": "Захват"}
        icon_dir = Path(__file__).parent / "assets" / "tools"
        names = ("brush", "eraser", "line", "select", "pan", "graph")
        self.physics_names = tuple(cls.name for cls, _ in TOOL_GROUPS["Физика"])
        self.physics_tools = W.QWidget()
        for container, tool_names in ((palette, names), (self.physics_tools, self.physics_names)):
            grid = W.QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(6)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            if container is palette:
                buttons.addLayout(grid)
            else:
                container.setLayout(grid)
                buttons.addWidget(container)
            for index, name in enumerate(tool_names):
                label = TOOL_LABELS[name]
                button = W.QToolButton()
                button.setObjectName("toolTile")
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
                button.setText(captions.get(name, label))
                button.setAccessibleName(label)
                button.setToolTip(label)
                button.setIcon(QtGui.QIcon(str(icon_dir / f"{name}.svg")))
                button.setIconSize(QtCore.QSize(28, 28))
                button.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Fixed)
                button.setFixedHeight(72)
                button.setCheckable(True)
                group.addButton(button)
                button.clicked.connect(lambda checked, key=name: self.set_tool(key))
                grid.addWidget(button, index // 2, index % 2)
                self.tool_buttons[name] = button
        self.physics_tools.hide()
        buttons.addStretch()
        scroll.setWidget(palette)
        W.QScroller.grabGesture(scroll.viewport(), W.QScroller.ScrollerGestureType.TouchGesture)
        column.addWidget(scroll)
        zoom = W.QHBoxLayout()
        column.addLayout(zoom)
        for symbol, title, factor in (("−", "Отдалить", 0.8), ("+", "Приблизить", 1.25)):
            button = W.QPushButton(symbol)
            button.setAccessibleName(title)
            button.setToolTip(title)
            button.clicked.connect(lambda checked, f=factor: self.canvas.zoom_at(f))
            button.setFixedWidth(54)
            zoom.addWidget(button)
            if factor < 1:
                level = W.QLabel("100%")
                level.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.canvas.viewChanged.connect(lambda: level.setText(f"{self.canvas.zoom:.0%}"))
                zoom.addWidget(level, 1)
        layout.addWidget(rail)
        layout.addWidget(self.canvas, 1)
        self.setCentralWidget(workspace)
        self.setStyleSheet("""
            QMainWindow, QToolBar, QMenuBar, QDockWidget, QScrollArea { background: #f8f5eb; color: #17372e; }
            QMenuBar::item { padding: 5px 12px; }
            #toolRail, #toolRail QScrollArea, #toolPalette { background: #f8f5eb; }
            #toolRail QLabel { color: #17372e; font-size: 15px; font-weight: bold; }
            #toolPalette QLabel { padding-top: 6px; }
            #toolRail QToolButton, #toolRail QPushButton {
                background: #e7e9dc; color: #183930; border: none; border-radius: 12px;
                min-height: 46px; font-size: 16px; font-weight: bold;
            }
            #toolRail QToolButton#toolTile { padding: 0; background: #e7e9dc; }
            #toolRail QToolButton#toolTile:checked, #toolRail QToolButton:checked { background: #ffc879; }
            #toolRail QPushButton { min-width: 54px; font-size: 26px; }
            QToolBar { border: none; spacing: 6px; padding: 6px 12px; }
            QToolBar QToolButton { min-height: 46px; padding: 0 12px; border-radius: 12px;
                color: #17372e; background: #e7e9dc; font-size: 15px; }
            QToolBar QToolButton:checked, QToolBar QToolButton:pressed { background: #ffd193; }
            QDockWidget { font-size: 15px; }
            QDockWidget::title { background: #e3e9d9; padding: 10px; }
            QGroupBox { font-weight: bold; border: none; margin-top: 18px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; }
            QSlider:horizontal { min-height: 42px; }
            QSlider::groove:horizontal { height: 6px; border-radius: 3px; background: #c1cbbf; }
            QSlider::sub-page:horizontal { background: #427958; border-radius: 3px; }
            QSlider::handle:horizontal { background: #24503f; width: 24px; margin: -9px 0; border-radius: 12px; }
        """)

    def _build_settings_panel(self):
        self.panel = W.QScrollArea()
        self.panel.setWidgetResizable(True)
        self.panel.setMinimumWidth(320)
        dock = W.QDockWidget("Настройки")
        self.settings_dock = dock
        dock.setWidget(self.panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

    def _build_timeline_bar(self):
        bar = W.QToolBar("История рисования")
        self.history_bar = bar
        bar.setMovable(False)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, bar)
        bar.hide()
        self.replay = bar.addAction("Воспроизвести историю", self.toggle_replay)
        self.tslider = W.QSlider(Qt.Orientation.Horizontal)
        self.tslider.setRange(0, 0)
        self.tslider.valueChanged.connect(
            lambda v: self.canvas.timeline.seek(min(v / 10, self.canvas.timeline.end))
        )
        bar.addWidget(self.tslider)
        self.tlabel = W.QLabel(" 0,0 с ")
        bar.addWidget(self.tlabel)

    def _build_simulation_bar(self):
        self.addToolBarBreak(Qt.ToolBarArea.BottomToolBarArea)
        bar = W.QToolBar("Симуляция")
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, bar)
        bar.setMovable(False)
        self.physics_bar = bar
        bar.toggleViewAction().toggled.connect(self._show_physics_tools)
        bar.hide()
        bar.addWidget(W.QLabel(" Физика "))
        self.sim_play = bar.addAction("Запустить", self.sim.toggle)
        bar.addAction("В начало", self.sim.stop)
        bar.addAction("Назад на 0,5 с", lambda: self.sim.back(self._half_second_frames()))
        bar.addAction("Вперёд на 0,5 с", lambda: self.sim.step(self._half_second_frames()))
        bar.addAction("К последнему кадру", self.sim.forward)
        self.fslider = W.QSlider(Qt.Orientation.Horizontal)
        self.fslider.setRange(0, 0)
        self.fslider.valueChanged.connect(self.sim.seek)
        bar.addWidget(self.fslider)
        self.flabel = W.QLabel(" 0,00 с ")
        bar.addWidget(self.flabel)

    def _build_page_bar(self):
        bar = W.QToolBar("Страницы")
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, bar)
        bar.setMovable(False)
        self.previous_page = bar.addAction("Предыдущая", lambda: self.goto(self.page - 1))
        self.plabel = W.QLabel(" 1/1 ")
        bar.addWidget(self.plabel)
        self.next_page = bar.addAction("Следующая", lambda: self.goto(self.page + 1))
        self.previous_page.setVisible(False)
        self.next_page.setVisible(False)
        bar.addAction("+ Страница", self.add_page)
        bar.addSeparator()
        bar.addAction("Отменить", self.canvas.undo)
        bar.addAction("Повторить", self.canvas.redo)
        spacer = W.QWidget()
        spacer.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Preferred)
        bar.addWidget(spacer)
        physics_toggle = self.physics_bar.toggleViewAction()
        physics_toggle.setText("Физика")
        bar.addAction(physics_toggle)

    @staticmethod
    def _add_action(menu, text, callback, shortcut=None):
        action = menu.addAction(text)
        action.triggered.connect(lambda _checked=False: callback())
        if shortcut is not None:
            action.setShortcut(QtGui.QKeySequence(shortcut))
        return action

    def _build_menus(self):
        act = self._add_action
        m = self.menuBar()
        f = m.addMenu("&Файл")
        act(f, "Новая доска", self.new, QtGui.QKeySequence.StandardKey.New)
        act(f, "Открыть", self.open, QtGui.QKeySequence.StandardKey.Open)
        act(f, "Сохранить", self.save, QtGui.QKeySequence.StandardKey.Save)
        act(f, "Экспорт страницы", self.export)
        e = m.addMenu("&Правка")
        act(e, "Отменить", self.canvas.undo, QtGui.QKeySequence.StandardKey.Undo)
        act(e, "Повторить", self.canvas.redo, QtGui.QKeySequence.StandardKey.Redo)
        act(
            e,
            "Удалить",
            lambda: self.canvas.remove(*self.canvas.selection),
            QtGui.QKeySequence("Del"),
        )
        act(
            e,
            "Сгруппировать",
            lambda: self.canvas.group(self.canvas.selection),
            QtGui.QKeySequence("Ctrl+G"),
        )
        act(
            e,
            "Разгруппировать",
            lambda: self.canvas.ungroup(self.canvas.selection),
            QtGui.QKeySequence("Ctrl+Shift+G"),
        )
        act(e, "На передний план", lambda: self.canvas.raise_(*self.canvas.selection))
        act(e, "На задний план", lambda: self.canvas.lower(*self.canvas.selection))
        act(e, "Очистить страницу", self.canvas.clear)
        i = m.addMenu("&Вставка")
        act(i, "График", self.insert_graph)
        act(i, "Изображение", self.insert_image)
        act(i, "Пример физической модели", self.physics_demo)
        v = m.addMenu("&Вид")
        v.addAction(self.history_bar.toggleViewAction())
        act(v, "Показать всё", lambda: self.canvas.fit(), QtGui.QKeySequence("Ctrl+0"))
        act(
            v,
            "Приблизить",
            lambda: self.canvas.zoom_at(1.25),
            QtGui.QKeySequence.StandardKey.ZoomIn,
        )
        act(
            v,
            "Отдалить",
            lambda: self.canvas.zoom_at(0.8),
            QtGui.QKeySequence.StandardKey.ZoomOut,
        )

    def _half_second_frames(self):
        return max(1, round(0.5 / self.sim.dt))

    def _sim_playing_ui(self, playing):
        self.sim_play.setText("Пауза" if playing else "Запустить")

    def _show_physics_tools(self, visible):
        self.physics_tools.setVisible(visible)
        if not visible and self.canvas.tool and self.canvas.tool.name in self.physics_names:
            self.set_tool("brush")

    def set_tool(self, name):
        if name in self.physics_names:
            self.physics_bar.show()
        self.canvas.tool = self.tools[name]
        self.tool_buttons[name].setChecked(True)

    def _panel(self):
        w = W.QWidget()
        lay = W.QVBoxLayout(w)
        tool = self.canvas.tool
        sel = self.canvas.selection
        item = sel[0] if len(sel) == 1 else None
        if isinstance(item, Graph):
            lay.addWidget(self._box("Формулы", self._graph_editor(item)))
            lay.addWidget(self._box("Вид графика", auto_panel(item, names=("axes", "grid", "labels", "degrees"))))
        else:
            if any(bound.spec.ui for bound in tool.settings):
                lay.addWidget(self._box(TOOL_LABELS[tool.name], tool.settings_panel()))
            if item is not None:
                lay.addWidget(self._box(ITEM_LABELS.get(type(item).__name__, "Объект"), auto_panel(item)))
        if not lay.count():
            lay.addWidget(W.QLabel("Выберите объект для настройки."))
        if tool.name in ("body", "rope", "force", "grab") or any(
            isinstance(it, (Body, Rope, Spring)) for it in sel
        ):
            lay.addWidget(self._box("Симуляция", auto_panel(self.sim)))
        lay.addStretch(1)
        self.panel.setWidget(w)

    @staticmethod
    def _box(title, widget):
        b = W.QGroupBox(title)
        W.QVBoxLayout(b).addWidget(widget)
        return b

    def _graph_editor(self, g):
        w = W.QWidget()
        lay = W.QVBoxLayout(w)
        edit = W.QPlainTextEdit("\n".join(e.text for e in g.exprs))
        info = W.QLabel()
        info.setWordWrap(True)
        timer = QTimer(w)
        timer.setSingleShot(True)

        def show():
            g.geometry()
            lines = []
            for e in g.exprs:
                if e.error:
                    lines.append(f"Ошибка в «{e.text}»: {e.error}")
                elif e.fit:
                    lines.append(f"{e.text}: " + ", ".join(f"{k}={v:.4g}" for k, v in e.fit.items()))
                elif e.kind == "value" and e.value is not None and not isinstance(e.value, tuple):
                    lines.append(f"{e.text} = {e.value}")
            info.setText("\n".join(lines))

        def apply():
            g.set_texts([t for t in edit.toPlainText().split("\n")])
            self._panel_params(g, params)
            show()

        timer.timeout.connect(apply)
        edit.textChanged.connect(lambda: timer.start(400))
        params = W.QVBoxLayout()
        lay.addWidget(edit)
        lay.addWidget(info)
        lay.addLayout(params)
        self._panel_params(g, params)
        show()
        return w

    def _panel_params(self, g, lay):
        while lay.count():
            lay.takeAt(0).widget().deleteLater()
        if g.params():
            lay.addWidget(auto_panel(g, names=list(g.params())))

    def _timeline_ui(self):
        tl = self.canvas.timeline
        self.tslider.blockSignals(True)
        self.tslider.setRange(0, math.ceil(tl.end * 10))
        self.tslider.setValue(self.tslider.maximum() if tl.time >= tl.end else int(tl.time * 10))
        self.tslider.blockSignals(False)
        self.tlabel.setText(f" {tl.time:.1f} с ".replace(".", ","))
        self.replay.setText("Остановить" if tl.playing else "Воспроизвести")

    def _sim_ui(self, frame):
        self.fslider.blockSignals(True)
        self.fslider.setRange(0, max(0, len(self.sim.frames) - 1))
        self.fslider.setValue(frame)
        self.fslider.blockSignals(False)
        self.flabel.setText(f" {self.sim.time:.2f} с ".replace(".", ","))

    def toggle_replay(self):
        tl = self.canvas.timeline
        tl.stop() if tl.playing else tl.play()
        self._timeline_ui()

    def goto(self, i):
        if 0 <= i < len(self.pages) and i != self.page:
            self.sim.pause()
            self.pages[self.page] = self.canvas.snapshot()
            self._show(i)

    def _show(self, i):
        self.page = i
        self.canvas.restore(self.pages[i] or BLANK)
        self.previous_page.setVisible(len(self.pages) > 1)
        self.next_page.setVisible(len(self.pages) > 1)
        self.previous_page.setEnabled(i > 0)
        self.next_page.setEnabled(i + 1 < len(self.pages))
        self.plabel.setText(f" {i + 1}/{len(self.pages)} ")

    def add_page(self):
        self.pages.insert(self.page + 1, None)
        self.goto(self.page + 1)

    def new(self):
        self.sim.pause()
        self.pages = [None]
        self._show(0)

    def snapshot(self):
        pages = self.pages.copy()
        pages[self.page] = self.canvas.snapshot()
        return copy.deepcopy({"format": "board", "pages": pages, "page": self.page})

    def restore(self, state):
        pages = copy.deepcopy(state["pages"])
        page = state.get("page", 0)
        if not isinstance(pages, list) or not 0 <= page < len(pages):
            raise ValueError("Invalid board page")
        self.sim.pause()
        self.pages = pages
        self._show(page)

    def save(self):
        path, _ = W.QFileDialog.getSaveFileName(
            self, "Сохранение доски", "Доска.board.gz", "Доска (*.board.gz)"
        )
        if path:
            with gzip.open(path, "wt", encoding="utf-8") as f:
                json.dump(self.snapshot(), f, ensure_ascii=False)

    def open(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "Открытие доски", "", "Доска (*.board.gz)")
        if path:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                self.restore(json.load(f))

    def export(self):
        path, _ = W.QFileDialog.getSaveFileName(
            self,
            "Экспорт страницы",
            "Страница.png",
            "Изображения и документы (*.png *.svg *.pdf)",
        )
        if path:
            self.canvas.export(path)

    def _center(self):
        return self.canvas.to_world(QPointF(self.canvas.width() / 2, self.canvas.height() / 2))

    def insert_graph(self):
        c = self._center()
        g = Graph(
            "y = a sin(bx)",
            "y > x^2 - 4 {x > 0}",
            "x^2 + y^2 = 9",
            x=c.x(),
            y=c.y(),
            w=640 / self.canvas.zoom,
            h=420 / self.canvas.zoom,
            unit=40 / self.canvas.zoom,
        )
        self.canvas.add(g)
        self.set_tool("select")
        self.canvas.select([g])

    def insert_image(self):
        path, _ = W.QFileDialog.getOpenFileName(
            self,
            "Вставка изображения",
            "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            c = self._center()
            self.canvas.add(Image.load(path, x=c.x(), y=c.y()))

    def physics_demo(self):
        c, k = self._center(), 1 / self.canvas.zoom
        x, y = c.x(), c.y()
        cv = self.canvas
        with cv.batch():
            cv.add(
                Body.from_points(
                    [
                        (x - 500 * k, y - 50 * k),
                        (x + 100 * k, y + 200 * k),
                        (x + 500 * k, y + 200 * k),
                    ],
                    kind="static",
                    width=8 * k,
                    color="#4a4f57",
                )
            )
            ball = cv.add(
                Body.circle(
                    x - 420 * k,
                    y - 120 * k,
                    26 * k,
                    mass=2,
                    label="Скорость: {v:.2f} м/с",
                    trace=True,
                    strobe=0.1,
                    fill="#552d70b3",
                )
            )
            cv.add(
                Body.box(
                    x + 200 * k,
                    y + 120 * k,
                    70 * k,
                    70 * k,
                    mass=1,
                    fill="#55c74440",
                    color="#c74440",
                )
            )
            bob = cv.add(
                Body.circle(
                    x + 380 * k,
                    y - 60 * k,
                    18 * k,
                    mass=1,
                    color="#388c46",
                    fill="#55388c46",
                )
            )
            cv.add(
                Rope(
                    a="",
                    b=bob.id,
                    pa=(x + 250 * k, y - 300 * k),
                    length=2.6,
                    segments=14,
                )
            )
            cv.add(Spring(a="", b=ball.id, pa=(x - 520 * k, y - 330 * k), k=8, damping=0.3))
        self.sim.show_v = True
        self.set_tool("grab")


def main(argv=None):
    QtCore.QLocale.setDefault(QtCore.QLocale("ru_RU"))
    app = W.QApplication(sys.argv if argv is None else argv)
    app.setFont(QtGui.QFont("Source Sans 3", 12))
    app.setApplicationName("Доска")
    window = Board()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
