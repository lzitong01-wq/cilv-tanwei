# 词律探微 · 清代词律声情智能研究助手

以冻结的清词声律数据为底座，构建"可复算的声律事实 + 可定位的文本证据 + 受约束的模型阐释"三位一体的研究助手。

**先读 [`项目一页纸.md`](./项目一页纸.md)**（五分钟速览），再按需进入规划。规划基线见 `词律探微-总体规划V6.1.md`；沿革与清理记录见 `CHANGELOG.md`；**定版结论见 [`app/reports/最终盘点V2.md`](./app/reports/最终盘点V2.md)**。

> **一句话记住产品的分寸：事实由引擎给，措辞由模型给，结论由人签字。**

---

## 0. 终版状态

**核心链路已闭环，代码侧可以定版。** 剩余项分三类——要外部数据的、要人工的、数据本身不支持的——都已如实标注，**没有一项靠估算或编数填上**。

| 项 | 值 |
|---|---|
| 语料 | **65,124 首**（Tier 1 官方 423 + 公开整理本 64,701：清 26,742 / 宋 27,281 / 历代词 10,678，五层分口径） |
| 实现规模 | **49 个 API 端点 · 前端 18 页 · 235 个测试**（21 个测试文件） |
| 公开 700 题 | 整段 453、归一化 566、代理 **700/700**、错误 0 |
| 保密 300 题（跨作品泛化） | 整段 188、归一化 242、代理 **300/300**、错误 0 |
| 修正表两级门禁 | 96.3% / 97.7%（均达标） |
| 体式消歧候选命中率 / 唯一消解率 | 86.5% / 6.6%（门槛 85%，达标） |
| 推理评测集 | 50/50（多跳、反事实、自洽、干扰项、缺数据各 10） |
| 确定性交互 p95 | 96.5 ms（门槛 200 ms） |
| 首屏体积 / WCAG 对比度 | 426 KB（不含字体 1.5 MB）/ 最低 5.25:1（门槛 4.5） |
| 检验 | 测试 251 通过（6 skip）· 冒烟 59 项全绿 · 逐页 18/18 · 真点击 7 项 · 契约 50-50-44 一致 · 不变量 13 条（claimed 12 / pending 1） |

**六条不能碰的红线**（对应 `docs/adr/0001–0008`）：模型不参与声律事实 · 三层语料不混算 · 未知一律拦截 · 运行时零外网 · 不做情感识别（只给候选方向）· 校订闭环止于导出。

---

## 1. 终版文件集

```
项目一页纸.md                     五分钟速览：定位、九个数字、六条红线、三个坑、下一步
词律探微-总体规划V6.1.md          唯一实施基准（定位 / 决策 / 不变量 / 里程碑 / 风险 / 口径）
分册A-声律引擎与词谱对齐.md    逐字平仄与四声、自注字音、多音字修正表、词谱体式消歧、词调身份层
分册B-交互板块与副功能.md      十板块制、检索、风格画像与对比、情感候选与 AI 标注基准集、校订闭环
分册C-数据管线与授权.md        四层语料、构建期管线、授权清单、API 契约、RAG 偏离决策
分册D-评测交付与演示.md        三重指标、不变量认领、负向对照、holdout、七类交付物、演示脚本
榜题对账表V1.md                榜题原文逐条比对与五处缺口处理
知识库API使用约定V1.md        官方 API 契约、刷新与限流退避、错误分类、端点分离
GLOSSARY.md                    统一术语表
前端计划V1.md                  前端计划（视觉方向、信息架构、设计系统、情感与交互、技术栈）
前后端一致性与接口契约V1.md    15 处冲突裁决、单机部署形态、49 个内部 API 端点
CHANGELOG.md                   沿革、终版文件集、被清理项、复现入口
README.md                      本文件
CONTRIBUTING.md                贡献指南（口径纪律、验收清单、LFS 规则）
LICENSE                        MIT 协议

docs/
  adr/0001–0008               八条不可逆决策（0008 判分规则版本化外置）
  报告/                        七份前序研究报告（评测、模型复测、审计、Tier2 验证等）
  词律探微_研究成果与设计关系总览.html   前序研究导读（工作态）

app/
  README.md                   实现侧总览：功能矩阵、目录树、验证结果、部署要点
  docs/                       使用教程（研究者/教学/库建设三类使用者）+ 情感标注规范
  reports/                    实现期实跑报告 7 份（含「最终盘点 V2」= 定版结论）
  deploy/                     部署文档 + CI/CD 说明 + systemd 单元 + 离线 wheels

tools/
  benchmark_engine.py          五类确定性求解 + 字段断言 + 证据链 + 整段渲染（评测主线）
  knowledge_audit.py           知识库快照抓取与全量审计
  model_recheck.py             双模型结构化复测
  llm_sidecar_probe.py         LLM 副功能基线
  c4_quote_llm_probe.py        C4 引文选择的 LLM 对照探针
  tier2_crosscheck_probe.py    Tier 2 同源与拼音口径比对
  polyphone_feasibility_probe.py  多音字修正表可行性上界

app/                             实现产物（竖切片：后端 + 前端，见 app/README.md）
data/
  知识库审计快照V1.jsonl       423 篇冻结快照（评测复现的唯一 Tier 1 输入）
  results/                     公开逐题基线与各探针原始结果
shots/                          逐页检查与界面截图（证据）
词律探微-交付包-YYYYMMDD.tar.gz  交付包（app/ + data/ + docs/ + tools/ + 顶层规范文档）

.workbuddy/reference/tier2/    构建期第三方语料输入（可重新下载，不随交付物分发）
```

