# OHMS/test_case 测试用例覆盖分析报告

> 分析对象：`机载航电/OHMS/test_case` 文件夹全部测试用例
> 对照基准：AHMU 仿真器后端 18 个功能模块（`ahmu-simulator/backend/app/services`）

## 一、测试文件清单

共 **17 个可执行测试用例**（另有 3 个 `.txt` 为辅助说明，其中 `成员系统.txt` 为空）：

| # | 文件 | 归属功能 |
| --- | --- | --- |
| 1 | 200NVM.py | 故障历史启动 + 多成员系统支持 |
| 2 | 429数据加载.py | A429 数据加载 |
| 3 | 664数据加载.py | A664 数据加载 |
| 4 | ACARS.py | ACARS 数据链 |
| 5 | AHMU频率测试.py | 故障处理频率/性能 |
| 6 | FDE消息.py | FDE 关联 |
| 7 | 参数监控报告.py | 参数监控 + 报告生成 |
| 8 | 启用数据加载.py | 数据加载启用 |
| 9 | 地面人机界面.py | 故障/失效/FDE 关联 |
| 10 | 打印.py | 打印管理 |
| 11 | 批量创建失效报告和故障模型.py | 批量故障/失效 |
| 12 | 故障历史报告获取664.py | 故障历史下载（A664） |
| 13 | 构型信息.py | 构型报告 |
| 14 | 构型参数发送.py | 构型参数（ACR） |
| 15 | 生命周期.py | 生命周期 |
| 16 | 维护模式控制.py | 维护模式控制 |
| 17 | 飞机事件信息.py | 飞机事件信息 |

## 二、按功能模块归类（对照 AHMU 后端模块）

| AHMU 功能模块 | 对应测试用例 | 覆盖程度 |
| --- | --- | --- |
| 故障诊断 | 地面人机界面、AHMU频率测试、批量创建失效报告和故障模型(.py/.txt)、故障与失效报告批量创建.txt | ✅ 强 |
| 失效报告 | 地面人机界面、批量创建失效报告和故障模型、故障与失效报告批量创建.txt | ✅ 强 |
| FDE（驾驶舱效应） | FDE消息、地面人机界面 | ✅ |
| 故障历史报告 | 200NVM、故障历史报告获取664 | ✅ |
| 数据加载（ARINC615/TFTP） | 429数据加载、664数据加载、启用数据加载 | ✅ 强 |
| 构型管理 | 构型信息、构型参数发送 | ✅ |
| 生命周期 | 生命周期 | ✅ |
| 打印管理 | 打印 | ✅ |
| 维护模式控制 | 维护模式控制 | ✅ |
| 启动测试 | 200NVM（199 成员启动服务） | ⚠️ 部分 |
| 参数监控 | 参数监控报告 | ⚠️ 弱 |
| ACARS 数据链 | ACARS | ⚠️ 弱 |
| 飞机状态消息 | 飞机事件信息、维护模式控制 | ⚠️ 弱 |
| **发动机配平（4.3.9）** | — | ❌ 未覆盖 |
| **数据重置管理（4.3.10）** | — | ❌ 未覆盖 |
| **数据下载管理（4.3.14）** | — | ❌ 未覆盖 |
| **通信安全防护（4.3.5）** | — | ❌ 未覆盖 |

## 三、各用例主要测试目的与验证要点

### 3.1 故障诊断 / 失效报告 / FDE

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 地面人机界面.py | 故障→失效→FDE 全链路 | 注入 `AOT3 Instance Loss Failure` + FDE `42IM0062`，逐一校验 `inbound_leg_failures_all / existing_failures / failure_history / failure_detail_fde / inbound_leg_fdes / existing_fdes` |
| FDE消息.py | FDE 与故障关联失效 | 发送 3 路 FDE 消息，`get_inbound_leg_fdes` 轮询校验 |
| AHMU频率测试.py | 不同周期故障处理能力 | 按 500/1000/2000/4000ms 周期发送故障，验证 AHMU 稳定处理 |
| 批量创建失效报告和故障模型.py/.txt | 批量故障模型 + 失效报告关联 | 5 航段×5 故障，`get_inbound_leg_failures_all` 与 `get_failure_history>=2000` 校验 |
| 故障与失效报告批量创建.txt | 大批量（2020 失效/2499 故障）压力 | `get_existing_failures>=2000`、`get_inbound_leg_failures_all>=1973` |

