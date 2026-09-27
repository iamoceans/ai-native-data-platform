# AI-Native 数据平台 · AI-Native Data Platform

[English](README.md) · **简体中文**

面向治理的数据查询与分析平台。分析师想从业务库里快速拿答案，但把 SQL 直连（更别说交给模型）既不安全也无法审计 —— 这个平台站在中间：用 SQL 和自然语言向受控数据源提问，全程只读安全、按数据集默认拒绝授权、异步可取消执行、每一步都有审计痕迹，并且结果的数字可以从留存的证据里重算。整套东西跑在一台机器上：PostgreSQL、MySQL、Doris 三个引擎在一个查询网关之后，DataHub 提供目录、上下文与血缘。

## 状态

- **已交付**：M0–M6。真实模型评测（A09）于 2026-09-27 通过 —— DeepSeek 上十个固定用例，目标格进入 Top-3 贡献者 **7/7**，无目标用例 **3/3** 未被强行归因，证据一致 **10/10**。
- **仍然开放**：规格里的 `/datasets/:id` 深链由目录详情面板承担，没有独立路由；分析步骤目前由规则扩展，尚未实现让模型自己选择步骤与工具。
- 逐阶段清单与执行过的证据在 [docs/todo.md](docs/todo.md) 与 [docs/acceptance.md](docs/acceptance.md)；未执行的项一律标注 `not executed`，从不计入通过。

## 它能做什么

- **查询**：只接受单语句 SELECT/UNION，进入网关前先过 AST 白名单、函数白名单与关联策略；每个引擎一套 provider，配只读账号、只读会话、服务端超时与实时取消。
- **治理**：数据集必须显式注册并授权（`discover`/`query`，按数据集默认拒绝，管理员也不例外）；权限在检索、提交、执行、下载、流式读取五个环节复查，撤销授权会取消正在跑的查询。
- **证据与可重放**：提交、执行、取消、授权变更全部进审计；结果以 Arrow IPC + JSON 落盘，带内容哈希与签名游标，数字可从留存证据重算。结果 7 天后过期。
- **确定性分析**：周期对比、带可加性校验的贡献度分解、对称的 impression × eCPM 驱动拆分，以及受限的白名单跨源关联。指标是版本化 YAML，由闭合语法编译器编译成 SQL —— 算术永远由确定性内核用 `Decimal` 完成，绝不由模型计算。
- **自然语言提问**：agent 循环（规划 → 经网关执行 → 观察 → 汇总）只把**一个**决策交给模型：拆解哪些允许的维度。模型永不提供工具、SQL、URL 或代码。未配置模型时平台降级为确定性模板路径并记录告警 —— 绝不假装调用过模型。
- **目录与血缘**：DataHub（默认关闭）提供检索、上下文与血缘；真实 `extracted` 边与声明式 demo 流水线血缘分开标注。
- **界面**：React SPA —— SQL 工作台、数据目录、查询历史、Ask/Analysis（图表、等价的表格视图、下钻），以及数据源与权限管理页。

## 快速开始（core profile）

