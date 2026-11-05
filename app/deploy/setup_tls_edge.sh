#!/usr/bin/env bash
# 为校园网内网地址提供 HTTPS(443) 入口，反代到本机 8080。
#
# 为什么需要：校园网 ACL 只放行 80/443 两类标准端口（实测同网段另一台机器的
# 8080/22/8000/9000 全部超时，而 80/443 正常）。本服务监听 8080，校园网访问不到，
# 故需在 443 上另开一个 TLS 入口。
#
# 幂等：重复执行安全。已在监听则只更新证书与配置后 reload。
set -euo pipefail

APP_PORT="${APP_PORT:-8080}"
TLS_PORT="${TLS_PORT:-443}"
BIND_ADDR="${BIND_ADDR:-0.0.0.0}"
CERT_DIR="${CERT_DIR:-/etc/cilv/tls}"
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"
CN="${TLS_CN:-10.27.80.204}"
NGINX_CONF=/etc/nginx/conf.d/cilv-tls.conf
LOG_TAG=cilv-tls
PEER_IP="${PEER_IP:-10.27.80.205}"   # 同网段参照机，用于判断校网 ACL 的放行口径

log() { echo "[$(date +%H:%M:%S)] [$LOG_TAG] $*"; }

# ══ 诊断 0：先说清楚「为什么校园网访问不到」═════════════════════════
# 现象：同网段机器 A（$PEER_IP）的 80/443 通、8080/22 全超时；
#       本机所有端口从客户端侧全超时。
# 两种可能，处理方式不同：
#   (a) 校园网 ACL 只放行 80/443  → 本机开 443 即可解决（但若本机 IP 不在 ACL 白名单则仍需网管开通）
#   (b) 本机 firewalld 拦了入站   → 本脚本放行 443 即可解决
# 下面两项输出用于区分。
log "════ 诊断开始 ════"
if command -v firewall-cmd >/dev/null 2>&1; then
  log "firewalld 运行中；当前放行端口：$(firewall-cmd --list-ports 2>/dev/null | tr '\n' ' ')"
  log "活动网卡与区域："
  firewall-cmd --get-active-zones 2>/dev/null | sed 's/^/    /'
else
  log "firewalld 未安装或未运行（入站不受其限制）"
fi
log "本机自测（绕过校园网）："
log "  curl 127.0.0.1:${APP_PORT} -> $(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:${APP_PORT}/api/health" 2>/dev/null || echo 000)"
log "  curl ${PEER_IP}:80 -> $(curl -s --max-time 5 -o /dev/null -w '%{http_code}' "http://${PEER_IP}/" 2>/dev/null || echo 000)"
log "  curl ${PEER_IP}:443 -> $(curl -s -k --max-time 5 -o /dev/null -w '%{http_code}' "https://${PEER_IP}/" 2>/dev/null || echo 000)"
log "  （参照机 80/443 能通 = 校网 ACL 确实放行这两个端口）"
log "════ 诊断结束 ════"

# ── 1. 证书：自签（SAN 含本机 IP，浏览器会提示"不安全"，点"继续访问"即可）──
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT" ] || [ ! -f "$KEY" ]; then
  log "生成自签证书（CN=$CN）…"
  openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
    -keyout "$KEY" -out "$CERT" \
    -subj "/C=CN/O=Cilv/OU=Project/CN=$CN" \
    -addext "subjectAltName=IP:$CN,DNS:localhost,DNS:$CN" \
    2>/dev/null
  chmod 600 "$KEY"
  chmod 644 "$CERT"
  log "  ✓ 证书已生成"
else
  log "证书已存在，沿用"
fi

# ── 2. 前端：优先 nginx，没有就用 Python 标准库写 TLS 反代 ──
if command -v nginx >/dev/null 2>&1; then
  log "使用 nginx 作为 443 前端"
  cat > "$NGINX_CONF" <<EOF
server {
    listen ${TLS_PORT} ssl;
    listen [::]:${TLS_PORT} ssl;
    server_name _;

    ssl_certificate     ${CERT};
    ssl_certificate_key ${KEY};
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_session_cache   shared:cilv:4m;

    client_max_body_size 32m;

    location / {
        proxy_pass         http://127.0.0.1:${APP_PORT};
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
    }
}
EOF
  nginx -t 2>&1 | sed 's/^/  /'
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl restart nginx || systemctl reload nginx
  log "  ✓ nginx 已重启"
