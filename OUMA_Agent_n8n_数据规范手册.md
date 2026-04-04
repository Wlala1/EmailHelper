# OUMA Agent 与 n8n 数据规范手册

版本：v1.0  
更新日期：2026-03-12  
主规范来源：`OUMA-v2.md`

## 1. 文档定位

本文档面向以下开发同事：

- Agent 输入输出开发同事
- n8n 编排与落库开发同事

本文档不是新的主规范，而是把 `OUMA-v2.md` 中与 Agent 和 n8n 开发直接相关的要求整理成执行手册。



## 2. 阅读路径

建议按以下顺序阅读：

1. 先看本手册第 3 到第 7 章，理解流程、状态和传输规则
2. 再看第 8 章的常见错误与禁止事项
3. 联调前用第 9 章检查清单逐项核对
4. 遇到字段语义不清，再看 `OUMA_字段语义与数据字典手册.md`
5. 需要追溯源规范时，再回到 `OUMA-v2.md`

## 3. 文档关系

相关文档的职责如下：

- `OUMA_Agent_n8n_数据规范手册.md`：执行手册，解决“应该怎么接”
- `OUMA_字段语义与数据字典手册.md`：字段手册，解决“字段是什么意思”

## 4. 变更记录

### v1.0

- 建立面向 Agent 与 n8n 的单独执行手册
- 固化统一 payload 外层结构
- 固化 `Attachment / RelationshipGraph / Schedule -> Response` 的触发门槛
- 固化 `skipped`、`is_current`、`transaction_id` 等关键规则
- 固化 Outlook 与 Neo4j 的写入边界

## 5. 端到端处理链

OUMA 固定采用以下处理链：

```text
邮件入库
-> Classifier Agent
-> Attachment Agent / RelationshipGraph Agent / Schedule Agent
-> Response Agent
-> Outlook / Neo4j side effects
```

顺序要求如下：

1. 邮件先落 `users / emails / email_recipients / attachments`
2. 生成本轮 `trace_id`
3. 触发 `Classifier Agent`
4. `Classifier` 成功后，并行触发 `Attachment / RelationshipGraph / Schedule`
5. 当 `Attachment / RelationshipGraph / Schedule` 三个分支都进入终态，且终态属于 `success` 或 `skipped` 时，才允许触发 `Response Agent`
6. `Schedule` 若给出 `create_tentative_event`，才允许写 Outlook
7. 当前版本关系事实和事件候选，按规则同步到 Neo4j

## 6. 全局必须遵守的传输规则

### 6.1 统一标识

所有 Agent 和 n8n 都必须使用以下统一标识：

- `user_id`
- `email_id`
- `attachment_id`
- `run_id`
- `trace_id`
- `candidate_id`
- `transaction_id`

必须遵守：

- `email_id` 是 OUMA 内部邮件主键，所有 Agent 用它串联业务对象
- `run_id` 表示一次唯一执行，同一 Agent 重跑必须换新值
- `trace_id` 表示一轮完整流水线
- `transaction_id` 是 Outlook 幂等键，必须稳定，不允许随机生成

### 6.2 统一 payload 外层结构

所有跨组件 payload 必须显式包含以下字段：

- `schema_version`
- `trace_id`
- `run_id`
- `email_id`
- `user_id`
- `agent_name`
- `produced_at_utc`
- `payload`

固定要求：

- 所有时间统一使用 UTC ISO 8601
- 所有内部主键统一使用 UUID 字符串
- 字段命名统一使用小写下划线
- 不允许同一语义出现多个字段名

### 6.3 `schema_version`

当前统一使用：

```text
ouma.v2
```

只要 payload 结构发生破坏性调整，必须先更新 `OUMA-v2.md`，再更新代码和本手册。

### 6.4 `is_current` 规则

OUMA 的结果表保留历史版本，同时保留当前快照。

所有实现都必须遵守：

1. 先写历史结果
2. 再把旧版本切成 `is_current = false`
3. 再把新版本写成 `is_current = true`
4. 不允许出现两个当前版本同时存在

