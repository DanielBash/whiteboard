from .drawing import Brush, Eraser, Pan, Select, LineTool
from .graph import GraphTool
from .physics import BodyTool, ForceTool, GrabTool, RopeTool

TOOL_GROUPS = {
    "Навигация": ((Select, "Выделение"), (Pan, "Перемещение доски")),
    "Рисование": ((Brush, "Кисть"), (LineTool, "Прямая линия"), (Eraser, "Ластик")),
    "Графики": ((GraphTool, "График"),),
    "Физика": (
        (BodyTool, "Физическое тело"),
        (RopeTool, "Верёвка и пружина"),
        (ForceTool, "Сила"),
        (GrabTool, "Захват тела"),
    ),
}
TOOL_LABELS = {cls.name: label for group in TOOL_GROUPS.values() for cls, label in group}
TOOL_TYPES = tuple(cls for group in TOOL_GROUPS.values() for cls, _ in group)


def create_tools():
    return {cls.name: cls() for cls in TOOL_TYPES}
