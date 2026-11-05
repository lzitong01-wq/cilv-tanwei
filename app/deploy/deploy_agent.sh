#!/usr/bin/env bash
# 服务器端部署代理：轮询 GitHub Releases，发现新版本就拉取 → 部署 → 健康检查 → 回滚。
#
# 设计要点（均由实测决定）：
# - **服务器主动拉**，不从 CI 推包：实测本机→服务器上行仅约 17 KB/s，scp 会中途截断；
#   而服务器→GitHub 的 git clone 与 API 都正常。
# - **版本目录 + 软链切换**：新旧版本并存，部署失败能秒回滚。
# - **健康检查为准**：不是「进程起来了」就算成功，/api/health 必须返回预期字段。
# - **失败自动回滚**：连续失败 N 次后停手，等人工介入，不无限重试。
#
# 安装：
#   install -m 0755 deploy_agent.sh /opt/cilv/bin/deploy-agent.sh
#   useradd -r -s /sbin/nologin cilv-deploy
#   # 写 /etc/cilv/deploy.env: GITHUB_REPO=owner/name
#
# 运行（前台调试）：GITHUB_REPO=owner/name ./deploy_agent.sh --once

set -uo pipefail

# ---------- 配置 ----------
GITHUB_REPO="${GITHUB_REPO:-}"                 # 必填: owner/repo
RELEASE_API="${RELEASE_API:-https://api.github.com}"
POLL_INTERVAL="${POLL_INTERVAL:-300}"          # 轮询间隔（秒）
DEPLOY_ROOT="${DEPLOY_ROOT:-/opt/cilv}"
RELEASES_DIR="${RELEASES_DIR:-$DEPLOY_ROOT/releases}"
CURRENT_LINK="${CURRENT_LINK:-$DEPLOY_ROOT/current}"
STATE_FILE="${STATE_FILE:-$DEPLOY_ROOT/.deploy-state}"
SERVICE_NAME="${SERVICE_NAME:-cilv}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8080/api/health}"
PORT="${PORT:-8080}"
MAX_FAILURES="${MAX_FAILURES:-3}"
GITHUB_TOKEN="${GITHUB_TOKEN:-}"               # 可选，提高 API 配额（60→5000/小时）
LOG_TAG="deploy-agent"

log()  { logger -t "$LOG_TAG" "$*" 2>/dev/null || echo "[$(date '+%F %T')] $*"; }
die()  { log "致命错误: $*"; exit 1; }

# ---------- 状态管理 ----------
read_state() {
    if [ -f "$STATE_FILE" ]; then
        DEPLOYED_TAG=$(grep -oP '(?<=^DEPLOYED_TAG=).*' "$STATE_FILE" 2>/dev/null | tail -1)
        FAIL_COUNT=$(grep -oP '(?<=^FAIL_COUNT=).*' "$STATE_FILE" 2>/dev/null | tail -1)
    fi
    DEPLOYED_TAG="${DEPLOYED_TAG:-}"
    FAIL_COUNT="${FAIL_COUNT:-0}"
}
write_state() {
    cat > "$STATE_FILE" <<EOF
DEPLOYED_TAG=${DEPLOYED_TAG}
FAIL_COUNT=${FAIL_COUNT}
LAST_RUN=$(date +%s)
EOF
}

# ---------- 检查 GitHub 最新 Release ----------
# 用 python3 解析 JSON，不靠 grep -oP：
# 实测 grep 在字段含斜杠/特殊字符时会报 "range out of order in character class"。
_json_get() {
    # $1 = json, $2 = 顶层键名
    printf '%s' "$1" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
v=d.get('$2','')
if isinstance(v,list):
    for i in v:
        if isinstance(i,dict) and i.get('name','').endswith('.tar.gz'):
            print(i.get('browser_download_url','')); break
else:
    print(v)
" 2>/dev/null
}