补充要求：

- `classifier_results` 以 `email_id` 维护当前版本
- `attachment_results` 以 `attachment_id` 维护当前版本
- `reply_suggestions` 以 `email_id` 维护当前版本

### 6.5 `skipped` 不是“没写”

当某分支逻辑上被跳过时，必须显式写出：

- `agent_runs.status = skipped`
- 必要时在 `output_payload` 中写明 `reason`

典型场景：

- 邮件没有附件时，`Attachment Agent` 必须有一条 `skipped` 运行
- 此时不写 `attachment_results`
- 但下游和审计系统必须知道该分支已经进入终态

### 6.6 `upstream_run_id` 不是完整门槛表达

`upstream_run_id` 只表示直接串行依赖，不表示完整分支门槛。

因此：

- `Classifier` 之后直接触发的分支可以挂 `run_classifier_xxx`
- `Response` 不能只靠一个 `upstream_run_id` 判定是否可执行
- `Response` 是否可执行，必须由 n8n 检查三条分支终态

## 7. 各阶段执行要求

### 7.1 阶段一：邮件入库 -> Classifier Agent

Classifier 只读取邮件基础事实，不读取其他 Agent 结果。

最小输入必须至少包含：

- `email.subject`
- `email.sender_name`
- `email.sender_email`
- `email.body_content`
- `email.body_preview`
- `email.received_at_utc`
- `email.has_attachments`

最小输出必须至少包含：

- `category`
- `urgency_score`
- `summary`
- `sender_role`
- `named_entities`
- `time_expressions`

n8n 写库顺序：

1. 写 `agent_runs(status = started)`
2. 调用 Classifier
3. 写 `classifier_results`
4. 切旧的 `is_current = false`
5. 写新的 `is_current = true`
6. 更新 `agent_runs.status = success`

### 7.2 阶段二：Classifier -> Attachment Agent

Attachment 读取：

- `emails`
- `attachments`
- 当前 `classifier_results`

有附件时输出至少包含：

- `results[].attachment_id`
- `results[].doc_type`
- `results[].relevance_score`
- `results[].topics`
- `results[].named_entities`
- `results[].time_expressions`
- `results[].extracted_text`

无附件时输出至少包含：

- `status = skipped`
- `reason = no_attachments`

n8n 写库规则：

- 有附件时写 `attachment_results`
- 无附件时不写 `attachment_results`
- 但无论是否有附件，都必须有对应 `agent_runs`
- 无附件时该运行必须为 `status = skipped`

### 7.3 阶段三：Classifier -> RelationshipGraph Agent

RelationshipGraph 读取：

- 基础邮件事实
- 收件人信息
- 当前 `classifier_results`

输出至少包含：

- `observations[].person_email`
- `observations[].person_name`
- `observations[].person_role`
- `observations[].organisation_name`
- `observations[].organisation_domain`
- `observations[].signal_type`
- `observations[].signal_weight`
- `observations[].observed_at_utc`

强约束：

- 一次运行可以生成多条 `observations`
- 不允许压缩成单条“最终关系权重”
- Agent 不允许绕过 PostgreSQL 直接写 Neo4j

### 7.4 阶段四：Classifier + Attachment -> Schedule Agent

Schedule 读取：

- 基础邮件事实
- 当前 `classifier_results`
- 可选：当前 `attachment_results`
- 可选：关系快照或关系摘要

输出至少包含：

- `candidates[].candidate_id`
- `candidates[].source`
- `candidates[].title`
- `candidates[].start_time_utc`
- `candidates[].end_time_utc`
- `candidates[].source_timezone`
- `candidates[].confidence`
- `candidates[].conflict_score`
- `candidates[].recommendation_rank`
- `candidates[].action`
- `candidates[].show_as`
- `candidates[].transaction_id`

强约束：

