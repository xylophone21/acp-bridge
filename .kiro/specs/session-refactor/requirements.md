# 需求文档

## 简介

对 AgentBridge（飞书聊天机器人桥接服务）进行会话管理重构和功能改造。主要变更包括：移除 `#new` 指令，改为基于飞书消息的引用关系自动管理会话生命周期；引入 LRU 和定时淘汰机制控制资源占用；简化配置文件结构（取消多 agent 配置）；清理历史遗留命名；完善文档。

## 术语表

- **Bridge**: AgentBridge 桥接服务主进程，负责连接飞书与 ACP agent
- **Session_Manager**: 会话管理器，负责会话的创建、查找、淘汰和生命周期管理
- **Session**: 一次用户与 agent 之间的对话上下文，包含 agent 进程句柄和消息历史
- **Root_Message**: 飞书消息引用链（通过 parent_id 关联）的根消息，即引用链中最顶层的那条消息，不一定是触发会话创建的消息
- **Trigger_Message**: 真正触发新 Session 创建的那条 @机器人 的消息；Session 通过其所在消息引用链的 Root_Message 的 message_id 来索引（而非 Trigger_Message 的 message_id），因为同一引用链下的所有消息都能沿 parent_id 找到 Root_Message；同一个 Root_Message 下只允许存在一个 Session
- **Thread**: 飞书中以某条消息为根的回复链（通过 parent_id 关联）
- **LRU_Eviction**: 基于最近最少使用策略的会话淘汰机制
- **TTL_Eviction**: 基于存活时间的会话定时淘汰机制
- **Config_File**: TOML 格式的桥接服务配置文件（bridge.toml）
- **ACP_Agent**: 通过 ACP 协议通信的 agent 进程（如 Kiro CLI）
- **Juan**: 本项目的前身开源项目，位于 ~/code/juan

## 需求

### 通用规则

1. THE Bridge 自身产生的所有提示消息（非 ACP_Agent 回复的内容）SHALL 使用英文

### 需求 1：自动会话创建（移除 #new 指令）

**用户故事：** 作为飞书用户，我希望直接 @机器人 就能开始对话，而不需要记住和输入 #new 指令，以降低使用门槛。

#### 验收标准

1. WHEN 用户在群聊中 @机器人 并发送非指令文本消息（不以 # 开头），且该消息所在的消息引用链的 Root_Message 没有已关联的 Session，THE Session_Manager SHALL 自动创建一个新 Session，以 Root_Message 的 message_id 作为 Session 的索引 key，并将该 @机器人 的消息作为 Trigger_Message
2. WHEN 用户在私聊（DM）中发送非指令文本消息（不以 # 开头），且该消息所在的消息引用链的 Root_Message 没有已关联的 Session，THE Session_Manager SHALL 自动创建一个新 Session，无需 @机器人
2. WHEN 用户引用（reply）已有 Session 所在消息引用链中的任意消息并发送文本（无需再次 @机器人），THE Session_Manager SHALL 沿 parent_id 找到 Root_Message，将该消息路由到 Root_Message 对应的已有 Session 中继续对话（前提：飞书应用已开启"接收群聊中所有消息"权限）
3. THE Bridge SHALL 移除 `#new` 指令的解析和处理逻辑
4. THE Bridge SHALL 移除 `#agents` 指令的解析和处理逻辑（因为不再有多 agent 选择）
5. THE Bridge SHALL 移除 `!` shell 指令功能及 `handler_shell.py` 模块（对非程序员用户过于危险）
5. WHEN 自动创建 Session 时，THE Session_Manager SHALL 使用 Config_File 中配置的唯一 agent 和默认 workspace 路径
6. WHEN 自动创建 Session 时，THE Session_Manager SHALL 记录 Trigger_Message 的文本内容前 20 个字符作为该 Session 的摘要（summary）
7. IF 自动创建 Session 过程中 ACP_Agent 启动失败，THEN THE Bridge SHALL 向用户回复错误信息并且不创建 Session
8. WHEN 用户在已有 Session 的消息引用链中再次 @机器人 发送非指令消息，THE Session_Manager SHALL 不创建新 Session，而是将该消息作为普通消息路由到已有 Session 中继续对话
9. WHEN 收到一条未 @机器人 的消息，且该消息来自群聊（非私聊），且该消息不属于任何已有 Session 的消息引用链（无 parent_id，或沿 parent_id 找到的 Root_Message 没有关联 Session），THE Bridge SHALL 忽略该消息
10. WHEN 收到一条 # 指令消息且该消息在已有 Session 的消息引用链中，THE Bridge SHALL 无需 @机器人 即可触发指令处理，并将指令作用于该 Session
11. WHEN 收到一条 # 指令消息且该消息不在任何已有 Session 的消息引用链中，THE Bridge SHALL 在群聊中仅当用户 @了机器人时触发指令处理，在私聊中无需 @机器人即可触发；对于依赖 Session 的指令（如 #session、#end、#cancel、#diff、#mode、#model），SHALL 回复"No active conversation"；对于不依赖 Session 的指令（如 #sessions、#help），SHALL 正常执行