fetch_latest_tag() {
    local url="$RELEASE_API/repos/$GITHUB_REPO/releases/latest"
    local auth=()
    [ -n "$GITHUB_TOKEN" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")

    local body
    body=$(curl -fsS --max-time 30 "${auth[@]}" "$url" 2>/dev/null) || return 0
    _json_get "$body" "tag_name"
}

fetch_asset_url() {
    local tag="$1"
    local url="$RELEASE_API/repos/$GITHUB_REPO/releases/tags/$tag"
    local auth=()
    [ -n "$GITHUB_TOKEN" ] && auth=(-H "Authorization: Bearer $GITHUB_TOKEN")

    local body
    body=$(curl -fsS --max-time 30 "${auth[@]}" "$url" 2>/dev/null) || return 0

    # 优先用 API 端点（api.github.com + Accept: application/octet-stream）。
    # 不用 browser_download_url：它要先连 github.com 才能 302 到
    # release-assets.githubusercontent.com，而 github.com 这个域名
    # 在部分网络下连不上（实测 000 超时），api.github.com 却通。
    # 实测 API 端点 Range 请求返回 206 + 精确字节数，完全可用。
    printf '%s' "$body" | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    sys.exit(0)
for a in d.get('assets', []):
    if a.get('name','').endswith('.tar.gz') and a.get('url'):
        print(a['url']); break
" 2>/dev/null
}

# ---------- 下载（分片 + 断点续传）----------
# 实测：单次 HTTP 下载超过约 3.9 MB 会被截断（本机与服务器同一出口 NAT）。
# 策略：按 2 MB 分片逐个下载，每片单独 curl，失败只重试该片；
#       全部下完再合并并校验总大小。
download_asset() {
    local url="$1" dest="$2"
    local workdir="${dest}.parts"
    local chunk_size=$((2 * 1024 * 1024))
    # API 端点必须带 Accept 头才会返回二进制而不是 JSON 元信息；
    # **且必须带 Authorization 头**——实测不带就 403（即使仓库是公开的，
    # assets API 端点仍要求认证）。两个头缺一不可。
    local accept=(-H "Accept: application/octet-stream")
    [ -n "$GITHUB_TOKEN" ] && accept+=(-H "Authorization: Bearer $GITHUB_TOKEN")

    mkdir -p "$workdir"

    # 拿总大小：用「Range: bytes=0-0」的 GET 探真实大小
    # （响应头 Content-Range 里有总长；HEAD 拿不到，会返回 0）。
    local total
    total=$(curl -fsSL --max-time 30 "${accept[@]}" -r 0-0 -D - -o /dev/null "$url" 2>/dev/null \
            | tr -d '\r' | grep -i '^content-range:' | tail -1 \
            | awk -F/ '{print $2}' | tr -d ' ')

    if [ -z "$total" ] || ! [ "$total" -gt 0 ] 2>/dev/null; then
        log "无法获取资产总大小（Content-Range 缺失），回退为单次下载"
        curl -fsSL "${accept[@]}" --retry 3 --retry-delay 5 --connect-timeout 30 --max-time 1800 \
             -o "$dest" "$url" && return 0
        return 1
    fi

    log "资产大小 $((total / 1024 / 1024)) MB，按 2 MB 分片下载"
    local total_chunks=$(( (total + chunk_size - 1) / chunk_size ))
    local i offset part attempt

    for i in $(seq 0 $((total_chunks - 1))); do
        offset=$((i * chunk_size))
        part="$workdir/part_$(printf '%04d' "$i")"

        # 已存在且大小正确的片跳过（断点续传）
        if [ -f "$part" ]; then
            local psize
            psize=$(stat -c%s "$part" 2>/dev/null || echo 0)
            local expect=$chunk_size
            [ $i -eq $((total_chunks - 1)) ] && expect=$((total - offset))
            [ "$psize" -eq "$expect" ] && continue
            rm -f "$part"
        fi

        for attempt in 1 2 3 4 5; do
            # -r 指定范围取这一片；失败重试只针对这片
            # -sS 静默进度条（否则日志被刷屏），-f 出错返回非零
            if curl -fsSL "${accept[@]}" --connect-timeout 30 --max-time 300 \
                    -r "${offset}-$((offset + chunk_size - 1))" \
                    -o "$part" "$url"; then
                break
            fi
            log "  第 $i 片第 $attempt 次失败，重试…"
            rm -f "$part"
            sleep 5
            [ $attempt -eq 5 ] && { log "第 $i 片最终失败"; return 1; }
        done
        printf "\r  已下 %d/%d 片 " $((i + 1)) "$total_chunks"
    done

    echo
    # 合并并校验
    cat "$workdir"/part_* > "$dest"
    local got
    got=$(stat -c%s "$dest" 2>/dev/null || echo 0)
    if [ "$got" -ne "$total" ]; then
        # 不删分片：下一轮轮询会跳过已下好的片（断点续传），避免重下几十 MB。
        log "下载大小不符: 期望 $total，实际 $got；保留分片供下一轮续传"
        return 1
    fi
    rm -rf "$workdir"
    log "下载完成并校验通过: $dest"
    return 0
}

# ---------- 解压与切换 ----------
extract_release() {
    local tarball="$1" tag="$2"
    local dir="$RELEASES_DIR/$tag"

    # 删旧目录：目录里可能有 root 属主的文件（手工部署或旧版脚本留下的），
    # 普通 rm 删不掉 → 用 sudo 删（visudo 白名单里已放开 deploy-agent.sh 自身，
    # 这里用 root 权限执行 rm 是必要的）。
    sudo -n rm -rf "$dir" 2>/dev/null || rm -rf "$dir" || {
        log "无法清理旧目录 $dir"
        return 1
    }
    mkdir -p "$dir" || { log "无法创建 $dir"; return 1; }

    # 不加 --strip-components：交付包的顶层就是 app/、data/、docs/、tools/ 与规范文档，
    # 剥掉一层会把 app/ 变成 backend/，导致下面的结构检查失败（实测踩过）。
    tar -xzf "$tarball" -C "$dir" || return 1
    [ -d "$dir/app" ] || { log "包结构异常：未找到 $dir/app 目录"; return 1; }
    [ -d "$dir/data" ] || { log "包结构异常：未找到 $dir/data 目录"; return 1; }

    # tar 解压出来的文件默认是属主自己的、权限 644/755（无写位）。
    # 下一轮部署要 rm -rf 这个目录，**没有写位就删不掉** →
    # 实测踩过：解压成功但下次部署报「文件已存在」而失败。
    # 这里给属主加写位，保证下次能干净重来。
    chmod -R u+w "$dir" 2>/dev/null || true
    return 0
}

switch_current() {
    local tag="$1"
    ln -sfn "$RELEASES_DIR/$tag" "$CURRENT_LINK"
    log "当前版本软链已切到 $tag"
}

# ---------- 依赖与重启 ----------
ensure_venv() {
    local app="$CURRENT_LINK/app"
    local py="${APP_PYTHON:-python3.11}"
    local venv="$app/.venv"

    if [ ! -x "$venv/bin/python" ]; then
        log "创建虚拟环境 $venv"
        "$py" -m venv "$venv" || return 1
        "$venv/bin/pip" install --upgrade pip --quiet || true
    fi

    local wheels="$app/deploy/wheels/linux-x86_64-py311"
    if [ -d "$wheels" ]; then
        log "离线安装必装依赖"
        "$venv/bin/pip" install --no-index --find-links "$wheels" \
            -r "$app/backend/requirements.txt" || return 1
        # 可选依赖：pysqlite3-binary（目标机系统 SQLite < 3.34 时补 FTS5 trigram）。
        # 装不上不阻断——应用层 sqlite_compat.py 会退回内存子串匹配。
        if [ -f "$app/backend/requirements-optional.txt" ]; then
            log "安装可选依赖（pysqlite3-binary）"
            "$venv/bin/pip" install --no-index --find-links "$wheels" \
                -r "$app/backend/requirements-optional.txt" \
                && log "  ✓ pysqlite3 已装" \
                || log "  ⚠ 可选依赖安装失败，≥3 字检索将退回内存匹配（功能可用、较慢）"
        fi
    else
        log "未找到离线 wheels，联网安装"
        "$venv/bin/pip" install -r "$app/backend/requirements.txt" || return 1
    fi
    return 0
}

restart_service() {
    # 部署代理以 cilv-deploy 用户运行，没有直接 systemctl 的权限
    # （polkit 会要求交互式认证）。用 sudo 走 visudo 的白名单：
    #   /etc/sudoers.d/cilv-deploy 只允许 restart/is-active 这两条 cilv 命令。
    log "重启服务 $SERVICE_NAME"
    sudo -n systemctl restart "$SERVICE_NAME" || return 1
    sleep 4
    sudo -n systemctl is-active --quiet "$SERVICE_NAME"
}

# ---------- 健康检查 ----------
health_check() {
    local tries=6
    while [ $tries -gt 0 ]; do
        local body
        body=$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null)
        if [ -n "$body" ]; then
            # app=ok 且 works 数量正常才算通过
            if echo "$body" | grep -q '"app": *"ok"' \
               || echo "$body" | grep -q '"app":"ok"'; then
                log "健康检查通过: $(echo "$body" | head -c 200)"
                return 0
            fi
            log "健康检查返回异常: $(echo "$body" | head -c 200)"
        fi
        tries=$((tries - 1))
        log "健康检查未通过，剩余重试 $tries"
        sleep 5
    done
    return 1
}

