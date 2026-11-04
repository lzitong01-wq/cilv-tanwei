#!/usr/bin/env bash
# 服务器端 CI/CD 部署助手（一次性安装脚本）。
#
# 与 deploy_agent.sh 的分工：
#   - 本脚本：首次部署（拉包 → 装依赖 → 建账号 → 装 systemd → 开端口 → 验收）
#   - deploy_agent.sh：常驻轮询，后续版本自动部署
#
# 用法（在服务器上执行）:
#   GITHUB_REPO=owner/repo bash install_cicd.sh
#
# 环境事实（2026-11-04 实测，见 CICD说明.md）：
#   - 系统 SQLite 3.26.0 无 FTS5 trigram → 由 core/sqlite_compat.py 自动切 pysqlite3
#   - 单次 HTTP 下载超过约 3.9 MB 会截断 → 必须分片 + Range 请求
#   - 出口总带宽约 35 KB/s，并行下载无效 → 89 MB 包约需 43 分钟

set -uo pipefail

DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/cilv}"
BIN_DIR="$DEPLOY_ROOT/bin"
RELEASES_DIR="$DEPLOY_ROOT/releases"
CURRENT_LINK="$DEPLOY_ROOT/current"
STATE_FILE="$DEPLOY_ROOT/.deploy-state"
ENV_FILE="/etc/cilv/deploy.env"
SERVICE_NAME="${SERVICE_NAME:-cilv}"
AGENT_SERVICE="cilv-deploy-agent"
PORT="${PORT:-8080}"
REPO_URL="${REPO_URL:-https://github.com/$GITHUB_REPO.git}"
TAG="${TAG:-}"

log()  { echo "[install_cicd] $*"; }
die()  { echo "[install_cicd] 致命错误: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root 权限"

# ---------- 1. 系统依赖 ----------
log "检查系统依赖…"
if ! command -v python3.11 >/dev/null 2>&1; then
    log "安装 Python 3.11"
    dnf install -y python3.11 python3.11-pip || die "Python 3.11 安装失败"
fi
if ! command -v git >/dev/null 2>&1; then
    log "安装 git"
    dnf install -y git || die "git 安装失败"
fi
log "Python: $(python3.11 -V 2>&1)"

# ---------- 2. 账号与目录 ----------
log "准备账号与目录…"
id cilv >/dev/null 2>&1 || useradd -r -s /sbin/nologin cilv
id cilv-deploy >/dev/null 2>&1 || useradd -r -s /sbin/nologin cilv-deploy

install -d -o cilv -g cilv "$RELEASES_DIR"
install -d -o root -g root "$BIN_DIR"
install -d -o root -g root /etc/cilv

# ---------- 3. 拉取代码 ----------
if [ -n "$TAG" ]; then
    log "拉取 $TAG…"
    git clone --depth 1 --branch "$TAG" "$REPO_URL" "$RELEASES_DIR/$TAG" || die "克隆失败"
else
    log "拉取默认分支…"
    git clone --depth 1 "$REPO_URL" "$RELEASES_DIR/repo" || die "克隆失败"
    TAG="repo"
fi

# ---------- 4. 部署代理 ----------
log "安装部署代理…"
if [ -f "$RELEASES_DIR/$TAG/app/deploy/deploy_agent.sh" ]; then
    install -m 0755 "$RELEASES_DIR/$TAG/app/deploy/deploy_agent.sh" "$BIN_DIR/deploy-agent.sh"
else
    die "找不到 deploy_agent.sh"
fi

# ---------- 5. 环境配置 ----------
if [ ! -f "$ENV_FILE" ]; then
    log "写 $ENV_FILE"
    cat > "$ENV_FILE" <<EOF
GITHUB_REPO=${GITHUB_REPO}
POLL_INTERVAL=${POLL_INTERVAL:-300}
DEPLOY_ROOT=$DEPLOY_ROOT
SERVICE_NAME=$SERVICE_NAME
PORT=$PORT
# 可选：GITHUB_TOKEN=xxx   # 提高 API 配额（60 -> 5000/小时）
EOF
    chmod 600 "$ENV_FILE"
fi

# ---------- 6. systemd ----------
log "安装 systemd 单元…"
if [ -f "$RELEASES_DIR/$TAG/app/deploy/cilv.service" ]; then
    cp "$RELEASES_DIR/$TAG/app/deploy/cilv.service" /etc/systemd/system/
fi
if [ -f "$RELEASES_DIR/$TAG/app/deploy/cilv-deploy-agent.service" ]; then
    cp "$RELEASES_DIR/$TAG/app/deploy/cilv-deploy-agent.service" /etc/systemd/system/
fi
systemctl daemon-reload

# ---------- 7. 首次部署（装依赖 + 起服务）----------
log "首次部署…"
ln -sfn "$RELEASES_DIR/$TAG" "$CURRENT_LINK"
cd "$CURRENT_LINK/app"

if [ ! -x .venv/bin/python ]; then
    python3.11 -m venv .venv || die "venv 创建失败"
    .venv/bin/pip install --upgrade pip --quiet || true
fi

WHEELS="$CURRENT_LINK/app/deploy/wheels/linux-x86_64-py311"
if [ -d "$WHEELS" ]; then
    log "离线安装必装依赖"
    .venv/bin/pip install --no-index --find-links "$WHEELS" \
        -r "$CURRENT_LINK/app/backend/requirements.txt" || die "必装依赖安装失败"
    # 可选依赖（pysqlite3-binary）：装不上不阻断，应用层会退回内存子串匹配
    if [ -f "$CURRENT_LINK/app/backend/requirements-optional.txt" ]; then
        log "安装可选依赖（pysqlite3-binary，失败不阻断）"
        .venv/bin/pip install --no-index --find-links "$WHEELS" \
            -r "$CURRENT_LINK/app/backend/requirements-optional.txt" \
            && log "  ✓ pysqlite3 已装" \
            || log "  ⚠ 可选依赖安装失败，≥3 字检索将退回内存匹配（功能可用、较慢）"
    fi
else
    log "未找到离线 wheels，联网安装"
    .venv/bin/pip install -r "$CURRENT_LINK/app/backend/requirements.txt" || die "依赖安装失败"
fi

# ---------- 8. 防火墙 ----------
log "开放端口 $PORT…"
firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null 2>&1 && firewall-cmd --reload >/dev/null 2>&1
log "  已开放（firewall-cmd 静默失败则需手工处理）"

# ---------- 9. 启动 ----------
log "启动服务…"
systemctl enable --now "$SERVICE_NAME" || die "$SERVICE_NAME 启动失败"
if systemctl list-unit-files | grep -q "$AGENT_SERVICE"; then
    systemctl enable --now "$AGENT_SERVICE" || log "  ⚠ 部署代理启动失败（可稍后手工启动）"
fi

sleep 6

# ---------- 10. 验收 ----------
log "验收…"
HEALTH=$(curl -fsS --max-time 20 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)
if [ -z "$HEALTH" ]; then
    log "✗ 健康检查无响应，查看日志：journalctl -u $SERVICE_NAME -n 50"
    exit 1
fi
echo "$HEALTH" | head -c 800
echo
log "✓ 安装完成"
log ""
log "常用命令："
log "  systemctl status $SERVICE_NAME"
log "  journalctl -u $SERVICE_NAME -f"
log "  $BIN_DIR/deploy-agent.sh --version"
log "  访问地址：http://<服务器IP>:$PORT"