### 需求 2：基于消息引用关系的会话路由

**用户故事：** 作为飞书用户，我希望通过回复之前的对话消息来延续会话上下文，使多轮对话自然流畅。

#### 验收标准

1. WHEN 收到一条带有 parent_id 的消息，THE Session_Manager SHALL 沿 parent_id 向上查找消息引用链，定位到 Root_Message，并检查 Root_Message 是否关联了 Session
2. WHEN 通过 Root_Message 找到对应的活跃 Session，THE Bridge SHALL 将消息转发到该 Session
3. IF Root_Message 没有对应的活跃 Session（可能从未创建过或已被淘汰），且当前消息是 @机器人 的非指令消息，THEN THE Session_Manager SHALL 以该 Root_Message 的 message_id 为索引 key 创建新 Session，同时 THE Bridge SHALL 向用户发送提示 "New conversation started"
4. IF Root_Message 没有对应的活跃 Session，且当前消息未 @机器人，THEN THE Bridge SHALL 忽略该消息
6. WHEN Session 处于 busy 状态时收到新消息，THE Bridge SHALL 静默缓存该消息（不向群聊发送任何提示），待当前 ACP_Agent 处理完成后，将缓存的消息合并为一条发送给 ACP_Agent
7. WHEN 合并缓存消息时，THE Bridge SHALL 按消息到达的时间顺序拼接，保留每条消息的发送者信息

### 需求 3：LRU 会话淘汰

**用户故事：** 作为系统管理员，我希望通过 LRU 策略限制最大并发会话数，以避免 agent 进程占用过多系统资源。

#### 验收标准

1. THE Config_File SHALL 包含 `max_sessions` 配置项，用于指定最大活跃会话数
2. WHEN 创建新 Session 时活跃会话数已达到 `max_sessions` 上限，THE Session_Manager SHALL 淘汰最近最少使用的空闲 Session（非 busy 状态）
3. WHEN Session 收到新消息或产生交互时，THE Session_Manager SHALL 更新该 Session 的最近使用时间戳
4. WHILE 所有已有 Session 均处于 busy 状态且会话数已达上限，THE Bridge SHALL 向用户回复 "All sessions are busy, please try again later"
5. WHEN 淘汰一个 Session 时，THE Session_Manager SHALL 终止对应的 ACP_Agent 进程并释放资源
6. WHEN 淘汰一个 Session 时，THE Bridge SHALL 在该 Session 的 Trigger_Message 和最后一条机器人回复消息上添加 reaction 表情（DONE），通知用户对话已结束

**用户故事：** 作为系统管理员，我希望长时间不活跃的会话能被自动清理，以避免资源长期占用。

#### 验收标准

