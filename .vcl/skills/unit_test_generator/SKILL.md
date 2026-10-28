---
name: unit_test_generator
description: 适用于为指定的 Python 或 TypeScript 代码文件生成高质量单元测试用例的专业指导规范，确保测试覆盖全面且符合继承与可见性约束。
---

# 🧪 Skill: Unit Test Generator (单元测试用例生成器)

## 🎯 触发场景
当用户提出以下类型的需求时，必须优先启用此技能：
1. 要求为给定的目标源码文件编写或补充单元测试。
2. 要求为现有类或函数生成全面的测试覆盖用例。
3. 需要基于特定单元测试约束（如方法覆盖率、继承方法测试等）构建 `pytest` 或 `jest` 测试用例。

---

## 📝 核心测试生成规范

在生成单元测试用例时，必须严格遵守以下四大基本原则：

### 1. 全方法覆盖 (Full Method Coverage)
* 必须为目标文件（Target File）中定义的**每一个公共方法、静态方法、类方法以及属性（Property）**编写对应的单元测试用例。
* 需涵盖正常边界条件（Happy Path）与典型的异常分支（Error Cases）。

### 2. 父类 Public 方法处理 (Inherited Public Methods)
* 若目标类继承了父类（Base Class），且在子类中重写或集成了父类的公共方法（Public Methods）：
  * 必须针对**该目标子类实例**下继承或调用的公共方法生成测试用例。
  * 验证子类在继承/重写后，这些公共方法的行为是否符合预期的协议与契约。

### 3. 禁止生成私有方法测试 (No Private Method Testing)
* **严禁**为目标类或父类的私有方法（如 `_private_method` 或 `__strictly_private`）单独编写独立的测试函数。
* 私有方法的逻辑必须通过测试调用它们的公共方法（Public API）间接进行验证。

### 4. 禁止生成父类的测试用例 (No Base Class Test Cases)
* 所有的测试类与测试用例，主体**必须仅针对目标子类或目标文件**本身。
* **严禁**专门为父类生成单独的测试类、测试用例文件或测试套件（例如：不应出现 `TestBaseDB`，只需针对 `SQLiteDB` 等具体目标文件进行测试）。

### 5. 禁止生成父类的测试用例 (No Base Class Test Cases)
* 所有的测试类与测试用例，主体**必须仅针对目标文件**本身。
* **严禁**专门为父文件生成单独的测试类、测试用例文件或测试套件。
* 
---

## 💻 代码工程规范 (Engineering Guidelines)

1. **测试框架选择**：
   * Python 项目默认使用 `pytest` + `pytest-asyncio`（若涉及异步方法）。
   * TypeScript/JavaScript 项目默认使用 `Jest` 或 `Vitest`。
2. ** Mock 与 Fixture 使用**：
   * 对外部服务（数据库连接、网络 IO、第三方 SDK）使用 `unittest.mock` / `jest.fn()` 进行隔离。
   * 必须保持测试函数的独立性与可重复运行性（Stateless & Isolated）。
3. **命名规范**：
   * 测试文件命名：`test_<target_name>.py` 或 `<target_name>.test.ts`。
   * 测试函数命名：`test_<method_name>_<scenario>`（例如 `test_add_history_success`）。

---

## 🚫 行为禁忌 (Don'ts)
- ❌ **严禁测试父类本身**：不要创建针对 Base/Abstract 抽象类的单独测试。
- ❌ **严禁直接测试私有方法**：不要直接调用或断言 `_method`。
- ❌ **严禁遗漏公共方法**：不得漏掉目标类中的任何一个公共方法。
- ❌ **严禁真实的未隔离 I/O**：单元测试中涉及外部数据库或 API 调用时，必须正确使用 Mock，防止真实 I/O 阻塞测试运行。