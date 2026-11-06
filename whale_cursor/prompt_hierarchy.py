"""Single-source instructions: character -> groups -> individual animation."""

import re
from .project import EVENTS

SHARED = "主体比例、镜头和锚点跨帧稳定，完整入格，使用真正透明背景。"
STYLE = "匹配参考图的角色、服装、配色、材质与画风。"
ROLE_FEATURES = {
    "Arrow": "指向部位表示正常选择，点击点位于指向尖端。",
    "Help": "表现询问或思考的姿态，体现帮助含义。",
    "AppStarting": "保留可操作的选择姿态，并体现后台等待状态。",
    "Wait": "表现忙碌等待，区别于可继续操作的后台运行状态。",
    "Crosshair": "围绕定位中心构图，精确点击位置在中心。",
    "IBeam": "呈现纤细的竖向文本定位姿态，插入点在中轴。",
    "NWPen": "持笔书写姿态，以笔尖作为点击位置。",
    "No": "清楚表现停止或拒绝，传达不可用含义。",
    "SizeNS": "呈现上下方向的对称伸展，体现垂直调整。",
    "SizeWE": "呈现左右方向的对称伸展，体现水平调整。",
    "SizeNWSE": "体现左上到右下的对角伸展方向。",
    "SizeNESW": "体现右上到左下的对角伸展方向。",
    "SizeAll": "呈现四向搬动或抓取姿态，体现可移动含义。",
    "UpArrow": "指向部位明确朝上，体现候选选择。",
    "Hand": "伸出食指示意可点击，指尖为点击位置。",
    "Pin": "表现地点标记，以定位尖端作为点击位置。",
    "Person": "呈现人物识别或友好招呼的姿态。",
}
TRIGGERS = {
    "click": "点击时轻压再回弹，完整播放一次。",
    "idle": "停留时轻微呼吸、眨眼或摆动，平稳循环。",
    "move": "移动时呈现前进动作，连续循环。",
    "left": "向左移动时朝左倾身，动作沿左方向循环。",
    "right": "向右移动时朝右倾身，动作沿右方向循环。",
    "up": "向上移动时向上抬起或伸展，连续循环。",
    "down": "向下移动时向下倾身或收拢，连续循环。",
    "left_click": "左键按下时轻触压下再回弹，播放一次。",
    "right_click": "右键按下时做一次侧向示意或轻敲，区别于左键。",
    "middle_click": "中键按下时做一次居中按压与回弹。",
    "double_click": "连续两次短促按压与回弹，双击节奏清楚。",
    "drag": "按住拖动时呈现抓握、牵引或用力姿态，连续循环。",
    "release": "松开时由紧绷到放松，轻微回弹后停稳，播放一次。",
    "hold": "长按时保持压住或蓄力姿态，微小动作循环，不反复松开。",
    "move_start": "从静止到运动，短促起步，播放一次。",
    "move_stop": "从运动到停稳，缓冲回正，播放一次。",
    "role_change": "切换用途时做一次简短过渡，结尾匹配新用途姿态。",
}


_CLICK_PROMPT = "点击装饰从出现、扩散到完全消散，播放一次；末帧不重新出现。"


def visual_instruction(text):
    # Also recognize previously saved defaults, while retaining user-written prompts.
    if text == _CLICK_PROMPT:
        return "点击装饰从出现、扩散到完全消散。"
    if text in TRIGGERS.values():
        return (
            text.replace("，完整播放一次", "")
            .replace("，播放一次", "")
            .replace("，连续循环", "，首尾姿态衔接")
            .replace("，平稳循环", "，首尾姿态衔接")
            .replace("动作沿左方向循环", "动作沿左方向延续")
            .replace("动作沿右方向循环", "动作沿右方向延续")
            .replace("微小动作循环", "保持微小动作")
        )
    return text


def group_defaults():
    result = {
        "系统指针": "按各子项的系统用途设计，功能指向清晰；多帧时采用克制的循环动作。",
        "互动指针": "按触发动作响应，子项进一步区分系统指针用途。",
        "装饰": "作为独立装饰绘制，不承担点击定位，构图以画布中心对齐。",
        "条件动画": "按子项给出的触发条件设计动作。",
    }
    result.update(
        {
            "互动指针/" + EVENTS[key]: visual_instruction(value)
            for key, value in TRIGGERS.items()
        }
    )
    return result


