# AI-Native 数据平台（AI-Native Data Platform）

[English](README.md) · **简体中文**

面向治理的数据查询与分析平台：在受控数据源上用 SQL（以及自然语言）提问，只读安全、按数据集默认拒绝授权、异步可取消、每条答案都有可审计证据且结果可重放。本地单机部署，PostgreSQL / MySQL / Doris 三个引擎统一走一个查询网关，DataHub 负责目录、上下文与血缘。

## 状态

- **交付范围**：M0–M6 已交付。真实模型评测（A09）于 2026-09-27 通过 —— DeepSeek 上十个固定用例，目标格进入 Top-3 贡献者 **7/7**，无目标用例 **3/3** 未被强行归因，证据一致 **10/10**。
- **仍然开放**：规格里的 `/datasets/:id` 深链由目录详情面板承担；分析步骤的扩展目前是规则驱动，尚未实现由模型自主选择步骤与工具。
- 逐阶段清单与验收证据见 [docs/todo.md](docs/todo.md) 与 [docs/acceptance.md](docs/acceptance.md)。未执行的项一律标注 `not executed`，不计入通过。

## 它能做什么

- **查询**：只接受单语句 SELECT/UNION，经 AST 白名单、函数白名单与关联策略校验后进入网关；三个引擎各有 provider 实现，只读账号 + 只读会话 + 服务端超时 + 实时取消。
- **治理**：数据集必须显式注册并授权（`discover` / `query` 按数据集默认拒绝，管理员也不例外）；权限在检索、提交、执行、下载、流式读取五个环节复查，撤销授权会取消正在跑的查询。
- **证据与可重放**：提交、执行、取消、授权变更全部进审计；结果以 Arrow IPC + JSON 落盘，带内容哈希与签名游标，数字可从留存证据重算；结果 7 天 TTL。
- **确定性分析**：周期对比、贡献度分解（含可加性校验）、impression × eCPM 驱动拆分、受限的白名单跨源关联。指标定义是版本化 YAML，由闭合语法编译器编译成 SQL —— **算术永远由确定性内核算，模型不做计算**。
- **自然语言提问**：agent 循环（规划 → 经网关执行 → 观察 → 汇总）只把"选择哪些允许的维度"交给模型；模型不能提供工具、SQL、URL 或代码。未配置模型时明确降级为确定性模板路径并在分析状态里留下告警，绝不假装调用过模型。
- **目录与血缘**：DataHub（默认关闭，按需开启）提供检索、上下文与血缘；真实 `extracted` 边与声明式 demo 血缘分开标注。
- **界面**：React SPA —— SQL 工作台、数据目录、查询历史、Ask/Analysis（图表 + 等价的表格视图 + 下钻）、数据源与权限管理页。

## 快速开始（core profile）

前置：Docker Desktop（Windows 用 WSL2 后端，需为其预留约 6–8 GiB 内存）、[uv](https://docs.astral.sh/uv/)、Node.js 20+。WSL2/Linux 用 `make`，Windows PowerShell 用 `.\scripts\dev.ps1`（target 同名）。

```bash
make setup-secrets   # 生成 .env（随机口令）与 infra/local-secrets/ 下的源库凭据
make doctor          # 环境自检：Docker、CPU/内存/磁盘、端口、密钥文件
make up-core         # 启动核心栈：控制库 + 源库 + API + query worker + agent worker + 前端
make migrate         # Alembic 迁移（幂等）
make bootstrap       # 建角色、容量与管理员账号（密码只打印一次）
```

打开 <http://127.0.0.1:3000>；OpenAPI 文档在 <http://127.0.0.1:8000/api/v1/docs>。

## 全量画像：MySQL / Doris / DataHub / 演示数据

```bash
# 1. DataHub 栈（pinned v1.7.0.1；Windows PowerShell 需先 $env:HOME=$env:USERPROFILE）
docker compose --project-name datahub --env-file .env \
  -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml \
  --profile quickstart up -d

# 2. 业务栈（核心 + MySQL 8.4.11 + Doris FE/BE 3.1.4）并开启 DataHub 集成
AIND_DATAHUB_ENABLED=1 make up-full

# 3. 种子与演示数据
make seed-sources      # 三源种子夹具
make demo-generate     # 确定性演示数据（SEED / AS_OF / SCALE / SCENARIO）
make demo-load         # Doris Stream Load + 按血缘清单里的那份 SQL 建派生表
make demo-verify       # 离线核对：文件哈希、合计、贡献度、驱动拆分、场景规则
```

## 用真实模型（可选）

默认 `AIND_LLM_PROVIDER=fake`，全离线、只走确定性模板。接真实模型：

```bash
# 1. 把密钥粘进 infra/local-secrets/llm_api_key（一行，不加引号，替换占位符行）
# 2. 用一次结构化调用验证端点
make llm-check
# 3. 跑十个固定用例的 A09 评测
make eval-agent
```

`.env` 默认指向 DeepSeek（`https://api.deepseek.com/v1`，`deepseek-chat`）。`AIND_LLM_RESPONSE_FORMAT` 默认 `json_object`：这是可移植模式，JSON Schema 随提示词一起下发、回复再按同一份 Schema 本地校验；只有实现了严格结构化输出的厂商才需要改成 `json_schema`（DeepSeek 会对 `json_schema` 直接返回 HTTP 400）。密钥只从挂载文件读取，永不进入 `.env`、请求体或浏览器；端点、模型与密钥路径都是管理员配置，用户提问无法覆盖。

## 验证

```bash
# 静态套件（unit + security + contract，无需服务）
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q

# 集成套件（先停 compose 的 worker，让宿主机侧测试独占队列）
docker compose stop query-worker agent-worker && make test-integration && docker compose start query-worker agent-worker

# 三引擎全量矩阵（需 full profile）
make test-full

# 端到端冒烟（对运行中的栈跑一条真实查询）
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<管理员密码> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo

# 浏览器旅程
cd frontend && E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<管理员密码> npx playwright test
```

本仓库的规矩：**每条"支持"的声明都要有在本机执行过的命令与输出**，记录在 [docs/acceptance.md](docs/acceptance.md)；未执行的项标注为 `not executed`，从不计入通过。

## 端口与资源

宿主机上 5432 / 3306 / 8080 / 9200 / 9092 已被占用，因此本地做了端口重映射：控制库 **55430**、源库 **55433**、MySQL **33060**、Doris FE **19030**、DataHub GMS **18080**、DataHub UI **9002**。

core profile 是日常默认；full profile 额外需要约 12 GiB 镜像与 4–6 GiB 运行时内存，在 16 GiB 宿主机上建议分层启动（详见 [docs/compatibility.md](docs/compatibility.md)）。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 模块边界、查询生命周期、M3/M4 流程 |
| [docs/security.md](docs/security.md) | 安全模型与明确的非目标 |
| [docs/compatibility.md](docs/compatibility.md) | 版本 pin 与镜像摘要、已验证 vs 待验证、本机踩坑记录 |
| [docs/runbook.md](docs/runbook.md) | 启停、恢复、排障 |
| [docs/acceptance.md](docs/acceptance.md) | 验收矩阵（A01–A18，逐条挂命令与执行输出） |
| [docs/todo.md](docs/todo.md) | 里程牌清单：已完成项与仍开放项 |

更细的逐阶段交付说明（M0→M6 每一段新增了什么、各阶段的验收记录）在英文 [README.md](README.md) 与上面的 `docs/` 里。

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。第三方组件（PostgreSQL、MySQL、Doris、DataHub，以及 `backend/uv.lock` 与 `frontend/package-lock.json` 中 pin 的依赖）各自保留其许可证。
