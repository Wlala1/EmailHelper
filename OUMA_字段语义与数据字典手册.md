# OUMA 字段语义与数据字典手册

版本：v1.0  
更新日期：2026-03-12  
主规范来源：`OUMA-v2.md`

## 1. 文档定位

本文档面向以下读者：

- Agent 输入输出开发同事
- n8n 编排开发同事


如果你要的是流程和状态推进，请看 `OUMA_Agent_n8n_数据规范手册.md`。  
如果你要的是字段含义和数据字典，请看本文档。

## 2. 如何使用本文档

建议这样使用：

1. 对接某个 Agent 前，先看第 7 到第 11 章对应阶段
2. 看到不熟悉的 id 字段，先看第 3 章核心标识
3. 不确定某个字段谁生产、谁消费，先看对应表或 payload 的字段表
4. 联调时重点看第 12 章常见混淆字段

## 3. 核心标识字典

### `user_id`

含义：OUMA 内部用户主键  
谁生成：OUMA 用户体系  
谁使用：全部 Agent、n8n、Neo4j  
注意：

- 多用户隔离依赖它
- 不是邮件发件人 id

### `email_id`

含义：OUMA 内部邮件主键  
谁生成：邮件入库层  
谁使用：全部 Agent、全部结果表、n8n  
注意：

- 它是全链路主关联键
- 不能用 `graph_message_id` 代替

### `attachment_id`

含义：OUMA 内部附件主键  
谁生成：邮件入库层  
谁使用：Attachment Agent、`attachment_results`  
注意：

- 一封邮件多个附件时，每个附件都有自己的 `attachment_id`
- 不是外部系统的附件 id

### `run_id`

含义：一次 Agent 执行的唯一编号  
谁生成：n8n / 调度层  
谁使用：`agent_runs`、所有结果表、审计回放  
注意：

- 一次执行一个 `run_id`
- 重跑必须生成新的 `run_id`

### `trace_id`

含义：一轮完整处理链路的编号  
谁生成：n8n / 调度层  
谁使用：全链路  
注意：

- 同一封邮件一次完整流程共享同一个 `trace_id`
- 用于追踪同一轮流水线

### `candidate_id`

含义：内部事件候选主键  
谁生成：Schedule 流程  
谁使用：Outlook 写入层、Neo4j、Response  
注意：

- 一个候选一个 `candidate_id`
- 不允许临时计算、临时丢失

### `transaction_id`

含义：Outlook 幂等键  
谁生成：Schedule 流程 / n8n  
谁使用：Outlook 写入层  
注意：

- 必须稳定
- 推荐格式：`ouma_sched_<candidate_id>`
- 不能随机生成，否则会写出重复事件

## 4. 邮件基础事实字段

### 4.1 `emails` 关键字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 常见误解 |
| --- | --- | --- | --- | --- | --- |
| `email_id` | OUMA 内部邮件主键 | 入库层 | 全链路 | 是 | 不是外部邮件 id |
| `user_id` | 这封邮件属于哪个 OUMA 用户 | 入库层 | 全部 Agent | 是 | 不是发件人 |
| `graph_message_id` | Graph 返回的普通邮件 id | 入库层 | 调试、对账 | 否 | 可能变化，不能做主键 |
| `graph_immutable_id` | Graph 的稳定邮件 id | 入库层 | 幂等检查 | 否 | 推荐作为外部对账键 |
| `internet_message_id` | 邮件标准头中的全局消息 id | 入库层 | 去重、对账 | 否 | 更适合跨系统对账 |
| `conversation_id` | 邮件所属会话 id | 入库层 | 会话分析 | 否 | 不是业务主键 |
| `sender_name` | 发件人显示名 | 入库层 | Classifier、Response | 否 | 可为空 |
| `sender_email` | 发件人邮箱 | 入库层 | Classifier、RelationshipGraph、Response | 是 | 关系图谱的重要来源 |
| `subject` | 邮件主题 | 入库层 | Classifier、Response | 否 | 不能替代正文 |
| `body_content_type` | 正文类型 | 入库层 | Classifier | 是 | 目前只接受 `text/html` |
| `body_content` | 邮件正文原文 | 入库层 | Classifier | 否 | 是摘要和时间抽取的主要来源 |
| `body_preview` | 邮件摘要预览 | 入库层 | 调试、快速展示 | 否 | 不能替代正文 |
| `received_at_utc` | 收件时间 UTC | 入库层 | 全部阶段 | 是 | 是时间线基准 |
| `has_attachments` | 是否有附件 | 入库层 | n8n、Attachment | 是 | 它直接影响附件分支是否执行 |