- `candidate_id` 必须稳定
- `transaction_id` 必须稳定，推荐 `ouma_sched_<candidate_id>`
- `action` 决定是否允许写 Outlook
- Schedule 允许输出多条候选

### 7.5 阶段五：Classifier + Attachment + Relationship + Schedule -> Response Agent

Response 只能在以下条件同时满足后触发：

- `Attachment / RelationshipGraph / Schedule` 三个分支都进入终态
- 每个分支的终态都属于 `success` 或 `skipped`

Response 读取：

- 当前 `classifier_results`
- `attachment_status`
- 当前 `relationship_snapshot` 或观察摘要
- 当前优先级最高的 `top_schedule_candidate`

最小输入至少包含：

- `classifier.category`
- `classifier.summary`
- `attachment_status`
- `relationship_snapshot.sender_email`
- `top_schedule_candidate.candidate_id`
- `top_schedule_candidate.action`

最小输出至少包含：

- `reply_required`
- `decision_reason`
- `tone_templates`

禁止事项：

- 禁止因为某一分支先完成就提前触发 `Response`
- 禁止把“没有附件”误判成“Attachment 分支还没执行”

## 8. n8n 编排与状态推进规范

### 8.1 执行前检查

每个 Agent 节点执行前都必须检查：

1. 当前 `trace_id + email_id + agent_name` 是否已有成功记录
2. 如果已有成功记录，默认跳过重复执行
3. 如果是显式重跑，必须新建 `run_id`

### 8.2 执行后写库顺序

统一顺序如下：

1. 写 `agent_runs.status = started`
2. 调用 Agent
3. 写结果表
4. 切旧的 `is_current = false`
5. 写新的 `is_current = true`
6. 更新 `agent_runs.status = success`

失败时必须：

1. 更新 `agent_runs.status = failed`
2. 写 `error_code`
3. 写 `error_message`
4. 不得留下半写入的当前版本

### 8.3 成功、失败、跳过的判定

- `success`：Agent 已完成，结果已按规范写库
- `failed`：Agent 或写库过程失败，必须带错误信息
- `skipped`：该分支按业务规则不需要执行，但必须有运行记录

### 8.4 重试规则

- 失败重试必须产生新的 `run_id`
- 历史失败记录保留，不能覆盖
- 成功后由新结果切换当前快照
- 不允许通过更新旧 run 的状态伪装成一次成功重试

## 9. Side Effects 边界

### 9.1 Neo4j

Neo4j 是从 PostgreSQL 派生出来的查询层，不是事实源。

因此：

- 只能从 PostgreSQL 当前版本数据派生写入 Neo4j
- `relationship_observations` 是关系事实来源
- `schedule_candidates` 可以作为事件节点来源
- Neo4j 出错可以重建，PostgreSQL 事实不能丢

### 9.2 Outlook

只有当 `schedule_candidates.action = create_tentative_event` 时，才允许调 Outlook API。

调用 Outlook 时必须带：

- `transaction_id`
- `title`
- `start_time_utc`
- `end_time_utc`
- `show_as = tentative`

调用后必须回写：

- `outlook_event_id`
- `outlook_weblink`
- `write_status`
- `last_write_error`（失败时）

## 10. 常见错误与禁止事项

以下做法一律视为不符合 OUMA 规范：

- 用 `graph_message_id` 代替 `email_id` 做主关联
- 重跑时复用旧 `run_id`
- 无附件时不写 `Attachment` 的 `skipped` 运行
- 让 `Response` 只依赖 `Classifier` 完成状态
- 把 RelationshipGraph 的多条观察压成单条记录
- 随机生成 `transaction_id`
- 写入新结果后不切旧的 `is_current`
- 失败后仍留下新的当前快照
- 在 n8n 中临时发明字段名

## 11. 联调检查清单

联调前至少确认以下问题：

