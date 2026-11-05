"""Stdio MCP tools operating the running cursor studio's native features."""

import argparse
import json
import os
import sys
from contextlib import ExitStack
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations, TextContent, ImageContent
from .mcp_connection import LiveClient


def create_server(editor=None, endpoint=None, client=None):
    app = client or LiveClient(editor, endpoint)
    server = FastMCP(
        "Whale Cursor Studio",
        instructions=(
            "这些工具直接操作正在运行的指针工坊，软件未打开时会尝试打开。"
            "生图流程：get_generation_request 读取当前勾选项的完整提示词、尺寸、帧数、热点与参考图片；"
            "你使用自己的生图能力生成图片，再调用 apply_generated_image 直接回填对应动画。"
            "点击特效用 set_click_effect 编写 JSON 关键帧，preview_click_effect 返回工坊真实渲染预览；"
            "音效用 configure_interaction_audio 配置。更改实时显示在软件中且可以撤销。"
            "工具不调用软件的付费生图 API，也不负责通用文件查询、文件管理或系统指针安装。"
        ),
    )
    read = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, openWorldHint=False
    )
    write = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, openWorldHint=False
    )

    @server.tool(annotations=read)
    def get_generation_request(
        targets: list[str] | None = None, include_references: bool = True
    ):
        """取得工坊 AI 页已勾选任务的提示词和真实参考图片，供你直接生图；可按目标 id 分批读取。返回 request_id 用于回填，生成时保留单格布局与热点。"""
        result = app.call(
            "get_generation_request",
            targets=targets,
            include_references=include_references,
        )
        references = result.pop("references", [])
        return [
            TextContent(type="text", text=json.dumps(result, ensure_ascii=False)),
            *[
                ImageContent(type="image", data=raw, mimeType="image/png")
                for raw in references
            ],
        ]

    @server.tool(annotations=write)
    def apply_generated_image(
        request_id: str,
        target: str,
        image_path: str | None = None,
        image_base64: str | None = None,
        hotspots: dict | None = None,
    ) -> dict:
        """把你已生成的本地图片或 PNG base64 直接放回工坊对应动画，自动切帧并应用该请求的热点和时长。hotspots 可传生图返回的热点 JSON；方案切换或用户修改目标后会拒绝覆盖。"""
        return app.call(
            "apply_generated_image",
            request_id=request_id,
            target=target,
            image_path=image_path,
            image_base64=image_base64,
            hotspots=hotspots,
        )

    @server.tool(annotations=read)
    def get_interaction_settings() -> dict:
        """读取工坊当前点击特效、素材、各触发音效及关键帧语法，作为编写动画和配音依据。"""
        return app.call("get_interaction_settings")

    @server.tool(annotations=write)
    def set_click_effect(
        keyframes: list[dict] | None = None,
        effect: str | None = None,
        motion: str | None = None,
        duration_ms: int | None = None,
        size: int | None = None,
        opacity: float | None = None,
    ) -> dict:
        """直接编写并应用点击效果。keyframes 为2～64帧JSON，t从0到1，可写x/y/scale/rotation/opacity，不执行任意代码。effect=custom使用点击装饰素材；其余可选ripple/petals/none。"""
        return app.call(
            "set_click_effect",
            keyframes=keyframes,
            effect=effect,
            motion=motion,
            duration_ms=duration_ms,
            size=size,
            opacity=opacity,
        )

    @server.tool(annotations=read)
    def preview_click_effect(progress: list[float] | None = None):
        """返回工坊当前点击特效在指定进度的真实渲染图与姿态，检查你编写的动画；默认6帧，最多12帧。"""
        frames = app.call("preview_click_effect", progress=progress)
        output = []
        for frame in frames:
            raw = frame.pop("image")
            output.extend(
                [
                    TextContent(
                        type="text", text=json.dumps(frame, ensure_ascii=False)
                    ),
                    ImageContent(type="image", data=raw, mimeType="image/png"),
                ]
            )
        return output

    @server.tool(annotations=write)
    def configure_interaction_audio(
        trigger: str,
        files: list[str] | None = None,
        mode: str | None = None,
        wait_finish: bool | None = None,
        interval_ms: int | None = None,
        volume: float | None = None,
    ) -> dict:
        """直接配置工坊的触发音效：files为PCM WAV本地路径（空数组清空），mode=random/sequence，wait_finish播完再播，interval_ms为同一触发最短开始间隔，volume=0～1。未传的参数保留。"""
        return app.call(
            "configure_interaction_audio",
            trigger=trigger,
            files=files,
            mode=mode,
            wait_finish=wait_finish,
            interval_ms=interval_ms,
            volume=volume,
        )

    return server


def run_stdio(server):
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
    parser = argparse.ArgumentParser(description="Whale Cursor Studio native app MCP")
    parser.add_argument("--editor")
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--workspace", help=argparse.SUPPRESS
    )  # Accept older launch configs without file tools.
    args = parser.parse_args()
    run_stdio(create_server(args.editor, args.endpoint))