else
  log "未装 nginx，改用 Python TLS 反代"
  cat > /opt/cilv/bin/cilv-tls-proxy.py <<'PYEOF'
"""极简 TLS→HTTP 反向代理：监听 443，转发到本机 8080。仅用标准库。"""
import http.client
import socket
import ssl
import sys
import threading

LISTEN = ("0.0.0.0", int(sys.argv[1]) if len(sys.argv) > 1 else 443)
TARGET = ("127.0.0.1", int(sys.argv[2]) if len(sys.argv) > 2 else 8080)
CERT = "/etc/cilv/tls/server.crt"
KEY = "/etc/cilv/tls/server.key"


def pump(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except Exception:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass


def handle(conn):
    try:
        raw = conn.recv(65536)
        if not raw:
            return
        # 只取第一行与 Host，其余头原样转发
        head, _, rest = raw.partition(b"\r\n\r\n")
        first = head.split(b"\r\n")[0]
        up = http.client.HTTPConnection(*TARGET, timeout=120)
        up.putrequest(first.decode("latin-1").split(" ")[0],
                     " ".join(first.decode("latin-1").split(" ")[1:]),
                     skip_host=True, skip_accept_encoding=True)
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith((b"host:", b"connection:")):
                continue
            up.putheader(line.decode("latin-1"))
        up.endheaders(rest if rest else None)
        resp = up.getresponse()
        data = resp.read()
        out = b"HTTP/1.1 %d %s\r\n" % (resp.status, resp.reason.encode())
        for k, v in resp.getheaders():
            if k.lower() in ("transfer-encoding", "content-length", "connection"):
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
srv.listen(64)
print("tls proxy listening on %s:%d -> %s:%d" % (LISTEN[0], LISTEN[1], TARGET[0], TARGET[1]), flush=True)
while True:
    c, _ = srv.accept()
    try:
        s = ctx.wrap_socket(c, server_side=True)
    except Exception:
        continue
    threading.Thread(target=handle, args=(s,), daemon=True).start()
PYEOF
  cat > /etc/systemd/system/cilv-tls-proxy.service <<EOF
[Unit]
Description=Cilv TLS reverse proxy (443 -> ${APP_PORT})
After=network.target cilv.service

[Service]
Type=simple
ExecStart=/usr/bin/python3.11 /opt/cilv/bin/cilv-tls-proxy.py ${TLS_PORT} ${APP_PORT}
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now cilv-tls-proxy
  log "  ✓ TLS 反代服务已启动"
fi

# ── 3. 防火墙放行 443 ──
if command -v firewall-cmd >/dev/null 2>&1; then
  log "放行防火墙 ${TLS_PORT}/tcp"
  firewall-cmd --permanent --add-port=${TLS_PORT}/tcp >/dev/null
  firewall-cmd --reload >/dev/null
  log "  ✓ 防火墙已放行"
fi

# ── 4. 自检 ──
sleep 3
CODE=$(curl -sk --max-time 12 -o /dev/null -w "%{http_code}" "https://127.0.0.1:${TLS_PORT}/api/health" 2>/dev/null || echo 000)
log "本机自检 https://127.0.0.1:${TLS_PORT}/api/health -> HTTP ${CODE}"
if [ "$CODE" = "200" ]; then
  log "════ 443 入口就绪 ════"
  log "  校园网内请访问：https://${CN}/"
  log "  证书为自签（SAN 含 ${CN}），浏览器提示「不安全」时点「继续访问」即可。"
  log "  若客户端仍打不开：说明校网 ACL 按 IP 放行而本机 ${CN} 未在白名单，"
  log "  需请网络管理为 ${CN} 开通 80/443 入站（这一步本脚本无法代替）。"
else
  log "⚠ 自检未通过，检查 nginx / cilv-tls-proxy 日志："
  log "    journalctl -u nginx -n 20 --no-pager"
  log "    journalctl -u cilv-tls-proxy -n 20 --no-pager"
  exit 1
fi
