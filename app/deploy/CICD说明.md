# CI/CD：GitHub → 龙蜥服务器自动部署

本目录是自动部署的配置与脚本。**代码已上传到 GitHub 前，先看本文档确认方案与本机实测约束一致。**

---

## 0. 结论先行：可行，但走「服务器主动拉取」而非「CI 推送包」

2026-11-04 在目标服务器实测（数据来自真实执行，非估算）：

| 链路 | 实测结果 | 影响 |
|---|---|---|
| 本机 → 服务器（scp / ssh 管道） | **约 17 KB/s**；传 2 MB 会截断成 1.3 M；20 MB 分块传输 11 块耗时 32 分钟 | **不能**用「CI 构建完推包到服务器」 |
| 服务器 → github.com | HTTP 200，1.6 秒 | 可用 |
| 服务器 → api.github.com | 200，未认证配额 60/小时 | 可用，配 token 可到 5000/小时 |
| 服务器 → git clone | 浅克隆公开仓库成功 | 可用 |
| 服务器 → pypi.org | 200 | 可用 |
| 服务器 → GitHub Release 资产（单连接） | **约 35 KB/s**；4.85 MB 用了 119 秒 | 可用 |
| 服务器 → GitHub Release 资产（**8 路并行**） | **21 KB/s，比单连接还慢** | **瓶颈是总出口带宽，不是单连接** |
| 服务器 → 大文件 | 90–120 秒超时，**截断在约 3.9 MB** | 必须断点续传 |

三个关键推论：

1. **本机与服务器走同一个出口 NAT**。本机下载 5 MB 截断在 3.9 M，服务器下载 4.85 MB 同样截断在 3.9 M——是同一条带宽整形策略，不是巧合。因此**任何单次下载超过约 3.9 MB 都必须带断点续传**。
2. **服务器主动出网拉取比本机推包快一倍**（35 KB/s vs 17 KB/s），且不占用本机上行。
3. **并行下载无效**——8 路并发聚合带宽反而低于单路，说明是出口总带宽受限。这条实测直接否掉了「用并发提速」的思路。

所以整条链路设计为：**本机 push → GitHub → Actions 构建并发布 Release → 服务器轮询 Release 资产 → 本地解压切换 → 健康检查**。
服务器主动出网拉取，绕开本机上行带宽瓶颈。

> `deploy_agent.sh` 的 `download_asset()` 已用「2 MB 分片 + Range 请求 + 每片重试 + 合并后大小校验」，
> 专门对付上面那个「3.9 MB 截断」。实测 4.85 MB 文件分 3 片下载后 tar 校验通过，比单次下载更快且成功。

### 0.1 但带宽是硬约束，必须正视耗时

当前交付包 **89.1 MB**（含 164 MB 解压后的 tier2 索引，gzip 后大幅压缩）。按实测速率：

| 路径 | 预计耗时 |
|---|---|
| 服务器从 GitHub 拉 Release（推荐） | **约 43 分钟** |
| 本机推包到服务器 | 约 89 分钟 |

**这不是方案不可行，而是「每次部署都要等 40+ 分钟」。** 缓解手段（按代价排序）：

1. **增量部署**：代码与数据分离。代码包小（几 MB，分钟级），大索引只在版本变更时传一次。
2. **索引瘦身**：`tier2_locator.sqlite3` 从 30.5 MB 涨到 164 MB（加 Tier 2b 宋词 + 紧凑倒排 + FTS5 trigram），
   其中 `works_fts_data` + `works_fts_content` 约 44 MB 只服务于「≥3 字检索」这一条路径。
   若评审不需要这个性能，可退回 30.5 MB 版本（CHANGELOG §「紧凑倒排」记录了当时的取舍理由）。
3. **首次投递用分块传输**（`~/.ssh/split_transfer.sh`），后续走 CI。

### 0.2 服务器环境实测（Anolis OS 8.6）

| 项 | 值 | 说明 |
|---|---|---|
| 系统 | Anolis OS 8.6 | 内核 `4.19.91-26.an8.x86_64` |
| Python | 3.6.8（默认）→ **已装 3.11.13** | `dnf install python3.11 python3.11-pip`（PowerTools 源有） |
| SQLite | **3.26.0** | ⚠️ 不支持 FTS5 trigram（3.34+ 才有），见下 |
| git | 2.43.7（已装） | Anolis 源正常 |
| Node / Docker | 无 | 前端必须在 CI 或本机构建后随包带走 |
| firewalld | active | 需 `--add-port=8080/tcp` |
| SELinux | Disabled | 无需 semanage |
| 磁盘 | 70 G（可用 62 G）/ /home 126 G | 足够 |

