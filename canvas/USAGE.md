# Canvas usage

`canvas` is a Qt 6 whiteboard widget with mouse, pen and touch input. It can be embedded in a Qt application; `main.py` is a fuller example with tools, pages, graphs and physics.

## Install and run

From the repository root, use a Python environment and install the listed dependencies plus a Qt binding. The demo also needs Pymunk when its physics simulation runs:

```sh
python -m pip install -r requirements.txt
python -m pip install PySide6 pymunk
python main.py
```

Alternatively install `PyQt6` instead of `PySide6`. The shim prefers PySide6 when both are installed; set `CANVAS_QT=pyqt6` to prefer PyQt6. NumPy is required by the canvas and graph code. Qt and Pymunk are currently absent from `requirements.txt`.

In the demo, choose a tool on the left and adjust its settings on the right. Select an item to edit its settings; select a graph to edit its expressions. The bottom bar controls undo/redo, drawing replay, physics playback and pages. File and Insert menus provide save, export and sample content.

## Embed a board

```python
import sys
from canvas import Canvas, Stroke, Text
from tools.drawing import Brush
from canvas.qt import QtWidgets

app = QtWidgets.QApplication(sys.argv)
board = Canvas()
board.resize(900, 600)
board.tool = Brush(color="#2d70b3", width=4)
board.add(Stroke(points=[(0, 0), (100, 50)], color="#c74440"))
board.add(Text(text="Hello", x=100, y=100))
board.show()
sys.exit(app.exec())
```

Items use world coordinates; the canvas view maps them to screen coordinates. `canvas.to_world(point)` and `canvas.to_screen(point)` convert between them. Use `canvas.pan(dx, dy)`, `canvas.zoom_at(factor)`, and `canvas.fit()` to navigate programmatically. Set `canvas.tool` to a `Brush`, `Eraser`, `Select`, `Pan`, or `TextTool` instance. Tools accept settings as keyword arguments, and their properties can be changed later (for example, `board.tool.color = "#000000"`).

## Items, edits and files

- `board.add(item)` inserts a finished item and returns it; `board.remove(item)` removes it. Other built-in items include `Image` (`Image.load(path, x=..., y=...)`) and `Text`.
- `with board.batch():` groups operations into one undo step. Use `with board.change(item):` around direct geometry/state edits; settings assignments are recorded automatically. Call `item.update()` after changing geometry directly.
- `board.undo()` / `board.redo()` operate on the drawing timeline; `board.timeline.play()`, `stop()` and `seek(seconds)` control replay.
- `board.save("page.json.gz")` and `board.load("page.json.gz")` save/load a single canvas (JSON, gzip when the filename ends in `.gz`). `snapshot()` / `restore(snapshot)` provide in-memory equivalents. Import custom item classes before loading so their types are registered.
- `board.export("page.png")` exports the content; SVG and PDF are also supported. `board.autosave(path, interval=30)` returns a timer that can be stopped.

The demo's `.board.gz` file is a separate multipage wrapper around canvas snapshots; use its File menu for those files.

## Graphs and physics

```python
from graph import Graph
from physics import Body, Sim

graph = board.add(Graph("y = a sin(x)", x=0, y=0))
graph.settings.a.value = 2
print(graph.evaluate("a + 1"))

sim = Sim(board)  # attach once per canvas
ball = board.add(Body.circle(0, 0, 20, mass=2))
board.add(Body.from_points([(-200, 150), (200, 150)], kind="static"))
sim.play()  # also: pause(), step(n), seek(frame), stop()
```

Graphs support expression rows via `add`, `remove`, `set_texts`, and tables via `table`; inspect a row's `kind`, `error`, `value`, and `fit`. Physics uses Pymunk by default and records frames independently of drawing replay. Board positions are pixels; `sim.scale` is pixels per metre. Create `Sim` and import any custom item types before loading a snapshot containing them.

## Extend

Subclass `Tool` for pointer behavior (`press`, `move`, `release`, `cancel`); `tools/drawing.py`'s `LineTool` shows the draft/commit lifecycle. Subclass `Item` or `BoxItem` for new objects: implement painting and bounds, support a no-argument constructor for deserialization, and use `state()` / `set_state()` for non-setting data. Settings descriptors and `auto_panel` provide editable properties; see `settings.py` and `ui.py` for the API.

## Module boundaries

`canvas/` provides the widget, item lifecycle, geometry, settings, history, and the `Tool` interface. It never imports application tools, graphs, or physics. `tools/` contains pointer behaviors: `drawing.py`, `graph.py`, and `physics.py`; `registry.py` creates the demo tool set. `graph/` and `physics/` are optional item and simulation extensions. Import only the extensions your application needs before loading their saved items.

A custom tool subclasses `canvas.Tool` and is assigned to `board.tool`; no registry changes in the core are needed. Pointer positions use world coordinates. Use `draft`/`commit`/`discard` for temporary items and `begin_change`/`end_change` for edits to existing items. Callback errors propagate to the caller; timeline grouping is restored even when a callback fails.
