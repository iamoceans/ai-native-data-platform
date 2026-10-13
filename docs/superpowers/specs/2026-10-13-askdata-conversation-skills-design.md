# Askdata 对话式分析与 Skill 管理设计

- 日期：2026-10-13
- 状态：设计交接稿；功能尚未实现，验收尚未执行。
- 面向：接手实施的模型、开发者与产品负责人。
- 当前代码基线：`98d890f`（本次代码核查时的 HEAD）。
- 本次交付：仅设计文档；不修改业务代码、数据库或运行中的服务，不创建 Git 提交。
- 需求来源：将 Askdata 的 AI 分析改成对话式模块，能够加载 Skill，并支持用户上传 Skill。
- 范围假设：一期支持 `SKILL.md` 和文本参考资料；脚本执行、任意外部工具以及某个第三方 Harness 的完整兼容性未获明确指定，不纳入一期承诺。

## 1. 产品目标与成功标准

Askdata 成为用户可以持续讨论数据问题的分析工作区。用户先用自然语言表达目的，系统在需要时澄清指标、期间和筛选范围，调用现有受治理分析能力，在对话中展示结果，并接受追问。

Skill 是可版本化的分析方法与参考知识包。用户能上传、预览、启用，在会话中显式选择或让系统在已启用范围内匹配，并看到每轮实际加载了哪个版本、用了哪些参考资料。

一期交付必须同时满足：

1. 连续追问使用同一个持久化会话，刷新后可恢复。
2. “为什么”“只看安卓”“再按渠道拆分”能够区分解释已有结果和发起新计算。
3. 缺少关键条件时先澄清，不能悄悄使用页面里的固定默认日期。
4. Skill 从上传、校验、版本固定、加载到作用于分析或解释形成闭环。
5. 数字、图表和结论能够关联已有计算产物与权限检查；对话不能绕过 Query Gateway。
6. 重复提交、断网重连、取消、权限撤销和 worker 重启有明确行为。
7. 未配置模型时明确显示能力受限；仍可通过结构化范围控件执行现有确定性分析。

不以“聊天气泡已完成”或“能上传文件”单独判定需求完成。

## 2. 当前实现与差距

以下为本次直接读取仓库代码确认的事实；不是对线上环境的验收结论。

| 当前模块 | 已有能力 | 本次需要补充 |
| --- | --- | --- |
| `frontend/src/pages/AskPage.tsx` | 数据集、指标、日期、维度表单；提交时创建 session 和 analysis | 持续会话、消息流、追问输入、Skill 选择；不再每次提交都新建会话并跳转 |
| `frontend/src/pages/AnalysisPage.tsx` | 独立分析详情、图表及下钻 | 可复用的对话内结果卡片，保留原详情深链接 |
| `backend/app/models/orm.py` | `agent_sessions`、`agent_messages`、`analysis_tasks`、步骤与产物 | 会话上下文、对话轮次、Skill 与不可变版本 |
| `backend/app/api/routes/analyses.py` | 创建会话、读取消息、提交分析、报告、证据与分析 SSE | 会话列表/详情、提交对话轮次、会话事件流 |
| `backend/app/agent/planner.py` | 模型输出主要是最多三个允许维度；计划结构封闭 | 单独的对话意图与范围解析契约，Skill 匹配契约 |
| `backend/app/agent/runner.py` | 比较、贡献分析、按规则追加驱动分解、预算、检查点 | 接收已解析范围与 Skill 快照，向对话轮次回写结果 |
| `backend/app/agent/llm.py` | 结构化模型适配器、fake provider | 新增对话 schema，继续复用适配器和预算 |
| `backend/app/agent/memory.py` | 既有业务知识提取与召回 | 与 Skill、会话上下文保持独立，按需引用 |

当前系统是自建的受治理分析运行框架。仓库中出现的 A09 harness 是评测入口，不能据此推断已经接入可安装 Skill 的第三方 Agent Harness。

已存在 `AgentSession` 与 `AgentMessage` 不代表已实现多轮理解：当前 planner 输入以本次问题、指标、维度与业务记忆为主，没有完整的会话范围合并协议。

## 3. 架构决策

### 3.1 采用现有框架扩展

比较过三个方向：

| 方向 | 收益 | 代价 / 限制 | 决定 |
| --- | --- | --- | --- |
| 扩展现有框架：对话编排 + Skill registry | 复用权限、查询、计算、审计与恢复机制 | 需要新增轮次生命周期和明确的上下文契约 | 一期采用 |
| 只做聊天 UI，将上传内容拼入 prompt | 页面改动小 | 不能可靠处理澄清、版本追踪与跨轮范围；不能等同于完整 Skill 支持 | 不采用 |
| 更换为外部通用 Harness | 可获得更广的工具与插件生态 | 具体产品未指定，需要重新验证许可、维护状况及平台控制的接入 | 后续单独评估 |

### 3.2 模块边界

```mermaid
flowchart TD
    UI[Askdata 对话界面] --> API[会话 / 对话轮次 API]
    API --> Store[(会话、轮次、消息)]
    API --> Queue[持久任务队列]
    Queue --> Orchestrator[对话编排 worker]
    Skills[(Skill 与不可变版本)] --> Orchestrator
    Context[当前权限下的目录 / 指标 / 业务记忆] --> Orchestrator
    Orchestrator --> Clarify[澄清或解释回复]
    Orchestrator --> Runner[既有分析 Runner]
    Runner --> Gateway[Query Gateway]
    Gateway --> QueryWorker[Query Worker]
    QueryWorker --> Sources[数据源]
    Runner --> Kernel[确定性计算与证据]
    Kernel --> Messages[结果引用 / 对话消息]
    Clarify --> Messages
    Messages --> Events[持久事件 / SSE]
    Events --> UI
```

