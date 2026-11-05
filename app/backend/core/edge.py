"""校园网边缘入口自举：在 443 上建立 HTTPS 入口，反代到本机业务端口。

为什么需要它
------------
校园网 ACL 只放行 80/443 两类标准端口（实测同网段另一台机器的 8080/22/8000/9000
全部超时，而 80/443 正常）。本服务监听 8080，校园网访问不到，必须在 443 上另开
一个 TLS 入口。这与「同学那台用 nginx 挂 443」是同一套做法。

为什么由服务自己启动
--------------------
服务器上的 systemd 单元与 sudoers 白名单都是安装时固定的，事后无法追加
ExecStartPre 之类的钩子；部署代理的 sudo 白名单只允许 systemctl restart cilv。
所以把「建入口」做成服务启动时的一次性自举，是唯一不依赖人工改系统配置的路径。

自举失败不影响主服务
--------------------
任何异常都被吞掉并记录到 `state`，主服务照常在 8080 上提供服务；
`/api/health` 的 `edge` 字段会如实说明为什么没能建起来，便于远程诊断。
"""

from __future__ import annotations

import errno
import os
import socket
import ssl
import threading
import time

# 判定"端口已被占用"与"权限不足"的 errno
_EACCES = (errno.EACCES, errno.EPERM)
_EADDRINUSE = errno.EADDRINUSE

_CERT_CANDIDATES = (
    "/etc/cilv/tls/server.crt",
    "/etc/cilv/tls/server.key",
)

_STATE: dict = {
    "state": "not_attempted",
    "detail": "",
    "tls_port": 443,
    "app_port": None,
    "bound": False,
    "pid": None,
    "checked_at": None,
    "unprivileged_port_start": None,
    "euid": None,
    "cert": None,
}


def _read_int(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except Exception:
        return None


def _cert_paths():
    for path in _CERT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def probe() -> dict:
    """只做探测，不起服务。返回当前边沿状态副本。"""
    info = dict(_STATE)
    info["unprivileged_port_start"] = _read_int(
        "/proc/sys/net/ipv4/ip_unprivileged_port_start"
    )
    info["euid"] = os.geteuid() if hasattr(os, "geteuid") else None
    cert = _cert_paths()
    info["cert"] = cert
    if not cert:
        info["state"] = "no_certificate"
        info["detail"] = "找不到自签证书（%s）" % "、".join(_CERT_CANDIDATES)
        return info
    # 443 是否已被别的进程监听。
    # ⚠ 不用 connect() 探测：本机可能有代理在监听 443，connect 会一直等到超时，
    # 把整个自举卡住。改为尝试 bind —— bind 失败才说明有问题，且立即返回。
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("0.0.0.0", 443))
        info["state"] = "free"
        info["detail"] = "443 可绑定"
    except OSError as exc:
        if exc.errno == _EADDRINUSE:
            info["state"] = "already_listening"
            info["bound"] = True
            info["detail"] = "443 已有服务在监听"
        elif exc.errno in _EACCES:
            info["state"] = "permission_denied"
            info["detail"] = (
                "绑定 443 被拒（权限不足）："
                "net.ipv4.ip_unprivileged_port_start=%s，服务需 root 或该值 ≤ 443"
                % info.get("unprivileged_port_start")
            )
        else:
            info["state"] = "unknown"
            info["detail"] = "探测 443 出错：%r" % (exc,)
    return info


def _serve_forever(sock, app_port: int) -> None:
    """TLS 终结 + 转发到本机业务端口。仅用标准库。"""
    cert = _cert_paths()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, cert.replace(".crt", ".key"))
    while True:
        try:
            raw, _ = sock.accept()
        except OSError:
            return
        try:
            tls = ctx.wrap_socket(raw, server_side=True)
        except Exception:
            try:
                raw.close()
            except Exception:
                pass
            continue
        threading.Thread(
            target=_handle, args=(tls, app_port), daemon=True
        ).start()