前置：Docker Desktop（Windows 用 WSL2 后端）并为它预留约 6–8 GiB 内存、[uv](https://docs.astral.sh/uv/)、Node.js 20+。WSL2/Linux 用 `make`，Windows PowerShell 用 `.\scripts\dev.ps1`（target 同名）。

```bash
make setup-secrets   # 生成 .env（随机口令）与 infra/local-secrets/ 下的源库凭据
make doctor          # 环境自检：Docker、CPU/内存/磁盘、端口、密钥文件
make up-core         # 控制库 + 源库 + API + query worker + agent worker + 前端
make migrate         # Alembic 迁移（幂等）
make bootstrap       # 建角色、容量与首个管理员账号（密码只打印一次）
```

然后打开 <http://127.0.0.1:3000>；OpenAPI 文档在 <http://127.0.0.1:8000/api/v1/docs>。手动注册数据源并给角色授权（五个步骤）写在 [docs/runbook.md](docs/runbook.md) 的 2.2 节。

## 全量画像：MySQL / Doris / DataHub / 演示数据

```bash
# 1. DataHub 栈（pinned v1.7.0.1；Windows PowerShell 需先 $env:HOME=$env:USERPROFILE）
docker compose --project-name datahub --env-file .env \
  -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml \
  --profile quickstart up -d

# 2. 业务栈（核心 + MySQL 8.4.11 + Doris FE/BE 3.1.4）并开启 DataHub 集成
AIND_DATAHUB_ENABLED=1 make up-full

# 3. 种子与确定性演示数据
make seed-sources      # 三源连接器夹具
make demo-generate     # SEED / AS_OF / SCALE / SCENARIO
make demo-load         # Doris Stream Load + 按记录下来的转换 SQL 建派生表
make demo-verify       # 离线核对：文件哈希、合计、贡献度、驱动拆分、场景规则
```

生成器会写出 CSV + Parquet 表、带行数与文件哈希的 manifest、含精确转换 SQL 的声明式流水线血缘，以及只用于评测的 `ground_truth.json`；八个场景是 `ecpm_drop`、`traffic_drop`、`mixed_offset`、`no_change`、`incomplete_day`、`schema_drift`、`config_duplicate`，以及规格里那份精确的 `canonical_67` 样本（10,000 → 9,000；目标 -670；占比 0.67）。细节见 [docs/architecture.md](docs/architecture.md) 与 [docs/acceptance.md](docs/acceptance.md)。

## 用真实模型（可选）

默认 `AIND_LLM_PROVIDER=fake`，全离线、只走确定性模板。接真实模型：

```bash
# 1. 把密钥粘进 infra/local-secrets/llm_api_key（一行，替换占位符行）
# 2. 用一次结构化调用验证端点
make llm-check
# 3. 跑十个固定用例的 A09 评测
make eval-agent
```

`.env` 默认指向 DeepSeek（`https://api.deepseek.com/v1`，`deepseek-chat`）。`AIND_LLM_RESPONSE_FORMAT` 默认 `json_object`：可移植模式，JSON Schema 随提示词一起下发、回复再按同一份 Schema 本地校验。只有实现了严格结构化输出的厂商才需要改成 `json_schema` —— DeepSeek 会对它直接返回 HTTP 400。每次分析的预算是 60,000 输入 / 12,000 输出 token、20 次工具调用、12 次查询、2 次 SQL 修复与 180 秒墙钟；预算耗尽产生 `PARTIAL`，而不是编造答案。密钥只从挂载文件读取 —— 不来自 `.env`、请求字段或浏览器；端点、模型与密钥路径属于管理员配置，用户提问无法覆盖。

## 验证

```bash
# 静态套件（无需服务）：unit + security + contract
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q

# 集成套件（先停 compose 的 worker，让宿主机侧测试独占队列）
docker compose stop query-worker agent-worker && make test-integration && docker compose start query-worker agent-worker

# 三引擎全量矩阵（需 full profile 已启动）
make test-full

# 对运行中的栈做端到端冒烟：跑一条真实查询
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<管理员密码> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo

# 浏览器旅程
cd frontend && E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<管理员密码> npx playwright test
```

本仓库遵循的规矩：**每条"支持"的声明都要有命令与执行输出**，记录在 [docs/acceptance.md](docs/acceptance.md)；未执行的项标注 `not executed`，从不计入通过。

## 端口与资源

宿主机上 5432 / 3306 / 8080 / 9200 / 9092 已被占用，因此做了端口重映射：控制库 **55430**、源库 **55433**、MySQL **33060**、Doris FE **19030**、DataHub GMS **18080**、DataHub UI **9002**。状态保存在命名卷里，`make down` 只停容器、不删数据。

core profile 是日常默认。full profile 额外需要约 12 GiB 镜像与 4–6 GiB 运行时内存；16 GiB 宿主机上建议分层启动（见 [docs/compatibility.md](docs/compatibility.md) 的 WSL2 说明）。

## 仓库结构

```
backend/      FastAPI API + query/agent worker（同一镜像）、Alembic、测试
frontend/     React SPA、生成的 OpenAPI 类型、Playwright 旅程
demo/         确定性生成器、场景、装载器
metadata/     版本化契约：recipes/、semantic/、metrics/、relations/
ingestion/    DataHub ingestion runner 与声明式血缘发布器
infra/        versions.env、pinned DataHub compose、各引擎初始化脚本
scripts/      doctor、setup_secrets、smoke_core、verify_schema、demo_*、dev.ps1
docs/         architecture、security、compatibility、runbook、acceptance、todo
compose.yaml  core 与 full profile；compose.dev.yaml 负责发布 DB 端口
```

## 已知限制

- 分析器按规则扩展计划；让模型自己选步骤、在工具间迭代尚未实现。
- 规划回复在本地按闭合 schema 校验，校验不通过会让那一次分析直接失败并告警 —— 没有格式修复循环，只有 SQL 修复预算。
- 规格里的 `/datasets/:id` 深链由目录详情面板承担；shared/dashboard 屏幕不在 V1 规格内，也没有被发明出来。
- Doris 只支持本地单 BE 画像；多 BE 拓扑、workload group 与外部 catalog 属于 V1 范围外。
- DataHub 开启认证的模式未验证（pinned quickstart 关掉了 GMS 认证），profiling 在 recipes 里按策略禁用；列级血缘不在范围内。
- 仅本地 HTTP 部署，回环绑定是补偿控制（TLS 之后应设 `AIND_COOKIE_SECURE=true`）；登录限流与 SSE 轮询是单进程实现，多 API 副本需要先做共享存储与 fan-out。
- 敏感数据保护基于注册时的列名；按设计没有通用的行列改写引擎。
- 整套栈（DataHub + Doris + 应用）可能超出 16 GiB WSL2 虚拟机的内存上限，建议分层启动。

完整"未执行/未验证"清单在 [docs/acceptance.md](docs/acceptance.md) 的 "Skipped or unverified"，安全模型明确不做的事在 [docs/security.md](docs/security.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 模块边界、查询生命周期、M3/M4 流程、尚未构建的部分 |
| [docs/security.md](docs/security.md) | 安全模型与明确的非目标 |
| [docs/compatibility.md](docs/compatibility.md) | 版本 pin 与镜像摘要、已验证 vs 待验证、本机踩坑记录 |
| [docs/runbook.md](docs/runbook.md) | 启停、恢复、排障 |
| [docs/acceptance.md](docs/acceptance.md) | 验收矩阵（A01–A18），逐条挂命令与执行输出 |
| [docs/todo.md](docs/todo.md) | 里程牌清单：已完成项与仍开放项 |

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。第三方组件（PostgreSQL、MySQL、Doris、DataHub，以及 `backend/uv.lock` 与 `frontend/package-lock.json` 中 pin 的依赖）各自保留其许可证。