职责分为四层：

- **会话层**：用户、消息、已确认范围、每轮状态、历史恢复。
- **Skill 层**：上传校验、可见性、版本、按需加载与加载记录。
- **对话编排层**：理解问题、解析范围、澄清、解释或提交分析。
- **分析执行层**：沿用确定性计算、指标编译、Query Gateway、权限、预算和证据。

一期不引入额外服务进程、向量数据库或通用插件运行容器。

### 3.3 对既有模型输出契约的显式扩展

仓库当前不变量限制模型只做极窄的规划选择。本设计会新增“对话层结构化输出”：意图、已登记指标 ID、日期、允许维度的筛选值、可访问的 Skill 版本 ID、证据引用。这是**待实施的契约扩展**，不是声称当前系统已经支持。

实施时必须同步更新仓库中的模型边界说明、DTO、验证器和攻击测试。不得修改仓库外部的平台规格文件；如果外部规格明确禁止该扩展，应记录兼容差异，由产品负责人处理，不能宣称完全符合原规格。

模型仍不输出可执行 SQL、shell、工具地址、代码或任意调用名称。由平台根据经过校验的意图调用固定能力。所有数值运算仍在确定性内核执行。

## 4. 一期范围与能力边界

### 4.1 包含

- 会话列表、新建、重命名、归档、恢复。
- 连续消息、澄清、上下文继承、解释已有结果、发起新分析。
- 消息内分析进度、结果卡片、图表与证据入口。
- 停止当前轮次、失败后重试、断线恢复。
- 内置 Skill、个人上传 Skill、不可变版本、停用。
- 上传单个 `SKILL.md` 或符合本设计目录规则的 ZIP。
- 会话固定选择 Skill，以及在明确启用的 Skill 池内自动匹配。
- Skill 使用记录：选择方式、版本、内容哈希、加载文件。
- 结构化范围面板作为自然语言解析的可见补充和无模型时的入口。

### 4.2 不包含

- Python、shell、JavaScript 等上传脚本执行。
- Skill 自动安装依赖、任意网络访问或按文件内容配置 MCP。
- 第三方 Skill 格式的完整兼容承诺。
- 自由 SQL 生成、动态工具注册、开放式自主反复调用工具。
- PDF、Word、图片 OCR 或任意二进制附件解析。
- Skill 市场、团队共享、协同编辑、跨用户会话。
- 自动从对话生成并发布 Skill。
- 为缺少指标定义的数据表自动猜测聚合口径。

### 4.3 Skill 在一期真实能影响什么

- 选择哪些已登记维度优先分析。
- 指出需要补齐的条件，并在对话中提出相应澄清。
- 指导解释时优先引用哪些已有证据、口径说明及限制。
- 提供术语、方法说明和下一步建议。
- 通过平台扩展字段约束适用指标、优先维度、必需输入和参考资料。

分析流程仍由平台能力决定。Skill 中“训练预测模型”“读取外网”“运行某脚本”等文字不会新增这些能力，也不承诺 markdown 中每条过程指令都能执行。UI 必须展示“方法指导模式”和不支持能力；禁止用“完整安装成功”模糊边界。

## 5. 交互与信息架构

### 5.1 路由与布局

- `/ask`：新会话入口。
- `/ask/:sessionId`：持久会话。
- `/skills`：Skill 库与上传管理。
- 保留 `/analyses/:id`：完整报告、图表与证据的深链接。

桌面布局：左侧会话列表，中间消息流与输入区，右侧可折叠的分析范围和 Skill 面板。窄屏改为抽屉，不挤压消息内容。

采用 HuggingFace + Notion 风格：浅色背景、留白、细分隔线、低饱和强调色，MiSans 优先、Source Sans Pro 回退。不要将 token、队列、schema 等实现细节放入主对话流程；诊断信息置于详情中。

### 5.2 消息类型

| 消息 | 展示内容 |
| --- | --- |
| 用户消息 | 原始问题、提交时显式选择的范围/Skill 摘要 |
| 澄清消息 | 缺少的关键条件和可操作选项 |
| 说明消息 | 方法说明、口径解释、能力限制 |
| 分析消息 | 当前阶段、停止按钮、分析范围 |
| 结果消息 | 结论、图表、证据链接、后续追问 |
| 失败/取消消息 | 已完成部分、原因、重试或修改问题入口 |

展示的执行步骤是实际任务事件摘要，不输出模型隐含推理过程。

用户问题原文是不可变消息。分析进度由关联轮次/分析状态渲染，避免每个进度事件都插入一条正式聊天消息。

### 5.3 关键旅程

**首次分析**

1. 用户问“上周收入为什么下降？”
2. 解析业务时区和候选指标；有多个“收入”口径或缺少时区时澄清。
3. 条件齐全后展示解析范围，并自动发起只读分析，不每轮另加确认。
4. 在当前会话展示阶段、结果、证据。
5. “最近完整一周”与“相对上一周”的具体日期必须可见，不能静默采用固定日期。

**连续追问**

- “只看安卓”：继承指标与日期，更新对应的合法筛选字段，生成新 analysis。
- “换成上个月”：替换当前期间；若原比较为同比/环比关系，则按关系重算基准期间。
- “为什么这样判断”：引用当前焦点分析的证据，不重新查库。
- “再按渠道拆”：继承范围，替换/追加允许维度；超出三个维度时要求选择保留哪些。
- “去掉筛选”：显式清空筛选；不是将未提供字段误解释为清空。
- “用另一个指标”：保留日期；旧指标不支持的维度/筛选先提示处理，不能静默丢弃。