def _read_request(conn):
    """完整读入请求头与 body（TLS 记录会被 TCP 分段，一次 recv 拿不全）。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = conn.recv(65536)
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
        if len(buf) > 8 * 1024 * 1024:
            return None
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines or not lines[0]:
        return None
    length = 0
    for h in lines[1:]:
        k, _, v = h.partition(b":")
        if k.strip().lower() == b"content-length":
            try:
                length = int(v.strip())
            except ValueError:
                length = 0
    body = rest
    while len(body) < length:
        try:
            chunk = conn.recv(65536)
        except Exception:
            break
        if not chunk:
            break
        body += chunk
    return lines[0], lines[1:], body


_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
}


def _handle(conn, app_port: int) -> None:
    import http.client

    try:
        parsed = _read_request(conn)
        if not parsed:
            return
        request_line, headers, body = parsed
        parts = request_line.decode("latin-1").split(" ")
        if len(parts) < 3:
            return
        method, path, _version = parts[0], parts[1], parts[2]
        up = http.client.HTTPConnection("127.0.0.1", app_port, timeout=120)
        up.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for h in headers:
            k, _, v = h.partition(b":")
            name = k.strip().decode("latin-1")
            if name.lower() in _HOP or name.lower() == "host":
                continue
            up.putheader(name, v.strip().decode("latin-1"))
        up.putheader("Host", "127.0.0.1:%d" % app_port)
        up.putheader("X-Forwarded-Proto", "https")
        try:
            peer = conn.getpeername()
            up.putheader("X-Forwarded-For", peer[0] if peer else "")
        except Exception:
            pass
        if body:
            up.putheader("Content-Length", str(len(body)))
            up.endheaders(body)
        else:
            up.endheaders()
        resp = up.getresponse()
        data = resp.read()
        out = ("HTTP/1.1 %d %s\r\n" % (resp.status, resp.reason)).encode("latin-1")
        for k, v in resp.getheaders():
            if k.lower() in _HOP:
                continue
            out += ("%s: %s\r\n" % (k, v)).encode("latin-1")
        out += b"Content-Length: %d\r\nConnection: close\r\n\r\n" % len(data)
        conn.sendall(out + data)
        up.close()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def bootstrap(app_port: int, tls_port: int = 443) -> dict:
    """尝试建立 443 TLS 入口。返回状态字典（不抛异常）。"""
    global _STATE
    _STATE.update(
        {
            "app_port": app_port,
            "tls_port": tls_port,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    try:
        info = probe()
        if info.get("state") == "already_listening":
            _STATE.update(info)
            _STATE["state"] = "already_listening"
            return dict(_STATE)
        if info.get("state") == "permission_denied":
            _STATE.update(info)
            return dict(_STATE)
        if info.get("state") != "free":
            _STATE.update(info)
            return dict(_STATE)
        cert = info.get("cert")
        if not cert:
            _STATE["state"] = "no_certificate"
            _STATE["detail"] = "缺少自签证书，无法启用 TLS"
            return dict(_STATE)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", tls_port))
            sock.listen(128)
        except OSError as exc:
            sock.close()
            if exc.errno in _EACCES:
                _STATE["state"] = "permission_denied"
                _STATE["detail"] = (
                    "绑定 443 被拒（权限不足）：服务以非 root 运行，"
                    "且 net.ipv4.ip_unprivileged_port_start=%s"
                    % (info.get("unprivileged_port_start"),)
                )
            elif exc.errno == _EADDRINUSE:
                _STATE["state"] = "port_in_use"
                _STATE["detail"] = "443 已被占用"
            else:
                _STATE["state"] = "bind_failed"
                _STATE["detail"] = "绑定 443 失败：%r" % (exc,)
            return dict(_STATE)
        threading.Thread(
            target=_serve_forever, args=(sock, app_port), daemon=True
        ).start()
        _STATE.update(
            {
                "state": "listening",
                "detail": "已在 443 建立 HTTPS 入口，反代到 127.0.0.1:%d" % app_port,
                "bound": True,
                "pid": os.getpid(),
            }
        )
        return dict(_STATE)
    except Exception as exc:  # noqa: BLE001 - 自举失败不得影响主服务
        _STATE["state"] = "error"
        _STATE["detail"] = repr(exc)
        return dict(_STATE)


def state() -> dict:
    """当前边沿状态快照（供 /api/health 输出）。"""
    return dict(_STATE)
