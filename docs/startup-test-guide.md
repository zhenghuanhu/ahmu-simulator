# 启动测试功能说明文档

## 概述

AHMU 仿真器的「启动测试」功能包含两个层面：

| 层面 | 说明 | 对应模块 |
| --- | --- | --- |
| **启动测试交互**（ARINC624） | 按成员系统规范模拟与成员系统的启动测试交互通信，支持维护人员经交互式页面触发成员系统执行测试 | `startup_test.py` |
| **启动测试服务**（199 成员） | 支持多达 199 个成员系统并发提供启动测试服务，核心为故障历史检索 | `fault_history.py` |

> 对向测试端参考：`机载航电/OHMS/test_case/200NVM.py`（199 成员启动服务）、`飞机事件信息.py`。

---

## 一、启动测试交互（ARINC624）

### 1.1 启动测试消息（表 9 + ACK）

| 方向 | 消息名称 | 说明 |
| --- | --- | --- |
| OHMS → 成员系统 | 启动测试运行测试指令 | 请求成员系统开始执行启动测试 |
| OHMS → 成员系统 | 启动测试终止指令 | 请求成员系统终止正在进行的启动测试 |
| OHMS → 成员系统 | 启动测试继续测试指令 | 通知成员系统维护人员已响应，请求继续执行 |
| OHMS → 成员系统 | ACK 确认 | 明确确认收到成员系统的响应 |
| 成员系统 → OHMS | 启动测试测试进行中响应 | 确认已启动或恢复执行启动测试 |
| 成员系统 → OHMS | 启动测试抑制测试响应 | 通知存在抑制条件，无法开始/继续 |
| 成员系统 → OHMS | 启动测试显示页面响应 | 请求 OHMS 显示预定义交互式测试页面 |
| 成员系统 → OHMS | 启动测试终止响应 | 确认已/将发生进行中测试的提前终止 |
| 成员系统 → OHMS | 成员系统状态报告 | 抑制条件/测试完成标志/故障报告 |

### 1.2 前置条件

启动测试允许执行前需满足：

| 条件 | 判定 |
| --- | --- |
| 飞机处于地面 | `all_gear_wow = True`（轮载信号，地） |
| 维护开关位置 | `maintenance_switch ∈ {ground_test, data_load}`（地面测试/加载位置） |

### 1.3 抑制条件监视

- 发出启动测试指令**之前**：检查成员系统状态报告中的抑制条件；
- 启动测试执行**期间**：持续监视成员系统是否报告抑制条件激活。

### 1.4 交互式启动测试场景

```
a) 成员系统报告不存在抑制条件
b) OHMS 发送"启动测试运行测试指令"
c) 成员系统以"测试进行中响应"响应，开始执行
d) OHMS 用 ACK 明确确认收到
e) 成员系统发送"显示页面响应"
f) OHMS 用 ACK 响应
g) OHMS 展示交互式测试页面，等待维护人员响应
h) 维护人员执行页面要求操作并提供响应
i) OHMS 发送"继续测试指令"（含维护人员响应）
j) 成员系统以"测试进行中响应"响应，继续执行
k) OHMS 用 ACK 确认
l) 抑制条件变为激活
m) 成员系统发送"抑制测试响应"终止执行，并报告抑制条件
n) OHMS 用 ACK 确认
o) OHMS 通知维护人员提前终止，提供抑制条件查看
```

### 1.5 非交互式启动测试场景

```
a) 成员系统报告抑制条件存在
b) OHMS 检查抑制条件并向维护人员显示
c) 维护人员清除抑制条件
d) 成员系统报告无抑制条件
e) 维护人员命令开始，OHMS 发送"运行测试指令"
f) 成员系统以"测试进行中响应"响应
g) OHMS 用 ACK 确认
h) 成员系统继续执行直到完成
i) 成员系统设置"测试完成"=TRUE，报告相关故障
   → OHMS 检查故障报告判定通过/失败
```

### 1.6 状态机

```
idle → command_received(指令已发送) → in_progress(测试进行中)
     → display_page(交互页面展示) → waiting_user(等待维护人员响应)
     → in_progress → completed / suppressed / terminated / aborted
```

---

## 二、启动测试服务（199 成员 / 故障历史检索）

对齐 `200NVM.py` 的测试流程，AHMU 提供故障历史检索接口：

| 接口 | 说明 |
| --- | --- |
| `POST /api/v1/FHATAsRequest` | 获取 ATA 编号列表 |
| `POST /api/v1/FHEquipRequest` | 获取成员系统设备列表（可按 ATA 过滤） |
| `POST /api/v1/FHRetrievalRequest` | 开始故障历史检索任务 |
| `POST /api/v1/FHStatusRequest` | 查询检索任务状态（`Complete`/`Error`） |

- 数据源：复用故障诊断服务的 400 个成员系统目录；
- 检索任务状态机：`InProgress → Complete`（Mock 环境 0.5s 完成）；
- 支持 199 个成员并发检索（每任务按 `equip_id` 独立记录状态）。

---

## 三、测试方法

### 3.1 一键自动化测试

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
:: 1. 启动后端（另开终端）
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8443

:: 2. 启动测试交互（7 项）
venv\Scripts\python.exe tests\test_startup_test.py

:: 3. 故障历史检索（199 成员，4 项）
venv\Scripts\python.exe tests\test_fault_history.py
```

### 3.2 手动模拟成员系统 + 前端操作

```cmd
:: 启动模拟成员系统（另开终端，模拟 CPM 上的成员系统）
venv\Scripts\python.exe tests\simulate_member.py --scenario interactive
```

前端操作流程：

1. 顶部模拟面板设置前置条件：**WOW → GROUND**、**维护开关 → GROUND TEST**；
2. 导航进入 **STARTUP TEST / 启动测试** 页面；
3. 输入成员系统（如 `PAA_L3`）、选择测试类型、点击「发起测试」；
4. 观察状态流转：`指令已发送 → 测试进行中 → 交互页面展示 → 等待维护人员响应 → 已完成`；
5. 交互式场景点击「提交交互响应 (继续测试)」，进行中可点击「终止测试」。

### 3.3 测试覆盖

| 测试脚本 | 项数 | 覆盖内容 |
| --- | --- | --- |
| `test_startup_test.py` | 7 | 前置条件、交互式正常、交互式抑制、非交互式(带/无故障)、初始抑制清除、终止 |
| `test_fault_history.py` | 4 | ATA 列表、设备列表≥199、199 成员检索 Complete、状态持久化 |

---

## 四、相关文件

| 文件 | 说明 |
| --- | --- |
| `backend/app/services/startup_test.py` | 启动测试交互服务（ARINC624 状态机） |
| `backend/app/services/fault_history.py` | 故障历史检索服务（199 成员启动测试服务） |
| `backend/app/core/icd_startup_test.py` | 启动测试消息编解码 |
| `backend/app/database.py` | `StartupTest`、`FaultHistoryTask` 数据模型 |
| `backend/app/api/routes.py` | 启动测试 + FH 检索接口 |
| `frontend/src/views/StartupTest.vue` | 启动测试前端页面 |
| `backend/tests/simulate_member.py` | 模拟成员系统（对向端） |
| `backend/tests/test_startup_test.py` | 启动测试交互测试 |
| `backend/tests/test_fault_history.py` | 故障历史检索测试 |