**上传与使用**

选择文件 → 校验与预览 → 显示支持程度 → 保存版本 → 加入当前会话或启用自动匹配。保存、启用、实际加载是不同状态。

**执行期间**

一期每会话只允许一个活跃轮次。用户可以继续编辑草稿；发送按钮显示“等待当前分析完成”及“停止”。另一浏览器标签页提交由服务端返回冲突，不静默排队或打断既有任务。

## 6. 多轮上下文与回复协议

### 6.1 会话上下文

保存结构化 `resolved_context`，至少包括：

- 指标 key 与版本。
- 数据集 ID 引用及业务时区。
- 基准/当前期间、比较关系（explicit、previous_period、year_over_year）。
- 维度与等值筛选。
- 数据完整性状态及来源。
- 当前焦点 analysis ID。
- 每个字段的来源：用户明确输入、继承、目录、模型解析或用户界面选择。

数据完整性只能是 `unknown / complete / incomplete`；用户声称“数据完整”不能直接成为可信检测结果。缺少平台完整性证据时展示限制，沿用 PARTIAL 规则，不把旧表单的 `dataComplete: true` 变成对话默认事实。桥接到现有布尔 `data_complete` 时仅 complete 映射为 true，另两种映射为 false，并在上下文中保留三态与依据，报告区分“未知”和“已知不完整”。

### 6.2 合并优先级

本轮显式 UI 值 > 本轮明确自然语言指令 > 已确认会话范围 > 可验证的目录默认值。

模型只提出结构化补丁，服务端负责合并和校验。补丁区分 `set` 与 `clear`；字段缺失表示继承。Skill 和业务记忆均不能覆盖用户的明确范围。

“上周”等相对时间按接收消息时刻与指标业务时区转换，冻结为绝对日期，并记录解释时刻。不会因 worker 延迟或重试而改变。

用户明确选择某历史结果卡片追问时，以该 analysis 为父分析；普通追问采用当前焦点分析。父分析必须属于当前用户且属于当前会话，不能只检查所有者。

### 6.3 对话决策

模型输出封闭结构，由平台验证：

| 意图 | 行为 |
| --- | --- |
| `clarify` | 返回缺失/歧义字段及候选项，不创建 analysis |
| `analyze` | 完成上下文校验后创建 analysis |
| `explain` | 基于仍有权访问的证据解释，不执行新的查询 |
| `help` | 解释平台支持的方法、范围和操作 |
| `unsupported` | 说明缺少的能力或指标契约，给出可执行下一步 |

未解析清楚的问题不能降级成随意选第一个指标。“重新运行”“换期间”“新增筛选”都属于新的分析，不能拿旧结果冒充新计算。

补充澄清产生新轮次，并携带待补齐的上下文。等待用户不占 worker lease，不消耗持续执行预算。

### 6.4 历史加载和预算

每轮使用：当前结构化范围、最近最多 12 条消息、必要证据摘要、已授权的 Skill 内容及业务记忆。对话历史与 Skill 共享输入预算，不能各自吃满全局预算。

优先保留用户当前指令、范围和证据引用；其次保留近轮消息；再保留 Skill 与业务记忆。超预算时记录省略项，不静默截断 Skill 方法正文。

一期不新增模型生成的长期会话摘要；更早上下文靠结构化范围与可引用分析恢复。任何历史检索都重新检查权限。

### 6.5 回复与数值约束

- 数值卡片、变化率、排序与图表均由已保存的计算产物渲染。
- 模型引用 `analysis_id + claim_id / artifact_id + field_path`，服务端验证引用存在、所属关系及权限。
- 数值表述使用模板插值，插值值从证据解析；不接受模型自由输出一个数字当成计算结果。
- 自由说明文字用于方法、限制、术语和建议；不得产生未经证据支持的新定量结论或因果结论。
- 证据缺失、过期、权限撤销时返回明确状态，不靠历史聊天里的数字兜底。
- 既有报告中的“无显著变化”“证据不完整”“未识别驱动”必须原样保留其含义，Skill 不能要求强行归因。

## 7. Skill 包格式与加载

### 7.1 支持的格式

单文件：文件名为 `SKILL.md`，UTF-8。

ZIP：根目录直接含 `SKILL.md`，或者只有一个顶层目录且该目录内含 `SKILL.md`；规范化后结构如下：

```text
revenue-diagnosis/
  SKILL.md
  references/
    metric-definitions.md
    interpretation-notes.txt
```

一期只接受 `SKILL.md` 与 `references/` 下的 `.md`、`.txt` 文件。存在 `scripts/`、可执行内容声明或其他文件类型时，整包以“不支持该包格式”拒绝，不静默丢掉脚本后宣称完整兼容。

### 7.2 Front matter

```yaml
---
name: revenue-diagnosis
description: 分析收入变化，优先核实口径、完整性和渠道贡献。
metadata:
  askdata:
    schema_version: 1
    metric_keys: [ads_revenue]
    preferred_dimensions: [platform, channel]
    required_inputs: [metric_key, baseline_period, current_period]
    references: [references/metric-definitions.md]
---
# 收入变化分析
先明确期间、时区和指标口径。
在数据完整的前提下，优先检查平台与渠道贡献。
只有现有指标声明支持时才解释驱动分解。
结论应引用已完成分析的证据；证据不足时说明限制。
```

规则：

