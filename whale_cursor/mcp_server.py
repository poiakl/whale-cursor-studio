"""Official MCP SDK stdio server over the same headless project operations as the UI."""

import argparse
import json
import os
import sys
from contextlib import ExitStack
from mcp.server.fastmcp import FastMCP, Image
from mcp.types import ToolAnnotations
from pydantic import BaseModel
from .mcp_core import Workspace


class PromptSection(BaseModel):
    name: str
    prompt: str


def create_server(workspace, editor=None, editor_env=None):
    work = Workspace(workspace, editor, editor_env)
    server = FastMCP(
        "Whale Cursor Studio",
        instructions=(
            "先调用 capabilities 和 list_files。路径均相对于用户选择的工作目录。"
            "使用交给 AI 的方案副本或创建新方案；导入素材、设置热点、导出结果后可 open_in_editor。"
            "所有操作是本地文件编辑，不安装系统指针、不执行任意代码、不调用收费生图接口。"
            "提示词分类只是整理用，最终合并为全局提示词；互动子项要承接父级触发动作。"
        ),
    )
    read = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, openWorldHint=False
    )
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, openWorldHint=False
    )

    @server.tool(annotations=read)
    def capabilities() -> dict:
        """查看支持的用途、触发、动画目标命名和工作流。"""
        return work.capabilities()

    @server.tool(annotations=read)
    def list_files() -> list[str]:
        """列出工作目录内的素材、方案和导出文件。"""
        return work.list_files()

    @server.tool(annotations=write)
    def create_project(
        project: str, name: str = "未命名", template: str = "empty"
    ) -> dict:
        """创建新 .whalepack；template 为 empty、built_in 或 Windows 的 system。已有文件不会覆盖。"""
        return work.create_project(project, name, template)

    @server.tool(annotations=read)
    def inspect_project(project: str, target: str | None = None) -> dict:
        """读取方案；指定 target 时返回从 0 开始的逐帧热点和时长。"""
        return work.inspect_project(project, target)

    @server.tool(annotations=write)
    def import_images(
        project: str, target: str, images: list[str], append: bool = False
    ) -> dict:
        """将工作目录中的 PNG/GIF/WebP/CUR/ANI 或连续帧导入指定动画。"""
        return work.import_images(project, target, images, append)

    @server.tool(annotations=write)
    def split_sprite_sheet(
        project: str, target: str, image: str, columns: int, rows: int, count: int
    ) -> dict:
        """按列、行、帧数切分精灵图并导入；每格须为正方形。"""
        return work.split_sprite_sheet(project, target, image, columns, rows, count)

    @server.tool(annotations=write)
    def set_hotspots(
        project: str, target: str, data: dict, include_timing: bool = False
    ) -> dict:
        """设置热点，例如 data={"hotspot":[30,40]}；支持逐帧 JSON，默认 256×256 编辑坐标。"""
        return work.set_hotspots(project, target, data, include_timing)

    @server.tool(annotations=write)
    def set_frame_timing(project: str, target: str, durations_ms: list[int]) -> dict:
        """统一时长传一个数，逐帧时长传与帧数相同的数组，范围 17～60000 ms。"""
        return work.set_frame_timing(project, target, durations_ms)

    @server.tool(annotations=write)
    def configure_sequence(project: str, target: str, controls: dict) -> dict:
        """设置 reverse、pingpong 布尔开关或 uniform_ms、speed；时长和速度传 null 关闭。往返不会重复追加。"""
        return work.configure_sequence(project, target, controls)

    @server.tool(annotations=write)
    def set_prompt_sections(
        project: str, style: str, sections: list[PromptSection]
    ) -> dict:
        """按衣服、动作等分类整理全局提示词。每项传 name 和 prompt，最终合并为整体提示词。"""
        return work.set_prompt_sections(
            project,
            style,
            [dict(key=item.name, value=item.prompt) for item in sections],
        )

    @server.tool(annotations=read)
    def build_generation_brief(
        project: str, selected: dict[str, int], size: int = 256, columns: int = 4
    ) -> str:
        """生成可复制给生图 AI 的分层文本；selected 将动画 id 映射到所需帧数，不发付费请求。"""
        return work.build_generation_brief(project, selected, size, columns)

    @server.tool(annotations=write)
    def export_animation(project: str, target: str, output: str) -> str:
        """只导出指定动画及配置到工作目录内的 .whaleanim。"""
        return work.export_animation(project, target, output)

    @server.tool(annotations=write)
    def export_cursors(project: str, folder: str, role: str | None = None) -> dict:
        """在工作目录内生成 CUR/ANI；role 可选单个系统用途，不会安装。"""
        return work.export_cursors(project, folder, role)

    @server.tool(annotations=read)
    def preview_frame(project: str, target: str, index: int = 0) -> Image:
        """以 MCP 图片内容预览一帧；index 从 0 开始。"""
        return Image(data=work.preview_frame(project, target, index), format="png")

    @server.tool(annotations=write)
    def add_condition_rule(project: str, rule: dict) -> dict:
        """添加条件动画规则：id、trigger、button、motion、role、lock、enabled。先添加再导入 animations.id。"""
        return work.add_condition_rule(project, rule)

    @server.tool(annotations=write)
    def open_in_editor(project: str) -> dict:
        """在指针工坊中打开工作目录内的结果方案供用户查看，不应用系统指针。"""
        return work.open_in_editor(project)

    @server.resource("whale://capabilities")
    def capability_resource() -> str:
        return json.dumps(work.capabilities(), ensure_ascii=False)

    return server


def run_stdio(server):
    # The SDK's UTF-8 wrappers own their underlying buffers. Give them duplicate
    # descriptors so their cleanup cannot close the interpreter's standard I/O.
    original = sys.stdin, sys.stdout
    with ExitStack() as stack:
        incoming = stack.enter_context(
            os.fdopen(os.dup(sys.stdin.fileno()), "r", encoding="utf-8")
        )
        outgoing = stack.enter_context(
            os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
        )
        try:
            sys.stdin, sys.stdout = incoming, outgoing
            server.run(transport="stdio")
        finally:
            sys.stdin, sys.stdout = original


def main():
    parser = argparse.ArgumentParser(description="Whale Cursor Studio local stdio MCP")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--editor")
    args = parser.parse_args()
    work = Workspace(args.workspace, args.editor)
    cache = work.path(".cache")
    cache.mkdir(exist_ok=True)
    # Embedded image/audio extraction also stays inside the chosen workspace.
    editor_env = os.environ.copy()
    os.environ["LOCALAPPDATA"] = str(cache)
    run_stdio(create_server(work.root, args.editor, editor_env))
