# OHMS/test_case 测试用例覆盖分析报告

> 分析对象：`机载航电/OHMS/test_case` 文件夹全部测试用例
> 对照基准：AHMU 仿真器后端功能模块（`ahmu-simulator/backend/app/services`，含 `fault_history_tftp` 等共 20 个模块）
> 更新日期：2026-10-30（随 AHMU 功能扩展更新：ACARS 完整实现、构型 ACR 三阶段、故障诊断优化、启动测试、TFTP 上传）

## 一、测试文件清单

共 **17 个可执行测试用例**（另有 3 个 `.txt` 为辅助说明，其中 `成员系统.txt` 为空）：

| # | 文件 | 归属功能 |
| --- | --- | --- |
| 1 | 200NVM.py | 故障历史检索（199 成员启动测试服务） |
| 2 | 429数据加载.py | A429 数据加载 |
| 3 | 664数据加载.py | A664 数据加载 |
| 4 | ACARS.py | ACARS 数据链 |
| 5 | AHMU频率测试.py | 故障处理频率/性能 |
| 6 | FDE消息.py | FDE 关联 |
| 7 | 参数监控报告.py | 参数监控 + 报告生成 |
| 8 | 启用数据加载.py | 数据加载启用 |
| 9 | 地面人机界面.py | 故障/失效/FDE 全链路 + getParameter 查询 |
| 10 | 打印.py | 打印管理 |
| 11 | 批量创建失效报告和故障模型.py/.txt | 批量故障/失效 |
| 12 | 故障与失效报告批量创建.txt | 批量失效/故障 + getParameter 查询 |
| 13 | 故障历史报告获取664.py | 故障历史检索 + TFTP 下载 |
| 14 | 构型信息.py | 构型报告（ACR 查询） |
| 15 | 构型参数发送.py | 构型参数（ACR 请求） |
| 16 | 生命周期.py | 生命周期 |
| 17 | 维护模式控制.py | 维护模式控制 |
| 18 | 飞机事件信息.py | 飞机事件信息（飞机状态消息） |

## 二、按功能模块归类（对照 AHMU 后端模块）

| AHMU 功能模块 | 对应测试用例 | 覆盖程度 |
| --- | --- | --- |
| 故障诊断（getParameter 查询 + ICD 接收） | 地面人机界面、AHMU频率测试、批量创建失效报告和故障模型(.py/.txt)、故障与失效报告批量创建.txt | ✅ 强 |
| 失效报告 | 地面人机界面、批量创建失效报告和故障模型、故障与失效报告批量创建.txt | ✅ 强 |
| FDE（驾驶舱效应） | FDE消息、地面人机界面 | ✅ |
| 故障历史检索 + TFTP 上传 | 200NVM、故障历史报告获取664 | ✅ |
| 数据加载（ARINC615/TFTP） | 429数据加载、664数据加载、启用数据加载 | ✅ 强 |
| 构型管理（ACR 查询 + 报告接收 + 请求响应） | 构型信息、构型参数发送 | ✅ |
| 生命周期 | 生命周期 | ✅ |
| 打印管理 | 打印 | ✅ |
| 维护模式控制 | 维护模式控制 | ✅ |
| 飞机状态消息 | 飞机事件信息、维护模式控制 | ⚠️ 弱 |
| 参数监控 | 参数监控报告 | ⚠️ 弱 |
| ACARS 数据链 | ACARS | ⚠️ 弱 |
| **启动测试交互（ARINC624）** | —（200NVM 仅覆盖故障历史检索，非交互测试） | ❌ 未覆盖 |
| **ICD 管理** | — | ❌ 未覆盖 |
| **发动机配平（4.3.9）** | — | ❌ 未覆盖 |
| **数据重置管理（4.3.10）** | — | ❌ 未覆盖 |
| **数据下载管理（4.3.14）** | — | ❌ 未覆盖 |
| **通信安全防护（4.3.5）** | — | ❌ 未覆盖 |

## 三、各用例主要测试目的与验证要点

### 3.1 故障诊断 / 失效报告 / FDE

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 地面人机界面.py | 故障→失效→FDE 全链路 + getParameter 查询 | 注入 `AOT3 Instance Loss Failure` + FDE `42IM0062`，校验 `get_inbound_leg_failures_all(0x0103)/get_existing_failures(0x0106)/get_failure_history(0x0108)/get_failure_detail_fde/get_inbound_leg_fdes(0x0101)/get_existing_fdes(0x0102)` |
| FDE消息.py | FDE 与故障关联失效 | 发送 3 路 FDE 消息，`get_inbound_leg_fdes` 轮询校验 |
| AHMU频率测试.py | 不同周期故障处理能力 | 按 500/1000/2000/4000ms 周期发送故障，验证 AHMU 稳定处理 |
| 批量创建失效报告和故障模型.py/.txt | 批量故障模型 + 失效报告关联 | 5 航段×5 故障，`get_inbound_leg_failures_all` 与 `get_failure_history>=2000` 校验 |
| 故障与失效报告批量创建.txt | 大批量（2020 失效/2499 故障）压力 + ICD 注入 | 经 AFDX 批量注入 ICD，`get_existing_failures>=2000`、`get_inbound_leg_failures_all>=1973` |