- `name`：1–64 个字符，小写字母、数字和单个连接符分段；作为 owner 命名空间内的 slug。
- `description`：1–500 字符；用于技能列表和候选匹配。
- 正文必须非空；YAML 使用安全解析，拒绝重复键、自定义 tag、过深嵌套或别名膨胀。
- `metadata.askdata` 可省略；省略时作为通用方法指导，不自动推断结构化限制。
- `metadata.askdata` 内未知字段或不支持版本直接拒绝。
- 顶层允许 `name`、`description`、`license`、`compatibility`、`metadata`；其他字段拒绝并解释。
- `compatibility` 是作者声明的说明文字，不能据此宣称已经验证可运行。
- `metadata` 中其他命名空间仅作为普通展示数据保留，不执行其指令。
- 自定义扩展中的指标、维度、必需字段和路径都必须通过平台白名单/存在性验证。
- Markdown 里的 URL 仅当作文本链接；后端不抓取，不加载外部相对依赖。
- 包中未知/缺失指标可保存为“不适用于当前环境”的版本，但不能自动加载用于该指标的分析；不得因此扩展指标注册表。

名称、简介和参考资料也属于不可信用户内容，不能因只在“候选阶段”使用就免除校验和预算。

### 7.3 导入限制

一期默认硬上限：

| 项目 | 上限 |
| --- | --- |
| 上传体积 | 2 MiB |
| 解压后总文本 | 4 MiB |
| 文件数 | 32 |
| 单个文件 | 256 KiB |
| SKILL.md | 64 KiB |
| 路径长度 / 目录深度 | 200 字符 / 4 层 |
| 压缩倍率 | 100:1；逐块解压时同时执行实际字节上限 |
| 每用户 Skill 数 / 每 Skill 版本数 | 50 / 20 |

拒绝绝对路径、盘符、UNC、`..`、NUL、符号链接、重复规范化路径、Windows 保留名、NTFS ADS、加密 ZIP 和嵌套压缩包。文件名统一按 POSIX 相对路径规范化，大小写折叠后去重。

验证器流式读取包并计数，不将用户路径直接解压到工作目录，不访问服务器已有文件。错误只返回业务说明，不泄露服务器路径。

### 7.4 存储与版本

一期全部为有界文本，解析后的文件内容存入 PostgreSQL JSONB，每个文件保存 path、media_type、content、sha256。无需新建共享文件服务；原始 ZIP 校验后不持久保存。

版本内容不可修改。内容哈希对“排序后的规范化路径 + 规范化 UTF-8 文件内容”的确定性序列计算，明确统一 BOM/换行规则。每个版本记录上传者、创建时间、manifest、content hash、兼容结果。

同一 Skill 重复导入相同 hash 返回已存在版本；不同内容生成新递增版本，不覆盖旧版本。新 slug 不能因与内置 Skill 同名覆盖内置版本。

保存成功只意味着版本已入库；启用后才参加匹配。更新新版本不会自动改变已有会话的版本绑定。用户显式升级后从下一轮生效；运行中轮次继续使用既有快照。

一期采用停用和归档，不做物理删除。私有 Skill 的所有者可管理自己的版本；内置 Skill 通过仓库发布，普通用户只读。

### 7.5 加载策略

每会话维护可用技能版本池，最多 10 个；默认提供经过维护的内置技能，个人新上传版本必须显式加入。模式为：

- `off`：不加载 Skill。
- `manual`：每轮最多显式选择 3 个版本。
- `auto`：从会话版本池简介中选择最多 3 个，所有选择必须返回候选池内 ID；不扫描机器技能目录。

分层加载：

1. 候选阶段只读版本 ID、名称、简介和适用范围。
2. 匹配后加载正文和 `metadata.askdata.references` 指定的参考资料。
3. 没有声明 references 时只加载正文；用户可在技能详情中显式选入包内参考文件，选择计入会话绑定和轮次快照。
4. 文件读取由平台实现，模型不能提出任意文件路径来读服务器文件。
5. 所有加载内容作为低优先级、有来源标签的方法资料；不能替换平台系统规则。
6. 同轮总 Skill 内容最多 24 KiB，并受模型输入预算进一步约束。manual 超额则要求减少；auto 按匹配顺序跳过超额版本并给出提示。
7. 冲突时平台契约和用户明确范围优先；Skill 之间的互斥必需条件需要澄清，不静默采取未知顺序。

记录“候选”“选中”“已加载”“因不适用跳过”，只有实际进入本轮上下文的文件计为已加载。该记录证明上下文使用情况，不声称能够测量模型内部对 Skill 的因果依赖。

### 7.6 与业务记忆的区别

| 内容 | 来源 | 用途 | 是否自动成为 Skill |
| --- | --- | --- | --- |
| 会话范围 | 当前用户和本会话 | 继承日期、指标、筛选 | 否 |
| Skill | 人维护的方法文件 | 指导分析与解释方式 | 否 |
| 业务记忆 | 既有分析学习结果 | 提供业务背景 | 否 |
| 分析证据 | 本次受治理查询与计算 | 支撑数字和结论 | 否 |

Skill 内容、导入预览、失败消息不能自动写入业务记忆。沿用现有“已完成分析产生知识”的边界，并测试新对话层不会扩大提取来源。

## 8. 数据模型

此处是逻辑设计；迁移文件名由实施时最新 Alembic head 决定，不预设迁移 revision。

### 8.1 现有表扩展

**agent_sessions：一行表示一个用户的持续对话。**

新增 `updated_at`、`archived_at`、`context_version`、`resolved_context JSONB`、`pending_clarification JSONB`、`skill_config JSONB`。Skill 配置保存 version ID、模式和选中的参考文件，不保存重复正文。pending_clarification 保存尚未确认的范围草稿、缺失字段和来源轮次，不与已确认范围混写。

