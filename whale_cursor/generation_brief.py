"""Headless generation specifications shared by the editor and local AI bridge."""

import json
import math
from .project import SLOTS, EVENTS, EFFECTS
from .codec import MAX_FRAMES
from .conditions import describe
from .prompt_hierarchy import (
    default_instruction,
    group_defaults,
    prompt_sections,
    STYLE as CHARACTER_STYLE,
    SHARED,
    without_inherited,
)


def entries(project):
    for role, title in SLOTS.items():
        yield ("slots." + role, ("系统指针",), title, project.slots.get(role, []), role)
    for trigger, title in EVENTS.items():
        for role, role_title in SLOTS.items():
            yield (
                "events." + role + "." + trigger,
                ("互动指针", title),
                role_title,
                project.event_frames(role, trigger),
                role,
            )
    for key, title in EFFECTS.items():
        yield ("effects." + key, ("装饰",), title, project.effects.get(key, []), None)
    for rule in project.settings.get("animation_rules", []):
        key = rule["id"]
        yield (
            "animations." + key,
            ("条件动画",),
            describe(rule),
            project.animations.get(key, []),
            rule.get("role"),
        )


ROLE_PROMPTS = {
    "Arrow": "正常选择：轻微呼吸或眨眼，指向位置明确，锚点稳定。",
    "Help": "帮助选择：好奇、询问或思考的小动作，体现帮助含义。",
    "AppStarting": "后台运行：保持可操作姿态，配合轻微等待动作，区别于完全忙碌。",
    "Wait": "忙碌等待：连贯的循环等待动作，节奏均匀，首尾衔接。",
    "Crosshair": "精确选择：围绕固定中心做克制的小动作，精确定位中心不漂移。",
    "IBeam": "文本选择：构图纤细、竖向，文本插入锚点清晰，不遮住阅读区域。",
    "NWPen": "手写：做书写或持笔动作，笔尖始终对齐热点。",
    "No": "不可用：明确的停止或拒绝姿态，动作简洁，不依赖文字。",
    "SizeNS": "垂直调整：上下方向的对称伸缩动作，中心锚点固定。",
    "SizeWE": "水平调整：左右方向的对称伸缩动作，中心锚点固定。",
    "SizeNWSE": "对角调整：体现左上到右下方向，沿该对角线小幅伸展。",
    "SizeNESW": "对角调整：体现右上到左下方向，沿该对角线小幅伸展。",
    "SizeAll": "移动：四向搬动或展开姿态，体现可移动含义，中心稳定。",
    "UpArrow": "候选选择：保持明确朝上的指向性，动作轻巧。",
    "Hand": "链接选择：轻触或伸手示意可点击，指尖对齐热点。",
    "Pin": "位置选择：定位或标记地点的动作，定位尖端对齐热点。",
    "Person": "人员选择：友好的招呼或人物识别姿态，轮廓清晰。",
}
TRIGGER_PROMPTS = {
    "click": "点击瞬间轻压再回弹，完整播放一次。",
    "idle": "停留时自然呼吸、眨眼或轻微摆动，循环平稳。",
    "move": "移动时呈现轻微前进姿态，可无缝循环。",
    "left": "向左移动时朝左轻倾，动作沿左方向，可无缝循环。",
    "right": "向右移动时朝右轻倾，动作沿右方向，可无缝循环。",
    "up": "向上移动时向上轻抬或伸展，可无缝循环。",
    "down": "向下移动时向下轻倾或收拢，可无缝循环。",
    "left_click": "左键按下时做一次轻触压下及回弹动作。",
    "right_click": "右键按下时做一次侧向示意或轻敲，区别于左键。",
    "middle_click": "中键按下时做一次居中的按压与回弹动作。",
    "double_click": "连续两次短促按压与回弹，双击节奏清楚。",
    "drag": "按住拖动时做抓握、牵引或用力姿态，循环时锚点稳定。",
    "release": "松开时由紧绷放松，轻微回弹后自然停稳，播放一次。",
    "hold": "长按时保持压住或蓄力姿态，以微小动作循环，不反复松开。",
    "move_start": "从静止过渡到运动，短促起步动作，播放一次。",
    "move_stop": "从运动减速停稳，小幅缓冲回正，播放一次。",
    "role_change": "切换指针用途时做一次简短过渡，结尾匹配新用途姿态。",
}


def legacy_default_instruction(key, role=None, title=""):
    reference = "匹配参考图中的角色、服装、配色、材质与画风；不改角色设计。"
    if key.startswith("effects."):
        action = {
            "click": "点击装饰：从出现、扩散到完全消散，只播放一次；末帧透明，不重新出现。",
            "trail": "拖尾装饰：柔和的小粒子或轨迹，从显现到淡出，边缘透明，不画入鼠标位移。",
            "companion": "跟随角色：完整身体或参考图对应主体，自然呼吸眨眼，循环连贯，中心稳定。",
        }[key.split(".")[-1]]
    elif key.startswith("events."):
        action = ROLE_PROMPTS.get(role, "") + TRIGGER_PROMPTS[key.split(".")[-1]]
    elif key.startswith("animations."):
        action = "根据条件「" + title + "」绘制相应动作，条件持续时稳定保持，动作连贯。"
    else:
        action = ROLE_PROMPTS.get(
            role or key.split(".")[-1], "保持对应指针的用途清晰。"
        )
    return action + reference + "主体大小和热点锚点全程一致，完整入格，背景真透明。"


