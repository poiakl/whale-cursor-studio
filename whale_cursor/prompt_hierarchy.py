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


def group_defaults():
    result = {
        "系统指针": "按各子项的系统用途设计，功能指向清晰；多帧时采用克制的循环动作。",
        "互动指针": "按触发动作响应，子项进一步区分系统指针用途。",
        "装饰": "作为独立装饰绘制，不承担点击定位，构图以画布中心对齐。",
        "条件动画": "按子项给出的触发条件设计动作。",
    }
    result.update({"互动指针/" + EVENTS[key]: value for key, value in TRIGGERS.items()})
    return result


def default_instruction(key, role=None, title=""):
    if key.startswith("effects."):
        return {
            "click": "点击装饰从出现、扩散到完全消散，播放一次；末帧不重新出现。",
            "trail": "绘制柔和粒子或短轨迹，由显现到淡出；不把鼠标位移画进单格。",
            "companion": "保留完整主体，自然呼吸眨眼，首尾连贯循环。",
        }[key.split(".")[-1]]
    if key.startswith("animations."):
        return "具体条件：「" + title + "」；突出该条件下的动作与姿态。"
    return ROLE_FEATURES.get(role or key.split(".")[-1], "突出当前用途的辨识特征。")


def keywords_dict(fields):
    result = {}
    reserved = {"id", "style", "extra_prompt"}
    for field in fields or []:
        key, value = (
            str(field.get("key", "")).strip(),
            str(field.get("value", "")).strip(),
        )
        if not key and not value:
            continue
        if not key:
            raise ValueError("请填写关键字")
        if key in reserved:
            raise ValueError("关键字与固定字段重名：" + key)
        if key in result:
            raise ValueError("关键字重复：" + key)
        result[key] = value
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
