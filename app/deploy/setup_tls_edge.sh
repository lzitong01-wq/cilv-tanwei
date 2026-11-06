#!/usr/bin/env bash
# 为校园网内网地址提供 HTTPS(443) 入口，反代到本机 8080。
#
# 为什么需要：本服务监听 8080，而校园网内访问常要求标准 HTTPS 端口（443），
# 与同网段参照机保持一致的访问形态便于评审。故在 443 上另开一个 TLS 入口。
#
# ⚠ 绑定地址不能无条件用 0.0.0.0：本机若同时跑着 `tailscale serve`
#   （公网 Funnel 入口，监听 <tailscale-IP>:443），绑 0.0.0.0:443 必然
#   EADDRINUSE，反代起不来。脚本会自动检测并改绑本机内网 IP。
#
# 幂等：重复执行安全。已在监听则只更新证书与配置后 reload。
set -euo pipefail

APP_PORT="${APP_PORT:-8080}"
TLS_PORT="${TLS_PORT:-443}"
# 留空 = 自动判定（443 已被别的进程占用时绑本机内网 IP，否则 0.0.0.0）
BIND_ADDR="${BIND_ADDR:-}"
CERT_DIR="${CERT_DIR:-/etc/cilv/tls}"
CERT="$CERT_DIR/server.crt"
KEY="$CERT_DIR/server.key"
CN="${TLS_CN:-10.27.80.204}"
NGINX_CONF=/etc/nginx/conf.d/cilv-tls.conf
LOG_TAG=cilv-tls
PEER_IP="${PEER_IP:-10.27.80.205}"   # 同网段参照机，用于判断校网 ACL 的放行口径

log() { echo "[$(date +%H:%M:%S)] [$LOG_TAG] $*"; }

# ══ 绑定地址自动判定 ══════════════════════════════════════════════════
# 若 443 已被别的进程占用（典型：`tailscale serve` 监听 <tailscale-IP>:443），
# 绑 0.0.0.0:443 会 EADDRINUSE 直接起不来。此时改绑本机内网 IP 即可共存。
detect_bind_addr() {
  if [ -n "$BIND_ADDR" ]; then
    echo "$BIND_ADDR"; return
  fi
  if ss -tln 2>/dev/null | grep -qE '(:443 |\]:443 )'; then
    local ip
    ip="$(ip -4 route get 1.1.1.1 2>/dev/null \
          | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')"
    echo "${ip:-0.0.0.0}"
  else
    echo "0.0.0.0"
  fi
}
BIND_ADDR="$(detect_bind_addr)"
if [ "$BIND_ADDR" = "0.0.0.0" ]; then
  log "绑定地址：0.0.0.0:${TLS_PORT}（443 空闲）"
else
  log "绑定地址：${BIND_ADDR}:${TLS_PORT}（443 已被占用，改绑内网 IP 以避开冲突）"
fi

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
if ! command -v openssl >/dev/null 2>&1; then
  log "未找到 openssl 命令，尝试安装…"
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y openssl >/tmp/cilv-openssl-install.log 2>&1 || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y openssl >/tmp/cilv-openssl-install.log 2>&1 || true
  fi
  command -v openssl >/dev/null 2>&1 || { log "✗ 无法安装 openssl，无法生成证书"; exit 1; }
  log "  ✓ openssl 就绪"
fi
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
NGINX_OK=1
if [ "${SKIP_NGINX:-0}" = "1" ]; then
  # 强制跳过 nginx：部分 Anolis OS 机器上 nginx 服务起不来（SELinux / 默认站点冲突），
  # 此时直接用 Python 标准库反代，功能等价且不依赖系统包。
  log "按要求跳过 nginx，直接用 Python 标准库反代"
  NGINX_OK=0
fi
if [ "$NGINX_OK" = "1" ] && ! command -v nginx >/dev/null 2>&1; then
  log "未装 nginx，尝试 dnf 安装（失败则回落到 Python 反代）…"
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y nginx >/tmp/cilv-nginx-install.log 2>&1 || true
  elif command -v yum >/dev/null 2>&1; then
    yum install -y nginx >/tmp/cilv-nginx-install.log 2>&1 || true
  fi
  command -v nginx >/dev/null 2>&1 && log "  ✓ nginx 安装成功" \
    || log "  nginx 装不上，用 Python 标准库反代（功能等价，不依赖外部包）"
