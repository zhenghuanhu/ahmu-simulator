# test_case 测试代码 vs AHMU 仿真器 功能覆盖对照报告

> 分析对象：`机载航电/OHMS/test_case` 目录下全部测试代码（17 个 `.py` 脚本 + 3 个 `.txt` 辅助说明）
> 对照基准：AHMU 仿真器当前实现（`ahmu-simulator/backend/app/api/routes.py` + `backend/app/services/*`）
> 核对方法：从测试代码中提取调用的 HTTP API 与 UDP 收发场景，逐项在 AHMU 后端检索对应端点/服务方法
> 更新日期：2026-11-09

## 一、覆盖核查口径说明

测试代码由两类元素构成，AHMU 仿真器**只需实现其中一类**：

| 类型 | 归属 | 是否需 AHMU 实现 |
| --- | --- | --- |
| 测试端 OHMS 库类（`A429Uploader`/`TFTPServer`/`Acars`/`TimeCycle_MS`/`DDS_MS`/`AircraftStatusMessage`/`AhmuApi` 等） | 测试框架自带，负责构造并发送 ICD/UDP 报文 | ❌ 不需要 |
| 测试端 HTTP 客户端函数（`get_DLATAs`/`get_PDStartViewRequest`/`Start_Fault_History`/`AhmuApi.get_*` 等） | 向 AHMU 后端发 HTTP 请求 | ✅ 需要 AHMU 提供对应端点 |
| AHMU 下行数据（TFTP 文件、ACARS 下行、打印 ps 文件、ACR 构型响应） | AHMU 经 UDP 回传给测试端 | ✅ 需要 AHMU 提供 UDP 发送能力 |
| AHMU 接收 ICD 数据（故障/失效/FDE/构型/飞机状态） | AHMU 经 UDP 接收 | ✅ 需要 AHMU 提供 UDP 接收能力 |

据此，覆盖判定标准为：**AHMU 后端是否提供了测试代码所依赖的 HTTP 端点与 UDP 收发能力**。

## 二、测试用例功能点与场景提取

| # | 测试文件 | 功能点 | 测试场景 | 依赖的 AHMU 接口 |
| --- | --- | --- | --- | --- |
| 1 | 地面人机界面.py | 故障诊断全链路 | 注入故障 `AOT3 Instance Loss Failure` + FDE `42IM0062`，走 故障→失效→FDE 链路并逐项查询 | `get_inbound_leg_failures_all` / `get_existing_failures` / `get_failure_history` / `get_inbound_leg_fdes` / `get_existing_fdes` / `get_failure_detail_fde` |
| 2 | FDE消息.py | FDE 关联失效 | 发送 3 路 FDE 消息，轮询校验 | `get_inbound_leg_fdes` |
| 3 | AHMU频率测试.py | 故障处理频率/性能 | 按 500/1000/2000/4000ms 周期发送故障 | 故障 ICD UDP 接收 |
| 4 | 批量创建失效报告和故障模型.py | 批量故障+失效关联 | 5 航段×5 故障注入 | `get_inbound_leg_failures_all` / `get_failure_history` |
| 5 | 批量创建失效报告和故障模型.txt | 批量故障/失效 | 2000 故障→2000 失效 | `get_inbound_leg_failures_all`(≥1) / `get_failure_history`(≥2000) |
| 6 | 故障与失效报告批量创建.txt | 大批量压力 | 2020 失效+2499 故障 | `get_existing_failures`(≥2000) / `get_inbound_leg_failures_all`(≥1973) |
| 7 | 200NVM.py | 故障历史检索（199 成员） | 遍历 199 成员发起检索并查状态 | `Start_Fault_History` / `Query_Fault_History_Task_Status` |
| 8 | 故障历史报告获取664.py | 故障历史检索+TFTP 下载 | 多成员检索 + TFTP 接收上传文件 | `Start_Fault_History_Retrieval_Task` / TFTP UDP 下载 |
| 9 | 打印.py | 打印管理 | A774 打印任务 + ps 文件下载 | A744/A774 打印协议 + ps 文件 UDP 下发 |
| 10 | ACARS.py | ACARS 数据链 | 10 个 EOR 上行 + ROLLOUT/TAXI-IN 滑入触发下行 | ACARS EOR 上行 UDP 接收 + 下行触发 |
| 11 | 构型信息.py | 构型报告回读验证 | 打包 ConfigReport → 发送 → 回读 verify | `verify_requestData` + ACR 报告回传 |
| 12 | 构型参数发送.py | ACR 构型请求(429/664) | 按 Deal_Type 区分 429/664 发送构型请求 | ACR 请求 UDP 接收 + 响应回传 |
| 13 | 生命周期.py | 生命周期获取 | 逐成员 tcRetrieval 获取 + get_tcstatus | `tcRetrieval` / `get_tcstatus` / TimeCycle |
| 14 | 维护模式控制.py | 维护模式切换 | normal_mode(engine_start) → maintenance_mode(maintenance) | 维护模式信号接收 |
| 15 | 飞机事件信息.py | 飞机事件信息 | event_mode 交替注入 FMS_IC_1_PARA=1/0（3 轮） | FMS_IC_1_PARA 事件接收 |
| 16 | 429数据加载.py | A429 数据加载 | 完整 DL 流程 | `get_DLATAs`/`get_DLEquips`/`get_DLPart`/`get_ACRValidConfig`/`get_DLLoadCondition`/`get_DLStartLoad`/`get_DLStatus` |
| 17 | 664数据加载.py | A664 数据加载(TFTP) | 完整 DL 流程 | 同上 DL API 族 |
| 18 | 启用数据加载.py | 数据加载启用 | 遍历 ATA 逐项 enable | `get_DLATAs` / `enable_ATA` |
| 19 | 参数监控报告.py | 参数监控+报告 | ATA73 `Selected_N1_Indicated` 采样 35 次→生成报告 | `get_PDATAsRequest`/`get_PDParamByATARequset`/`get_PDStartViewRequest`/`get_PDStopViewRequest`/`get_PDParamValueRequest`/`get_PDGenerateReportRequest`/`get_PDContinueRequest` |
| 20 | 成员系统.txt | （空文件，无测试逻辑） | — | — |