1. 输入 payload 是否都带 `schema_version / trace_id / run_id / email_id / user_id / agent_name`
2. 输出 payload 是否只使用规范中的字段名
3. 每个 Agent 的成功、失败、跳过是否都能映射到 `agent_runs.status`
4. `Attachment` 无附件时是否显式返回 `skipped`
5. `Response` 是否在三条分支都进入 `success` 或 `skipped` 后才执行
6. `transaction_id` 是否稳定可复用
7. `is_current` 是否按“先旧后新”顺序切换
8. Neo4j 与 Outlook 是否都通过 PostgreSQL 当前结果驱动

### agent输出规范
## 12. Agent 数据传递规范

## 12.1 通用封装格式

所有 Agent 的输入和输出，都必须遵守统一包裹结构：

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_xxx",
  "email_id": "email_xxx",
  "user_id": "user_xxx",
  "agent_name": "classifier",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {}
}
```

统一要求：

- 所有时间一律使用 UTC ISO 8601
- 所有内部主键统一使用 UUID 字符串
- 字段命名统一使用小写下划线
- 不允许同一语义出现多个名字

例如：

- 只能用 `decision_reason`
- 不允许一会儿叫 `decision_reason`，一会儿叫 `reply_decision_reason`

## 12.2 阶段一：邮件入库 -> Classifier Agent

### 输入字段

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_classifier_xxx",
  "email_id": "uuid",
  "user_id": "uuid",
  "agent_name": "classifier",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {
    "user": {
      "user_id": "uuid",
      "primary_email": "student@u.nus.edu",
      "display_name": "某同学",
      "timezone": "Asia/Singapore"
    },
    "email": {
      "email_id": "uuid",
      "graph_message_id": "xxx",
      "graph_immutable_id": "xxx",
      "internet_message_id": "<xxx@example.com>",
      "conversation_id": "xxx",
      "sender_name": "Prof Lim",
      "sender_email": "prof.lim@nus.edu.sg",
      "subject": "Call for Papers",
      "body_content_type": "html",
      "body_content": "...",
      "body_preview": "...",
      "received_at_utc": "2026-03-12T01:00:00Z",
      "has_attachments": true
    },
    "attachments": [
      {
        "attachment_id": "uuid",
        "name": "cfp.pdf",
        "content_type": "application/pdf",
        "size_bytes": 102400
      }
    ]
  }
}
```

### 输出字段

```json
{
  "category": "AcademicConferences",
  "urgency_score": 0.87,
  "summary": "这是一封关于学术会议征稿的邮件，正文和附件中包含投稿截止日期。",
  "sender_role": "Professor",
  "named_entities": [],
  "time_expressions": []
}
```

### 落库要求

- 写 `agent_runs`
- 写 `classifier_results`
- 将该结果设置为当前版本 `is_current = true`
- 同一封邮件旧的当前分类结果改为 `is_current = false`

## 12.3 阶段二：Classifier -> Attachment Agent

### 触发条件

只有当 `emails.has_attachments = true` 时才触发。

### 输入字段

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_attachment_xxx",
  "email_id": "uuid",
  "user_id": "uuid",
  "agent_name": "attachment",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {
    "email_id": "uuid",
    "classifier_summary": "这是一封学术会议征稿邮件。",
    "attachments": [
      {
        "attachment_id": "uuid",
        "graph_attachment_id": "xxx",
        "name": "cfp.pdf",
        "content_type": "application/pdf",
        "size_bytes": 102400
      }
    ]
  }
}
```

### 输出字段

```json
{
  "results": [
    {
      "attachment_id": "uuid",
      "doc_type": "cfp",
      "relevance_score": 0.92,
      "topics": ["数据库", "图学习"],
      "named_entities": [],
      "time_expressions": [],
      "extracted_text": "......"
    }
  ]
}
```

### 落库要求

- 先写 `agent_runs`
- 再写 `attachment_results`
- 结果必须以 `attachment_id` 为粒度
- 不允许只返回附件文件名，不返回内部主键

## 12.4 阶段三：Classifier -> RelationshipGraph Agent

### 输入字段

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_relationship_xxx",
  "email_id": "uuid",
  "user_id": "uuid",
  "agent_name": "relationship_graph",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {
    "email": {
      "email_id": "uuid",
      "sender_email": "prof.lim@nus.edu.sg",
      "sender_name": "Prof Lim",
      "received_at_utc": "2026-03-12T01:00:00Z"
    },
    "classifier": {
      "sender_role": "Professor",
      "summary": "......"
    }
  }
}
```

