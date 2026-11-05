"""Current-user-only local app bridge; dispatches every action on the UI thread."""

import json
from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from .app_actions import AppActions
from .mcp_connection import MAX_MESSAGE, endpoint_name


class LiveBridge(QObject):
    def __init__(self, window, endpoint=None):
        super().__init__(window)
        self.actions = AppActions(window)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.endpoint = endpoint or endpoint_name()
        if not self.server.listen(self.endpoint):
            probe = QLocalSocket()
            probe.connectToServer(self.endpoint)
            connected = probe.waitForConnected(100)
            probe.abort()
            if connected:
                raise OSError("已有工坊占用 AI 连接，请先关闭另一实例")
            QLocalServer.removeServer(self.endpoint)
            if not self.server.listen(self.endpoint):
                raise OSError("无法打开工坊 AI 连接：" + self.server.errorString())
        self.server.newConnection.connect(self.incoming)
        self.sockets = {}

    def incoming(self):
        while self.server.hasPendingConnections():
            channel = self.server.nextPendingConnection()
            self.sockets[channel] = bytearray()
            channel.readyRead.connect(lambda channel=channel: self.read(channel))
            channel.disconnected.connect(lambda channel=channel: self.release(channel))
            timer = QTimer(channel)
            timer.setSingleShot(True)
            timer.timeout.connect(channel.disconnectFromServer)
            timer.start(65000)
            if channel.bytesAvailable():
                self.read(channel)

    def release(self, channel):
        self.sockets.pop(channel, None)
        channel.deleteLater()

    def read(self, channel):
        data = self.sockets.get(channel)
        if data is None:
            return
        data.extend(bytes(channel.readAll()))
        if len(data) > MAX_MESSAGE:
            self.respond(channel, dict(ok=False, error="请求超过 64 MB"))
            return
        if b"\n" not in data:
            return
        self.sockets.pop(channel, None)
        try:
            request = json.loads(data)
            if not isinstance(request, dict) or request.get("version") != 1:
                raise ValueError("工坊连接协议不匹配，请重新复制连接配置")
            result = self.actions.dispatch(
                request.get("operation"), request.get("params", {})
            )
            self.respond(channel, dict(ok=True, result=result))
        except Exception as error:
            self.respond(channel, dict(ok=False, error=str(error)))

    def respond(self, channel, result):
        raw = (
            json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            + b"\n"
        )
        if len(raw) > MAX_MESSAGE:
            raw = (
                json.dumps(
                    dict(ok=False, error="响应过大，请分批读取生图任务或省略参考图")
                ).encode()
                + b"\n"
            )
        channel.write(raw)
        channel.disconnectFromServer()  # Qt drains queued response bytes first.

    def close(self):
        for channel in list(self.sockets):
            channel.disconnectFromServer()
        self.server.close()