### 4.2 `attachments` 关键字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 常见误解 |
| --- | --- | --- | --- | --- | --- |
| `attachment_id` | OUMA 内部附件主键 | 入库层 | Attachment Agent | 是 | 不是 Graph 附件 id |
| `email_id` | 附件属于哪封邮件 | 入库层 | Attachment、联表查询 | 是 | 外键，不是独立业务对象 id |
| `graph_attachment_id` | Graph 中的附件 id | 入库层 | 对账、调试 | 否 | 外部系统标识 |
| `name` | 文件名 | 入库层 | Attachment Agent | 是 | 只用于展示和辅助判断 |
| `content_type` | MIME 类型 | 入库层 | Attachment Agent | 否 | 影响解析路由 |
| `size_bytes` | 附件大小 | 入库层 | 调试、策略控制 | 否 | 可用于跳过超大文件 |
| `is_inline` | 是否内嵌附件 | 入库层 | Attachment Agent | 是 | 可用于过滤 logo、签名图 |

## 5. 编排与运行字段

### `agent_runs` 关键字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 常见误解 |
| --- | --- | --- | --- | --- | --- |
| `run_id` | 一次运行的唯一编号 | n8n / 调度层 | 全链路 | 是 | 一次执行一个 `run_id` |
| `trace_id` | 一轮完整链路编号 | n8n / 调度层 | 全链路 | 是 | 同一轮共享 |
| `email_id` | 本次运行对应哪封邮件 | n8n / 调度层 | 全链路 | 是 | 用它串业务对象 |
| `agent_name` | 当前运行的是哪个 Agent | n8n / 调度层 | 全链路 | 是 | 固定枚举，不允许自定义 |
| `upstream_run_id` | 直接上游运行编号 | n8n / 调度层 | 调试、依赖追踪 | 否 | 不是完整门槛表达 |
| `status` | 当前运行状态 | n8n / 调度层 | n8n、调试 | 是 | 至少区分 `started/success/failed/skipped` |
| `model_name` | 使用的模型名称 | Agent / 调度层 | 调试 | 否 | 用于回放和比较 |
| `model_version` | 使用的模型版本 | Agent / 调度层 | 调试 | 否 | 建议保留 |
| `prompt_version` | Prompt 版本 | Agent / 调度层 | 调试、重跑对比 | 否 | 很关键 |
| `input_payload` | 本次运行输入快照 | n8n / 调度层 | 调试 | 是 | 用于复盘当时收到什么 |
| `output_payload` | 本次运行输出快照 | n8n / 调度层 | 调试 | 是 | 要与结果表能互相印证 |
| `error_code` | 错误码 | Agent / 调度层 | 排错 | 否 | 失败时尽量标准化 |
| `error_message` | 错误信息 | Agent / 调度层 | 排错 | 否 | 不要只写“执行失败” |

特别说明：

- `agent_runs` 记录的是执行历史，不是当前快照表
- “唯一成功运行”是编排层保证，不是数据库自己推断出来的

## 6. 当前快照字段

### `is_current`

含义：当前默认版本标记  
谁写：n8n / 调度层  
谁读：下游 Agent、UI、查询层  
注意：

- 它表示“当前快照”，不表示“只有这一条记录存在”
- 新结果写入后，必须先切旧，再写新

## 7. Classifier 阶段字段

### 7.1 输入 payload 重点字段

| 字段名 | 字段含义 | 谁提供 | 谁消费 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `payload.email.subject` | 邮件主题 | 入库层 / n8n | Classifier | 否 | 辅助分类 |
| `payload.email.sender_name` | 发件人显示名 | 入库层 / n8n | Classifier | 否 | 可为空 |
| `payload.email.sender_email` | 发件人邮箱 | 入库层 / n8n | Classifier | 是 | 影响角色判断 |
| `payload.email.body_content` | 正文原文 | 入库层 / n8n | Classifier | 否 | 摘要和实体抽取主来源 |
| `payload.email.body_preview` | 正文预览 | 入库层 / n8n | Classifier | 否 | 仅辅助 |
| `payload.email.received_at_utc` | 收件时间 UTC | 入库层 / n8n | Classifier | 是 | 时间线基准 |
| `payload.email.has_attachments` | 是否有附件 | 入库层 / n8n | Classifier | 是 | 影响后续附件分支 |

### 7.2 输出字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `category` | 邮件类别 | Classifier | Schedule、Response | 是 | 影响下游处理策略 |
| `urgency_score` | 紧急度分数 | Classifier | Response、提醒策略 | 是 | 建议 `0~1` |
| `summary` | 邮件摘要 | Classifier | Attachment、Response | 是 | 下游最常读字段之一 |
| `sender_role` | 发件人角色推断 | Classifier | RelationshipGraph、Response | 是 | 影响关系和语气 |
| `named_entities` | 命名实体结果 | Classifier | 下游 Agent | 是 | 先用 JSON 数组 |
| `time_expressions` | 时间表达式结果 | Classifier | Schedule | 是 | Schedule 核心输入 |