## 三、覆盖对照总表

| 测试覆盖的功能域 | 测试文件 | AHMU 后端实现 | 覆盖状态 |
| --- | --- | --- | --- |
| 故障诊断全链路 | 地面人机界面 / FDE消息 / AHMU频率测试 / 批量创建*.py/.txt / 故障与失效*.txt | `/getParameter`（按 messageID 0x0101~0x0108 分发）+ `fault_diagnosis.py` 六查询方法 | ✅ 已覆盖 |
| 故障历史检索 | 200NVM / 故障历史报告获取664 | `/FHATAsRequest` / `/FHEquipRequest` / `/FHRetrievalRequest` / `/FHStatusRequest` + TFTP 下载 | ✅ 已覆盖 |
| 打印管理 | 打印.py | `print_mgr.py`（ARINC744A，UDP 34801/34802，ps 文件下发对齐 A744_print_ACK） | ✅ 已覆盖 |
| ACARS 数据链 | ACARS.py | `acars.py`（ARINC619，UDP 34810/34811，EOR 上行→下行） | ✅ 已覆盖 |
| 构型管理(ACR) | 构型信息 / 构型参数发送 | `config_management.py`（ACR 目录、构型报告接收、Deal_Type 429/664 请求响应、ACR*Request 端点） | ✅ 已覆盖 |
| 生命周期 | 生命周期.py | `lifecycle.py`（`retrieve_member` 真实/模拟 + UDP TimeCycle 接收） | ✅ 已覆盖 |
| 维护模式 | 维护模式控制.py | `maintenance_mode.py`（模式监控 + `/system/maintenance-switch`） | ✅ 已覆盖 |
| 飞机事件信息 | 飞机事件信息.py | `aircraft_status.py`（`fms_ic_1_para` 事件参数 + 写 EventReport） | ✅ 已覆盖 |
| **数据加载(ARINC615)** | 429数据加载 / 664数据加载 / 启用数据加载 | `data_load.py` 仅 `start_load`/`get_load_tasks`；缺 DL API 族 | ❌ **未覆盖** |
| **参数监控实时采样** | 参数监控报告.py | `param_monitor.py` 仅配置管理/报告历史；缺 PD 采样控制 API | ❌ **未覆盖** |

## 四、已覆盖功能清单

