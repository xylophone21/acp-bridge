# 实现计划：会话管理重构

## 概述

按依赖关系从底层模块到上层逻辑逐步重构：先改数据模型和配置，再改飞书事件层，然后改会话管理核心，最后改路由和指令处理，最终清理遗留代码和完善文档。

## 任务

- [x] 1. 重构配置模块（单 agent 模式）
  - [x] 1.1 修改 `src/config.py`：`Config.agents: list[AgentConfig]` 改为 `Config.agent: AgentConfig`；`BridgeConfig` 新增 `max_sessions: int = 10` 和 `session_ttl_minutes: int = 60`；`Config.load()` 解析 `[agent]` 单表替代 `[[agents]]` 数组表；`_validate()` 校验有且仅有一个 agent 配置；`Config.init()` 生成的样例配置使用新格式（`[agent]` 单表、包含 `max_sessions` 和 `session_ttl_minutes`）
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 1.2 更新 `tests/test_config.py`：所有测试适配 `config.agent`（单数）；新增测试验证 `max_sessions` 和 `session_ttl_minutes` 的默认值；新增测试验证缺少 `[agent]` 时校验失败；新增测试验证 `init` 生成的样例配置格式正确
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [x] 1.3 编写配置缺省值属性测试
    - **Property 13: 配置缺省值**
    - 使用 Hypothesis 生成随机配置字典（随机缺少 `max_sessions` / `session_ttl_minutes` 字段），验证缺省值始终为 10 和 60
    - **验证: 需求 5.5**

- [x] 2. 扩展飞书事件模型
  - [x] 2.1 修改 `src/feishu.py`：`FeishuEvent` 新增 `root_id: Optional[str] = None`、`is_mention_bot: bool = False`、`sender_name: str = ""` 字段；`_on_message_receive()` 从事件数据提取 `root_id`、`mentions`（判断是否 @机器人）和 `sender` 信息；新增 `FeishuConnection.add_reaction(message_id, emoji_type)` 方法，调用飞书 Reaction API
    - _需求: 2.1, 1.1, 1.2, 3.6_

  - [x] 2.2 更新 `tests/test_feishu.py`：新增 `FeishuEvent` 含 `root_id`、`is_mention_bot`、`sender_name` 字段的测试
    - _需求: 2.1_

  - [x] 2.3 编写 Root_Message ID 提取属性测试
    - **Property 6: Root_Message ID 提取的正确性**
    - 使用 Hypothesis 生成随机 FeishuEvent（有/无 root_id），验证 `root_id or message_id` 的提取逻辑
    - **验证: 需求 2.1**

- [x] 3. 检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请询问用户。

- [x] 4. 重构会话管理核心（SessionManager）
  - [x] 4.1 修改 `src/session.py`：`SessionState` 按设计文档重构字段（移除 `agent_name`、`workspace`、`auto_approve`，`initial_ts` 重命名为 `trigger_message_id`，新增 `summary`、`last_active`、`last_bot_message_id`、`message_buffer`）；`SessionManager._sessions` 改用 `collections.OrderedDict`；新增 `create_session_auto(root_message_id, channel, trigger_text)` 方法（自动使用唯一 agent 配置，触发 LRU 淘汰检查，记录 summary 为前 20 字符）；新增 `touch(root_message_id)` 方法；新增 `evict_lru()` 方法（从 OrderedDict 头部找最久未使用的非 busy Session）；新增 `evict_ttl_expired()` 方法；新增 `buffer_message(root_message_id, sender, text)` 方法；新增 `flush_buffer(root_message_id)` 方法（按时间顺序合并，格式 `[sender]: text`）；新增 `all_busy()` 方法；移除旧的 `create_session()` 方法
    - _需求: 1.1, 1.5, 1.6, 2.6, 2.7, 3.1, 3.2, 3.3, 3.5_

  - [x] 4.2 重写 `tests/test_session.py`：所有测试适配新的 `SessionState` 字段和 `SessionManager` 接口；新增 `create_session_auto` 的单元测试（验证 summary 截断、索引 key、LRU 淘汰触发）；新增 `touch`、`evict_lru`、`evict_ttl_expired`、`buffer_message`、`flush_buffer`、`all_busy` 的单元测试
    - _需求: 1.1, 1.5, 1.6, 2.6, 2.7, 3.1, 3.2, 3.3, 3.5_

  - [x] 4.3 编写 Session 摘要截断属性测试
    - **Property 7: Session 摘要为触发消息前 20 字符**
    - 使用 Hypothesis 生成随机 Unicode 字符串，验证 summary 长度 ≤ 20 且为原文本前缀
    - **验证: 需求 1.6**

  - [x] 4.4 编写消息缓存合并属性测试
    - **Property 8: 消息缓存按时间顺序合并并保留发送者**
    - 使用 Hypothesis 生成随机消息列表（含 sender 和 timestamp），验证合并结果按时间升序且包含所有 sender
    - **验证: 需求 2.6, 2.7**

  - [x] 4.5 编写 LRU 淘汰属性测试
    - **Property 9: LRU 淘汰选择最久未使用的空闲 Session**
    - 使用 Hypothesis 生成随机 Session 集合（不同 last_active 和 busy 状态），验证淘汰的是非 busy 中 last_active 最小的
    - **验证: 需求 3.2**

  - [x] 4.6 编写 Touch 时间戳属性测试
    - **Property 10: Touch 更新最近使用时间戳**
    - 使用 Hypothesis 生成随机 Session，验证 touch 后 last_active ≥ 调用前的值
    - **验证: 需求 3.3**

  - [x] 4.7 编写 TTL 淘汰属性测试
    - **Property 11: TTL 淘汰移除超时 Session**
    - 使用 Hypothesis 生成随机 Session 集合和随机 TTL 值，验证仅淘汰超时的 Session
    - **验证: 需求 4.3**