**agent_messages：一行表示一条正式用户或助手消息。**

新增 nullable `turn_id`、会话内递增 `sequence` 与 `content_schema_version`。`content` 使用封闭消息 DTO；角色仍是 user/assistant。对新消息建立唯一 `(session_id, sequence)`。旧消息按 `created_at, id` 回填 sequence，保留原 content 并由兼容读取器转换。

**analysis_tasks：一行继续表示一次计算分析。**

新增 nullable `conversation_turn_id`，对非空值唯一。一期一个轮次最多创建一个 analysis。保留 `parent_id` 分析链；添加同用户、同会话的业务验证。

### 8.2 新增表

| 表 | 一行代表什么 | 主要字段/约束 |
| --- | --- | --- |
| `conversation_turns` | 一次被接收的用户输入及其处理生命周期 | id、session_id、user_message_id、status、intent、request_hash、idempotency_key、context_before/after、base_context_version、skill_snapshot、budget、deadline_at、checkpoint、error、timestamps |
| `skills` | 一个可管理技能身份 | id、owner_user_id（内置为 null）、source、slug、enabled、archived_at、created_at、updated_at |
| `skill_versions` | 一份不可变技能内容 | id、skill_id、version、manifest、files、content_hash、compatibility、created_by、created_at |

约束要求：

- 每会话至多一个 `QUEUED / RESOLVING / ANALYZING / RESPONDING` 轮次，使用数据库部分唯一索引与会话行锁保证。
- `conversation_turns` 的状态必须有 CHECK 和显式转换表。
- `(session_id, idempotency_key)` 唯一；重复 key 不同请求 hash 返回 409。
- `(skill_id, version)`、`(skill_id, content_hash)` 唯一。
- 私有技能的 `(owner_user_id, slug)` 唯一；内置 slug 使用独立的部分唯一索引，不依赖 NULL 的普通唯一语义。
- `skills.source` 与 owner 是否为空必须一致，限制值为 builtin / upload。
- 文件内容与 hash 只允许在版本创建时写入；启用状态放在技能身份上。

### 8.3 生命周期

轮次转换：

```text
QUEUED -> RESOLVING
RESOLVING -> RESPONDING -> COMPLETED
RESOLVING -> WAITING_INPUT
RESOLVING -> ANALYZING -> RESPONDING -> COMPLETED
非终态 -> FAILED 或 CANCELLED
```

`WAITING_INPUT` 是本轮终态，表示澄清消息已提交，下一条用户消息创建下一轮。`PARTIAL` 保留为 analysis 的结果状态；有部分证据的轮次可 COMPLETED，消息必须显式标为“部分结果”。

失败/取消不更新会话已确认范围，也不能将执行一半的分析作为新的默认证据。WAITING_INPUT 只更新 pending_clarification，不替换 resolved_context。下一轮结合澄清草稿与用户新输入继续解析；条件确认且成功回复提交后，原子更新 resolved_context 并清除相应草稿。上下文、草稿或 Skill 配置的变更均递增 context_version，并做并发版本校验；help 或 unsupported 回复不改变分析范围。

## 9. API 合约

路径均以 `/api/v1` 为前缀。沿用现有 Cookie 认证、CSRF、错误 envelope 与签名分页游标。以下为拟新增/扩展接口，不是当前已有 API 清单。

| 方法与路径 | 行为 |
| --- | --- |
| `GET /sessions` | 当前用户会话列表，按 updated_at/id 倒序，支持归档过滤与分页 |
| `POST /sessions` | 兼容原接口，创建会话，可带初始 Skill 配置 |
| `GET /sessions/{id}` | 会话上下文、Skill 配置、活跃轮次 |
| `PATCH /sessions/{id}` | 重命名、归档、变更 Skill 配置；要求 expected_context_version |
| `GET /sessions/{id}/messages` | 扩展旧接口，返回有序消息和可恢复分页游标 |
| `POST /sessions/{id}/turns` | 接收消息，原子保存 user message + turn + queue，202 返回 IDs |
| `GET /sessions/{id}/turns/{turn_id}` | 当前轮次状态、关联 analysis ID 和恢复信息 |
| `POST /sessions/{id}/turns/{turn_id}/cancel` | 取消轮次及其尚未完成的分析/查询；幂等 |
| `GET /sessions/{id}/events` | 会话 SSE，支持 Last-Event-ID |
| `GET /skills` | 内置 + 自己的 Skill，含是否启用、适用性及版本摘要 |
| `POST /skills/import-preview` | multipart file，执行完整解析校验，仅返回有界预览及规范化 hash |
| `POST /skills` | multipart file + expected_hash，重新校验并创建个人技能及首版本 |
| `POST /skills/{id}/versions` | multipart file + expected_hash，创建新版本；所有者限定 |
| `GET /skills/{id}` | 元信息和版本列表 |
| `GET /skills/{id}/versions/{version_id}` | manifest、正文和包内文本文件 |
| `PATCH /skills/{id}` | 启用、停用、归档；不能改写版本内容 |

预览不需要持久化临时上传记录。确认保存时浏览器再次发送同一个 File 与 expected_hash；服务端重新执行完整校验，hash 不同返回冲突。全量响应不包含服务器文件路径。

轮次请求示例：

```json
{
  "text": "只看安卓，再按渠道拆分",
  "expected_context_version": 3,
  "parent_analysis_id": null,
  "scope_patch": null,
  "skill_selection": {
    "mode": "manual",
    "version_ids": ["<version-uuid>"]
  }
}
```