def make_brief(
    project,
    selected,
    size,
    columns,
    style,
    extra="",
    item_instructions=None,
    group_instructions=None,
    character_name=None,
    keyword_fields=None,
):
    items = []
    groups = {}
    common = [
        style.strip() or CHARACTER_STYLE,
        SHARED,
        extra.strip(),
        *prompt_sections(keyword_fields),
    ]
    inherited = dict(group_defaults(), **(group_instructions or {}))
    for key, path, title, frames, role in entries(project):
        if key not in selected:
            continue
        count = max(1, min(MAX_FRAMES, int(selected[key])))
        decorative = key.startswith("effects.")
        reference = frames or project.slots.get(role, [])
        timeline = []
        for i in range(count):
            frame = reference[i % len(reference)] if reference else None
            hotspot = (
                [
                    min(size - 1, round(frame.x * size / 256)),
                    min(size - 1, round(frame.y * size / 256)),
                ]
                if frame and not decorative
                else [size // 2, size // 2]
            )
            timeline.append(
                dict(
                    frame=i + 1,
                    hotspot_px=hotspot,
                    duration_ms=frame.duration if frame else 100,
                )
            )
        cols = min(columns, count)
        rows = math.ceil(count / cols)
        items.append(
            dict(
                id=key,
                purpose=" / ".join((*path, title)),
                parent_id="/".join(path),
                extra_prompt=without_inherited(
                    (item_instructions or {})
                    .get(key, default_instruction(key, role, title))
                    .strip(),
                    common
                    + [
                        inherited.get("/".join(path[:depth]), "")
                        for depth in range(1, len(path) + 1)
                    ],
                ),
                frame_count=count,
                cell_px=[size, size],
                sheet_grid=[cols, rows],
                sheet_px=[cols * size, rows * size],
                unused_cells=cols * rows - count,
                loop=(
                    count > 1
                    and not (
                        key == "effects.click"
                        or key.split(".")[-1]
                        in (
                            "click",
                            "left_click",
                            "right_click",
                            "middle_click",
                            "double_click",
                            "release",
                            "move_start",
                            "move_stop",
                            "role_change",
                        )
                    )
                ),
                anchor_source="装饰画布中心"
                if decorative
                else "现有动画"
                if frames
                else ("对应系统指针参考" if reference else "画布中心"),
                timeline=timeline,
            )
        )
        for depth in range(1, len(path) + 1):
            group_id = "/".join(path[:depth])
            groups.setdefault(
                group_id,
                dict(
                    id=group_id,
                    parent_id="/".join(path[: depth - 1]) if depth > 1 else "character",
                    extra_prompt=without_inherited(
                        inherited.get(group_id, "").strip(),
                        common
                        + [
                            inherited.get("/".join(path[:d]), "")
                            for d in range(1, depth)
                        ],
                    ),
                ),
            )
        if decorative:
            settings = project.settings
            effect = key.split(".")[-1]
            items[-1]["effect_playback"] = dict(
                motion=settings.get("effect_motion", "fade")
                if effect == "click"
                else "跟随鼠标",
                display_size_px=settings.get(
                    {
                        "click": "click_size",
                        "trail": "trail_size",
                        "companion": "companion_size",
                    }[effect],
                    64,
                ),
                lifetime_ms=settings.get(
                    "trail_lifetime" if effect == "trail" else "effect_lifetime", 850
                ),
                note="画布中心为构图锚点；点击和拖尾按中心绘制，跟随角色按左上角加偏移绘制；运行时不使用素材点击热点",
            )
    if not items:
        return "请先在左侧勾选需要生成的内容。"
    request = dict(
        character=dict(
            id=character_name or project.name,
            style=style.strip() or CHARACTER_STYLE,
            extra_prompt="\n".join(
                filter(None, [SHARED, extra.strip(), *prompt_sections(keyword_fields)])
            ),
        ),
        coordinate_origin="每个单帧左上角；x 向右，y 向下；单位像素",
        groups=list(groups.values()),
        items=items,
    )
    return (
        "请为 Windows 鼠标指针与桌面互动制作透明 PNG 动画素材。\n"
        "character 是全局角色要求；groups 通过 parent_id 逐层继承父级，items 继承所属分组。父级要求仅写一次，子项只写自身差异；character.extra_prompt 中的分类标题仅用于整理思路，所有分类都是对全部动画生效的整体提示词，不是独立字段。不要把指令文字画进图片。\n"
        "每个 items 条目单独生成一张序列图，文件名使用 id；不同用途不要混在同一张图。\n"
        "严格按 sheet_grid 的列数、行数排列，从左到右、从上到下；每格大小为 cell_px，"
        "保留完整画布，不裁边、不留格间距，unused_cells 对应末尾透明空格。\n"
        "不要文字、编号、水印、网格线、棋盘底或阴影背景；透明区域必须是真正的 alpha。\n"
        "同一动画中角色大小、镜头、朝向与锚点连续稳定，避免意外抖动。循环项首尾自然衔接；"
        "点击装饰从出现到消散。duration_ms 是播放参考，不要画进图片。\n"
        "hotspot_px 是该格内的目标点击/对齐锚点，请让相应部位对齐；不要画热点标记。"
        "它来自当前方案参考，生成后仍需在制作页核对热点。\n"
        "生成器若无法精确满足大图尺寸，请分条目或分批生成，保持单格尺寸与帧序。"
        "导入时选择对应格尺寸切分；保持每格为正方形，尺寸限制不允许时按比例等比缩放整张序列图。\n\n"
        + json.dumps(request, ensure_ascii=False, indent=2)
    )