def default_instruction(key, role=None, title=""):
    if key.startswith("effects."):
        return {
            "click": visual_instruction(_CLICK_PROMPT),
            "trail": "绘制柔和粒子或短轨迹，由显现到淡出；不把鼠标位移画进单格。",
            "companion": "保留完整主体，自然呼吸眨眼，首尾连贯循环。",
        }[key.split(".")[-1]]
    if key.startswith("animations."):
        return "具体条件：「" + title + "」；突出该条件下的动作与姿态。"
    if key.startswith("events."):
        trigger = key.rsplit(".", 1)[-1]
        subject = ROLE_SUBJECTS.get(role, "当前用途的标志性部位")
        return f"让{subject}跟随父级「{EVENTS[trigger]}」动作；{TRIGGER_DETAILS[trigger]}，保持该用途的功能可辨识。"
    return ROLE_FEATURES.get(role or key.split(".")[-1], "突出当前用途的辨识特征。")


ROLE_SUBJECTS = {
    "Arrow": "指向手势的尖端",
    "Help": "询问姿态和帮助标记",
    "AppStarting": "选择姿态与后台等待标记",
    "Wait": "忙碌等待标记",
    "Crosshair": "准星的中心和四向结构",
    "IBeam": "文本定位的细长竖向结构",
    "NWPen": "笔尖和握笔手势",
    "No": "停止手势和禁止标记",
    "SizeNS": "上下方向的伸展结构",
    "SizeWE": "左右方向的伸展结构",
    "SizeNWSE": "左上至右下的对角结构",
    "SizeNESW": "右上至左下的对角结构",
    "SizeAll": "四向搬动的抓取姿态",
    "UpArrow": "朝上的指向部位",
    "Hand": "伸出的食指和指节",
    "Pin": "定位标记的尖端",
    "Person": "人物识别的招呼姿态",
}
TRIGGER_DETAILS = {
    "idle": "对应部位随父动作微动和舒展，避免只展示一张静态系统指针",
    "move": "对应部位跟随父动作前进或轻倾，产生连续的运动反馈",
    "left": "对应部位沿父动作的左向运动轻倾或拖动",
    "right": "对应部位沿父动作的右向运动轻倾或拖动",
    "up": "对应部位随父动作上抬并舒展",
    "down": "对应部位随父动作下倾并收拢",
    "click": "对应部位随父级按压节拍压缩和回弹",
    "left_click": "对应部位与父级左键按压同步下压和恢复",
    "right_click": "对应部位与父级右键反馈同步侧转或轻敲",
    "middle_click": "对应部位与父级中键反馈同步居中收缩和回弹",
    "double_click": "对应部位跟随父级两段节拍产生两次清晰反馈",
    "drag": "对应部位随父级牵引方向绷紧、抓握或后拖",
    "release": "对应部位随父级松开动作舒展，并缓冲回到该用途姿态",
    "hold": "对应部位跟随父级长按保持受压或蓄力感，不自行提前松开",
    "move_start": "对应部位随父级起步动作从静止过渡到前倾",
    "move_stop": "对应部位随父级制动动作缓冲并回正",
    "role_change": "对应部位跟随父级过渡动作切换成当前用途的标志性姿态",
}


def prompt_sections(fields):
    """Category names organize prose; they are not JSON property names."""
    result = []
    for field in fields or []:
        name, value = (
            str(field.get("key", "")).strip(),
            str(field.get("value", "")).strip(),
        )
        if not name and not value:
            continue
        if not name:
            raise ValueError("请填写提示词分类名称")
        if value:
            result.append(f"【{name}】{value}")
    return result


def without_inherited(text, ancestors):
    """Remove only identical complete clauses; never guess semantic equivalence."""
    clauses = lambda value: [
        part.strip() for part in re.split(r"[。；\n]+", value) if part.strip()
    ]
    inherited = {part for parent in ancestors for part in clauses(parent)}
    if not inherited.intersection(clauses(text)):
        return text
    return "".join(
        part
        for part in re.findall(r"[^。；\n]+[。；\n]*", text)
        if part.rstrip("。；\n").strip() not in inherited
    ).strip()