以下 8 个功能域已完整对齐测试代码（含对应 AHMU 端点/服务）：

1. **故障诊断全链路**（6 个查询接口全部实现）
   - `get_inbound_leg_failures_all`(0x0103)、`get_existing_failures`(0x0106)、`get_failure_history`(0x0108)、`get_inbound_leg_fdes`(0x0101)、`get_existing_fdes`(0x0102)、`get_failure_detail_fde`
   - 统一由 `POST /getParameter` 按 `messageID` 分发，`fault_diagnosis.py` 实现全部查询方法。

2. **故障历史检索 + TFTP 下载**
   - `POST /FHATAsRequest`、`/FHEquipRequest`、`/FHRetrievalRequest`、`/FHStatusRequest`（注释明确「对齐 OHMS test_case/200NVM.py」），`fault_history_tftp.py` 支持 TFTP 上传。

3. **打印管理**：`print_mgr.py` 基于 ARINC744A，UDP 34801/34802，`_execute_file_transfer` 对齐 ETest `A744_print_ACK.py` 的接收解析逻辑。

4. **ACARS 数据链**：`acars.py` 基于 ARINC619，UDP 34810/34811，`_handle_uplink_eor` 接收 EOR 上行并触发 `_send_downlink` 下行。

5. **构型管理(ACR)**：`config_management.py` 构建 ACR 目录，`process_config_report`/`apply_config_report` 处理构型报告，`handle_acr_request` 按 Deal_Type(1=429/2=664) 响应构型请求，ACR*Request 端点齐全。

6. **生命周期**：`lifecycle.py` 提供 `retrieve_member`（真实飞控 UDP 指令 + 模拟回填），UDP TimeCycle 接收循环。

7. **维护模式**：`maintenance_mode.py` 模式监控循环（normal/maintenance 条件判定）+ `/system/maintenance-switch`。

8. **飞机事件信息**：`aircraft_status.py` 维护 `fms_ic_1_para` 事件参数，变化时写 `EventReport`。

## 五、未覆盖 / 存在差异的功能清单

### 5.1 数据加载 DL API 族（❌ 完全缺失，3 个测试文件受影响）

测试代码依赖 8 个 HTTP API 构成完整加载流程，AHMU 后端**一个都未实现**：

| 测试调用 | 用途 | AHMU 现状 |
| --- | --- | --- |
| `get_DLATAs(sessionID, pageNumber)` | 获取 ATA 列表 | ❌ 无对应端点 |
| `get_DLEquips(sessionID, ata, pageNumber)` | 获取设备列表 | ❌ 无对应端点 |
| `get_DLPart(sessionID, IsPrint, selectedType, selectedID)` | 获取部件列表 | ❌ 无对应端点 |
| `get_ACRValidConfig(...)` | ACR 有效构型校验 | ❌ 无对应端点 |
| `get_DLLoadCondition(...)` | 加载条件检查 | ❌ 无对应端点 |
| `get_DLStartLoad(...)` | 发起加载 | ❌ 无对应端点 |
| `get_DLStatus(...)` | 轮询加载状态 | ❌ 无对应端点 |
| `enable_ATA(ata)` | 启用 ATA | ❌ 无对应端点 |

> 现状：`data_load.py` 仅有 `start_load`/`_execute_load`/`get_load_tasks`/`active_load_count`，`routes.py` 仅有 `/dataload/start`、`/dataload/list` 两个端点，与测试代码的 DL API 族命名、参数、返回结构均不匹配。

### 5.2 参数监控 PD 实时采样 API 族（❌ 完全缺失，1 个测试文件受影响）

测试代码依赖 8 个 HTTP API 构成「开始采样→停采→取值→生成报告→继续」流程，AHMU 后端**未实现实时采样控制部分**：

| 测试调用 | 用途 | AHMU 现状 |
| --- | --- | --- |
| `get_PDATAsRequest` | 参数 ATA 列表 | ⚠️ 部分对应（`/params/atas`，字段名不同） |
| `get_PDParamByATARequset` | 按 ATA 查参数 | ⚠️ 部分对应（`/params/list`，字段名不同） |
| `get_PDParamByATARequset_awargs` | 按关键字过滤参数 | ❌ 无对应 |
| `get_PDStartViewRequest` | 开始采样（返回 startResult） | ❌ 无对应 |
| `get_PDStopViewRequest` | 停止采样（返回 sampleCount） | ❌ 无对应 |
| `get_PDParamValueRequest` | 获取采样值 | ❌ 无对应 |
| `get_PDGenerateReportRequest` | 生成参数报告 | ⚠️ 部分对应（`/params/report`，字段名不同） |
| `get_PDContinueRequest` | 结束后继续采样 | ❌ 无对应 |

