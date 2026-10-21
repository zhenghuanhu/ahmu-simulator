# 打印管理功能测试与操作指南

## 概述

本文档说明 AHMU 仿真器「打印管理」功能（技术方案 4.3.2）的测试与操作方法。

AHMU 仿真器通过 **ARINC744A 打印协议** 将打印内容（如数据重置结果、数据下载结果、用户自定义内容）经 **AFDX（ARINC 664）虚拟链路** 发送至信息系统打印机（GIPC），打印机逐块回 ACK 并反馈作业状态，最终打印结果输出为 **ps 格式文件** 至电子盘（模拟 `A:\printlog`，实际落盘 `backend/data/printlog/`）。

测试方式分为两种：

| 测试方式       | 说明                                              |
| ---------- | ----------------------------------------------- |
| 端到端自动化测试   | 运行 `tests/test_print.py`，脚本内置「模拟打印机」，验证打印协议完整闭环 |
| 手动 HTTP 调用 | 通过 curl 提交打印任务、查询打印任务列表与状态                      |

---

## 目录

- [一、测试目标](#一测试目标)
- [二、前置条件](#二前置条件)
- [三、关键概念](#三关键概念)
  - [3.1 打印协议消息格式](#31-打印协议消息格式)
  - [3.2 数据链路与端口](#32-数据链路与端口)
  - [3.3 打印队列与降级](#33-打印队列与降级)
- [四、操作方法](#四操作方法)
  - [方法 1：端到端自动化测试（推荐）](#方法-1端到端自动化测试推荐)
  - [方法 2：手动提交打印任务](#方法-2手动提交打印任务)
  - [方法 3：查询打印任务列表与状态](#方法-3查询打印任务列表与状态)
  - [方法 4：通过数据重置/下载间接触发打印](#方法-4通过数据重置下载间接触发打印)
- [五、预期结果](#五预期结果)
- [六、常见问题排查](#六常见问题排查)
- [七、相关文件](#七相关文件)

---

## 一、测试目标

| 编号 | 测试目标           | 验证点                                            |
| -- | -------------- | ---------------------------------------------- |
| T1 | 打印任务提交         | `POST /print/submit` 返回 `status=ok` 与 `job_id` |
| T2 | ARINC744A 协议闭环 | URQ → ACK → DATA×N(每块 ACK) → STATUS 完整交互       |
| T3 | 打印数据分块与一致性     | 打印内容按 150 字节分块传输，接收端拼接后与提交内容一致                 |
| T4 | 作业状态反馈         | 打印机回 `JobStatus=0`(成功)，后端任务流转至 `completed`     |
| T5 | ps 文件生成        | 打印完成后在 `data/printlog/` 生成 `.ps` 文件            |
| T6 | 打印队列管理         | 多任务串行处理，任务状态 `queued → sending → completed`    |
| T7 | 无打印机降级         | 无外部打印机响应时，降级为内置模拟完成（任务仍 `completed`）           |
| T8 | 前端提交 + 界面反馈   | 通过前端「提交打印」入口触发打印，验证 WebSocket 广播的协议帧/完成/失败反馈 |

---

## 二、前置条件

1. **后端已启动**：双击项目根目录 `start.bat`，或执行：
   ```cmd
   cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator
   start.bat
   ```
2. **HTTP 服务就绪**（返回 200）：
   ```cmd
   curl http://localhost:8443/api/v1/system/mode
   ```
3. **打印 UDP 通道就绪**（随后端自动监听）：
   ```cmd
   netstat -ano | findstr 34801
   netstat -ano | findstr 34802
   ```
   预期看到 `UDP  0.0.0.0:34802  *:*  <PID>`（接收端口）；34801 为发送端口，不常驻监听。
4. **ICD 已加载**：后端启动日志出现「ICD快照加载完成」（打印协议依赖 ICD 的 `Printer_GIPC_Data` 消息定义）。
5. **测试脚本就位**：`backend/tests/test_print.py`。

---

## 三、关键概念

### 3.1 打印协议消息格式

打印内容经 ICD 消息 `Printer_GIPC_Data`（AFDX A664，VL 23247，共 156 字节）承载，内部为打印私有协议：

| 字节区间  | 字段         | 说明                                           |
| ----- | ---------- | -------------------------------------------- |
| 0     | `optCode`  | `0`=URQ（用户请求）、`1`=DATA（数据块）                  |
| 1–2   | `blockNum` | uint16 大端；URQ 时为**总块数**，DATA 时为**当前块号**（从 1） |
| 3–155 | `payload`  | 153 字节；URQ 时为文件名（ASCII，NUL 填充），DATA 时为数据块    |

> 每块有效数据 `BLOCK_SIZE = 150` 字节（payload 153 = 150 数据 + 3 填充），对齐 ETest `A744_print_ACK.py` 的解析逻辑（`data[3:]` 为数据块）。

**打印流程**：

```
AHMU ──URQ(optCode=0, 文件名, 总块数)──▶ 打印机
AHMU ◀────────────── ACK(block_number=0) ── 打印机
AHMU ──DATA(optCode=1, 块号, 数据块)──▶ 打印机
AHMU ◀──────────── ACK(block_number=块号) ── 打印机   (重复 N 次)
AHMU ◀────── STATUS(JobStatus=0 成功) ─────── 打印机   (最后一块后)
```

### 3.2 数据链路与端口

| 方向         | 通道                  | 端口            | 说明                  |
| ---------- | ------------------- | ------------- | ------------------- |
| AHMU → 打印机 | AFDX（A664 VL 23247） | UDP **34801** | 发送 URQ/DATA 分块      |
| 打印机 → AHMU | A429                | UDP **34802** | 回 ACK / 作业状态 STATUS |

> Mock 环境以 UDP 单播模拟 AFDX/A429 总线（真实环境为 A664 VL 与 A429 32 位字）。ACK/状态反馈在 Mock 下以 JSON over UDP 传输，见 `icd_print.py`。

### 3.3 打印队列与降级

- **打印队列**：打印任务串行处理（一次一个），`submit_print` 仅入队，后台 worker 依次执行协议交互，避免多任务并发 UDP 冲突。
- **降级模式**：URQ 发出后若无外部打印机 ACK 响应（如未运行模拟打印机），打印任务**自动降级为内置模拟完成**（仍生成 ps 文件、任务 `completed`），保证无打印机环境下其他功能（数据重置/下载的打印结果）不阻塞。

---

## 四、操作方法

### 方法 1：端到端自动化测试（推荐）

- **用途**：一键验证打印协议完整闭环（T1~T5），脚本内置「模拟打印机」。
- **步骤**：
  1. 确保后端已启动（见前置条件）；
  2. 执行：
     ```cmd
     cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
     venv\Scripts\python.exe tests\test_print.py
     ```
- **脚本内部行为**（模拟打印机）：
  1. 监听 UDP 34801，等待 AHMU 打印数据；
  2. 通过 HTTP 提交一条打印任务；
  3. 收到 URQ → 回 ACK(0)；
  4. 逐块收到 DATA → 回 ACK(块号)；
  5. 最后一块后 → 回 STATUS(JobStatus=0)；
  6. 校验接收数据与提交内容一致，校验后端任务 `completed` 且生成 ps 文件。

### 方法 2：手动提交打印任务

- **方法名称**：`POST /api/v1/print/submit`
- **用途**：提交一条打印任务（**参数经 URL query 传递，非 JSON body**）。
- **步骤**：
  1. 打开 CMD，提交打印：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/print/submit?content=AHMU打印测试&job_type=file_transfer"
     ```
  2. 返回含 `job_id` 即提交成功。
- **注意事项**：
  - `content` 为打印内容（需 URL 编码，中文可用 `curl --data-urlencode` 或省略）；
  - `job_type` 可选 `file_transfer`（文件传输）或 `block_transfer`（块传输，当前为内部模拟）。

### 方法 3：查询打印任务列表与状态

- **方法名称**：`GET /api/v1/print/jobs`
- **用途**：查询打印任务列表、状态、ps 文件路径。
- **步骤**：
  ```cmd
  curl "http://localhost:8443/api/v1/print/jobs?page=1&size=10"
  ```
- **返回字段**：`items[].id / job_type / content / status / printer_status / file_path / created_at / completed_at`。

### 方法 4：通过数据重置/下载间接触发打印

- **用途**：验证数据重置/下载结果打印（真实业务场景，走同一打印服务）。
- **步骤**：
  1. 进入维护模式：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/system/force-mode?mode=maintenance"
     ```
  2. 执行一次数据重置：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/nvm-reset/reset" -H "Content-Type: application/json" -d "{\"member_system\":\"MEM001\",\"reset_type\":\"full\",\"operator\":\"TEST\"}"
     ```
  3. 打印该重置结果（返回 `print_job_id`）：
     ```cmd
     curl -X POST "http://localhost:8443/api/v1/nvm-reset/print/<reset_id>"
     ```
  4. 查询打印任务确认 `completed`（见方法 3）。

### 方法 5：前端提交打印请求（界面反馈验证）

- **用途**：验证「通过前端打印管理页面提交打印请求」的完整调用链路与界面反馈（T8），由 `tests/test_print.py` 的测试项 6 自动执行。
- **触发方式**：前端 `Print.vue` 的「SUBMIT PRINT / 提交打印」面板 → 选择模式 + 输入打印内容 → 点击「提交打印」按钮。
- **请求参数**（`submitPrint` 发出的请求，参数经 URL query 传递）：

  | 参数 | 说明 |
  |------|------|
  | `content` | 打印内容（URL 编码，`encodeURIComponent`） |
  | `job_type` | `file_transfer`（文件传输）/ `block_transfer`（块传输） |

  等价请求示例：
  ```cmd
  curl -X POST "http://localhost:8443/api/v1/print/submit?content=%E6%89%93%E5%8D%B0%E6%B5%8B%E8%AF%95&job_type=file_transfer"
  ```

- **调用链路**：
  ```
  前端「提交打印」按钮 → POST /print/submit?content=xx&job_type=xx
    → print_mgr.submit_print (入打印队列, 状态 queued)
    → 打印 worker 执行 ARINC744A 协议 (URQ/DATA 分块经 UDP 34801 发送)
    → 打印机回 ACK/STATUS (UDP 34802)
    → 后端 WebSocket 广播 print_frame / print_state / print_completed / print_failed
    → 前端界面实时展示协议帧、进度、任务状态
  ```
- **界面反馈**：

  | 场景 | 前端反馈 |
  |------|---------|
  | 内容为空 | `ElMessage.warning('请输入打印内容')`，不发起请求 |
  | 提交成功 | `ElMessage.success('打印任务已提交')`，任务列表出现「排队/发送中」 |
  | 打印中 | 协议交互区实时展示 URQ/ACK/DATA 帧序列，进度条递增 |
  | 打印成功 | 收到 `print_completed` → 进度 100%，任务列表状态「Successful」+ 显示 ps 文件路径 |
  | 打印失败 | 收到 `print_failed` → 任务列表状态「Failed」，`ElMessage.error` 提示失败原因 |

- **自动化验证**（`tests/test_print.py` 测试项 6，通过 WebSocket 监听与前端相同的广播事件）：
  ```cmd
  venv\Scripts\python.exe tests\test_print.py
  ```
  覆盖：模拟前端按钮提交、`print_frame` 广播（含 URQ/DATA）、`print_completed` 广播、任务 `completed` + ps 文件、空内容被拒绝。

---

## 五、预期结果

| 场景         | 预期                                                      |
| ---------- | ------------------------------------------------------- |
| 提交打印任务     | `{"status":"ok","job_id":"<uuid>","message":"打印任务已提交"}` |
| 协议闭环（有打印机） | 模拟打印机依次收到 URQ → DATA → 回 ACK/STATUS，内容一致                |
| 后端任务状态     | 从 `queued` 经 `sending` 流转至 `completed`，`file_path` 非空   |
| ps 文件      | 生成于 `backend/data/printlog/print_<jobid前8位>_<时间戳>.ps`   |
| 无打印机降级     | 任务约 3 秒后仍 `completed`（日志出现「无外部打印机响应, 降级为内置模拟完成」）        |
| 自动化测试      | 末尾显示「测试结果: 4/4 通过」，退出码 `0`                              |

---

## 六、常见问题排查

| 现象                    | 可能原因                        | 解决                                               |
| --------------------- | --------------------------- | ------------------------------------------------ |
| 提交打印报「打印内容不能为空」       | 参数未按 query 传递（误用 JSON body） | 用 `?content=xxx&job_type=file_transfer` 方式传参     |
| 打印任务长时间停在 `sending`   | 打印机未响应且未触发降级                | 确认后端为最新版本；无打印机时会约 3 秒降级为 `completed`             |
| 模拟打印机收不到数据            | 端口 34801 被占用或后端未启动          | `netstat -ano \| findstr 34801` 排查；重启后端          |
| ACK 匹配失败（块重发）         | ACK 与 STATUS 几乎同时到达的竞态（旧版本） | 升级至当前版本（已改用反馈队列按类型匹配）                            |
| ps 文件未生成              | 打印任务失败                      | 查 `backend/data/printlog/` 目录与后端日志 `[Print]` 关键字 |
| `test_print.py` 报端口占用 | 上次运行的打印机进程未退出               | 结束残留 python 进程后重试                                |

---

## 七、相关文件

| 文件                                    | 说明                                    |
| ------------------------------------- | ------------------------------------- |
| `backend/app/core/icd_print.py`       | ARINC744A 打印协议编解码（URQ/DATA 分块、ACK/状态） |
| `backend/app/services/print_mgr.py`   | 打印管理服务（UDP 收发、队列、状态机、降级）              |
| `backend/app/config.py`               | `PRINT_CONFIG`（打印通道端口/块大小/超时/重发）      |
| `backend/app/database.py`             | `PrintJob` 表（打印任务状态与 ps 文件路径）         |
| `backend/tests/test_print.py`         | 打印功能端到端测试程序（内置模拟打印机）                  |
| `docs/lifecycle-test-guide.md`        | 生命周期测试指南（同类文档参考）                      |
| `docs/maintenance-mode-test-guide.md` | 维护模式控制测试指南（方法 4 前置依赖）                 |