- [x] 5. 检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请询问用户。

- [x] 6. 重构事件路由和消息处理
  - [x] 6.1 重写 `src/handler.py`：移除 `handler_shell` 导入和 `!` 指令路由；路由逻辑改为基于 `root_message_id = event.root_id or event.message_id` 查找 Session；根据 Session 存在与否 + `event.is_mention_bot` + 消息类型（# 指令 / 普通文本）进行分发（详见设计文档路由流程图）；移除 `pending_permissions` 的 `thread_key` 逻辑，改用 `root_message_id`
    - _需求: 1.1, 1.2, 1.5, 1.8, 1.9, 1.10, 1.11, 2.1, 2.2, 2.3, 2.4_

  - [x] 6.2 重写 `src/handler_message.py`：支持自动创建 Session（调用 `session_manager.create_session_auto`）；Session busy 时调用 `session_manager.buffer_message()` 静默缓存消息（不发送提示）；`_do_prompt` 完成后调用 `session_manager.flush_buffer()` 获取合并文本，若有则作为新 prompt 发送；调用 `session_manager.touch()` 更新活跃时间；记录 `last_bot_message_id`
    - _需求: 1.1, 1.5, 1.7, 2.3, 2.6, 2.7, 3.3, 3.4_

  - [x] 6.3 修改 `src/handler_command.py`：移除 `#new` 和 `#agents` 指令处理逻辑；增强 `#sessions` 输出（显示 summary、状态、最近使用时间，不显示 agent 名称）；`#session` 输出适配新字段（移除 agent_name、workspace 显示）；所有指令适配 `root_message_id` 参数传递；更新 `HELP_MESSAGE` 移除 `#new`、`#agents`、`!` 相关说明
    - _需求: 1.3, 1.4, 1.5, 6.1, 6.2_

  - [x] 6.4 更新 `tests/test_handler.py`：适配新的路由逻辑（基于 `root_message_id`、`is_mention_bot`）；移除 shell 指令测试；新增测试：无 Session + 未 @机器人 → 忽略；无 Session + @机器人 + 普通文本 → 自动创建 Session；有 Session + 普通文本 → 路由到已有 Session；有 Session + # 指令 → 无需 @机器人 即可处理
    - _需求: 1.1, 1.2, 1.8, 1.9, 1.10, 1.11_

  - [x] 6.5 编写自动创建 Session 属性测试
    - **Property 1: 自动创建 Session 以 Root_Message 为索引**
    - 使用 Hypothesis 生成随机消息（mention=True, 非指令文本, 无已有 Session），验证创建的 Session 索引 key 等于 root_message_id
    - **验证: 需求 1.1, 1.5, 2.3**

  - [x] 6.6 编写消息路由到已有 Session 属性测试
    - **Property 2: 消息路由到已有 Session**
    - 使用 Hypothesis 生成随机消息 + 已有 Session 的 root_message_id，验证路由到已有 Session 且不创建新 Session
    - **验证: 需求 1.2, 1.8, 2.2**

  - [x] 6.7 编写忽略无关消息属性测试
    - **Property 3: 忽略无关消息**
    - 使用 Hypothesis 生成随机消息（mention=False, 无 Session），验证消息被忽略
    - **验证: 需求 1.9, 2.4**

  - [x] 6.8 编写 Session 内指令属性测试
    - **Property 4: Session 内指令无需 @机器人**
    - 使用 Hypothesis 生成随机 # 指令 + 已有 Session，验证无论是否 @机器人都能处理
    - **验证: 需求 1.10**

  - [x] 6.9 编写 Session 外指令属性测试
    - **Property 5: Session 外指令需要 @机器人**
    - 使用 Hypothesis 生成随机 # 指令 + 无 Session，验证仅 @机器人 时触发处理
    - **验证: 需求 1.11**