- `Idempotency-Key` 对新轮次为必填；请求 hash 包含所有影响语义的字段。
- `parent_analysis_id=null` 表示采用会话当前焦点；显式值需同会话校验。
- `scope_patch=null` 表示没有 UI 补丁，由自然语言解析并继承范围。
- `skill_selection` 省略表示使用会话配置；`mode=off` 才表示清空本轮技能。
- 接收时要求 analysis.create 能力；Skill 上传一期同样以 analysis.create 为前提，并强制 owner 限制，不新增默认开放的全局管理入口。
- 活跃轮次冲突返回 `409 SESSION_BUSY`，上下文版本过期返回 `409 CONTEXT_VERSION_CONFLICT`。
- 活跃轮次期间仅允许重命名；修改范围、Skill 配置或归档均返回 SESSION_BUSY。配置变化只影响后续轮次。
- 导入限制分别返回 `413 SKILL_TOO_LARGE`、`422 SKILL_INVALID`、`422 SKILL_UNSUPPORTED`；ID 不可见统一 404。
- 既有 `POST /analyses`、图表下钻和详情接口继续兼容。它们创建的新任务也必须参与同会话忙碌保护，不能从旧入口绕过。
- 归档前要求无活跃轮次；归档后不可提交，恢复后允许继续。

## 10. 持久执行、恢复与事件

### 10.1 队列与 worker

扩展现有 `task_queue.kind`，新增 `conversation`；不能直接提交一个当前 CHECK 不允许的新值。新增对应 claim/release 和 worker dispatch，并沿用 fencing token、租约、检查点机制。

API 仅保存请求与入队；模型调用也在 worker 执行。API 不对源数据库执行用户 SQL。

对话 worker 的阶段：

1. claim 轮次，加载并重新验证用户/技能/数据权限。
2. 固定上下文、模型/提示词版本、Skill 版本和文件 hash。
3. 调用结构化意图解析，验证输出。
4. 澄清/帮助/解释：保存回复、终态、审计和事件。
5. 新分析：原子创建关联 analysis 并入队，保存检查点，释放轮次 lease。
6. analysis 结束后，通过持久完成事件或轮次 reconciling 重新调度回复阶段。
7. 发布最终消息与上下文更新，关闭轮次。

等待 analysis 期间不持有长事务或轮次 lease，不占住一个 worker 进程做轮询。需要同时处理分析失败、取消、PARTIAL 和 worker 丢失后恢复。

模型请求在数据库事务外执行；claim 事务固定输入快照并预留预算，返回后用 fencing token、轮次状态和检查点版本提交。取消先完成时，迟到模型回复必须丢弃，不能发布成功消息或创建 analysis。租约续期与失效判断使用数据库时间/既有机制，不能以长时间持有行锁来代替。

### 10.2 幂等与预算

- 消息提交、分析创建和最终助手消息均有唯一业务键，worker 重试不得复制。
- 新对话 analysis 的终态消息统一由对话编排层写入；旧直接分析继续使用 runner 的原消息路径，避免双重助手回复。
- 外部模型响应到达但尚未持久化就崩溃，可能发生重复模型调用；不能声称外部调用 exactly-once。必须限制重试并记录 usage/attempt。
- claim 时预留本次模型调用的最坏 token 预算；在不确定是否已消耗时保守计入，恢复不能免费重复调用。
- 一个轮次总预算覆盖意图解析、Skill 匹配、分析 planner 和回复；analysis 获得剩余额度子预算，不能每阶段重新获得完整额度。
- `deadline_at` 持久化为 UTC 时刻，恢复时计算剩余墙钟时间；不能仅靠进程 monotonic 起点恢复预算。
- WAITING_INPUT 终止当前预算；下一次用户输入创建新的明确计费/预算轮次。
- 重试失败轮次是新请求、新 key，可引用旧范围与技能快照；显式显示“重试”，不改写旧记录。

### 10.3 SSE 与恢复

会话事件包括：

`message.created`、`turn.state.changed`、`turn.scope.resolved`、`turn.skills.loaded`、`analysis.linked`、`analysis.state.changed`、`analysis.result.available`、`turn.failed`、`turn.cancelled`。

事件在数据库持久化，以单调事件 ID 续传。分析到会话的事件投影要有源事件唯一键，避免 reconciler 重放产生重复通知。

事件优先携带 ID/状态引用；正文、图表和证据从受权限保护的读取接口取得。客户端收到事件后按 ID 幂等合并，不依赖“消息一定先到”之类顺序假设。

断线重连携带 Last-Event-ID；游标失效时提示 resync，重新拉取会话/消息/活跃轮次。刷新页面不重新 POST 用户问题。会话列表/消息使用稳定游标，新消息插入不使翻页丢失。

## 11. 权限与内容边界

以下是新功能引入的具体控制要求，不以 prompt 提示代替服务端验证。

