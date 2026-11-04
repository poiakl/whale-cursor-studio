"""Headless editor operations confined to an explicitly chosen AI workspace."""

import io
import subprocess
import sys
from pathlib import Path
from PIL import Image
from .project import Project, SLOTS, EVENTS, EFFECTS, event_parts
from .codec import import_images, split_sheet, MAX_FRAMES
from .conditions import validate_rule
from .hotspots import parse_hotspots
from .sequence_controls import commit, set_option
from .animation_package import extract
from .generation_brief import make_brief, entries
from .prompt_hierarchy import prompt_sections


class Workspace:
    def __init__(self, root, editor=None):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.editor = Path(editor).resolve() if editor else None

    def path(self, filename, exists=False):
        path = (self.root / filename).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("路径超出 AI 工作目录")
        if exists and not path.is_file():
            raise ValueError("找不到工作目录内的文件：" + filename)
        return path

    def project_path(self, filename, exists=False):
        path = self.path(filename, exists)
        if path.suffix.lower() not in (".whalepack", ".whaleanim"):
            raise ValueError("方案路径必须以 .whalepack 或 .whaleanim 结尾")
        return path

    def load(self, filename):
        project = Project.load(self.project_path(filename, True))
        project.migrate_events()
        return project

    def target(self, project, target):
        group, _, key = target.partition(".")
        if group == "slots" and key in SLOTS:
            return group, key
        if group == "effects" and key in EFFECTS:
            return group, key
        if group == "events":
            event_parts(key)
            if "." in key:
                return group, key
        if group == "animations" and any(
            rule["id"] == key for rule in project.settings.get("animation_rules", [])
        ):
            return group, key
        raise ValueError("未知动画目标；先读取 capabilities，条件动画须先添加规则")

    def summary(self, project):
        return dict(
            name=project.name,
            size=project.size,
            pixel=project.pixel,
            animations={
                group + "." + key: dict(
                    frame_count=len(frames), duration_ms=sum(f.duration for f in frames)
                )
                for group in ("slots", "events", "effects", "animations")
                for key, frames in getattr(project, group).items()
                if frames
            },
            frame_controls=project.settings.get("frame_controls", {}),
        )

    def save(self, filename, project):
        project.save(self.project_path(filename))
        return self.summary(project)

    def capabilities(self):
        return dict(
            workspace=str(self.root),
            slots=SLOTS,
            triggers=EVENTS,
            effects=EFFECTS,
            target_examples=[
                "slots.Arrow",
                "events.Hand.hold",
                "effects.click",
                "animations.rule_id",
            ],
            workflow="create_project → import_images / split_sprite_sheet → set_hotspots → set_frame_timing / configure_sequence → build_generation_brief → export_animation / export_cursors → open_in_editor",
            prompt_scopes="character → groups → items；自定义分类合并为 character.extra_prompt",
            limitations="只在工作目录内读写；不安装系统指针，不执行任意代码，不发付费 AI 请求。",
        )

    def list_files(self):
        allowed = {
            ".whalepack",
            ".whaleanim",
            ".png",
            ".webp",
            ".gif",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".cur",
            ".ani",
            ".json",
        }
        result = []
        for path in self.root.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in allowed
                and not any(
                    part.startswith(".") for part in path.relative_to(self.root).parts
                )
            ):
                if path.resolve().is_relative_to(self.root):
                    result.append(str(path.relative_to(self.root)))
            if len(result) >= 1000:
                break
        return sorted(result)

    def create_project(self, filename, name="未命名", template="empty"):
        path = self.project_path(filename)
        if path.exists():
            raise ValueError("文件已存在，请使用新文件名")
        if template == "built_in":
            project = Project.defaults()
        elif template == "system":
            from .native import require

            require()
            from .first_run import read_system_project

            project, warnings = read_system_project()
        elif template == "empty":
            project = Project()
        else:
            raise ValueError("template 可选 empty / built_in / system")
        project.name = str(name)[:120]
        return self.save(filename, project)

    def inspect_project(self, filename, target=None):
        project = self.load(filename)
        result = self.summary(project)
        if target:
            group, key = self.target(project, target)
            result["frames"] = [
                dict(index=i, hotspot=[f.x, f.y], duration_ms=f.duration)
                for i, f in enumerate(getattr(project, group).get(key, []))
            ]
        return result

    def put_frames(self, filename, target, frames, append=False):
        project = self.load(filename)
        group, key = self.target(project, target)
        current = getattr(project, group).get(key, [])
        result = current + frames if append else frames
        if not 1 <= len(result) <= MAX_FRAMES:
            raise ValueError("每个动画需要 1～512 帧")
        commit(project, target)
        getattr(project, group)[key] = result
        project.originals[target] = [f.clone() for f in result]
        return self.save(filename, project)

    def import_images(self, filename, target, images, append=False):
        if not images or len(images) > MAX_FRAMES:
            raise ValueError("请选择 1～512 个素材文件")
        project = self.load(filename)
        frames = []
        for image in images:
            frames.extend(import_images(self.path(image, True), project.pixel))
            if len(frames) > MAX_FRAMES:
                raise ValueError("导入后超过 512 帧")
        return self.put_frames(filename, target, frames, append)

    def split_sprite_sheet(self, filename, target, image, columns, rows, count):
        project = self.load(filename)
        with Image.open(self.path(image, True)) as source:
            frames = split_sheet(source, columns, rows, count, project.pixel)
        return self.put_frames(filename, target, frames)

    def set_hotspots(self, filename, target, data, include_timing=False):
        project = self.load(filename)
        group, key = self.target(project, target)
        changes = parse_hotspots(data, project, (group, key))
        for change in changes:
            destination = change.group + "." + change.key
            commit(project, destination)
            frame = getattr(project, change.group)[change.key][change.frame]
            frame.x, frame.y = change.x, change.y
            if include_timing and change.duration is not None:
                frame.duration = change.duration
        return self.save(filename, project)

    def set_frame_timing(self, filename, target, durations_ms):
        project = self.load(filename)
        group, key = self.target(project, target)
        frames = getattr(project, group).get(key, [])
        if len(durations_ms) not in (1, len(frames)) or not frames:
            raise ValueError("时长列表应为一个数值或与帧数一致")
        if any(
            type(value) is not int or not 17 <= value <= 60000 for value in durations_ms
        ):
            raise ValueError("帧时长应为 17～60000 毫秒")
        commit(project, target)
        for i, frame in enumerate(frames):
            frame.duration = (
                durations_ms[0] if len(durations_ms) == 1 else durations_ms[i]
            )
        return self.save(filename, project)

    def configure_sequence(self, filename, target, controls):
        project = self.load(filename)
        self.target(project, target)
        for key, value in controls.items():
            if key in ("reverse", "pingpong") and type(value) is not bool:
                raise ValueError("反转 / 往返必须为布尔值")
            set_option(project, target, key, value)
        return self.save(filename, project)

    def set_prompt_sections(self, filename, style, sections):
        prompt_sections(sections)
        project = self.load(filename)
        config = project.settings.setdefault("ai_prompt", {})
        config.update(style=style, keyword_fields=sections, prompt_schema=3)
        return self.save(filename, project)

    def build_generation_brief(self, filename, selected, size=256, columns=4):
        if not 32 <= size <= 2048 or not 1 <= columns <= 16:
            raise ValueError("单帧尺寸为 32～2048，每行帧数为 1～16")
        project = self.load(filename)
        known = {item[0] for item in entries(project)}
        if not selected or any(
            key not in known or type(count) is not int or not 1 <= count <= MAX_FRAMES
            for key, count in selected.items()
        ):
            raise ValueError("用途或帧数无效")
        config = project.settings.get("ai_prompt", {})
        return make_brief(
            project,
            selected,
            size,
            columns,
            config.get("style", ""),
            config.get("extra", ""),
            config.get("item_instructions"),
            config.get("group_instructions"),
            config.get("character_name"),
            config.get("keyword_fields"),
        )

    def export_animation(self, filename, target, output):
        destination = self.project_path(output)
        if destination.suffix.lower() != ".whaleanim":
            raise ValueError("单动画导出使用 .whaleanim")
        if destination == self.project_path(filename):
            raise ValueError("输出文件不能覆盖源方案")
        extract(self.load(filename), target).save(destination)
        return str(destination.relative_to(self.root))

    def export_cursors(self, filename, folder, role=None):
        if role is not None and role not in SLOTS:
            raise ValueError("未知系统用途")
        destination = self.path(folder)
        project = self.load(filename)
        # Validate existing file symlinks too, before writing any output.
        for key, frames in project.slots.items():
            if frames and (role is None or key == role):
                output = destination / (key + (".ani" if len(frames) > 1 else ".cur"))
                self.path(str(output))
        paths = project.export(destination, role)
        return {
            key: str(Path(path).relative_to(self.root)) for key, path in paths.items()
        }

    def preview_frame(self, filename, target, index=0):
        project = self.load(filename)
        group, key = self.target(project, target)
        frames = getattr(project, group).get(key, [])
        if not 0 <= index < len(frames):
            raise ValueError("帧索引越界（从 0 开始）")
        output = io.BytesIO()
        frames[index].image.save(output, format="PNG")
        return output.getvalue()

    def add_condition_rule(self, filename, rule):
        validate_rule(rule)
        project = self.load(filename)
        rules = project.settings.setdefault("animation_rules", [])
        if any(item["id"] == rule["id"] for item in rules):
            raise ValueError("规则 id 已存在")
        rules.append(dict(rule))
        return self.save(filename, project)

    def open_in_editor(self, filename):
        path = self.project_path(filename, True)
        self.load(filename)  # Validate before launching a GUI.
        if not self.editor or not self.editor.is_file():
            raise ValueError("未配置可用的编辑器路径，请从软件设置复制 MCP 配置")
        command = (
            [str(self.editor)]
            if self.editor.suffix.lower() == ".exe"
            else [sys.executable, str(self.editor)]
        )
        subprocess.Popen(
            [*command, "--open", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(self.editor.parent),
        )
        return dict(opened=str(path.relative_to(self.root)))
