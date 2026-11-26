from .qt import Qt
from .settings import HasSettings


class Tool(HasSettings):
    name = "tool"
    cursor = Qt.CursorShape.ArrowCursor
    nav_fingers = 2
    canvas = None

    def settings_panel(self, parent=None):
        from .ui import auto_panel

        return auto_panel(self, parent=parent)

    def activate(self): ...
    def deactivate(self): ...

    def event(self, ev) -> bool:
        return False

    def press(self, p): ...
    def move(self, p): ...
    def release(self, p): ...
    def cancel(
        self, p
    ): ...  # Указатель потерян или стал частью жеста: отменить действие, начатое при нажатии
    def hover(self, p): ...  # Движение мыши или пера без нажатия

    def wheel(self, ev) -> bool:
        return False

    def key(self, ev) -> bool:
        return False

    def paint(self, p):
        pass

    def active(self) -> list:
        return [q for q in self.canvas.pointers.values() if q.tool is self and not q.nav]

    def px(self, v) -> float:
        return v / self.canvas.zoom
