import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QTimer
from whale_cursor.ui import Window, STYLE, wait_for_saves


def open_argument(arguments):
    if "--open" not in arguments:
        return None
    index = arguments.index("--open")
    if index + 1 >= len(arguments):
        raise ValueError("--open 缺少方案路径")
    from pathlib import Path

    path = Path(arguments[index + 1]).resolve()
    if path.suffix.lower() not in (".whalepack", ".whaleanim") or not path.is_file():
        raise ValueError("请选择已有的指针方案或单动画文件")
    return path


def apply_open_message(window, message):
    import json
    from whale_cursor.project import Project

    request = json.loads(message)
    if not isinstance(request, dict) or not isinstance(request.get("open"), str):
        raise ValueError("无效的打开方案请求")
    path = open_argument(["--open", request["open"]])
    window.set_project(Project.load(path))
    window.show_window()


def main():
    requested = open_argument(sys.argv)
    app = QApplication(sys.argv)
    app.setApplicationName("WhaleCursorStudio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.aboutToQuit.connect(wait_for_saves)
    if "--smoke-test" in sys.argv:
        from whale_cursor.project import Project, user_dir, assets_dir
        from PySide6.QtGui import QPixmap
        from whale_cursor import native

        before = native.snapshot() if native.WINDOWS else None
        window = Window()
        window.show()
        app.processEvents()
        for index in range(3):
            window.open_editor_tool(index)
            app.processEvents()
            if not window.editor_drawer.isVisible():
                raise RuntimeError("制作工具面板无法展开")
        window.editor_drawer.hide()
        for name in ("spin-up.svg", "spin-down.svg"):
            if QPixmap(str(assets_dir() / name)).isNull():
                raise RuntimeError("数值控件箭头资源无法读取")
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
        if not window.project.slots or window.canvas.width() < 100:
            raise RuntimeError("打包程序资源 / 窗口检查失败")
        bundled = user_dir() / "schemes" / "鲸鱼娘（自带）.whalepack"
        if len(Project.load(bundled).slots) != 17:
            raise RuntimeError("打包程序自带方案检查失败")
        if window.project.name != "本机原有指针":
            raise RuntimeError("首次打开未读取本机指针")
        window.prompt_page.character_button.setChecked(True)
        window.prompt_page.add_keyword("portable_probe", "reference-only")
        import json

        tasks = window.prompt_page.generation_tasks()
        payload = tasks[0]["prompt"]
        brief = json.loads(payload[payload.index("{") :])
        if "reference-only" not in brief["character"]["extra_prompt"]:
            raise RuntimeError("自定义关键字未写入生图提示词")
        app.processEvents()
        window.close()
        app.processEvents()
        resumed = Window()
        if resumed.project.name != "本机原有指针":
            raise RuntimeError("重新打开未恢复编辑内容")
        resumed.close()
        app.processEvents()
        if native.WINDOWS and native.snapshot() != before:
            raise RuntimeError("首次打开改变了系统指针")
        return 0

    def release_clipboard():
        mime = app.clipboard().mimeData()
        if mime and mime.hasFormat("application/x-whale-frames"):
            app.clipboard().clear()

    app.aboutToQuit.connect(release_clipboard)
    # One instance per user: prevents two live-interaction engines overwriting each other's restore state.
    import getpass, hashlib

    name = (
        "WhaleCursorStudio2-"
        + hashlib.sha256(getpass.getuser().encode()).hexdigest()[:12]
    )
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(300):
        if requested:
            import json

            probe.write((json.dumps({"open": str(requested)}) + "\n").encode("utf-8"))
        else:
            probe.write(b"startup" if "--startup" in sys.argv else b"show")
        probe.waitForBytesWritten(300)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    if not server.listen(name):
        raise RuntimeError("无法创建单实例通信通道")
    from whale_cursor.project import Project

    window = Window(Project.load(requested) if requested else None)

    def incoming():
        socket = server.nextPendingConnection()
        if socket:
            if not socket.bytesAvailable():
                socket.waitForReadyRead(100)
            message = bytes(socket.readAll())
            socket.disconnectFromServer()
            socket.deleteLater()
            if message.startswith(b"{"):
                window.guard(lambda: apply_open_message(window, message))
            elif message != b"startup":
                window.show_window()

    server.newConnection.connect(incoming)
    app.aboutToQuit.connect(
        lambda: window.engine.stop() if window.engine.active else None
    )
    if "--startup" in sys.argv:
        QTimer.singleShot(0, window.launch_at_login)
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        from whale_cursor.project import user_dir

        log = user_dir() / "error.log"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        if sys.platform == "win32" and "--smoke-test" not in sys.argv:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, "启动失败，错误日志：" + str(log), "指针工坊", 16
            )
        raise