### 3.2 故障历史检索 + TFTP

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 200NVM.py | 故障历史检索 + 199 成员支持 | `Start_Fault_History`→`Query_Fault_History_Task_Status`，`check(pmat_inittest_ms >= 199)` |
| 故障历史报告获取664.py | 多成员故障历史检索 + TFTP 下载 | 逐成员 `Start_Fault_History_Retrieval_Task(数字 equipID)` + `Query_Fault_History_Task_Status`（`Complete`）+ TFTPServer 接收上传文件，`check(failed==0)` |

### 3.3 数据加载

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 429数据加载.py | A429 通道 ARINC615 加载 | `A429Uploader` + `get_DLATAs/DLEquips/DLPart/ACRValidConfig/DLLoadCondition/DLStartLoad/DLStatus` |
| 664数据加载.py | A664 通道 TFTP(615A) 加载 | `TFTPServer` + 同一组 DL API |
| 启用数据加载.py | 全 ATA 启用 | `get_DLATAs` 分页遍历 + `enable_ATA` |

### 3.4 构型管理（ACR）

| 用例 | 测试目的 | 验证要点 |
| --- | --- | --- |
| 构型信息.py | 构型报告发送与回读验证（ACR 查询） | `ConfigReport` 打包 → AFDX 发送 → `verify_requestData` 经 `ACRHardInfo/ACRSpecific/ACRSoftInfo/ACRSoftPart` 校验四类字段与 Excel 一致 |
| 构型参数发送.py | ACR 构型请求（429/664） | 按端口遍历 `make_429/664_acr_protocol` 发送，`Deal_Type` 区分通道 |

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
| **启动测试交互（ARINC624）** | AHMU 后端有 `startup_test.py`（运行/终止/继续指令 + ACK + 抑制监视 + 交互式/非交互式场景），但 test_case 中**无对应测试用例**——`200NVM.py` 仅覆盖故障历史检索（199 成员启动测试服务），不覆盖 ARINC624 交互流程 |
| **ICD 管理** | AHMU 后端有 ICD 管理接口（`icd_parser`/`routes` ICD 分组），test_case 无对应测试 |
| **发动机配平（4.3.9）** | 后端有 `engine_trim.py`，无测试用例（配平指令下发/越界校验/来源校验） |
| **数据重置管理（4.3.10）** | 后端有 `nvm_reset.py`，无对应测试（`200NVM.py` 名字含 NVM 但实为故障历史） |
| **数据下载管理（4.3.14）** | 后端有 `nvm_download.py`，无对应测试 |
| **通信安全防护（4.3.5）** | 后端有 `comm_security.py`，无对应测试 |

### 4.2 覆盖薄弱（⚠️）

| 功能 | 薄弱点 |
| --- | --- |
| 参数监控 | 仅测 ATA73 单一参数 `Selected_N1_Indicated`，未覆盖多参数/多 ATA/AFDX 数据链路（`afdx_receiver`） |
| ACARS | 仅测上行 EOR 请求 + 滑入阶段触发下行，未测完整上下行握手（RTS/CTS/DATA/ACK）、链路状态监控（空闲/繁忙/丢失）、优先级/下传链路/触发逻辑配置 |
| 飞机状态消息 | 仅测维护模式 + FMS_IC_1_PARA，其余 14 个飞行阶段（power_on/climb/cruise/descent/approach 等）缺少逐阶段覆盖 |

## 五、总结

test_case 对 AHMU 的**故障诊断/失效/FDE、故障历史检索+TFTP、数据加载、构型管理（ACR）、打印、生命周期**等核心功能覆盖较充分（其中故障诊断的 getParameter 查询、ICD 批量注入、TFTP 下载已与 AHMU 本次优化对齐）。

但存在两类缺口：

1. **完全未覆盖**（6 项）：启动测试交互（ARINC624）、ICD 管理、发动机配平、数据重置、数据下载、通信安全防护；
2. **覆盖薄弱**（3 项）：参数监控、ACARS 完整握手、飞机状态消息逐阶段。

> 建议后续优先补充：①启动测试交互（ARINC624 交互式/非交互式场景）的测试用例；②四个完全未覆盖后端模块（发动机配平、数据重置、数据下载、通信安全防护）；③参数监控多参数/AFDX 链路、ACARS 完整握手、飞机状态逐阶段。
