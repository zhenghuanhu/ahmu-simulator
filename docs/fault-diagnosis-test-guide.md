# 故障诊断功能说明与测试文档

## 概述

AHMU 仿真器的「故障诊断」功能（技术方案 4.3.6）实现成员系统故障/失效报告的接收、处理、查询与历史检索下传。本次优化对齐对向测试端（OHMS 测试用例）的协议，使仿真器能按协议接收解析指令、生成符合预期的响应数据，用于替代真实 AHMU 设备联调。

| 层面 | 说明 | 对应模块 |
| --- | --- | --- |
| 故障报告处理 | 接收成员系统故障报告，数据校验→级联过滤→FDE 关联→失效报告生成→与/或逻辑定位根源故障 | `fault_diagnosis.py` |
| 失效/故障报告批量接收 | 经 AFDX 批量接收 ICD 故障/失效消息，生成报告 | `fault_diagnosis.py` |
| 统一查询接口 | `POST /getParameter` 按 messageID 区分，返回本航段/现有故障与失效报告 | `fault_diagnosis.py` + `routes.py` |
| 故障历史检索 | FHRetrieval 流程（ATA→设备→检索→状态），支持数字 equipID | `fault_history.py` |
| 故障历史下传 | 检索完成后经 TFTP 上传故障历史文件 | `fault_history_tftp.py` |

> 对向测试端参考：`机载航电/OHMS/test_case/故障历史报告获取664.py`、`故障与失效报告批量创建.txt`、`地面人机界面.py`、`200NVM.py`。

---

## 一、协议梳理

### 1.1 统一查询接口（`POST /getParameter`）

测试端权威 `AhmuApi`（`libs/fault_diag_libs/api_libs.py`）统一 `POST /getParameter`，按 `messageID` 区分，响应为 list 包裹（`data.json()[0]`），`[0]` 含 `totalCount`：

| messageID | 方法 | 响应列表字段 | 功能 |
| --- | --- | --- | --- |
| 257 (0x0101) | get_inbound_leg_fdes | `inboundLegFDEStructList` | 本航段活跃故障关联 FDE |
| 258 (0x0102) | get_existing_fdes | `existingFDEStructList` | 现有 FDE |
| 259 (0x0103) | get_inbound_leg_failures_all | `inboundLegFailureAllStructList` | 本航段所有失效报告 |
| 262 (0x0106) | get_existing_failures | `existingFailureStructList` | 现有失效报告 |
| 264 (0x0108) | get_failure_history | `failureHistoryStructList` | 故障历史（按航段/ATA/阶段筛选） |

失效报告协议字段：`failureName`/`fimCode`/`flightLeg`/`failureDataTime`/`failureSequence`；FDE 字段：`fdeCode`/`fdeText`/`fdeClass`。

### 1.2 ICD 故障/失效报告接收

测试端《故障与失效报告批量创建.txt》经 AFDX 批量发送故障/失效 ICD 消息（`return_dp_dict_all_val(val=0/1)` 生成两种状态值）。Mock 环境用 UDP 承载 JSON，`value=1` 表示故障/失效激活：

| 字段 | 说明 |
| --- | --- |
| `member_system` | 成员系统标识 |
| `fault_code` / `failure_code` | 故障代码 / 失效代码 |
| `severity` | 严重程度（minor/major/critical） |
| `value` | 1=激活（生成报告），0=消失（忽略） |

### 1.3 故障历史检索（FHRetrieval）

| 接口 | messageID | 说明 |
| --- | --- | --- |
| `POST /FHATAsRequest` | 1794 | ATA 章节列表 |
| `POST /FHEquipRequest` | 1795 | 设备列表（按 ATA 过滤） |
| `POST /FHRetrievalRequest` | 1796 | 启动检索任务（`equipID` 数字 MemberID） |
| `POST /FHStatusRequest` | 1797 | 查询状态（`data[0]['fhStatusStructList'][0]['status']` ∈ `Complete`/`Error`） |

### 1.4 TFTP 下载

检索完成后，AHMU 作为 TFTP 客户端向测试端 `TFTPServer`（端口 59）上传故障历史文件。采用标准 TFTP（RFC 1350 + blksize 选项，ARINC 615A 数据加载底层基础）：