### 输出字段

```json
{
  "observation": {
    "person_email": "prof.lim@nus.edu.sg",
    "person_name": "Prof Lim",
    "person_role": "Professor",
    "organisation_name": "National University of Singapore",
    "organisation_domain": "nus.edu.sg",
    "signal_type": "email_from",
    "signal_weight": 1.0,
    "observed_at_utc": "2026-03-12T01:00:00Z"
  }
}
```

### 落库要求

- 先写 `agent_runs`
- 再写 `relationship_observations`
- 再由图同步任务把 observation 写入 Neo4j
- 禁止 Agent 直接跨过 PostgreSQL 写 Neo4j

## 12.5 阶段四：Classifier + Attachment -> Schedule Agent

### 输入字段

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_schedule_xxx",
  "email_id": "uuid",
  "user_id": "uuid",
  "agent_name": "schedule",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {
    "user": {
      "user_id": "uuid",
      "timezone": "Asia/Singapore"
    },
    "classifier": {
      "summary": "......",
      "time_expressions": []
    },
    "attachment_results": [
      {
        "attachment_id": "uuid",
        "time_expressions": []
      }
    ],
    "relationship_snapshot": {
      "sender_email": "prof.lim@nus.edu.sg",
      "relationship_weight": 0.84
    }
  }
}
```

### 输出字段

```json
{
  "candidates": [
    {
      "candidate_id": "uuid",
      "source": "attachment",
      "title": "论文投稿截止提醒",
      "start_time_utc": "2026-04-15T00:00:00Z",
      "end_time_utc": "2026-04-15T01:00:00Z",
      "source_timezone": "Asia/Singapore",
      "is_all_day": false,
      "location": null,
      "attendees": [],
      "confidence": 0.78,
      "conflict_score": 0.10,
      "recommendation_rank": 1,
      "action": "create_tentative_event",
      "show_as": "tentative",
      "transaction_id": "ouma_sched_uuid"
    }
  ]
}
```

### 落库要求

- 先写 `agent_runs`
- 再写 `schedule_candidates`
- 所有候选必须自带 `candidate_id`
- `transaction_id` 必须由内部生成，不能临时随机拼

## 12.6 阶段五：Classifier + Attachment + Relationship + Schedule -> Response Agent

### 输入字段

```json
{
  "schema_version": "ouma.v2",
  "trace_id": "tr_xxx",
  "run_id": "run_response_xxx",
  "email_id": "uuid",
  "user_id": "uuid",
  "agent_name": "response",
  "produced_at_utc": "2026-03-12T10:00:00Z",
  "payload": {
    "classifier": {
      "summary": "......",
      "category": "TeamsMeetings"
    },
    "attachment_status": "success_or_skipped",
    "relationship_snapshot": {
      "sender_email": "alice.tan@u.nus.edu",
      "relationship_weight": 0.72,
      "sender_role": "Teammate"
    },
    "top_schedule_candidate": {
      "candidate_id": "uuid",
      "title": "项目周会",
      "action": "create_tentative_event"
    },
    "user_preferences": {
      "reply_style_preference": "professional"
    }
  }
}
```

### 输出字段

```json
{
  "reply_required": true,
  "decision_reason": "发件人为项目成员，邮件涉及明确会议时间，建议生成回复草稿。",
  "tone_templates": {
    "professional": "......",
    "casual": "......",
    "colloquial": "......"
  }
}
```

### 落库要求

- 先写 `agent_runs`
- 再写 `reply_suggestions`
- 回复模板统一放入 `tone_templates`
- 字段名统一叫 `decision_reason`

---