## 8. Attachment 阶段字段

### 8.1 输入 payload 重点字段

| 字段名 | 字段含义 | 谁提供 | 谁消费 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `payload.email_id` | 当前邮件 id | n8n | Attachment | 是 | 主关联键 |
| `payload.classifier_summary` | 分类摘要 | n8n | Attachment | 否 | 辅助理解附件上下文 |
| `payload.attachments[].attachment_id` | 当前附件 id | n8n | Attachment | 是 | 附件级关联键 |
| `payload.attachments[].name` | 文件名 | n8n | Attachment | 是 | 辅助解析 |
| `payload.attachments[].content_type` | MIME 类型 | n8n | Attachment | 否 | 解析路由 |
| `payload.attachments[].size_bytes` | 附件大小 | n8n | Attachment | 否 | 策略控制 |

### 8.2 输出字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `results[].attachment_id` | 结果对应哪个附件 | Attachment | n8n、Schedule | 是 | 不能丢 |
| `results[].doc_type` | 文档类型判断 | Attachment | 调试、Schedule | 否 | 例如 `cfp` |
| `results[].relevance_score` | 解析相关度 | Attachment | 调试、过滤 | 否 | 建议 `0~1` |
| `results[].topics` | 主题列表 | Attachment | 调试、Schedule | 是 | JSON 数组 |
| `results[].named_entities` | 附件抽出的实体 | Attachment | 调试、Schedule | 是 | JSON 数组 |
| `results[].time_expressions` | 附件抽出的时间 | Attachment | Schedule | 是 | 很重要 |
| `results[].extracted_text` | 抽取文本 | Attachment | 调试、追责 | 否 | 可较长 |

### 8.3 `skipped` 语义

无附件时，Attachment 输出的关键不是“空数组”，而是显式状态：

- `status = skipped`
- `reason = no_attachments`

这表示该分支已经合法结束，而不是漏跑。

## 9. RelationshipGraph 阶段字段

### 9.1 输入 payload 重点字段

| 字段名 | 字段含义 | 谁提供 | 谁消费 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `payload.email.sender_email` | 发件人邮箱 | n8n | RelationshipGraph | 是 | 联系人主来源 |
| `payload.email.sender_name` | 发件人显示名 | n8n | RelationshipGraph | 否 | 可为空 |
| `payload.email.received_at_utc` | 邮件观察时间 | n8n | RelationshipGraph | 是 | 可作为 `observed_at_utc` 来源 |
| `payload.classifier.sender_role` | 发件人角色推断 | n8n | RelationshipGraph | 是 | 辅助联系人角色 |
| `payload.classifier.summary` | 分类摘要 | n8n | RelationshipGraph | 否 | 辅助判断关系信号 |

### 9.2 输出字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `person_email` | 被观察到的联系人邮箱 | RelationshipGraph | Neo4j | 是 | `Person` 节点主来源 |
| `person_name` | 联系人显示名 | RelationshipGraph | Neo4j、展示 | 否 | 可更新 |
| `person_role` | 联系人角色 | RelationshipGraph | Neo4j、Response | 否 | 如教授、队友 |
| `organisation_name` | 组织名称 | RelationshipGraph | Neo4j | 否 | 用于生成组织节点 |
| `organisation_domain` | 组织域名 | RelationshipGraph | Neo4j | 否 | 更适合作为组织主键 |
| `signal_type` | 关系信号类型 | RelationshipGraph | Neo4j 聚合逻辑 | 是 | 如 `email_from` |
| `signal_weight` | 这次观察的强度 | RelationshipGraph | Neo4j 聚合逻辑 | 是 | 不是最终权重 |
| `observed_at_utc` | 观察发生时间 | RelationshipGraph | Neo4j、调试 | 是 | 用于时间序列重算 |

特别说明：

- 一次运行可以产出多条观察
- `relationship_observations` 存的是事实，不是聚合结论

## 10. Schedule 阶段字段

### 10.1 输入 payload 重点字段

| 字段名 | 字段含义 | 谁提供 | 谁消费 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `payload.user.timezone` | 用户时区 | n8n | Schedule | 是 | 防止时间转换歧义 |
| `payload.classifier.summary` | 邮件摘要 | n8n | Schedule | 是 | 语义理解输入 |
| `payload.classifier.time_expressions` | 邮件时间表达式 | n8n | Schedule | 是 | 核心输入 |
| `payload.attachment_results[]` | 附件时间补充 | n8n | Schedule | 否 | 没有附件时可为空 |
| `payload.relationship_snapshot` | 关系摘要 | n8n | Schedule | 否 | 可辅助优先级判断 |

