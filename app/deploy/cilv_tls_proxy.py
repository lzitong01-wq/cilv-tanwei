#!/usr/bin/env python3
"""修正版 TLS 反代：完整读取请求头与 body（支持分段到达）。

原版 bug：conn.recv(65536) 一次调用假定拿全请求，TLS 记录被 TCP 分段时
只拿到半条记录 → 解密失败 → 连接被关闭。已用本地验证复现。
"""
import http.client
import socket
import ssl
import sys
import threading

LISTEN = ("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 443)
TARGET = ("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 8080)
CERT = sys.argv[3] if len(sys.argv) > 3 else "/etc/cilv/tls/server.crt"
KEY = sys.argv[4] if len(sys.argv) > 4 else "/etc/cilv/tls/server.key"

HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length",
}


def read_request(conn):
    """完整读入请求头与 body。返回 (请求行bytes, 头列表, body bytes) 或 None。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            chunk = conn.recv(65536)
        except Exception:
            return None
        if not chunk:
            return None
        buf += chunk
        if len(buf) > 4 * 1024 * 1024:
            return None
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    if not lines or not lines[0]:
        return None
    request_line = lines[0]
    headers = lines[1:]
    # 按 Content-Length 补齐 body
    length = 0
    for h in headers:
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
    return request_line, headers, body


def handle(conn):
    try:
        parsed = read_request(conn)
        if not parsed:
            return
        request_line, headers, body = parsed
        parts = request_line.decode("latin-1").split(" ")
        if len(parts) < 3:
            return
        method, path, version = parts[0], parts[1], parts[2]

        up = http.client.HTTPConnection(*TARGET, timeout=120)
        up.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for h in headers:
            k, _, v = h.partition(b":")
            name = k.strip().decode("latin-1")
            if name.lower() in HOP_BY_HOP:
                continue
            if name.lower() == "host":
                continue
            up.putheader(name, v.strip().decode("latin-1"))
        # Host 头必须存在（HTTP/1.1 强制），指向后端
        up.putheader("Host", "%s:%d" % TARGET)
        up.putheader("X-Forwarded-Proto", "https")
        up.putheader("X-Forwarded-For", conn.getpeername()[0] if conn.getpeername() else "")
        if body:
            up.putheader("Content-Length", str(len(body)))
            up.endheaders(body)
        else:
            up.endheaders()

        resp = up.getresponse()
        data = resp.read()
        out = ("HTTP/1.1 %d %s\r\n" % (resp.status, resp.reason)).encode("latin-1")
        for k, v in resp.getheaders():
            if k.lower() in HOP_BY_HOP:
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


ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(LISTEN)
srv.listen(128)
print("tls proxy listening on %s:%d -> %s:%d" % (LISTEN[0], LISTEN[1], TARGET[0], TARGET[1]), flush=True)
while True:
    c, _ = srv.accept()
    try:
        s = ctx.wrap_socket(c, server_side=True)
    except Exception:
        try:
            c.close()
        except Exception:
            pass
        continue
    threading.Thread(target=handle, args=(s,), daemon=True).start()
