import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QTimer
from whale_cursor.ui import Window, STYLE


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("WhaleCursorStudio")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    if "--smoke-test" in sys.argv:
        from whale_cursor.project import Project

        window = Window(Project.defaults())
        window.show()
        app.processEvents()
        for index in range(window.tabs.count()):
            window.tabs.setCurrentIndex(index)
            app.processEvents()
        if not window.project.slots or window.canvas.width() < 100:
            raise RuntimeError("打包程序资源 / 窗口检查失败")
        window.close()
        app.processEvents()
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
        probe.write(b"startup" if "--startup" in sys.argv else b"show")
        probe.waitForBytesWritten(300)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    if not server.listen(name):
        raise RuntimeError("无法创建单实例通信通道")
    window = Window()

    def incoming():
        socket = server.nextPendingConnection()
        if socket:
            if not socket.bytesAvailable():
                socket.waitForReadyRead(100)
            message = bytes(socket.readAll())
            socket.disconnectFromServer()
            socket.deleteLater()
            if message != b"startup":
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
