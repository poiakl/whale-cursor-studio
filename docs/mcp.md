# AI 连接 · MCP

需要支持本地 **stdio MCP** 的 AI 客户端。此功能让 AI 调用工坊的本地工具；不会自动连接当前聊天，也不等于生图 API。

1. 完整解压 Windows 便携版，保留同目录下的 `WhaleCursorMCP.exe`。
2. 在工坊的“设置 → AI 连接 · MCP”选择工作目录，点击“复制连接配置”。
3. 在客户端的 MCP 服务配置中加入复制的 `mcpServers` 配置；若客户端逐项填写，使用其中 `command` 和 `args`，不再套一层 JSON。重新连接服务。路径含空格或中文时也应保留各参数为独立项。
4. 点击工坊里的“交给 AI 当前方案”。软件会提交尚未完成的编辑，并在工作目录保存独立副本；文件名复制到剪贴板。把要处理的图片、动画、热点 JSON 也放入该目录。
5. 对 AI 说：“读取 capabilities 和 list_files，编辑这份方案：<粘贴文件名>。将图片导入 slots.Arrow，调整热点和时长，导出后在工坊中打开。”
6. 在工坊中预览结果，需要使用时点击生成并安装。

工作目录中的已有方案可被 AI 编辑覆盖。需要保留版本时先复制方案或新建文件名。`create_project` 不会覆盖已有文件。更换软件位置或工作目录后需重新复制客户端连接配置。工作目录应选择专用文件夹。

## 可调用的工具

| 工具 | 用途 |
| --- | --- |
| capabilities / list_files | 查用途、触发、工作流与文件 |
| create_project / inspect_project | 新建空白、自带或 Windows 当前指针方案，查询帧数据 |
| import_images / split_sprite_sheet | 导入有序图片、GIF、WebP、CUR、ANI，或裁切精灵图 |
| set_hotspots / set_frame_timing | 设置全帧或逐帧热点、停留时长 |
| configure_sequence | 可逆反转、往返、统一时长与调速 |
| set_prompt_sections / build_generation_brief | 按主题整理全局要求，生成带父子动作层级、热点、帧数及尺寸的生图文本 |
| preview_frame | 以 MCP 图片返回帧预览 |
| add_condition_rule | 添加条件动画规则 |
| export_animation / export_cursors | 导出当前动画与配置，或 CUR / ANI |
| open_in_editor | 在工坊中打开工作目录内的方案 |

动画目标示例：`slots.Arrow`、`events.Hand.hold`、`effects.click`。先用 capabilities 查询实际可用名称。帧索引从 0 开始，热点默认采用 256×256 编辑坐标。分类提示词传 `sections: [{"name":"衣服","prompt":"深蓝长裙"}]`，与 style 一起构成全局要求。

服务仅通过客户端启动的标准输入输出通信，不开放网络端口；只读写所选工作目录，不提供任意代码执行、系统指针安装或付费生图工具。生图 API 仍在软件 AI 页面配置并由用户发送。

## 源码运行

安装 requirements.txt 后运行 `python mcp_entry.py --workspace <专用目录> --editor <main.py绝对路径>`。不要手动输入协议消息，使用 MCP 客户端连接；日志输出到 stderr，stdout 留给协议。Windows 发行包由独立控制台进程提供此通道，GUI 可照常关闭。