```
WRQ(0x0002) → ACK(0)/OACK(0x0006) → DATA(0x0003) 分块 → 逐块 ACK(0x0004)
```

---

## 二、功能改动点（本次优化）

| 改动 | 内容 |
| --- | --- |
| 改动 1 | `fault_diagnosis.py` 新增 5 个查询方法 + `routes.py` 新增 `POST /getParameter`（messageID 分发，兼容 `0x` 十六进制） |
| 改动 2 | `config.py` 新增 `FAULT_RX_CONFIG`（UDP 34850）；`fault_diagnosis.py` 新增 UDP 接收循环 + `_handle_fault_message` + `process_failure_report` |
| 改动 3 | `fault_history.py` 重构：数字 equipID 目录（3000+序号，AHMU→ATA45），`_normalize_equip_key` 兼容数字 MemberID 与成员 code 字符串 |
| 改动 4 | `fault_history_tftp.py`（新）TFTP 客户端；`fault_history.py` 检索完成后触发上传；`config.py` 新增 `FAULT_HISTORY_TFTP_CONFIG` |

---

## 三、测试方法

### 3.1 前置条件

启动 AHMU 仿真器后端（HTTP 8443 + UDP 34850 + TFTP 59）：

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8443
```

### 3.2 测试脚本

| 脚本 | 说明 |
| --- | --- |
| `tests/test_fault_diag_664.py` | 改动 1+2 端到端（ICD 批量注入 → getParameter 查询验证，5 项） |
| `tests/simulate_fault_inject.py` | 模拟测试端批量注入故障/失效 ICD |
| `tests/test_fault_history.py` | 改动 3（199 成员故障历史检索，4 项） |
| `tests/test_fault_history_tftp.py` | 改动 4（TFTP 上传，2 项） |
| `tests/simulate_tftp_server.py` | 模拟测试端 TFTP 服务器 |

### 3.3 运行方式

```cmd
:: 改动 1+2: getParameter 接口 + ICD 接收
venv\Scripts\python.exe tests\test_fault_diag_664.py

:: 改动 3: 故障历史检索 (数字 equipID)
venv\Scripts\python.exe tests\test_fault_history.py

:: 改动 4: TFTP 上传
venv\Scripts\python.exe tests\test_fault_history_tftp.py
```

---

## 四、测试场景覆盖

| 场景 | 来源测试文件 | 验证点 |
| --- | --- | --- |
| 失效报告批量注入 | 故障与失效报告批量创建.txt | 注入 50 条失效 ICD → `get_existing_failures`（0x0106）totalCount 增加 ≥50 |
| 故障报告批量注入 | 故障与失效报告批量创建.txt | 注入 50 条故障 ICD → `get_inbound_leg_failures_all`（0x0103）totalCount 增加 ≥50 |
| getParameter 各 messageID | api_libs.py | 5 个 messageID 均含 totalCount + 对应列表字段 |
| 故障历史筛选 | fault_diag.py | `get_failure_history`（0x0108）按航段/ATA 筛选 |
| 未知 messageID | 边界 | 空结果不报错 |
| 199 成员故障历史检索 | 200NVM.py / 故障历史报告获取664.py | `FHRetrieval → FHStatus = Complete`（数字 equipID） |
| TFTP 上传 | 故障历史报告获取664.py | 检索完成后上传 `.LUI` 文件，服务器收到完整内容 |

---

## 五、相关文件

| 文件 | 说明 |
| --- | --- |
| `backend/app/services/fault_diagnosis.py` | 故障诊断引擎（getParameter 查询 + ICD 接收 + 失效/故障处理） |
| `backend/app/services/fault_history.py` | 故障历史检索（数字 equipID） |
| `backend/app/services/fault_history_tftp.py` | TFTP 客户端 |
| `backend/app/api/routes.py` | `POST /getParameter` 等接口 |
| `backend/app/config.py` | `FAULT_RX_CONFIG`、`FAULT_HISTORY_TFTP_CONFIG` |
| `backend/app/database.py` | `FaultReport`/`FailureReport`/`FDE`/`FaultHistoryTask` 模型 |
| `backend/tests/test_fault_diag_664.py` 等 | 测试脚本 |