- [x] 7. 检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请询问用户。

- [x] 8. 淘汰机制集成与 Bridge 适配
  - [x] 8.1 修改 `src/bridge.py`：适配单 agent 配置（`config.agent` 替代 `config.agents`）；启动 TTL 定时检查 `asyncio.create_task`（每 60 秒调用 `session_manager.evict_ttl_expired()`）；淘汰时调用 `agent_manager.end_session()` 终止进程，调用 `feishu.add_reaction()` 添加 DONE 表情；`_flush_buffers` 等辅助函数适配新的 Session 查找方式
    - _需求: 3.5, 3.6, 4.2, 4.3, 4.4, 4.5_

  - [x] 8.2 编写淘汰清理属性测试
    - **Property 12: 淘汰时终止进程并添加 Reaction**
    - 使用 mock 验证淘汰 Session 时调用了 `agent_manager.end_session()` 和 `feishu.add_reaction()`
    - **验证: 需求 3.5, 3.6, 4.4, 4.5**

- [x] 9. 删除遗留代码与清理命名
  - [x] 9.1 删除 `src/handler_shell.py` 文件；删除 `src/handler_permission.py` 中与 shell 相关的逻辑（如有）；确保 `src/handler.py` 不再导入 `handler_shell`
    - _需求: 1.5_

  - [x] 9.2 全局搜索并清理所有源码中的 "juan" 字样（变量名、注释、字符串）；确保 `pyproject.toml` 中项目名称和描述不包含 "juan"
    - _需求: 7.2, 7.3_

  - [x] 9.3 更新 `tests/test_handler_command.py`：移除 `#new`、`#agents` 相关测试；新增 `#sessions` 增强输出的测试（验证显示 summary、状态、最近使用时间，不显示 agent 名称）
    - _需求: 1.3, 1.4, 6.1, 6.2_

  - [x] 9.4 编写 #sessions 输出格式属性测试
    - **Property 14: #sessions 输出格式**
    - 使用 Hypothesis 生成随机 Session 集合，验证输出包含 summary、状态、最近使用时间，不包含 agent 名称
    - **验证: 需求 6.1, 6.2**

- [x] 10. 完善 README 文档
  - [x] 10.1 重写 `README.md`：包含项目简介（ACP Bridge 用途和核心功能）；完整安装步骤（Python 版本、依赖安装、配置文件生成、启动命令）；飞书应用创建和配置指引（含"接收群聊中所有消息"权限说明）；可用指令列表（不含 #new、#agents、! shell）；MIT 协议声明，注明源自 Juan 项目（https://github.com/DiscreteTom/juan）并保留原始版权信息
    - _需求: 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 11. 添加 Hypothesis 依赖
  - [x] 11.1 在 `pyproject.toml` 的 `[dependency-groups] dev` 中添加 `hypothesis>=6.100.0`
    - _需求: 设计文档测试策略_

- [x] 12. 最终检查点 - 确保所有测试通过
  - 确保所有测试通过，如有问题请询问用户。

## 备注

- 标记 `*` 的子任务为可选任务，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求条款以确保可追溯性
- 检查点确保增量验证
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 需求 4（TTL 淘汰）的验收标准编号为 4.1-4.5，在需求文档中位于需求 3 之后