> 现状：`param_monitor.py` 实现了参数配置管理、报告历史、快捷清单、监控服务启停、仿真模式，但**没有实时采样控制链路**（开始/停止采样、逐秒取值、继续采样）。

## 六、改进建议

### 6.1 数据加载 DL API 族补齐（P0，阻塞 3 个测试文件）

在 `data_load.py` 与 `routes.py` 中补齐 8 个 DL 端点，命名、参数、返回结构须与 OHMS `http_client` 客户端一致：

1. 新增端点 `/dataload/atas`（对应 `get_DLATAs`，返回 `data[0]['dlATAStructList']`）；
2. 新增 `/dataload/equips`（`get_DLEquips`，返回 `dLEquipStructList`）；
3. 新增 `/dataload/parts`（`get_DLPart`，返回 `dlPartStructList`）；
4. 新增 `/dataload/acr-valid-config`（`get_ACRValidConfig`）；
5. 新增 `/dataload/load-condition`（`get_DLLoadCondition`）；
6. 新增 `/dataload/start-load`（`get_DLStartLoad`，与现有 `start_load` 合并或封装）；
7. 新增 `/dataload/status`（`get_DLStatus`，返回 `dlStatusStructList` 含 `loadId`/`status`，状态枚举对齐测试端 `status_list1`/`status_list2`）；
8. 新增 `/dataload/enable-ata`（`enable_ATA`，返回 `dlEquipStructList`）。

> 注意返回结构必须为 `list` 包裹（测试端按 `data[0][...]` 取值），且字段名（`dlATAStructList` 等）严格对齐。

### 6.2 参数监控 PD 采样控制链路补齐（P0，阻塞 1 个测试文件）

在 `param_monitor.py` 中新增实时采样控制能力：

1. 实现「采样会话」状态机（空闲→采样中→已停止），记录采样计数与逐秒参数值；
2. 新增 `/params/view/start`（`get_PDStartViewRequest`，返回 `startResult`）、`/params/view/stop`（`get_PDStopViewRequest`，返回 `stopResult`+`sampleCount`）、`/params/value`（`get_PDParamValueRequest`，返回 `pdParamValueStructList`）、`/params/view/continue`（`get_PDContinueRequest`）；
3. 将参数列表查询返回结构调整为测试端期望的 `pdATAStructList`/`pdParamStructList` 字段；
4. 采样数据来源：接入现有 AFDX 参数接收（`ingest_afdx_signals`/`receive_paa_params`），使 ATA73 `Selected_N1_Indicated` 等参数可被采样。

### 6.3 建议的推进顺序

| 优先级 | 事项 | 影响 |
| --- | --- | --- |
| P0 | 6.1 数据加载 DL API 族 | 解除 429/664/启用数据加载 3 个测试阻塞 |
| P0 | 6.2 参数监控 PD 采样链路 | 解除参数监控报告测试阻塞 |
| P1 | 字段名/返回结构一致性回归 | 确保已覆盖接口的字段与测试端期望严格一致 |
| P2 | 端到端联调验证 | 逐个跑通 20 个测试用例，形成覆盖闭环 |

## 七、结论

`test_case` 目录 20 个文件覆盖 **10 个功能域**，其中 8 个已由 AHMU 仿真器完整支持（故障诊断、故障历史检索、打印、ACARS、构型管理、生命周期、维护模式、飞机事件信息），**2 个功能域存在完全缺失**（数据加载 DL API 族、参数监控 PD 实时采样 API 族），导致 4 个测试文件（429数据加载、664数据加载、启用数据加载、参数监控报告）无法跑通。

缺口集中在「数据加载」与「参数监控实时采样」两条链路的 HTTP API 层，属于**接口层补齐**问题，不涉及协议栈重构，改动可控，建议按 6.1/6.2 优先补齐。