### 10.2 输出字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `candidate_id` | 事件候选主键 | Schedule | Outlook、Neo4j、Response | 是 | 稳定主键 |
| `source` | 候选来源 | Schedule | 调试 | 是 | `email/attachment/both` |
| `title` | 事件标题 | Schedule | Outlook、UI | 是 | 展示字段 |
| `start_time_utc` | 开始时间 UTC | Schedule | Outlook | 是 | 核心字段 |
| `end_time_utc` | 结束时间 UTC | Schedule | Outlook | 是 | 必须大于开始时间 |
| `source_timezone` | 原始解析时区 | Schedule | 调试、展示 | 是 | 防止时区歧义 |
| `confidence` | 可信度 | Schedule | 排序、过滤 | 是 | 建议 `0~1` |
| `conflict_score` | 冲突风险 | Schedule | 排序、过滤 | 是 | 越高越可能冲突 |
| `recommendation_rank` | 推荐顺位 | Schedule | UI、Response | 否 | 用于当前版本排序 |
| `action` | 建议动作 | Schedule | n8n、Outlook | 是 | 决定是否建事件 |
| `show_as` | 日历占用状态 | Schedule | Outlook | 是 | 常见为 `tentative` |
| `transaction_id` | Outlook 幂等键 | Schedule / n8n | Outlook | 是 | 必须稳定 |
| `outlook_event_id` | Outlook 返回的事件 id | 写入层 | 查询、同步 | 否 | 外部系统 id |
| `write_status` | 写 Outlook 的状态 | n8n / 写入层 | 调试、重试 | 是 | `pending/written/failed` |

## 11. Response 阶段字段

### 11.1 输入 payload 重点字段

| 字段名 | 字段含义 | 谁提供 | 谁消费 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `payload.classifier.category` | 分类结果 | n8n | Response | 是 | 决定语气和策略 |
| `payload.classifier.summary` | 摘要 | n8n | Response | 是 | 核心理解输入 |
| `payload.attachment_status` | 附件分支终态 | n8n | Response | 是 | 用于区分 `success` 与 `skipped` |
| `payload.relationship_snapshot.sender_email` | 主要联系人邮箱 | n8n | Response | 否 | 若有关系信息则带 |
| `payload.relationship_snapshot.relationship_weight` | 关系强度摘要 | n8n | Response | 否 | 是摘要，不是事实表 |
| `payload.top_schedule_candidate.candidate_id` | 首选候选 id | n8n | Response | 否 | 用于引用候选 |
| `payload.top_schedule_candidate.action` | 首选候选动作 | n8n | Response | 否 | 用于判断是否会建事件 |
| `payload.user_preferences.reply_style_preference` | 用户偏好回复语气 | n8n | Response | 否 | 个性化输入 |

### 11.2 输出字段

| 字段名 | 字段含义 | 谁写入 | 谁读取 | 必填 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `reply_required` | 是否建议回复 | Response | UI、提醒逻辑 | 是 | 最核心决策字段 |
| `decision_reason` | 为什么建议或不建议回复 | Response | UI、调试 | 否 | 统一命名，不能漂移 |
| `tone_templates` | 多种语气模板集合 | Response | UI、后续发送层 | 是 | 统一放一个 JSON |

特别说明：

- `Response` 不能在上游三条分支状态不明确时执行
- `attachment_status` 必须显式传入，不能靠猜

## 12. 常见混淆字段对照

| 容易混淆的字段 | 正确理解 | 错误理解 |
| --- | --- | --- |
| `email_id` vs `graph_message_id` | `email_id` 是内部主键 | `graph_message_id` 也能当主键 |
| `attachment_id` vs `graph_attachment_id` | `attachment_id` 是内部附件主键 | 两者可以混用 |
| `run_id` vs `trace_id` | `run_id` 是单次执行，`trace_id` 是整轮链路 | 两者随便用一个就行 |
| `signal_weight` vs `relationship_weight` | 前者是单次观察强度，后者通常是摘要或聚合结果 | 两者是同一个概念 |
| `candidate_id` vs `transaction_id` | 前者是内部候选 id，后者是 Outlook 幂等键 | 二者可随机生成 |
| `skipped` vs “没写记录” | `skipped` 表示该分支已合法结束 | 没有记录就等于 skipped |
| `is_current` vs “唯一记录” | `is_current` 表示当前快照 | 表里只会有这一条历史 |

## 13. 联调时最容易传错的字段

优先检查这些：

1. `attachment_status` 是否真的显式传给了 Response
2. `transaction_id` 是否稳定，而不是每次运行都重新生成
3. `run_id` 是否在重试时换新
4. `email_id` 是否始终贯穿全链路
5. `time_expressions` 是否既可能来自邮件正文，也可能来自附件
6. `relationship_observations` 是否被错误压缩成单条记录
7. `decision_reason` 是否保持统一命名，没有漂移成其他名字

