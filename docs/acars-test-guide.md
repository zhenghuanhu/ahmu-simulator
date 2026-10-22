# ACARS 管理功能测试指南

## 概述

本文档说明 AHMU 仿真器「ACARS 管理」功能的上行（上传）与下行（下传）测试方法。

AHMU 仿真器**仅实现 AHMU 自身功能**：并行提取内部数据（故障/失效+FDE/构型/事件+参数）→ 整合为结构化维护数据包 → 封装为 ARINC664 帧（载荷=维护数据包）→ 通过 A664 总线（Mock UDP）向下发送，并接收 CPM 转发的上行 EOR 请求与 A664 ACK。

ARINC619 报文的封装、664↔429 转换、CMU 通信终端等**下游功能合并到一个模拟程序** `simulate_acars_chain.py` 中实现，完整模拟 14 步下行数据流中的步骤 5~14。

---

## 目录

- [一、测试目标](#一测试目标)
- [二、前置条件](#二前置条件)
- [三、关键概念](#三关键概念)
  - [3.1 下行数据流（14 步）](#31-下行数据流14-步)
  - [3.2 端口与链路](#32-端口与链路)
  - [3.3 EOR 报告标识](#33-eor-报告标识)
- [四、上行（上传）测试](#四上行上传测试)
- [五、下行（下传）测试](#五下行下传测试)
- [六、预期结果](#六预期结果)
- [七、注意事项](#七注意事项)
- [八、相关文件](#八相关文件)

---

## 一、测试目标

### 1.1 上行（上传）测试目标

| 编号 | 测试目标 | 验证点 |
| --- | --- | --- |
| U1 | 上行 EOR 请求接收 | AHMU 收到 A664 帧（载荷=EOR 标识）后触发下行 |
| U2 | 维护数据提取与整合 | 按 EOR 类型从数据库提取故障/失效/FDE/构型/事件数据并整合为维护数据包 |
| U3 | 维护数据下发 | 故障报告、失效报告、FDE、构型+事件报告均能按 EOR 正确下发 |
| U4 | 消息记录 | 上行/下行消息正确入库，方向、类型、优先级字段一致 |

### 1.2 下行（下传）测试目标

| 编号 | 测试目标 | 验证点 |
| --- | --- | --- |
| D1 | 正常下传 | 下游逐级 ACK 反馈，AHMU 收到 A664 ACK，下传成功且维护数据 CRC 校验正确 |
| D2 | 下游丢失处理 | 下游不回 A664 ACK 时，AHMU 重发后判「A664 ACK 超时」并标记链路丢失 |
| D3 | 异常响应处理 | 下游回错误帧时，AHMU 丢弃非匹配帧后判超时 |

---

## 二、前置条件

1. **后端已启动**：AHMU 仿真器运行中，监听 HTTP `8443` 与 UDP `34810`。
   ```cmd
   cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
   venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8443
   ```
2. **端口未占用**：`34810`（AHMU 收上行）与 `34811`（AHMU 发下行）未被其他进程占用。
3. **测试脚本就位**：`backend/tests/` 下存在 `simulate_acars_chain.py`、`test_acars.py`、`test_acars_downlink.py`。

---

## 三、关键概念

### 3.1 下行数据流（14 步）

AHMU 仿真器只负责步骤 1~4，步骤 5~14 由模拟程序 `simulate_acars_chain.py` 合并实现：

| 步骤 | 节点 | 动作 |
| --- | --- | --- |
| 1 | AHMU | 并行提取内部数据（故障/失效+FDE/构型/事件+参数） |
| 2 | AHMU | 整合为结构化维护数据包（JSON） |
| 3 | AHMU | 封装为 A664 帧（载荷=维护数据包） |
| 4 | AHMU | 通过 A664 总线向下发送 |
| 5 | CPM | 解析 A664 帧，提取维护数据包 |
| 6 | CPM | 将维护数据转换为 ARINC619 协议报文 |
| 7 | CPM | 将 ARINC619 报文重新封装成新的 A664 帧 |
| 8 | CPM | 通过 A664 总线向下发送（载荷=ARINC619 报文） |
| 9 | RDCU | 解析 A664 帧头，提取 ARINC619 报文 |
| 10 | RDCU | 执行协议转换：ARINC664 → ARINC429 |
| 11 | RDCU | 通过 A429 总线转发给 CMU |
| 12 | CMU | 返回 A429 链路确认（ACK）给 RDCU（虚线反馈） |
| 13 | RDCU | 返回 A664 总线确认（ACK）给 CPM（虚线反馈） |
| 14 | CPM | 返回 A664 总线确认（ACK）给 AHMU（虚线反馈） |

### 3.2 端口与链路

| 端口 | 方向 | 说明 |
| --- | --- | --- |
| `34810` | CPM → AHMU | AHMU 上行接收端口（下游发送到此处） |
| `34811` | AHMU → CPM | AHMU 下行发送端口（下游监听此处） |

### 3.3 EOR 报告标识

| EOR 标识 | 报告类型 |
| --- | --- |
| `DNL_ACMF101~106/0/EOR` | 故障报告（fault_report） |
| `DNL_ACMF201~202/0/EOR` | 失效报告（failure_report） |
| `DNL_ACMF301/0/EOR` | FDE（驾驶舱效应） |
| `DNL_ACMF401/0/EOR` | 构型数据 + 状态监测事件报告 |

---

## 四、上行（上传）测试

### 方法 1：端到端自动化测试（推荐）

一键运行完整上行链路测试（配置/状态/消息记录/上行 EOR 请求触发下行）：

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
venv\Scripts\python.exe tests\test_acars.py
```

预期末尾输出 `测试结果: 8/8 通过`。

### 方法 2：手动模拟上行请求

发一个 EOR 上行请求触发 AHMU 下行，并接收处理 AHMU 下发的维护数据（展示完整 14 步）：

```cmd
venv\Scripts\python.exe tests\simulate_acars_chain.py --uplink DNL_ACMF101/0/EOR --once
```

可选参数：

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--uplink` | 无 | 要发送的 EOR 上行请求标识 |
| `--once` | — | 处理一次后退出 |
| `--recv-port` | `34811` | 下游监听端口 |
| `--send-port` | `34810` | 下游发送端口 |

---

## 五、下行（下传）测试

### 方法 1：自动化测试三场景（推荐）

一键运行下行下传三场景（正常/下游丢失/异常响应）：

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
venv\Scripts\python.exe tests\test_acars_downlink.py
```

预期末尾输出 `测试结果: 3/3 通过`。

### 方法 2：手动单场景模拟

```cmd
:: 正常响应（验证下传成功）
venv\Scripts\python.exe tests\simulate_acars_chain.py --mode normal --once

:: 下游丢失（不回 ACK，验证超时与链路丢失）
venv\Scripts\python.exe tests\simulate_acars_chain.py --mode ack-timeout --once

:: 异常响应（回错误帧，验证丢弃非匹配帧）
venv\Scripts\python.exe tests\simulate_acars_chain.py --mode bad-response --once
```

> 注意：手动单场景需**先启动模拟程序，再在另一终端触发下传**（页面「下传」或 curl），模拟程序会响应一次后退出。

### 方法 3：通过 ACARS 管理页面手动下传

1. 先启动下游模拟（`simulate_acars_chain.py --mode normal`，不加 `--once` 持续监听）；
2. 打开 ACARS 管理页面（导航「ACARS」），在「下行消息下发」区选择类型/优先级、输入内容；
3. 点击「下传」按钮。

- 下游在线 → 提示「消息已下传」，消息记录新增一条下行记录；
- 下游不在线 → 提示「下传失败（需 CPM/CMU 在线响应）」。

---

## 六、预期结果

| 测试 | 场景 | 预期结果 |
| --- | --- | --- |
| 上行 | `test_acars.py` | 8/8 通过 |
| 上行 | `simulate_acars_chain.py --uplink ... --once` | 依次输出步骤 5~14，最终「下游链路完成 crc_ok=True」 |
| 下行 | `test_acars_downlink.py` | 3/3 通过 |
| 下行 | `normal` | 后端 `status=ok`「下行已发送」，下游收到 CRC 正确维护数据 |
| 下行 | `ack-timeout` | 后端 `status=error`「无 CPM/CMU 响应 (A664 ACK 超时)」 |
| 下行 | `bad-response` | 后端 `status=error`「无 CPM/CMU 响应 (A664 ACK 超时)」 |

---

## 七、注意事项

1. **启动顺序**：必须先启动后端，再运行模拟程序/测试脚本，否则会报「未收到 AHMU 响应」。
2. **端口冲突**：34810/34811 被占用时，模拟程序绑定会失败；需先释放端口。
3. **页面下传依赖下游在线**：页面「下传」走真实 A664 链路，无下游响应时返回「A664 ACK 超时」，属预期行为，并非功能缺陷。
4. **超时耗时**：AHMU 默认 `ack_timeout_sec=3`、`max_retry=3`，下游丢失场景约耗时 9 秒，测试时请耐心等待。
5. **消息记录时间**：消息时间为 UTC 存储（带 `Z` 后缀），前端已自动转换为本地时区显示。
6. **优先级独立**：页面「传输配置」的默认优先级与「下行下发」选择的优先级相互独立，发送时不改变配置默认值。

---

## 八、相关文件

| 文件 | 说明 |
| --- | --- |
| `backend/tests/test_acars.py` | 上行端到端测试（8 项） |
| `backend/tests/test_acars_downlink.py` | 下行下传测试（3 场景） |
| `backend/tests/simulate_acars_chain.py` | 模拟下游链路（合并 CPM+RDCU+CMU，模拟 14 步步骤 5~14） |
| `backend/app/core/icd_acars.py` | A664 帧封装 + ARINC619 编解码（ARINC619 供模拟程序 CPM 封装用）+ EOR 映射 |
| `backend/app/services/acars.py` | ACARS 通信服务（AHMU 侧：提取/整合/封 A664/收发/链路状态） |