1. THE Config_File SHALL 包含 `session_ttl_minutes` 配置项，用于指定会话的最大空闲存活时间（单位：分钟）
2. THE Session_Manager SHALL 定期检查所有活跃 Session 的最近使用时间戳
3. WHEN 一个 Session 的空闲时间超过 `session_ttl_minutes` 配置值，THE Session_Manager SHALL 自动淘汰该 Session
4. WHEN 定时淘汰一个 Session 时，THE Session_Manager SHALL 终止对应的 ACP_Agent 进程并释放资源
5. WHEN 定时淘汰一个 Session 时，THE Bridge SHALL 在该 Session 的 Trigger_Message 和最后一条机器人回复消息上添加 reaction 表情（DONE），通知用户对话已结束

### 需求 5：简化配置文件（单 agent 模式）

**用户故事：** 作为系统管理员，我希望配置文件更简洁，只需配置一个 agent，降低配置复杂度。由于移除了 `#new` 指令，用户无法在创建会话时选择 agent，因此多 agent 配置不再有意义。

#### 验收标准

1. THE Config_File SHALL 使用 `[agent]` 单表替代原有的 `[[agents]]` 数组表来配置唯一的 ACP_Agent
2. THE Config_File SHALL 在 `[bridge]` 表中包含 `max_sessions` 和 `session_ttl_minutes` 配置项
3. THE Config_File 的 `init` 命令生成的样例配置 SHALL 使用 Kiro CLI 作为默认 agent 示例
4. THE Bridge SHALL 在启动时校验 Config_File 中有且仅有一个 `[agent]` 配置
5. WHEN Config_File 中缺少 `max_sessions` 或 `session_ttl_minutes` 配置项，THE Bridge SHALL 使用合理的默认值（max_sessions 默认 10，session_ttl_minutes 默认 60）

### 需求 6：#sessions 指令增强

**用户故事：** 作为飞书用户，我希望在查看活跃会话列表时能看到每个会话的简要内容，以便快速识别各会话。

#### 验收标准

1. WHEN 用户执行 `#sessions` 指令，THE Bridge SHALL 在每个 Session 条目中显示：摘要（Trigger_Message 文本前 20 个字符）、状态（busy/idle）、最近使用时间
2. WHEN 用户执行 `#sessions` 指令，THE Bridge SHALL 不再显示 agent 名称（因为只有一个 agent）

### 需求 7：移除 Juan 项目遗留命名

**用户故事：** 作为项目维护者，我希望代码和配置中不再包含来自 Juan 项目的命名痕迹，使项目具有独立的身份标识。

#### 验收标准

1. THE Config_File 的默认文件名 SHALL 从 `bridge.toml` 保持不变（确认当前无 juan 相关命名）
2. THE Bridge SHALL 确保所有源代码文件中不包含 "juan" 字样的变量名、注释或字符串
3. THE Bridge SHALL 确保 pyproject.toml 中的项目名称和描述不包含 "juan" 字样
4. IF 代码中存在从 Juan 项目引用过来但未适配的逻辑，THEN THE Bridge SHALL 根据 Juan 项目源码（~/code/juan）进行对照修复

### 需求 8：完善 README 文档

**用户故事：** 作为新用户或贡献者，我希望 README 包含完整的安装步骤和项目来源说明，以便快速上手和了解项目背景。

#### 验收标准

1. THE README SHALL 包含完整的安装步骤，包括 Python 版本要求、依赖安装、配置文件生成和启动命令
2. THE README SHALL 包含飞书应用创建和配置的基本指引，包括需要开启"接收群聊中所有消息"权限的说明
3. THE README SHALL 包含可用指令的说明列表（反映重构后的指令集，不含 #new、#agents 和 ! shell 指令）
4. THE README SHALL 根据 MIT 协议要求，明确声明本项目源自 Juan 项目（https://github.com/DiscreteTom/juan），并保留原始版权信息
5. THE README SHALL 包含项目简介，说明 AgentBridge 的用途和核心功能