# ---------- 主流程 ----------
deploy_tag() {
    local tag="$1"
    log "════ 开始部署 $tag ════"

    local asset_url tarball
    asset_url=$(fetch_asset_url "$tag")
    [ -n "$asset_url" ] || { log "未找到 $tag 的 .tar.gz 资产"; return 1; }

    tarball="/tmp/cilv-$tag.tar.gz"
    download_asset "$asset_url" "$tarball" || { log "下载失败"; return 1; }

    log "解压到 $RELEASES_DIR/$tag"
    extract_release "$tarball" "$tag" || { log "解压失败"; rm -f "$tarball"; return 1; }
    rm -f "$tarball"

    switch_current "$tag"
    ensure_venv   || { log "依赖安装失败"; return 1; }
    restart_service || { log "服务重启失败"; return 1; }
    health_check   || { log "健康检查失败"; return 1; }

    log "════ 部署 $tag 成功 ════"
    return 0
}

rollback() {
    local failed_tag="$1"
    local prev
    prev=$(ls -1t "$RELEASES_DIR" 2>/dev/null | grep -v "^${failed_tag}$" | head -1)
    if [ -n "$prev" ]; then
        log "回滚到上一版本: $prev"
        switch_current "$prev"
        ensure_venv || true
        restart_service || true
        if health_check; then
            log "✓ 回滚成功，已恢复到 $prev"
        else
            log "✗ 回滚后仍不健康，需人工介入"
        fi
    else
        log "无历史版本可回滚"
    fi
}