fi

if command -v nginx >/dev/null 2>&1 && [ "$NGINX_OK" = "1" ]; then
  log "使用 nginx 作为 443 前端"
  cat > "$NGINX_CONF" <<EOF
server {
    listen ${BIND_ADDR}:${TLS_PORT} ssl;
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
  nginx -t 2>&1 | sed 's/^/  /' || true
  systemctl enable nginx >/dev/null 2>&1 || true
  # nginx 起不来（SELinux 限制 / 默认站点冲突 / 端口占用）时不能直接退出——
  # set -e 会把整段脚本干掉，后面的防火墙放行与自检都不执行。改为回落 Python 反代。
  if systemctl restart nginx 2>/tmp/cilv-nginx-restart.log || systemctl reload nginx 2>>/tmp/cilv-nginx-restart.log; then
    if systemctl is-active --quiet nginx; then
      log "  ✓ nginx 已启动"
    else
      log "  ⚠ nginx 重启后未处于 active，回落到 Python 反代"
      systemctl disable nginx >/dev/null 2>&1 || true
      systemctl stop nginx >/dev/null 2>&1 || true
      NGINX_OK=0
    fi
  else
    log "  ⚠ nginx 启动失败，回落到 Python 反代"
    log "  失败原因：$(tail -2 /tmp/cilv-nginx-restart.log 2>/dev/null | tr '\n' ' ' | cut -c1-160)"
    systemctl disable nginx >/dev/null 2>&1 || true
    systemctl stop nginx >/dev/null 2>&1 || true
    NGINX_OK=0
  fi
else
  log "未装 nginx，改用 Python TLS 反代"
  # 反代实现放在同目录的 cilv_tls_proxy.py（与本脚本一起分发，避免两份代码不同步）
  SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
  install -m 0755 "$SCRIPT_DIR/cilv_tls_proxy.py" /opt/cilv/bin/cilv-tls-proxy.py
  # 不能写死 /usr/bin/python3.11：Anolis 最小化安装里该路径常常不存在，
  # 单元会一直 crash-loop（表现为「启动了但 curl 拒绝连接」）。
  PY_BIN=""
  for p in /opt/cilv/current/app/.venv/bin/python /usr/bin/python3.11 \
           /usr/local/bin/python3.11 /usr/bin/python3; do
    if [ -x "$p" ] && "$p" -c "import ssl,socket" >/dev/null 2>&1; then
      PY_BIN="$p"; break
    fi
  done
  if [ -z "$PY_BIN" ]; then
    log "✗ 找不到可用 python（需 3.x 且带 ssl 模块），无法启动反代"
    exit 1
  fi
  log "  python：$PY_BIN"
  cat > /etc/systemd/system/cilv-tls-proxy.service <<EOF
[Unit]
Description=Cilv TLS reverse proxy (443 -> ${APP_PORT})
After=network.target cilv.service

[Service]
Type=simple
Environment=BIND_ADDR=${BIND_ADDR}
ExecStart=${PY_BIN} /opt/cilv/bin/cilv-tls-proxy.py ${TLS_PORT} ${APP_PORT}
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
  log "  若客户端仍打不开：说明校网按 IP 放行而本机 ${CN} 未在白名单，"
  log "  需请网络管理为 ${CN} 开通 80/443 入站（这一步本脚本无法代替）。"
  log ""
  log "客户端验证命令（在校园网内网机器上执行）："
  log "  curl -k https://${CN}/api/health"
  log "  浏览器打开 https://${CN}/（自签证书提示不安全时点『继续访问』）"
else
  log "⚠ 自检未通过，检查 nginx / cilv-tls-proxy 日志："
  log "    journalctl -u nginx -n 20 --no-pager"
  log "    journalctl -u cilv-tls-proxy -n 20 --no-pager"
  exit 1
fi