1. **技能所有权**：私有上传只有所有者可读、更新、使用。内置技能只读。所有版本读取和提交绑定都要重新检查。
2. **数据权限**：会话 owner 检查不足以保护数据。历史消息、analysis detail、列表内嵌 final_report、证据、图表与 SSE 都需检查相关依赖的当前 discover/query 权限。
3. **跨分析解释**：解释消息保存所有引用分析的依赖集合；任一引用无权访问时隐藏相应内容。新模型调用不能读入已撤权的旧对话证据。
4. **撤权与缓存**：执行前再次校验；撤权取消活跃任务，SSE 关闭或发失效事件，前端清除对应已缓存的结果。不能承诺撤回用户已经看到或下载的内容。
5. **技能停用**：停止参与后续轮次；若当前轮次已完成 claim 并固定快照，可完成该轮。尚未 claim 的绑定停用版本轮次失败并提示重新选择，不悄悄换版本。停用不是紧急撤销机制。
6. **内容展示**：Markdown 禁用原始 HTML，过滤不安全 URL，不自动加载远程图片；用户文本、front matter 和文件名均正确转义。
7. **指令注入**：Skill、参考资料和业务记忆不能修改系统规则、授予权限、增加工具或绕过预算。安全性由封闭 DTO、服务端校验与执行边界保证。
8. **上传请求**：认证、CSRF、体积限制和每用户配额在服务端实施；失败不能留下半个技能版本。
9. **审计**：保存上传、版本创建、启停、轮次创建/取消、技能加载/跳过等行为与资源 ID/hash。不要把正文、密钥或数据库凭据写入日志。
10. **模型上下文**：不将服务器环境变量、配置密钥、任意工作目录文件提供给模型。Skill 中出现“读取本地文件”等指令不会触发文件系统能力。

本次代码核查发现既有消息读取主要验证会话所有权，而报告读取有专门依赖权限检查。将历史消息接入模型之前，实施者必须统一这条读取边界；不能假设历史接口已经覆盖新场景。

## 12. 前后端实施拆分

建议的逻辑文件边界如下；跟随实施时仓库实际结构调整文件名，不为达到清单数量机械拆文件。

| 范围 | 新增/改造位置 | 职责 |
| --- | --- | --- |
| Skill 领域 | `backend/app/skills/` | 包解析、格式校验、版本 hash、适用性、加载 |
| 对话领域 | `backend/app/agent/conversation/` | DTO/意图、范围合并、状态、编排、回复证据验证 |
| 持久化 | `backend/app/repositories/`、`models/orm.py` | 会话、轮次、Skill、事务与权限边界 |
| HTTP | `backend/app/api/routes/`、`dto.py` | sessions/turns/skills API，复用原 analyses |
| 队列 | `repositories/queue.py`、`workers/common.py` | conversation claim、dispatch、恢复 |
| 分析桥接 | `agent/runner.py` | 关联轮次、共享预算、完成通知、避免重复消息 |
| 前端 | `pages/AskPage.tsx`、会话/技能组件、`App.tsx` | 会话路由、消息、范围、Skill 管理 |
| API 类型 | OpenAPI 导出、`frontend/src/api/` | 先导出再生成，不能手写覆盖生成类型 |
| 内置技能 | `metadata/skills/` | 少量版本化方法技能及参考文本 |
| 测试 | unit/security/contract/integration、Playwright | 本文验收矩阵 |
| 文档 | README、architecture、todo、acceptance | 实施后按实际验证结果同步 |

内置首批只做三项：收入变化解读、渠道贡献分析、数据完整性检查指导。每项明确依赖现有指标与计算能力；不要给不存在的检查器起一个技能名后宣称已实现。

## 13. 实施顺序与验收矩阵

### 13.1 分阶段交付

- [x] **P1 — 持久会话基础**：迁移、轮次状态、会话 API、幂等、忙碌保护、稳定消息顺序、旧消息兼容。
- [x] **P2 — Skill 完整闭环**：上传预览、文本校验、个人所有权、版本、启用与快照、管理 UI。
- [x] **P3 — 对话编排**：范围解析/继承/澄清、技能加载、解释与分析分流、共享预算、worker 恢复。
- [x] **P4 — Askdata 交互**：消息流、范围面板、技能选择、图表/证据内嵌、取消、断线恢复。
- [x] **P5 — 回归与验收**：安全矩阵、现有分析回归、真实模型多轮用例、文档和结果记录。

P1/P2 可以分别完成，但 P3/P4/P5 未完成前不得将整个需求标为交付。本清单表达依赖与顺序，不授权创建提交、推送或部署。

### 13.2 必须覆盖的行为

| ID | 场景 | 验收结果 | 建议层级 |
| --- | --- | --- | --- |
| C01 | 连续问三个问题后刷新 | 同一会话、历史顺序稳定、活跃任务不重建 | 集成 + E2E |
| C02 | 缺指标或期间 | 澄清；没有 analysis/query 入队 | 单元 + 集成 |
| C03 | “只看安卓”“换成上个月”“去掉筛选” | 继承与 set/clear 正确，范围与 SQL 参数一致 | 单元 + 集成 |
| C04 | 相对日期跨时区、月界与重试 | 日期按接收时刻固定，不漂移 | 单元 |
| C05 | 解释旧结果 | 无新增查询；每项数字可解析为证据值 | 集成 |
| C06 | 相同 key 重发；两个标签页同时发送 | 一条用户消息、一个轮次、最多一个 analysis；冲突明确 | 集成 |
| C07 | worker 在模型后/analysis 创建后/回复前退出 | 可恢复，无重复任务或正式消息，预算不会重置 | 集成 |
| C08 | 取消发生在各执行阶段 | 不再启动新查询，旧查询走既有取消链，结果不覆盖取消态 | 集成 + E2E |
| C09 | SSE 断线、重复事件、游标失效 | 无丢消息/重复消息，能 resync | 合约 + E2E |
| C10 | 历史数据权限撤销 | 消息、详情、模型上下文、SSE、缓存都不能继续暴露受限结果 | 安全 + 集成 |
| C11 | 无模型/模型超时/无效结构输出 | 明确降级或失败；不假装智能回复成功 | 单元 + E2E |
| S01 | 单文件/合法 ZIP 导入后选择使用 | 实际加载正确正文与参考文件，记录版本/hash | 单元 + 集成 + E2E |
| S02 | 同内容重复上传/新内容升级 | 幂等返回/创建新版本；旧会话保持原版本 | 集成 |
| S03 | 跨用户读/改/加载 Skill | 统一不可见，不因猜中 UUID 访问成功 | 安全 |
| S04 | Zip Slip、symlink、Windows 路径、大小写重名、ZIP 炸弹 | 在限制内拒绝，无工作区文件写入 | 安全 |
| S05 | 恶意 YAML、HTML、URL、指令注入 | 不能执行代码、读取文件、扩大权限或新增工具 | 安全 |
| S06 | 脚本包、超大正文、不支持字段 | 明确不支持；不能显示完整兼容或偷偷忽略 | 单元 + 合约 |
| S07 | 技能停用/归档/显式升级 | 符合 claim 与版本规则，历史可追溯 | 集成 |
| S08 | Skill 与用户范围冲突/自动匹配超预算 | 用户范围优先，冲突澄清，加载记录诚实 | 单元 + 集成 |
| S09 | 不同 Skill 指导不同允许维度 | fake 可验证注入与分支；真实模型用例记录实际影响 | 单元 + 模型验收 |
| R01 | 原直接分析及图表下钻 | 无重复消息，旧报告链接与权限继续有效 | 回归 + E2E |
| R02 | no_change / incomplete_day / 驱动分解 | 不强行归因，PARTIAL 规则与证据约束不退化 | 原矩阵 + A09 |

