# 生命周期管理功能测试与操作指南

## 概述

本文档说明 AHMU 仿真器「生命周期管理」功能（技术方案 4.3.11）的测试与操作方法。

AHMU 仿真器在**维护模式**下向成员系统发出「生命周期获取指令」，获取并展示成员系统的**上电运行时间**与**上电循环计数**，支持对生命周期数据进行存储与显示，并生成生命周期获取日志（操作时间 / 操作用户 / 被操作的成员系统 / 操作状态 / 数据来源）。

生命周期数据分为两类来源：

| 数据来源 | 标识 | 说明 |
|---------|------|------|
| 模拟数据 | `simulated` | AHMU 内部模拟成员系统响应，无外部设备交互 |
| 真实数据 | `real` | AHMU 发送指令并与外部设备（飞控模块）实际交互后生成，记录指令发送 / 设备响应时间与结果 |

## 目录

- [一、前置条件](#一前置条件)
- [二、关键概念](#二关键概念)
  - [2.1 数据来源标识](#21-数据来源标识)
  - [2.2 维护模式前提](#22-维护模式前提)
- [三、操作方法](#三操作方法)
  - [方法 1：查询成员系统生命周期列表](#方法-1查询成员系统生命周期列表)
  - [方法 2：查看成员系统生命周期信息](#方法-2查看成员系统生命周期信息)
  - [方法 3：查询生命周期获取日志](#方法-3查询生命周期获取日志)
  - [方法 4：主动存储生命周期数据](#方法-4主动存储生命周期数据)
  - [方法 5：端到端自动化测试](#方法-5端到端自动化测试)
  - [方法 6：模拟飞控模块发送生命周期数据](#方法-6模拟飞控模块发送生命周期数据)
- [四、常见问题排查](#四常见问题排查)
- [五、相关文件](#五相关文件)

---

## 一、前置条件

1. **后端已启动**：双击项目根目录 `start.bat`，或执行：
   ```cmd
   cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator
   start.bat
   ```
2. **HTTP 服务就绪**（返回 200）：
   ```cmd
   curl http://localhost:8443/api/v1/system/mode
   ```
3. **UDP 接收器就绪**（随后端自动监听飞控模块数据端口 31696）：
   ```cmd
   netstat -ano | findstr 31696
   ```
   预期看到一行 `UDP  0.0.0.0:31696  *:*  <PID>`。
4. **生命周期数据已初始化**：首次启动后端会自动为 51 个成员系统生成初始生命周期数据。
5. **测试脚本就位**：`backend/tests/` 下的两个脚本：
   - `test_lifecycle.py`（端到端自动化测试）
   - `simulate_fcm_time_cycle.py`（飞控模块模拟器）

> 说明：生命周期获取（方法 2）需在**维护模式**下进行，正常模式会被拒绝。

---

## 二、关键概念

### 2.1 数据来源标识

每条生命周期信息与获取日志均带 `data_source` 字段，取值为：

| 值 | 含义 | 适用成员系统 |
|----|------|-------------|
| `simulated` | 模拟数据 | 除飞控模块外的所有成员系统（如 `HF_HSCU`、`ADIRU_1`） |
| `real` | 真实数据 | 飞控模块 `HF_FCM_1` / `HF_FCM_2` / `HF_FCM_3` |

- 模拟数据路径：AHMU 内部完成「发指令 → 模拟响应 → 解包」全流程，`response_result = ok`；
- 真实数据路径：AHMU 经 UDP 下发获取指令（端口 31697），等待外部飞控模块实际响应（端口 31696），`response_result = received`，并记录**指令发送时间**（`command_sent_at`）与**设备响应时间**（`response_received_at`）。

### 2.2 维护模式前提

生命周期数据获取（方法 2）要求系统处于**维护模式**，两种进入方式：

- **快速切换（调试）**：
  ```cmd
  curl -X POST "http://localhost:8443/api/v1/system/force-mode?mode=maintenance&operator=admin"
  ```
- **正常路径（30 秒持续计时）**：通过维护开关信号触发，见《维护模式控制测试指南》。

---

## 三、操作方法

### 方法 1：查询成员系统生命周期列表

- **方法名称**：`GET /api/v1/lifecycle/members`
- **用途**：获取全部成员系统及其当前生命周期数据（上电运行时间、上电循环计数、数据来源、最近获取时间），用于界面「成员系统」下拉选择。
- **调用步骤**：
  1. 打开命令提示符（CMD），进入后端目录：
     ```cmd
     cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
     ```
  2. 执行查询：
     ```cmd
     curl http://localhost:8443/api/v1/lifecycle/members
     ```
  3. 查看返回 JSON 中的 `memberList` 数组，每个成员含 `memberSystem` / `memberName` / `powerOnTime` / `powerCycleCount` / `dataSource` / `statusString` / `lastRetrieved`。
- **预期结果**：
  - `total` 为 51（成员系统总数）；
  - `memberList` 含 `HF_HSCU`、`ADIRU_1`、`HF_FCM_1/2/3` 等；
  - 每条记录 `dataSource` 为 `simulated` 或 `real`。
- **注意事项**：
  - 该接口为只读查询，不改变任何数据；
  - 飞控模块 `HF_FCM_1` 在收到真实 UDP 数据前，`dataSource` 为 `simulated`，收到后变为 `real`。

### 方法 2：查看成员系统生命周期信息

- **方法名称**：`POST /api/v1/lifecycle/member-retrieve/{member}`
- **用途**：向指定成员系统发出「生命周期获取指令」，获取并返回其生命周期信息；根据成员系统类型自动区分**模拟数据**与**真实数据**。
- **调用步骤**：
  1. 确认处于维护模式（见 2.2）；
  2. 对非飞控模块（模拟数据，如 `HF_HSCU`）：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/lifecycle/member-retrieve/HF_HSCU?operator=TEST"
     ```
  3. 对飞控模块（真实数据，如 `HF_FCM_1`），需**先启动飞控模拟器**（见方法 6 的指令响应模式）：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/lifecycle/member-retrieve/HF_FCM_1?operator=TEST"
     ```
- **预期结果**：
  | 字段 | 模拟数据（HF_HSCU） | 真实数据（HF_FCM_1） |
  |------|---------------------|----------------------|
  | `status` | `ok` | `ok` |
  | `data_source` | `simulated` | `real` |
  | `power_on_time` | 按真实墙钟时间连续递增 | 外部设备上报值 |
  | `response_result` | `ok` | `received` |
  | `command_sent_at` / `response_received_at` | 指令发出/模拟响应时间（差约 2s） | 指令发出/设备响应时间 |
- **注意事项**：
  - 正常模式下调用返回 `status=error`，提示「非维护模式，无法获取生命周期数据」；
  - 模拟数据路径含 2 秒模拟响应延迟，连续查看时上电运行时间差值约等于两次查看间的真实时长（约 2~5 秒）；
  - 真实数据路径若 3 秒内未收到外部设备响应，返回超时（`response_result=timeout`），此时应检查飞控模拟器是否已启动。

### 方法 3：查询生命周期获取日志

- **方法名称**：`GET /api/v1/lifecycle/logs`
- **用途**：查询生命周期获取日志，含操作时间、操作用户、被操作成员系统、操作状态、数据来源及真实数据的指令/响应详情；支持按数据来源筛选。
- **调用步骤**：
  1. 查询全部日志：
     ```cmd
     curl "http://localhost:8443/api/v1/lifecycle/logs?page=1&size=20"
     ```
  2. 仅查询真实数据日志：
     ```cmd
     curl "http://localhost:8443/api/v1/lifecycle/logs?data_source=real"
     ```
  3. 仅查询模拟数据日志：
     ```cmd
     curl "http://localhost:8443/api/v1/lifecycle/logs?data_source=simulated"
     ```
  4. 可选筛选参数：`status`（`success`/`failed`/`rejected`/`timeout`）、`equip_id`（成员系统标识）。
- **预期结果**：
  - 每条日志含 `operated_at`、`operator`、`equip_id`、`equip_name`、`status`、`power_on_time`、`power_cycle_count`、`data_source`、`data_source_label`；
  - 真实数据日志额外含 `command_sent_at`、`response_received_at`、`response_result`（如 `received`）。
- **注意事项**：
  - 日志按 `operated_at` 倒序排列（最新在前）；
  - 历史日志（数据来源字段为空）已在服务启动时自动回填为 `simulated`，筛选「模拟数据」可完整命中。

### 方法 4：主动存储生命周期数据

- **方法名称**：`POST /api/v1/lifecycle/save`
- **用途**：将当前采集到的成员系统生命周期数据保存到指定路径（JSON 文件），可选仅保存指定成员系统。
- **调用步骤**：
  1. 保存全部成员系统：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/lifecycle/save" -H "Content-Type: application/json" -d "{\"storage_path\":\"\",\"member_system\":null}"
     ```
  2. 仅保存 `HF_HSCU`：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/lifecycle/save" -H "Content-Type: application/json" -d "{\"member_system\":\"HF_HSCU\"}"
     ```
- **预期结果**：
  - 返回 `status=ok`、`count`（条数）、`file_path`（生成的 JSON 文件路径）；
  - 文件名为 `lifecycle_data_YYYYMMDD_HHMMSS.json`，未指定路径时默认存于 `backend/data/lifecycle_storage/`。
- **注意事项**：
  - `storage_path` 留空时使用默认目录；
  - 该操作仅读取并导出当前数据，不修改数据库。

### 方法 5：端到端自动化测试

- **方法名称**：`tests/test_lifecycle.py`
- **用途**：一键运行生命周期功能的端到端自动化测试，覆盖 7 个场景（列表查询、正常模式拒绝、强制维护、获取数据、日志查询、主动存储、恢复正常）。
- **调用步骤**：
  1. 确保后端已启动；
  2. 执行：
     ```cmd
     cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
     venv\Scripts\python.exe tests\test_lifecycle.py
     ```
- **预期结果**：
  - 输出各场景 `[PASS]`/`[FAIL]`，末尾显示「测试结果: 7/7 通过」；
  - 退出码为 `0` 表示全部通过。
- **注意事项**：
  - 脚本会自动强制进入维护模式并在结束时恢复正常模式；
  - 若某场景 `[FAIL]`，检查后端是否就绪、端口是否被占用。

### 方法 6：模拟飞控模块发送生命周期数据

- **方法名称**：`tests/simulate_fcm_time_cycle.py`
- **用途**：模拟飞控模块 `HF_FCM_1` 通过 A664(AFDX) 向 AHMU 发送生命周期数据（`FCS_OutputData_TO_OMS_BLK_5.DS1.Time_Cycle_Status`），支持**周期发送**与**指令响应**两种模式。
- **关键参数**：

  | 参数 | 默认 | 说明 |
  |------|------|------|
  | `--host` | `127.0.0.1` | AHMU 地址 |
  | `--port` | `31696` | 数据 UDP 端口（AHMU 监听） |
  | `--period` | `0.4` | 发送周期（秒） |
  | `--count` | `10` | 发送次数（`0`=无限） |
  | `--power-on-time` | `3600` | 初始上电运行时间（秒） |
  | `--cycle-count` | `12` | 初始上电循环计数 |
  | `--step` | 自动 | 每次周期上电时间增量（秒） |
  | `--listen` | 关 | 开启指令响应模式 |
  | `--listen-port` | `31697` | 指令监听端口（AHMU 下发获取指令） |

- **调用步骤**：
  1. **周期发送模式**（主动周期上报真实数据）：
     ```cmd
     venv\Scripts\python.exe tests\simulate_fcm_time_cycle.py --count 20 --period 0.4
     ```
  2. **指令响应模式**（收到 AHMU 获取指令后响应，配合方法 2 的真实数据路径）：
     ```cmd
     venv\Scripts\python.exe tests\simulate_fcm_time_cycle.py --listen --power-on-time 20000 --cycle-count 5 --step 10
     ```
  3. 在另一窗口对飞控模块发起获取（见方法 2 步骤 3）。
- **预期结果**：
  - 周期模式：日志逐条打印「发送 HF_FCM_1 Time_Cycle_Status → …」，上电时间递增；AHMU 端 `HF_FCM_1` 的 `dataSource` 变为 `real`；
  - 指令模式：收到 AHMU 指令后打印「收到 AHMU 生命周期获取指令」，并响应一条数据；AHMU 端返回 `data_source=real`、`response_result=received`。
- **注意事项**：
  - 指令响应模式下脚本持续运行，按 `Ctrl+C` 停止；
  - 真实数据链路需**同时**满足：后端 UDP 31696 接收器就绪 + 飞控模拟器 `--listen` 运行 + 维护模式，三者缺一不可；
  - Mock 环境简化为一端口一成员（31696 → `HF_FCM_1`），真实 AFDX 通过 VL ID/源 IP 区分发送方。

---

## 四、常见问题排查

| 现象 | 可能原因 | 解决 |
|------|---------|------|
| 获取返回「非维护模式」 | 当前为正常模式 | 先切维护模式（见 2.2） |
| 真实数据获取超时（3s） | 飞控模拟器未启动或端口不符 | 启动 `simulate_fcm_time_cycle.py --listen`，确认监听 31697、响应 31696 |
| UDP 31696 未监听 | 后端未启动或端口被占用 | 重启后端，`netstat -ano | findstr 31696` 确认 |
| 日志筛选「模拟」不完整 | 历史数据 `data_source` 为空 | 重启后端触发自动回填，或等待 `_backfill_data_source` 执行 |
| 连续查看上电时间差值异常 | 历史版本随机递增（已修复） | 升级至当前版本，按真实墙钟时间累积 |

---

## 五、相关文件

| 文件 | 说明 |
|------|------|
| `backend/app/services/lifecycle.py` | 生命周期管理服务（数据来源标识、双路径获取、日志、存储） |
| `backend/app/core/icd_time_cycle.py` | Time_Cycle_Status ICD 编解码（160 位块） |
| `backend/app/api/routes.py` | 生命周期相关端点（`/lifecycle/*`） |
| `backend/app/database.py` | `LifecycleData` / `LifecycleRetrievalLog` 表 |
| `backend/tests/test_lifecycle.py` | 端到端自动化测试脚本 |
| `backend/tests/simulate_fcm_time_cycle.py` | 飞控模块生命周期数据模拟器 |
| `docs/maintenance-mode-test-guide.md` | 维护模式控制测试指南（前置依赖） |