### 3.2 故障历史报告

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 200NVM.py | 故障历史启动 + 199 成员支持 | `Start_Fault_History`→`Query_Fault_History_Task_Status`，`check(pmat_inittest_ms >= 199)` |
| 故障历史报告获取664.py | 多成员故障历史 TFTP 下载 | 逐成员 `Start_Fault_History_Retrieval_Task` + `Query_Fault_History_Task_Status`，`check(failed==0)` |

### 3.3 数据加载

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 429数据加载.py | A429 通道 ARINC615 加载 | `A429Uploader` + `get_DLATAs/DLEquips/DLPart/ACRValidConfig/DLLoadCondition/DLStartLoad/DLStatus` |
| 664数据加载.py | A664 通道 TFTP(615A) 加载 | `TFTPServer` + 同一组 DL API |
| 启用数据加载.py | 全 ATA 启用 | `get_DLATAs` 分页遍历 + `enable_ATA` |

### 3.4 构型管理

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 构型信息.py | 构型报告发送与回读验证 | `ConfigReport` 打包 → 发送 → `verify_requestData` 校验硬件/软件/特定数据与 Excel 一致 |
| 构型参数发送.py | ACR 构型参数（429/664） | 按端口遍历 `make_429/664_acr_protocol` 发送 |

### 3.5 参数监控 / 打印 / ACARS / 生命周期 / 维护模式 / 飞机事件

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 参数监控报告.py | 参数监控 + 报告生成 | ATA73 `Selected_N1_Indicated` 35 次采样，`get_PDStartViewRequest/StopView/ParamValue/GenerateReport/Continue` |
| 打印.py | 打印任务 | `A774_Print` 启动 + ps 文件下载目录 |
| ACARS.py | ACARS 上行 EOR + 下行触发 | `Acars`(10 个 EOR) + 触发滑入阶段(ROLLOUT/TAXI-IN) |
| 生命周期.py | 成员系统生命周期 | `TimeCycle_MS` + `tcRetrieval/get_tcstatus` 校验 |
| 维护模式控制.py | 正常/维护模式切换 | `normal_mode('engine_start')` / `maintenance_mode('maintenance')` |
| 飞机事件信息.py | 飞机事件信息 | `event_mode` 交替注入 `FMS_IC_1_PARA=1/0`（3 轮）校验回传 |

## 四、未覆盖 / 覆盖薄弱的功能

### 4.1 未覆盖（❌）

| 功能 | 说明 |
| --- | --- |
| **发动机配平（4.3.9）** | AHMU 后端有 `engine_trim.py`，但 test_case 中无任何测试用例（配平指令下发/越界校验/来源校验等均未覆盖） |
| **数据重置管理（4.3.10）** | 后端有 `nvm_reset.py`，无对应测试（`200NVM.py` 名字含 NVM 但实为故障历史） |
| **数据下载管理（4.3.14）** | 后端有 `nvm_download.py`，无对应测试 |
| **通信安全防护（4.3.5）** | 后端有 `comm_security.py`，无对应测试 |

### 4.2 覆盖薄弱（⚠️）

| 功能 | 薄弱点 |
| --- | --- |
| 参数监控 | 仅测 ATA73 单一参数 `Selected_N1_Indicated`，未覆盖多参数/多 ATA/AFDX 数据链路（`afdx_receiver`） |
| ACARS | 仅测上行 EOR 请求 + 滑入阶段触发下行，未测完整下行握手（RTS/CTS/DATA/ACK）、链路状态监控（空闲/繁忙/丢失）、优先级/下传链路/触发逻辑配置 |
| 飞机状态消息 | 仅测维护模式 + FMS_IC_1_PARA，其余 14 个飞行阶段（power_on/climb/cruise/descent/approach 等）缺少逐阶段覆盖 |
| 启动测试 | 仅覆盖"199 成员启动测试服务"，设备级(PAA_L3)/系统级启动测试流程未完整覆盖 |

## 五、总结

test_case 对 AHMU 的**故障诊断/失效/FDE、数据加载、构型、故障历史、打印、生命周期**等核心功能覆盖较充分；但 **发动机配平、数据重置、数据下载、通信安全防护** 四个后端模块**完全无测试用例**，且参数监控、ACARS、飞机状态消息、启动测试四个模块覆盖偏弱。

> 建议后续针对上述未覆盖/薄弱项补充测试用例，优先补齐四个完全未覆盖的后端模块（发动机配平、数据重置、数据下载、通信安全防护）。