**SQLite 版本问题已解决**：系统 SQLite 3.26.0 无 trigram 分词器，而 `index.sqlite3` 与
`tier2_locator.sqlite3` 都含 `tokenize='trigram'` 的 FTS5 表，直接在 3.26.0 上打开会报
`no such tokenizer: trigram`。Anolis 源里没有更新版 `sqlite-libs`。

解法：`core/sqlite_compat.py` 检测到系统 SQLite 不支持 trigram 时，自动改用
`pysqlite3-binary`（PyPI 上的预编译轮子，自带 SQLite 3.51.1）。**实测在服务器上生效**：

```json
"sqlite": "pysqlite3 3.51.1",
"sqlite_trigram": true
```

装不上 `pysqlite3-binary` 时也不阻断部署——应用层自动退回内存子串匹配，功能可用只是慢，
并在响应里如实标 `index: "memory-fallback"`。详见 `app/backend/requirements-optional.txt` 的说明。

> `pysqlite3-binary` **只发布 Linux wheel**，所以它是**可选依赖**（`requirements-optional.txt`），
> 不是必装项——放进 `requirements.txt` 会让 Windows 的离线安装直接失败。

### 0.3 打包完整性校验（新增，防回归）

2026-11-04 实测踩到：某次打包漏了 `app/backend/core/corpus.py`，服务器上应用直接起不来
（`from core import corpus` → ImportError），而打包脚本当时**没有任何告警**。

`build_package.py` 现在在打包后做一道校验：源码目录（`app/backend`、`app/scripts`）里
所有该进包的 `.py` 是否都在包内，缺一个就**中止打包并列出缺失文件**。

---

## 1. 组件清单

| 文件 | 位置 | 作用 |
|---|---|---|
| 工作流 | `.github/workflows/build-release.yml` | 测试 → 构建前端 → 打包 → 发 Release |
| 部署代理 | `app/deploy/deploy_agent.sh` | 服务器端轮询、拉取、切换、健康检查、回滚 |
| 服务单元 | `app/deploy/cilv.service` | 应用本体（systemd） |
| 代理单元 | `app/deploy/cilv-deploy-agent.service` | 部署代理（systemd，常驻轮询） |

---

## 2. 首次部署（手工，一次性）

### 2.1 服务器准备

```bash
# 专用账号
useradd -r -s /sbin/nologin cilv
useradd -r -s /sbin/nologin cilv-deploy

# 目录
install -d -o cilv -g cilv /opt/cilv/releases
install -d -o root -g root /opt/cilv/bin
install -d -o root -g root /etc/cilv

# Python 3.11（Anolis 8 默认 3.6，不够）
dnf install -y python3.11 python3.11-pip
```

### 2.2 首次投递产物（走分块传输，绕过带宽限制）

本地：

```bash
python app/scripts/build_package.py
bash ~/.ssh/split_transfer.sh 词律探微-交付包-<日期>.tar.gz /opt/cilv/releases/manual.tar.gz 2
```

> 实测：单次 scp 会截断；`split_transfer.sh` 按 2 MB 分块 + 失败重试 + 远端合并后校验大小，可靠。

服务器：

```bash
cd /opt/cilv/releases
mkdir -p manual && tar -xzf manual.tar.gz -C manual --strip-components=1
ln -sfn /opt/cilv/releases/manual /opt/cilv/current

# 装依赖（离线 wheels 已在包内）
cd /opt/cilv/current/app
python3.11 -m venv .venv
.venv/bin/pip install --no-index --find-links deploy/wheels/linux-x86_64-py311 \
    -r backend/requirements.txt
```

### 2.3 装 systemd 单元

```bash
cp app/deploy/cilv.service /etc/systemd/system/
cp app/deploy/cilv-deploy-agent.service /etc/systemd/system/
install -m 0755 app/deploy/deploy_agent.sh /opt/cilv/bin/deploy-agent.sh

cat > /etc/cilv/deploy.env <<'EOF'
GITHUB_REPO=<你的账号>/<仓库名>
POLL_INTERVAL=300
DEPLOY_ROOT=/opt/cilv
SERVICE_NAME=cilv
EOF
chown root:root /etc/cilv/deploy.env && chmod 600 /etc/cilv/deploy.env

systemctl daemon-reload
systemctl enable --now cilv
systemctl enable --now cilv-deploy-agent
```