### 13.3 实施后的验证入口

按照仓库 AGENTS.md 执行。以下是后续执行计划，本次未执行：

```powershell
# 项目根目录；Python 始终通过 uv 且 frozen
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp

# 集成需要服务就绪且按仓库说明停止 compose workers，避免争抢队列
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp

# Windows 不使用 make
.\scripts\dev.ps1 api-spec
.\scripts\dev.ps1 types

# 前端目录
npm.cmd run build
npx.cmd playwright test
```

集成执行前，复核当前 compose 状态、端口与环境；停止/恢复服务只在后续实施授权范围内进行。MySQL/Doris/DataHub 未运行导致的 skip 不能记为全矩阵通过。

涉及 SQL validator/provider 改动必须运行攻击语料。真实模型测试单独记录模型 ID、提示词版本、Skill hash、输入轮次、证据一致性、耗时、token、失败原因；fake 测试不能代替真实模型验收。

已有文档中的 282 静态测试、73 全矩阵及 A09 结果属于历史基线，不代表本设计已通过这些测试。

## 14. 迁移、发布与回退

- 采用增量迁移：新增表/nullable 关联、会话字段和消息 sequence 回填；先验证旧数据形状，再加约束。
- 导入内置 Skill 使用幂等初始化过程；内容 hash 变化创建新版本，不能覆盖生产历史快照。
- 会话列表包括旧 sessions，旧分析消息通过 content schema 兼容层显示。
- 新功能放在单一配置开关 `AIND_CONVERSATIONAL_ASK_ENABLED` 后，默认关闭；UI 入口与新 API 写入同时受控。
- 开关关闭时不接受新 turn，已入队轮次仍可完成/取消，新 worker 保持可用，避免遗留任务永久悬挂。
- 回退前停止新写入并处理活跃 conversation 任务，再切回旧 Ask 页面；新增数据保留。
- 未清空/完成新类型队列项前不能直接回退到不识别 conversation 的旧 worker 镜像。
- 不承诺无损 down migration 删除新表；先备份并给出数据影响，禁止用回退脚本默默丢弃会话或技能。
- 实施后更新 README、architecture、todo、acceptance；记录实际命令、通过/失败/跳过和未执行项。
- 本次只新增设计文档，不修改现有“已实现”说明或历史验收记录。

## 15. 交接说明

接手模型应先阅读：

1. 根目录 `AGENTS.md`、`README.md`、`docs/architecture.md`。
2. 本设计文档。
3. `docs/todo.md`、`docs/acceptance.md` 的最近记录。
4. 本文第 2、12 节列出的实现入口，并重新检查 HEAD、工作区改动和服务状态。

实施原则：

- 用户此次只要求输出设计，**本文不是当前会话的实施授权**。后续由用户另行发起实施任务。
- 首个实现切片应能够“创建会话 → 提问 → 持久化轮次 → 展示回复/分析”，然后接入 Skill；不要先重写整个 agent。
- 不安装某个外部 Harness 或修改本机全局 Skill 目录来代替平台内上传管理。
- 不将上传 Skill 自动当成拥有所有数据权限的系统提示词。
- 不把外部规格未实现项、脚本运行或任意工具循环夹带为本期必需。
- 任何删减范围必须说明缺少哪个验收场景，不能将降级版本描述成全功能。
- 文档所示命名是明确建议，可按现有仓库模式调整；状态、权限、版本、预算和恢复语义不能在未说明的情况下改变。

### 设计完成记录

- [x] 核查 Ask 页面、会话/分析 API、模型/runner、持久化与队列入口。
- [x] 区分当前实现事实与拟议能力。
- [x] 明确一期文本 Skill 范围及不支持项。
- [x] 明确接口、生命周期、迁移、失败恢复与验收计划。
- [x] 自查澄清草稿与已确认范围、版本升级、取消竞态、旧入口兼容及命令引用的一致性。
- [x] 功能实施与测试：P1–P5 于 2026-10-13 完成；证据在 `docs/acceptance.md` 的 P5 一节与 `runtime/acceptance/conversation-20261013-220711.json`，未完成项列在 `docs/todo.md`。

本次为文档交付，未执行应用测试。文档校验覆盖文件落盘、引用命令、Markdown 结构、变更范围与设计一致性；功能行为仍需按上述矩阵验收。