run_once() {
    read_state
    [ -n "$GITHUB_REPO" ] || die "未设置 GITHUB_REPO"

    local latest
    latest=$(fetch_latest_tag)
    if [ -z "$latest" ]; then
        log "获取 Release 失败（网络或仓库问题），跳过本轮"
        return 0
    fi

    if [ "$latest" = "$DEPLOYED_TAG" ]; then
        log "已是最新版本 $latest，无需操作"
        return 0
    fi

    if [ "$FAIL_COUNT" -ge "$MAX_FAILURES" ]; then
        log "已连续失败 $FAIL_COUNT 次，暂停自动部署，等待人工介入"
        return 1
    fi

    if deploy_tag "$latest"; then
        DEPLOYED_TAG="$latest"
        FAIL_COUNT=0
        write_state
        return 0
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        write_state
        rollback "$latest"
        return 1
    fi
}

# ---------- 入口 ----------
case "${1:-}" in
    --once)
        run_once
        ;;
    --version)
        read_state
        echo "已部署: ${DEPLOYED_TAG:-无}  连续失败: $FAIL_COUNT"
        ;;
    *)
        log "部署代理启动，轮询间隔 ${POLL_INTERVAL}s，仓库 ${GITHUB_REPO:-未配置}"
        while true; do
            run_once || log "本轮部署未成功（已记录）"
            sleep "$POLL_INTERVAL"
        done
        ;;
esac
