# Oceanus

[English](README.md) · **简体中文**

Oceanus 是一个面向治理的数据查询与分析平台。分析师想从业务库里快速拿答案，但把 SQL 直连（更别说交给模型）既不安全也无法审计 —— 这个平台站在中间：用 SQL 和自然语言向受控数据源提问，全程只读安全、按数据集默认拒绝授权、异步可取消执行、每一步都有审计痕迹，并且结果的数字可以从留存的证据里重算。整套东西跑在一台机器上：PostgreSQL、MySQL、Doris 三个引擎在一个查询网关之后，DataHub 提供目录、上下文与血缘。

## 状态

- **Spark/Hive 预览**：已接入共用 Hive Metastore 的 Spark Thrift 查询适配器和 DataHub HMS 元数据配方；连接器路径、取消与 120 秒超时路径已于 2026-09-28/29 在真实的 Spark/Hive 服务上验收（注册、目录刷新、授权、查询、单一 Hive URN 映射、取消记为 `CANCELLED`、超时记为 `TIMED_OUT`）；Spark 作业血缘、只读源授权与浏览器 E2E 尚待验收。见 [操作手册](docs/runbook.md#spark--hive-shared-metastore-preview)。
- **已交付**：M0–M6，以及 2026-10-13 的对话式 Ask 与技能包（真实模型多轮对话 + 全量回归验收，见 [docs/acceptance.md](docs/acceptance.md)）。真实模型评测（A09）于 2026-09-27 通过 —— DeepSeek 上十个固定用例，目标格进入 Top-3 贡献者 **7/7**，无目标用例 **3/3** 未被强行归因，证据一致 **10/10**。
- **新增功能**：封闭动作范围内的模型步骤选择、结构化输出修复、证据解释模板、团队／个人业务记忆与本地域语义检索，以及 Top-10 图表展开。当前版本的真实 DeepSeek／A09 验收仍按用户要求暂缓。
- 逐阶段清单与执行过的证据在 [docs/todo.md](docs/todo.md) 与 [docs/acceptance.md](docs/acceptance.md)；未执行的项一律标注 `not executed`，从不计入通过。

## 它能做什么

- **查询**：只接受单语句 SELECT/UNION，进入网关前先过 AST 白名单、函数白名单与关联策略；每个引擎一套 provider，配只读账号、只读会话、服务端超时与实时取消。
- **治理**：数据集必须显式注册并授权（`discover`/`query`，按数据集默认拒绝，管理员也不例外）；权限在检索、提交、执行、下载、流式读取五个环节复查，撤销授权会取消正在跑的查询。
- **证据与可重放**：提交、执行、取消、授权变更全部进审计；结果以 Arrow IPC + JSON 落盘，带内容哈希与签名游标，数字可从留存证据重算。结果 7 天后过期。
- **确定性分析**：周期对比、带可加性校验的贡献度分解、对称的 impression × eCPM 驱动拆分，以及受限的白名单跨源关联。指标是版本化 YAML，由闭合语法编译器编译成 SQL —— 算术永远由确定性内核用 `Decimal` 完成，绝不由模型计算。
- **自然语言提问**：agent 循环（规划 → 经网关执行 → 观察 → 汇总）允许模型选择对比／维度拆解，再根据观测结果选择结束或调用已声明的因子分解动作。模型不能提供任意工具、SQL、URL 或代码。未配置模型时平台降级为确定性模板路径并记录告警 —— 绝不假装调用过模型。
- **对话与技能**：Ask 是持久化的对话工作区。会话保存已确认的分析范围（指标、区间、维度、完整性），后续提问直接继承；缺什么字段模型就问什么，不用页面默认值猜；解释类问题读取既有分析的证据，不发起新查询。技能包（`SKILL.md` 加参考文件，支持文本或 ZIP 上传，另有 `metadata/skills/` 下的内置技能库）按版本不可变保存并记录内容哈希，按轮次以 `off`/`manual`/`auto` 模式加载；技能只是建议性文本，它给出的任何结构化提议仍要通过与模型输出相同的全部关卡。该模块由 `AIND_CONVERSATIONAL_ASK_ENABLED` 开关控制（默认关闭）。
- **目录与血缘**：DataHub（默认关闭）提供检索、上下文与血缘；真实 `extracted` 边与声明式 demo 流水线血缘分开标注。数据目录可按数据源、库（schema/namespace）与业务域筛选：业务域来自 `metadata/semantic/*.yaml` 的声明，筛选选项带数量，没有声明的表有独立选项。
- **界面**：React SPA —— SQL 工作台、数据目录、查询历史、Ask/Analysis（图表、等价的表格视图、下钻），以及数据源与权限管理页。

## 界面语言

登录页和工作区顶栏可选择 **简体中文 / English**，即时切换整个平台界面，
保留表单草稿和正在运行的查询。首次进入时按浏览器首选语言选择（`zh-*` 使用中文，
其余使用英文）；手动选择保存在 `localStorage`，刷新后保留，并在标签页之间同步。
日期和图表数字随所选语言格式化。

页面文案、状态、校验和 API 错误说明均已适配。SQL、查询结果、目录描述、用户内容、
模型生成文本和已存储的报告／证据引文保留原文，不因切换语言被翻译或重新生成。
错误旁的「原始错误」按钮可展开服务端诊断详情。
验收记录见 [docs/acceptance.md](docs/acceptance.md#2026-10-15--chineseenglish-interface-localization)。

## 快速开始（core profile）

前置：Docker Desktop（Windows 用 WSL2 后端）并为它预留约 6–8 GiB 内存、[uv](https://docs.astral.sh/uv/)、Node.js 20+。WSL2/Linux 用 `make`，Windows PowerShell 用 `.\scripts\dev.ps1`（target 同名）。

```bash
make setup-secrets   # 生成 .env（随机口令）与 infra/local-secrets/ 下的源库凭据
make doctor          # 环境自检：Docker、CPU/内存/磁盘、端口、密钥文件
make up-core         # 控制库 + 源库 + API + query worker + agent worker + 前端
make migrate         # Alembic 迁移（幂等）
make bootstrap       # 建角色、容量与首个管理员账号（密码只打印一次）
```

然后打开 <http://127.0.0.1:3000>；OpenAPI 文档在 <http://127.0.0.1:8000/api/v1/docs>。手动注册数据源并给角色授权（五个步骤）写在 [docs/runbook.md](docs/runbook.md) 的 2.3 节。

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

- 模型动作限定为对比、维度拆解和一次已声明的因子分解；执行仍受材料性、权限、查询预算和深度限制，未配置模型时使用规则路径。
- 结构化输出校验失败后最多修复一次，两次调用均计入预算；仍不合法则明确失败。
- 记忆采用本地中英分析概念向量和词／字符特征，尚非通用预训练 embedding 模型。每次最多召回 200 条指标候选，经团队／个人作用域和源数据权限过滤后排序。每个用户至多一个团队；无团队时记忆仅本人可见。团队变更不会迁移历史记忆。启动前须应用迁移 `e4b8a1c79d20`。已完成双作者、3 个分析／2 个查询 worker、4 个记忆 worker 的两分钟有界持续负载验证；不代表过夜运行或生产 SLO。
- `/datasets/:id` 已支持独立深链；shared/dashboard 屏幕仍不在 V1 范围内。
- Doris 只支持本地单 BE 画像；多 BE 拓扑、workload group 与外部 catalog 属于 V1 范围外。
- DataHub 开启认证的模式未验证（pinned quickstart 关掉了 GMS 认证），profiling 在 recipes 里按策略禁用；列级血缘不在范围内。
- 仅本地 HTTP 部署，回环绑定是补偿控制（TLS 之后应设 `AIND_COOKIE_SECURE=true`）。登录／改密失败限流已共享至 PostgreSQL，SSE 轮询持久事件并持续检查会话和数据权限，已在同机双 API 进程部署验收；多机 HA／共享对象存储仍属后续范围。
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

SQL 结果表支持连续行号及上一页／下一页，页大小可选 20／50／100 条，默认
50 条，显示当前页、总页数和已存储行数。切换页大小或查询后从第一页开始；
返回上一页会重新校验权限与有效期。结果被截断时继续显示警告，不应用于精确汇总。

## 个人设置与会话历史

个人页现已提供“我的数据权限”：按数据源分组列出实际数据库、表、业务域、
发现／查询授权，以及每个授权角色和有效期；业务域汇总覆盖所有分页。
只有当前账号有效的授权会显示，过期和撤销授权在刷新后移除（可手动刷新，
前台每 15 秒复查）。同时可分页查看自己的权限申请，申请状态不代表当前授权。
账号信息包含用户 ID、创建时间和审计保留期内最近一次成功登录；不会展示其他
账号授权或数据源凭据。入口仍是 `/settings`，`/profile` 会跳转到这里。


账户入口的“个人设置”（`/settings`，`/profile` 会跳转）支持显示名称、
界面语言和 IANA 时区，偏好按用户存储并在换设备登录后恢复。时区仅改变
时间显示，不改变分析期间。旧版本提交会提示冲突并保留草稿，角色和权限只读；
个人设置还支持校验当前密码后修改密码，并撤销该账户所有旧登录会话。登录前语言选择保留在浏览器，登录后以账户偏好为准。

会话侧栏每次加载 20 条，可点击“加载更多会话”；会话内消息另有向前加载历史的
分页入口。升级须先执行 `alembic upgrade head`，应用迁移 `d2e6a9f18c40`。

会话支持重命名、归档与恢复，可勾选“显示已归档会话”浏览历史；
归档只禁用继续提问，不删除消息或未发送草稿。SQL 工作台的“已保存的查询”
面板支持命名保存、分页、载入和删除，载入不会自动执行 SQL。

2026-10-16 运行能力补齐：个人设置可保存新会话默认指标、维度和技能模式；
分析页面支持幂等重跑为新报告，保留原报告并重置完整性。启动前应用迁移
`f6a12c83d095`。Spark 已支持强制验证证书的 TLS、要求 TLS 的 PLAIN 配置、
通过 AST 字面量绑定的标量参数；真实 TLS／参数／浏览器验证通过，PLAIN 身份认证、
只读源授权和共享 HMS listener 血缘仍待真实服务验收。