### 2.4 开防火墙端口

```bash
firewall-cmd --permanent --add-port=8080/tcp && firewall-cmd --reload
```

### 2.5 验收

```bash
curl -s http://127.0.0.1:8080/api/health | python3.11 -m json.tool
curl -sI http://127.0.0.1:8080/fonts/NotoSerifSC-Regular.subset.woff2 | grep -i content-type
```

---

## 3. 日常使用

```bash
# 本机
git add -A && git commit -m "..." && git push origin main
```

推上去之后：

1. Actions 自动跑测试 → 构建前端 → 打包 → 发 Release；
2. 服务器上的 `cilv-deploy-agent` 在 `POLL_INTERVAL` 内发现新 Release；
3. 拉取 → 解压到 `releases/<tag>/` → 软链 `current` 切换 → 重启服务 → 查 `/api/health`；
4. **健康检查不通过自动回滚到上一版本**；连续失败 3 次后停止重试，等人工介入。

查看状态：

```bash
systemctl status cilv-deploy-agent
/opt/cilv/bin/deploy-agent.sh --version    # 已部署版本 + 连续失败次数
journalctl -u cilv-deploy-agent -f
```

---

## 4. 设计上的几个取舍

| 取舍 | 理由 |
|---|---|
| 服务器轮询而非 webhook 回调 | 服务器出网稳定；webhook 需要 GitHub 能反向连到服务器，而服务器在 NAT 后且只走 Tailscale |
| 版本目录 + 软链，而非原地覆盖 | 回滚要快；覆盖式部署失败后没有干净的旧版本可退 |
| 健康检查看 `app=ok` 而非「进程在」 | 进程活着但快照缺失、dist 不全的情况必须判为失败 |
| 失败 3 次后停手 | 避免配置错误时无限重试刷日志，也避免网络抖动引发连续回滚 |
| 构建在 Actions 做，部署在服务器做 | Actions  runner 出网快（构建前端要 npm ci），服务器只管下载和切换 |

---

## 5. 已知限制（如实记录）

- **GitHub API 未认证配额 60/小时**：服务器轮询本身用不到多少，但若加多个仓库要留意。配 `GITHUB_TOKEN`（Release 读取权限即可）可提到 5000/小时。
- **Release 资产大于约 4 MB 时下载会截断**：`deploy_agent.sh` 用 2 MB 分片 + Range 请求 + 断点续传兜底。当前包 88 MB，服务器出网实测约 35 KB/s，约需 20–40 分钟。**如果演示时间紧，建议首次投递用分块传输（`~/.ssh/split_transfer.sh`），之后走自动部署。**
- **服务器到 `github.com` 域名可能连不上**：实测 `github.com` 超时（000）但 `api.github.com` 通（200）。资产实际托管在 `release-assets.githubusercontent.com`，而 `browser_download_url` 必须先连 `github.com` 才能跳转过去 → **部署代理改用 API 端点**（`https://api.github.com/repos/{o}/{r}/releases/assets/{id}` + `Accept: application/octet-stream`），实测返回 206 + 精确字节数。
- **连续失败 3 次会停止自动部署**：`MAX_FAILURES` 保护，避免网络抖动时无限重试刷日志。恢复方法：
  ```bash
  ssh 龙蜥 'printf "DEPLOYED_TAG=<当前版本>\nFAIL_COUNT=0\n" > /opt/cilv/.deploy-state'
  ```
  **注意**：如果网络故障导致计数满了，代理会「装作没事」——它不部署了，但也不报错。看到服务器上的版本没更新，先查 `/opt/cilv/.deploy-state` 里的 `FAIL_COUNT`。
- **服务器没有 Node.js**：前端必须在 CI 或本机构建后随包带走（`app/deploy/README.md` §3 已有说明）。若要服务器端自建前端，需 `dnf install nodejs`，但 Anolis 源里的 Node 版本通常偏旧。
- **`tier2_locator.sqlite3` 占 165 MB**（解压后是交付包里最大的一块）：它是构建期产物，已随包分发，不在 CI 里重新生成。若要减小包体，需改成分库下载或用 `sqlite3 .dbconfig` 压缩。
- **服务器端 `GITHUB_TOKEN` 是另一回事**：Actions 内置的 `GITHUB_TOKEN` 与它无关；服务器读公开仓库不需要凭据，配 token 只是为了提高 API 配额。

