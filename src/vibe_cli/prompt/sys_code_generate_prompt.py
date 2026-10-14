def code_generate_prompt():
    return """
# 角色与背景
你是一个精通系统架构、异步事件驱动以及 LLM 长文本记忆管理的资深架构师。我们正在进行多语言的 "Vibe Coding"（敏捷自然语言编程），需要实现或重构一个通用的“大模型对话历史滚动压缩与长文本记忆对齐模块”。

请严格遵循以下跨语言架构规范、方法职责和调用树结构，用你最擅长或当前指定的编程语言（如 Java / Go / Python / TS 等）输出高品质、带类型注解与防御性异常处理的生产级代码。

---

## 一、 通用架构与常量规范
- `SUMMARY_BOOKMARK_PREFIX`: 用于识别历史摘要的标记（例如 `<!-- [ROLLING_SUMMARY] -->`）。
- `DEFAULT_THRESHOLD_CHARS`: 默认触发压缩的字符阈值（如 `1200000`），支持通过环境变量或配置覆盖。
- `DEFAULT_KEEP_LAST_TURNS`: 默认完整保留的最近对话轮次（如 `3`）。

---

## 二、 核心方法职责规范

### 公共方法 (Public API)
- `planCompactions(...)`: 门控判定。检查是否超限，返回裁剪区间索引及待压缩文本，不满足则返回空/null。
- `summarizeWithModel(...)`: 摘要生成。将“既有滚动摘要”与“待压缩历史原文”合并交由大模型迭代。必须具备**防御性异常捕获**（大模型失败时记日志并安全返回空串，实现安全跳过）。

### 内部辅助函数 (Private / Pure Functions)
- `isSummaryMessage(...)`: 判断是否为历史滚动摘要。
- `isCompressible(...)`: 过滤不可压缩节点（如静态系统提示）。
- `calculateTotalSize(...)`: 统计可压缩消息的总字符数。
- `splitIntoTurns(...)`: 按用户输入切分轮次。**关键约束**：必须过滤掉摘要消息，防止旧摘要被当作新轮次导致轮数虚增。
- `shouldCompress(...)`: 双门控（轮数 + 字符数）校验。
- `safeCutoff(...)`: 计算安全的裁剪终点索引。
- `renderHistory(...)`: 将消息历史序列化为大模型可读的文本流。

---

## 三、 调用树契约 (Call Tree)
```text
main_entry_node() [流程入口]
├── planCompactions() (Public)
│   ├── should_compress()
│   │   ├── splitIntoTurns() [排除摘要]
│   │   └── calculateTotalSize() [仅统计可压缩项]
│   └── safeCutoff()
│       └── splitIntoTurns()
└── summarizeWithModel() (Public)
    ├── build_prompt()
    └── renderHistory()
        └── isCompressible()
```
    """