---

## 2. 三条阅读路径

| 读者 | 顺序 | 目标 |
|---|---|---|
| 评审 / 接手 | V6.1 总纲 → 分册 D → 榜题对账表 | 5 分钟内知道产品做什么、怎么验收、边界在哪 |
| 开发 | 分册 A → 分册 C → 分册 B → 分册 D | 按里程碑 M0→M6 实施 |
| 数据方 / 赛方沟通 | 榜题对账表 §3 → 分册 C §C1.1、§C9 → 知识库 API 使用约定 V1 | 申请项、契约、边界 |

---

## 3. 快速复现

```bash
# 公开 700 题基线（评测主线，秒级）
python tools/benchmark_engine.py \
  --works data/知识库审计快照V1.jsonl \
  --questions "<题库目录>/公开测试集_700题.jsonl" \
  --out data/results/公开700题确定性基线与逐题结果V1.json

# 知识库快照抓取与审计（需 QINGCI_KNOWLEDGE_API_KEY）
python tools/knowledge_audit.py --questions-dir "<题库目录>" \
  --snapshot data/知识库审计快照V1.jsonl --out data/results/知识库与题库审计结果.json

# 分析型探针（需 pypinyin；运行时不依赖）
"C:/Python314/python.exe" tools/polyphone_feasibility_probe.py
"C:/Python314/python.exe" tools/tier2_crosscheck_probe.py 清 .workbuddy/reference/tier2/qing
```

环境：Python 3.11+；`pypinyin` 仅用于分析型探针。所有凭据只从环境变量读取（`QINGCI_KNOWLEDGE_API_KEY` / `SPARK_MODEL_KEY`）。

---

## 4. 关键事实速查

| 事实 | 数值 |
|---|---|
| 官方语料 | 423 篇（清 200 / 宋 203 / 元 20），243 位词人，100 个词牌 |
| 公开整理本 | 64,701 首（poetry-source 九朝代 53,869 + chinese-poetry《全宋词》去重新增 10,832），3,593 位词人，**2,491 个词牌**（归一后） |
| 题库 | 1000 题 = 五类各 200；普通 800 / 临界 200 / 超界 0（定义保留）；锚点 2,023 个全部唯一命中 |
| 确定性求解 | 字段 10,446/10,446；证据 1,000/1,000；C1–C3 整段 600/600；C4 归一化 200/200；C5 代理字段 200/200；错误 0 |
| 跨作品泛化 | 公开 303 篇与保密 120 篇零重叠、并集 423 → 保密 300 题实为跨作品验证 |
| 口径一致性 | pypinyin 首读 90.79%；放开上下文改读音上界 99.16%，27 句数学无解 |
| 同源关系 | 官方 200 篇清词与 poetry-source 逐字一致 200/200；宋词非同一版本 |
| 词调身份层 | 官方 100 词调 / 237 体式；公开整理本 2,491 词调（归一后） |
| 模型 | qwen 结构化 6/6、均 2.64s；flash 结构化不合规、均 14.6s；两模型都会事实错误 |
| 评分口径 | 评测集 60%（参考题库、同构）+ 体验 40% |
| 知识库 API | 只读（405 不支持写入）、不返回赛题答案与语义结论、限流 120 次/分钟、与模型端点分离 |
| 关键约束 | 事实由引擎给、候选由模型给、结论由人签字（ADR-0001） |

> **同一个词牌在不同层可能对应不同体式**，检索与统计都按层分开，不混算（ADR-0002）。

---

## 5. 交付边界