---

## 5.1 首次部署实跑暴露的 8 个真问题（都已修并提交）

按发现顺序：

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 解压后 `app/` 变成 `backend/`，结构检查失败 → **部署静默失败**，软链不切换 | `tar --strip-components=1` 把交付包顶层剥掉了 | 不加该参数；同时校验 `app/` 与 `data/` 存在 |
| 2 | `Failed to restart cilv.service: Interactive authentication required` | 代理以 `cilv-deploy` 运行，polkit 要求交互认证 | 改用 `sudo -n systemctl` + `/etc/sudoers.d/cilv-deploy` 白名单 |
| 3 | `sudo: 需要密码`（改了脚本仍失败） | **sudo 按完整路径匹配命令**，`systemctl` 在本机是 `/bin/systemctl` 与 `/usr/bin/systemctl` 两个名字，只写一个不匹配 | 两条路径都写进 sudoers |
| 4 | 下载 82 MB 后整包消失，下轮从零重下 | 大小校验失败时 `rm -rf "$workdir"` 删掉分片 | 校验失败时**保留分片**供断点续传 |
| 5 | Release 上传成功但脚本报「下载校验失败」 | Git Bash 的 `/tmp` 路径映射让 python 读不到 curl 的输出文件，`browser_download_url` 解析为空 | 临时文件改放工作区 `.tmp/`；下载地址解析加两层兜底 |
| 6 | 代理发现新 Release 但下载一直失败、刷进度条 | 服务器连不上 `github.com`（000 超时），探测失败后走了「单次下载」回退分支 | 改用 API 端点（`api.github.com`）+ `Accept: application/octet-stream` |
| 7 | **代理静默跳过，日志无报错，版本不更新** | **GitHub API 配额耗尽**（未认证 60 次/小时，代理 5 分钟轮询一次） | `/etc/cilv/deploy.env` 配 `GITHUB_TOKEN`（配额 5000/小时）；env 文件权限改 `root:cilv-deploy 640`（代理要读 token） |
| 8 | 解压与切软链静默失败（root 手动做都对） | **`cilv-deploy` 无权写 `releases/` 与 `/opt/cilv/`** | `chown cilv:cilv-deploy releases` + `chmod 775`、`chown root:cilv-deploy /opt/cilv`；另外给 `cilv-deploy` 建可写的 HOME |

另外：`install_cicd.sh` 原本用 `git clone` 拉交付物，但交付包是**构建产物**不是源码树，
已改为从 GitHub Release 下载（与 `deploy_agent.sh` 路径一致），并补上 sudoers 与权限配置。

### 部署「看起来没发生」时按这个顺序查

```bash
ssh 龙蜥 'cat /opt/cilv/.deploy-state'                    # 1. FAIL_COUNT 非 0 = 已停手
ssh 龙蜥 'journalctl -u cilv-deploy-agent -n 20 -o cat'   # 2. 有没有「软链已切到」
ssh 龙蜥 'ls -ld /opt/cilv/releases /opt/cilv'            # 3. cilv-deploy 有没有写权限
ssh 龙蜥 'grep GITHUB_TOKEN /etc/cilv/deploy.env'         # 4. token 配了没
```


---

## 5.2 为什么用本机脚本发版而不是 GitHub Actions

本机 GitHub 凭据是 **OAuth App token**（scopes: `repo`、`delete_repo`、`admin:org` 等），
**缺 `workflow` scope**。GitHub 会拒绝任何写入 `.github/workflows/` 的操作：

```
refusing to allow an OAuth App to create or update workflow
`.github/workflows/build-release.yml` without `workflow` scope
```

git push 被拒、REST API 返回 404、推独立分支也被拒（**仓库级硬限制**），
且 OAuth App 的 scope 在授权时确定、事后无法追加。

因此发版改由本机 `scripts/release.sh` 调 Release API 完成，**链路效果相同**：

```
scripts/release.sh  →  GitHub Release（资产即交付包 tar.gz）
                    →  服务器代理轮询发现 → 分片下载 → 切软链 → 重启 → 健康检查
```

`.github/workflows/build-release.yml` 保留在仓库外的本地备份
（`~/.ssh/ci-backup/build-release.yml`），将来换成带 `workflow` scope 的
Personal Access Token 后即可切回 Actions。

**Release 资产名必须纯 ASCII**——中文名会被 GitHub 判 `400 Bad request`（返回的是 HTML 错误页，不是 JSON）。

