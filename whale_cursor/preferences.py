"""Application-wide preferences; deliberately separate from portable projects."""

from __future__ import annotations
import json
import re
from PySide6.QtGui import QColor
from .project import user_dir

THEMES = {
    "mint": "薄荷绿",
    "light": "简洁浅色",
    "dark": "深色",
    "ocean": "海蓝",
    "violet": "淡紫",
    "custom": "自定义背景",
}
BACKGROUNDS = {
    "mint": "#F4F7F5",
    "light": "#F5F6F8",
    "dark": "#1E242B",
    "ocean": "#EDF5FA",
    "violet": "#F4F0FA",
}
ACCENTS = {
    "mint": "#347D68",
    "light": "#356B9D",
    "dark": "#347D68",
    "ocean": "#286D9B",
    "violet": "#755493",
    "custom": "#347D68",
}


def load():
    try:
        data = json.loads((user_dir() / "preferences.json").read_text("utf-8"))
        if not isinstance(data, dict):
            return {}
        result = {}
        name = data.get("theme")
        if isinstance(name, str) and name in THEMES:
            result["theme"] = name
        background = data.get("background")
        if isinstance(background, str) and QColor(background).isValid():
            result["background"] = QColor(background).name()
        if isinstance(data.get("startup_interaction"), bool):
            result["startup_interaction"] = data["startup_interaction"]
        return result
    except (OSError, ValueError):
        return {}


def save(data):
    path = user_dir() / "preferences.json"
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)


def stylesheet(base, theme="mint", background="#F4F7F5"):
    theme = theme if theme in THEMES else "mint"
    bg = QColor(background if theme == "custom" else BACKGROUNDS[theme])
    if not bg.isValid():
        bg = QColor("#F4F7F5")
    dark = bg.lightness() < 128
    surface = bg.lighter(135).name() if dark else "#FFFFFF"
    foreground = "#E4ECE8" if dark else "#24413D"
    muted = "#A9B9B2" if dark else "#687E77"
    border = "#485750" if dark else "#DCE5DE"
    hover = "#35473F" if dark else bg.darker(103).name()
    selected = "#345449" if dark else bg.darker(107).name()
    accent = ACCENTS[theme]
    mapping = {
        "#24413D": foreground,
        "#F4F7F5": bg.name(),
        "#FFF": surface,
        "#7A8D85": muted,
        "#E1E8E2": border,
        "#DCE5DE": border,
        "#EAF4EE": hover,
        "#A2CDBB": border,
        "#D5EADD": selected,
        "#A7B2AB": muted,
        "#F5F6F5": surface,
        "#347D68": accent,
        "#276C58": QColor(accent).darker(115).name(),
        "#7B8C83": muted,
        "#286C55": foreground if dark else accent,
        "#E4F1E9": selected,
        "#215C48": foreground,
        "#EDF3EF": surface,
        "#567363": muted,
        "#F0F6F2": hover,
        "#EAF1EC": surface,
        "#61786A": muted,
    }
    result = re.sub(
        r"#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{3}",
        lambda m: mapping.get(m.group().upper(), m.group()),
        base,
    )
    result = result.replace("background:white", "background:" + surface)
    return (
        result
        + f"""
QMenu,QComboBox QAbstractItemView {{background:{surface};color:{foreground};border:1px solid {border};}}
QMenu::item:selected {{background:{selected};color:{foreground};}}
QToolTip {{background:{foreground};color:{bg.name()};}}
QWidget#themeBody {{background:{bg.name()};}}
QScrollBar:vertical {{background:{surface};width:10px;margin:0;}}
QScrollBar:horizontal {{background:{surface};height:10px;margin:0;}}
QScrollBar::handle {{background:{border};border-radius:4px;min-height:20px;min-width:20px;}}
QScrollBar::add-line,QScrollBar::sub-line {{height:0;width:0;}}
QTreeWidget::item:selected,QTableView::item:selected {{background:{selected};color:{foreground};}}
"""
    )