- 规划、代码与实测数据均为本次竞赛产出；`docs/报告/` 的数字均可按 §3 复现；
- 第三方语料与词谱**不随交付物分发**，仅在构建期使用，授权状态见分册 C §C4；
- 保密题金标不复制进本工作区；保密验证用同一命令对题库目录原地复跑；
- 任何对外表述遵循 V6 §11 的"可说 / 不可说"清单；
- 校订闭环止于幂等导出，写回由官方流程完成（ADR-0007）。

---

## 6. 部署与 CI/CD

本仓库带一套 GitHub → 服务器的自动部署，配置见 [`app/deploy/CICD说明.md`](./app/deploy/CICD说明.md)。

```
scripts/release.sh          # 本机执行：构建交付包 → 发到 GitHub Release
          ↓
GitHub Release（资产即交付包 tar.gz）
          ↓ 服务器部署代理轮询发现（默认 300 秒）
分片下载 → 解压 → 切软链 → 重启 → 健康检查 /api/health
          ↓ 失败自动回滚上一版
```

| 文件 | 作用 |
|---|---|
| `scripts/release.sh` | 发版脚本：构建前端 → 打包 → 建 Release → 上传资产 → 校验可下载 |
| `app/deploy/deploy_agent.sh` | 服务器端部署代理（轮询 / 分片下载 / 切换 / 健康检查 / 回滚） |
| `app/deploy/install_cicd.sh` | 首次安装：账号、目录、systemd、防火墙、验收 |
| `app/deploy/cilv.service` | 应用服务单元 |
| `app/deploy/cilv-deploy-agent.service` | 部署代理单元 |

**部署方式是「服务器主动从 GitHub 拉」，不是「CI 推包到服务器」**——实测本机到服务器的上行只有约 17 KB/s，而服务器出网约 35 KB/s，且单次 HTTP 下载超过约 3.9 MB 会截断（两端同一出口 NAT）。`deploy_agent.sh` 因此用 2 MB 分片 + Range 请求 + 大小校验。

> **为什么用本机脚本发版而不是 GitHub Actions**：本机 GitHub 凭据是 OAuth App token
> （scopes: `repo`、`delete_repo`、`admin:org` 等），**缺 `workflow` scope**，
> GitHub 会拒绝任何写入 `.github/workflows/` 的操作（git push 与 REST API 都被拦：
> `refusing to allow an OAuth App to create or update workflow … without workflow scope`）。
> 发版因此改由 `scripts/release.sh` 调 Release API 完成，链路效果相同。
> 仓库里保留了 `.github/workflows/build-release.yml`，将来换成带 `workflow` scope 的
> Personal Access Token 后即可切回 Actions。

**大文件（`app/data/*.sqlite3`）走 Git LFS**——`tier2_locator.sqlite3` 约 165 MB，超过 GitHub 单文件 100 MB 硬限制。

服务器侧环境要点：Anolis OS 8.6、Python 3.11、系统 SQLite 3.26.0（不支持 FTS5 trigram，由 `core/sqlite_compat.py` 自动切 `pysqlite3`）。

手工部署（不走 CI）见 [`app/deploy/交付与部署说明V1.md`](./app/deploy/交付与部署说明V1.md) 与 [`app/deploy/README.md`](./app/deploy/README.md)；离线依赖在 `app/deploy/wheels/linux-x86_64-py311/`。

---

## 7. 已知边界

如实列出，不粉饰（完整版见 `app/reports/最终盘点V2.md` §3）：

| 项 | 现状 | 能不能补 |
|---|---|---|
| **自注字音线索** | Tier 1 快照 423 条里没有自注字段；`chars.source` 只有 `local_dict` 一值 | 不能。需数据方导出自注读音；读音来源优先级链已实现，接口位留好 |
| **官方史证数据** | 官方接口 `total=0`，图谱关系位保留不填充 | 不能。等官方发布 |
| **体式消歧人工抽样一致率** | 门槛 ≥90%（抽 ≥30 篇），**未做**——无人工核验资源 | 不用模型复核冒充人工，如实留空 |
| **情感基准集人工抽检** | 规范建议 ≥10%，**未做**，同上 | 同上 |
| 韵位匹配 / 用字重合度 | 词谱 `sketch` 只给韵数与平仄，**不含韵部名与用字字面** | 数据不支持，非未做 |

**明确不该再做的**：追 C5 整段逐字、把"无法判定"硬填成答案、为凑门禁估数、扩到多用户/写回/向量库、领域微调。

---

## 8. 许可

代码采用 [MIT 协议](./LICENSE)。

数据与字体各有独立授权，随仓库分发的部分已在 `app/deploy/交付与部署说明V1.md` 与分册 C §C4 登记：思源宋体子集为 SIL OFL 1.1；题库与词谱原始文件**不入库**，仅作构建期输入。

