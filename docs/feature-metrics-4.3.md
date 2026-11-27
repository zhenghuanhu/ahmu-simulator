# AHMU 仿真器 4.3.1 ~ 4.3.20 功能指标汇总

> 本文档汇总 AHMU 仿真器技术文档「4.3 AHMU 仿真器」章节（4.3.1 ~ 4.3.20 二十个子功能）中，**已确认完成且可经代码核验**的功能指标。
>
> **范围与约定**：
> - 严格按 4.3.1 ~ 4.3.20 原有编号组织，不新增编号之外的指标；
> - 每项指标标注：指标名称、所属功能、定义与计算口径、数据来源、使用场景、当前实现状态、实现代码定位（文件路径 + 函数/类/组件 + 调用位置）；
> - 状态标记：✅ 已实现；⚠️ 部分实现；🚫 未实现（硬件项/无软件实现）；
> - 凡口径、数据来源或代码对应关系不明确的，均以「待确认」标注并说明缺少的依据，不臆断。

---

## 4.3.1 共享内存 / API

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 共享内存总容量 | 4MB（`SHM_CONFIG["size"] = 4*1024*1024`） | `config.py:356 SHM_CONFIG` | AHMU 与系统试验软件平台间 IPC | ✅ |
| 共享内存区域数 | 4 个区域：`a664` / `a429` / `control` / `status` | `config.py:359 SHM_CONFIG["regions"]` | 按总线/用途分区的环形缓冲区 | ✅ |
| 环形缓冲区 | 每区域一个 RingBuffer，写入长度超出 `size-8` 则拒绝 | `shared_memory.py:20 RingBuffer` | 双向数据交互 | ✅ |

**代码定位**：
- `backend/app/config.py:356` `SHM_CONFIG`（size/regions）
- `backend/app/core/shared_memory.py:20` `RingBuffer`（write/read 环形读写）
- `backend/app/core/shared_memory.py:71` `SharedMemoryChannel`（`:82 initialize` 分区、`:119 write`、`:128 read`、`:137 write_a664` 等）
- 调用位置：`backend/app/main.py:57` `shm_channel.initialize()`、`:134` `shm_channel.close()`

---

## 4.3.2 打印管理

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 数据块大小 | 每块有效数据 150 字节（对齐 ETest BLOCK_SIZE） | `config.py:175 PRINT_CONFIG["block_size"]` | ARINC744A 打印数据分块 | ✅ |
| ACK 超时 | 3 秒（打印机 ACK 等待） | `config.py:176 ack_timeout_sec` | 打印确认超时判定 | ✅ |
| 最大重发次数 | 3 次（`max_retry`；`print_mgr.py` 内 `MAX_NAK_RETRY=3`） | `config.py:177` / `print_mgr.py:62` | NAK 后重发 | ✅ |
| RECORD 最大长度 | 255 字 | `print_mgr.py:58 RECORD_MAX_LENGTH` | 打印记录长度上限 | ✅ |
| 虚拟链路 VL | 23247（Printer_GIPC_Data） | `config.py:171 afdx_vl_id` | A664 打印通道语义参考 | ✅ |

**代码定位**：
- `backend/app/config.py:162` `PRINT_CONFIG`
- `backend/app/services/print_mgr.py:58/62` `RECORD_MAX_LENGTH` / `MAX_NAK_RETRY`
- `backend/app/services/print_mgr.py:410` 重发判定（`retries < MAX_NAK_RETRY`）
- `backend/app/core/icd_print.py` ARINC744A 编码
- 前端：`frontend/src/views/Print.vue`

---

## 4.3.3 地面人机界面

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 人机界面功能 | 地面人机界面（故障诊断等功能的 Web 交互页面） | `frontend/src/views/*.vue` | 维护人员地面操作 | ⚠️ 见「待确认」 |

**代码定位**：
- 前端页面目录 `frontend/src/views/`（Dashboard/FaultDiagnosis/ParamMonitor 等）
- 后端对应服务：`backend/app/services/fault_diagnosis.py` 等

**待确认**：
- 4.3.3「地面人机界面」为界面性功能，**无独立的可量化性能指标**（如容量/频率），文档未定义该功能的明确指标口径；此处仅能定位到前端页面与后端服务，具体指标需依据技术文档原条款确认。

---

## 4.3.4 维护模式

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 空速判定阈值 | 80 kts（维护<80，正常>80） | `maintenance_mode.py:40 AIRSPEED_THRESHOLD` | 维护/正常模式切换条件 | ✅ |
| 条件持续时间 | 30 秒（三者同时满足且持续≥30s） | `maintenance_mode.py:41 HOLD_DURATION` | 模式切换计时 | ✅ |
| 维护模式条件数 | 3 个：轮载=地 / 空速<80 / 维护开关∈{地面测试,数据加载} | `config.py:310 MAINTENANCE_MODE_CONDITIONS` | 进入维护模式判定 | ✅ |

**代码定位**：
- `backend/app/services/maintenance_mode.py:40/41` `AIRSPEED_THRESHOLD` / `HOLD_DURATION`
- `backend/app/services/maintenance_mode.py:96` 持续计时判定（`elapsed >= hold_duration`）
- `backend/app/services/maintenance_mode.py:139` 维护条件判定（`airspeed < AIRSPEED_THRESHOLD`）
- `backend/app/config.py:310` `MAINTENANCE_MODE_CONDITIONS`
- `backend/app/services/maintenance_rx.py:73` `MAINTENANCE_HOLD_SEC = 30`（UDP 接收侧对齐）

---

## 4.3.5 通信安全

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| CRC 校验算法 | CRC32（8 位十六进制），错误则丢弃包 | `comm_security.py:80 _crc32` | 链路数据完整性校验 | ✅ |
| VPN 加密算法 | AES-256-GCM | `comm_security.py:132` | 无线链路加密隧道 | ✅ |
| WiFi 白名单 | 可信 SSID + PSK（WPA2-PSK） | `comm_security.py:151 add_whitelist` | 可信网络接入控制 | ✅ |

**代码定位**：
- `backend/app/services/comm_security.py:80` `_crc32`（CRC 计算）
- `backend/app/services/comm_security.py:90` `receive_packet`（校验 + 丢弃）
- `backend/app/services/comm_security.py:121` `establish_vpn_tunnel`（AES-256-GCM）
- `backend/app/services/comm_security.py:151/166` `add_whitelist` / `remove_whitelist`
- `backend/app/services/comm_security.py:214` `authenticate`
- 前端：`frontend/src/views/CommSecurity.vue`

---

## 4.3.6 故障诊断

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 成员系统数 | 400（真实 51 + 虚拟补足） | `fault_diagnosis.py:31 FAULT_MEMBER_COUNT` | 故障诊断容量 | ✅ |
| 每成员故障数 | 6（每成员 6 条故障模型） | `fault_diagnosis.py:33 FAULTS_PER_MEMBER` | 故障模型生成 | ✅ |
| FDE 消息数 | 100 条（不少于 100） | `fault_diagnosis.py:35 FDE_COUNT` | 驾驶舱效应消息池 | ✅ |
| 最大故障报告数 | 50000 | `config.py:70 fault_report_max` | 故障报告存储上限 | ✅ |
| 最大失效报告数 | 25000 | `config.py:71 failure_report_max` | 失效报告存储上限 | ✅ |
| 每航段故障数 | 2500 | `config.py:72 fault_per_segment` | 单航段故障容量 | ✅ |
| 航段数 | 512 | `config.py:73 segment_count` | 航段存储范围 | ✅ |

**代码定位**：
- `backend/app/services/fault_diagnosis.py:31/33/35` `FAULT_MEMBER_COUNT` / `FAULTS_PER_MEMBER` / `FDE_COUNT`
- `backend/app/services/fault_diagnosis.py:156` 虚拟成员补足
- `backend/app/services/fault_diagnosis.py:179` FDE 池初始化
- `backend/app/services/fault_diagnosis.py:225` 故障模型生成
- `backend/app/config.py:68` `SIMULATION_CONFIG`（容量参数）
- 前端：`frontend/src/views/FaultDiagnosis.vue`

---

## 4.3.7 飞机状态消息

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 发布周期 | 1Hz（1000ms） | `config.py:197 publish_period_ms` | OHMS 飞机状态消息发布 | ✅ |
| 双源参数数 | 8 个（左/右双源） | `config.py:218 dual_source_params` | 飞行阶段计算输入 | ✅ |
| 飞行阶段数 | 15 个（1~15） | `aircraft_status.py:54 FlightPhase` | 飞行阶段判定 | ✅ |

**代码定位**：
- `backend/app/config.py:196` `AIRCRAFT_STATUS_CONFIG`（`publish_period_ms` / `dual_source_params`）
- `backend/app/services/aircraft_status.py:54` `FlightPhase`（1~15 枚举）
- `backend/app/services/aircraft_status.py:584` `_publish_loop`（1Hz 发布）
- `backend/app/services/aircraft_status.py:393` `_calc_flight_phase`（阶段判定）
- `backend/app/core/icd_aircraft_status.py` `pack_aircraft_status`
- 前端：`frontend/src/views/AircraftStatus.vue`

---

## 4.3.8 启动测试

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| ACK/响应超时 | 5 秒 | `config.py:350 ack_timeout_sec` | 成员系统响应等待 | ✅ |
| 交互页面超时 | 60 秒 | `config.py:351 interactive_page_timeout_sec` | 维护人员交互响应 | ✅ |
| 抑制条件轮询周期 | 1 秒 | `config.py:352 inhibit_poll_interval_sec` | 抑制条件监视 | ✅ |

**代码定位**：
- `backend/app/config.py:346` `STARTUP_TEST_CONFIG`
- `backend/app/services/startup_test.py:70` `StartupTestService`（`:239 start_test`、`:288 _run_test_flow`）
- `backend/app/services/startup_test.py:198` `_wait_msg`（超时等待）
- `backend/app/services/startup_test.py:230` `_member_inhibited`（抑制判定）
- `backend/app/core/icd_startup_test.py`（ARINC624 消息）
- 前端：`frontend/src/views/StartupTest.vue`

---

## 4.3.9 发动机配平

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 发动机数量 | 4（四发飞机） | `config.py:112 engine_count` | 配平目标发动机 | ✅ |
| 配平值范围 | ±2.5% N1 RPM | `config.py:113 trim_range` | 配平值有效性校验 | ✅ |
| 配平上报周期 | 200ms | `config.py:120 data_report_period_ms` | EMU 配平数据上报 | ✅ |
| 指令来源数 | 3（ground/cockpit/pmat） | `config.py:116 command_sources` | 配平指令来源 | ✅ |
| 并发限制 | 每发动机最多 1 条并发指令 | `config.py:119 max_concurrent_per_engine` | 并发配平控制 | ✅ |

**代码定位**：
- `backend/app/config.py:111` `ENGINE_TRIM_CONFIG`
- `backend/app/services/engine_trim.py`（配平指令/数据接收）
- `backend/app/core/engine_monitor_mock.py`（EMU 模拟）
- 前端：`frontend/src/views/EngineTrim.vue`

---

## 4.3.10 数据重置

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 重置响应超时 | 10 秒 | `config.py:150 reset_timeout_sec` | 成员系统重置响应 | ✅ |
| 最大并发重置数 | 5 | `config.py:153 max_concurrent_resets` | 并发重置控制 | ✅ |
| 重置类型数 | 3（full/partial/fault_history） | `config.py:152 reset_types` | 重置类型分类 | ✅ |
| 启用成员数 | 52（来自 `nvm_reset_config.json`） | `nvm_reset_config.json` | 启用重置的成员列表 | ✅ |

**代码定位**：
- `backend/app/config.py:148` `NVM_RESET_CONFIG`
- `backend/app/services/nvm_reset.py`（重置服务）
- `backend/config/nvm_reset_config.json`（52 个成员系统配置文件）
- 前端：`frontend/src/views/NVMReset.vue`

---

## 4.3.11 生命周期

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 消息大小 | 28 字节（FCS_OutputData_TO_OMS_BLK_5） | `icd_time_cycle.py:43 size` | 生命周期数据帧 | ✅ |
| 发送周期 | 400ms | `icd_time_cycle.py:44 period_ms` | 生命周期数据上报周期 | ✅ |
| 虚拟链路 VL | 21162 | `icd_time_cycle.py:42 vl_id` | A664 通道语义 | ✅ |
| 监听端口 | 31696 | `icd_time_cycle.py:45 port` | 飞控模块数据接收 | ✅ |

**代码定位**：
- `backend/app/core/icd_time_cycle.py:38` `TIME_CYCLE_MSG`（size/period_ms/vl_id/port）
- `backend/app/services/lifecycle.py:496` 端口 31696 → HF_FCM_1 接收
- `backend/app/services/lifecycle.py:642` `get_lifecycle_list`（查询）
- 前端：`frontend/src/views/Lifecycle.vue`

---

## 4.3.12 参数监控

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 参数报告生成 | 参数报告（标识/名称/个数/摘要/状态） | `param_monitor.py:464 get_param_report` | 地面人机界面获取参数报告 | ✅ |
| 参数基本配置 | 名称/类型/采样频率/精度/单位/是否记录/是否显示 | `database.py:340 ParamConfig` | 参数配置项管理 | ✅ |
| 快捷访问列表（关联） | 100 张 / 每张 50~64 参数（详见 5.4.3/5.4.4） | `quick_list_manager.py` | 参数快捷访问 | ✅ |

**代码定位**：
- `backend/app/services/param_monitor.py:170` `_init_default_params`（参数初始化）
- `backend/app/services/param_monitor.py:464` `get_param_report`
- `backend/app/database.py:340` `ParamConfig`、`:310 ParamSnapshot`、`:369 ParamReport`
- `backend/app/core/quick_list_manager.py`（快捷访问列表，见 5.4.3/5.4.4）
- 前端：`frontend/src/views/ParamMonitor.vue`

---

## 4.3.13 构型管理

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 成员系统数 | 400（真实 51 + 虚拟补足） | `config_management.py:41 CONFIG_MEMBER_COUNT` | 构型报告容量 | ✅ |
| 周期接收间隔 | 5 秒 | `config_management.py:53 RECEIVE_INTERVAL` | 周期接收构型报告 | ✅ |
| 每周期处理数 | 15 个成员 | `config_management.py:54 RECEIVE_BATCH` | 批量处理节奏 | ✅ |
| 构型项数 | 7（MF/MFR/PNR/SER/DMF/SW_PN/SW_LOC） | `config_management.py:27 CONFIG_ITEMS` | 设备标识状态消息构型项 | ✅ |

**代码定位**：
- `backend/app/services/config_management.py:41/53/54` `CONFIG_MEMBER_COUNT` / `RECEIVE_INTERVAL` / `RECEIVE_BATCH`
- `backend/app/services/config_management.py:27` `CONFIG_ITEMS`
- `backend/app/services/config_management.py:153` `_config_receive_loop`（周期接收）
- `backend/app/core/icd_config_acr.py`（ACR 编解码）
- 前端：`frontend/src/views/ConfigManagement.vue`

---

## 4.3.14 数据下载

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 获取超时 | 15 秒 | `config.py:185 retrieve_timeout_sec` | NVM 数据获取超时 | ✅ |
| 最大并发获取数 | 5 | `config.py:186 max_concurrent_retrieves` | 并发获取控制 | ✅ |
| 数据大小范围 | 1KB ~ 256KB | `config.py:187 data_size_range` | NVM 数据量范围 | ✅ |
| 进度步数 | 20（每步 ~0.1s） | `config.py:190 progress_steps` | 获取进度显示 | ✅ |

**代码定位**：
- `backend/app/config.py:183` `NVM_DOWNLOAD_CONFIG`
- `backend/app/services/nvm_download.py`（下载服务）
- 前端：`frontend/src/views/NVMDownload.vue`

---

## 4.3.15 维护接口板

**状态**：⚠️ 部分覆盖（硬件项）

**说明**：4.3.15「维护接口板」为工控机千兆以太网口硬件项，符合 IEEE802.3，**无独立软件实现**，故无软件层可量化指标。

**代码定位**：
- 无独立软件实现；依赖 Linux 原生千兆网口驱动（见技术路线方案「第四层 Linux 下位机」）

**待确认**：
- 千兆以太网口为硬件交付项，需确认工控机硬件配置（网口数量/型号）；软件侧无对应指标。

---

## 4.3.16 ACARS

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 上行接收端口 | 34810 | `config.py:291 recv_port` | 接收 CPM 上行（EOR/ACK） | ✅ |
| 下行发送端口 | 34811 | `config.py:292 send_port` | 发送下行维护数据 | ✅ |
| ACK 超时 | 3 秒 | `config.py:297 ack_timeout_sec` | 下行 ACK 等待 | ✅ |
| 最大重发次数 | 3 | `config.py:298 max_retry` | 下行无 ACK 重发 | ✅ |
| 报告批量大小 | 20 条 | `acars.py:42 REPORT_BATCH_SIZE` | 各类报告收集上限 | ✅ |

**代码定位**：
- `backend/app/config.py:290` `ACARS_CONFIG`
- `backend/app/services/acars.py:42` `REPORT_BATCH_SIZE`
- `backend/app/services/acars.py:54` `ACARSService`（`:146 _handle_frame`、`:178 _send_downlink`）
- `backend/app/core/icd_acars.py`（ARINC619 编解码）
- 前端：`frontend/src/views/ACARS.vue`

---

## 4.3.17 人机界面

**状态**：⚠️ 部分覆盖

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 前端页面 | 各功能独立 Web 页面（Dashboard/FaultDiagnosis/ParamMonitor/ACARS/Print 等） | `frontend/src/views/*.vue` | 人机交互 | ✅ |
| 数据上传页面 | 独立「数据上传」页面 | — | 数据上传 | 🚫 未实现（缺独立页面） |
| 连续参数页面 | 「连续参数」展示 | — | 连续参数显示 | ⚠️ 见「待确认」 |

**代码定位**：
- 前端页面目录 `frontend/src/views/`（20+ 个 `.vue` 文件）
- 路由：`frontend/src/router/index.js`
- 导航：`frontend/src/views/Layout.vue`（`mainTabs` / `subTabs`）

**待确认**：
- 「连续参数」是否已并入 `ParamMonitor.vue` 需明确对应关系；「数据上传」无独立页面，属功能缺口。

---

## 4.3.18 配置文件读取与模型解析

**状态**：⚠️ 部分覆盖

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 配置文件读取 | 数据重置成员列表从 `nvm_reset_config.json` 读取（52 成员） | `config.py:149 config_file` + `nvm_reset_config.json` | 数据重置配置 | ✅ |
| 模型解析（其它） | 故障诊断/启动测试/构型报告/生命周期模型从配置加载 | — | 各功能模型加载 | 🚫 未实现（代码内置生成） |

**代码定位**：
- `backend/app/config.py:148` `NVM_RESET_CONFIG["config_file"]`
- `backend/config/nvm_reset_config.json`（52 成员列表）
- `backend/app/services/nvm_reset.py`（读取配置文件）

**待确认**：
- 故障诊断模型（`fault_diagnosis.py:142 _build_fault_model`）、启动测试模型、构型报告、生命周期模型均为**代码内置生成**，未从 OHMS 维护模型配置工具输出的配置文件加载，属功能缺口（已在覆盖报告标注）。

---

## 4.3.19 ICD 导入

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| ICD 解析 | 解析 ICD 文件（JSON/XML）为 ICD 条目 | `icd_parser.py:243 parse_icd_file`、`icd_xml_parser.py` | ICD 数据导入 | ✅ |
| ICD 快照缓存 | msgpack 序列化快照，后续启动直接加载 | `icd_parser.py:495 load_cache` | 避免重复解析 | ✅ |

**代码定位**：
- `backend/app/core/icd_parser.py:243` `parse_icd_file`
- `backend/app/core/icd_parser.py:495` `load_cache`
- `backend/app/core/icd_parser.py:530` `get_all_members`
- `backend/app/core/icd_parser.py:678` `generate_demo_icd`
- `backend/app/core/icd_xml_parser.py`（XML 解析）
- 前端：`frontend/src/views/ICDManagement.vue`

---

## 4.3.20 功能模型生成

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 状态 |
| --- | --- | --- | --- | --- |
| 功能模型生成 | 各功能模型由代码内置生成（故障模型/FDE 池/构型目录等） | 各服务 `_build_*` 方法 | 服务启动初始化 | ⚠️ 见「待确认」 |

**代码定位**：
- `backend/app/services/fault_diagnosis.py:142` `_build_fault_model`（故障模型）
- `backend/app/services/fault_diagnosis.py:179` `_build_fde_pool`（FDE 池）
- `backend/app/services/config_management.py:416` `_build_acr_catalog`（ACR 目录）
- `backend/app/services/fault_diagnosis.py:165` `_build_rx_channels`（接收通道）

**待确认**：
- 「功能模型生成」是否指从配置文件/工具生成模型，与 4.3.18 的边界需依据技术文档原条款明确；当前实现为代码内置生成，非外部工具输出。

---

## 附：指标代码核验清单（全局）

| 文件 | 关键常量/配置 |
| --- | --- |
| `backend/app/config.py:68` | `SIMULATION_CONFIG`（member 500 / fault 50000 / failure 25000 / segment 512 / 每航段 2500 / 1Hz / 并发 3 / 终端 5） |
| `backend/app/config.py:111` | `ENGINE_TRIM_CONFIG`（4 发 / ±2.5% / 200ms / 3 来源） |
| `backend/app/config.py:148` | `NVM_RESET_CONFIG`（10s / 并发 5 / 3 类型） |
| `backend/app/config.py:183` | `NVM_DOWNLOAD_CONFIG`（15s / 并发 5 / 1KB~256KB） |
| `backend/app/config.py:196` | `AIRCRAFT_STATUS_CONFIG`（1Hz / 8 双源参数） |
| `backend/app/config.py:290` | `ACARS_CONFIG`（34810/34811 / 3s / 重发 3） |
| `backend/app/config.py:346` | `STARTUP_TEST_CONFIG`（5s / 60s / 1s） |
| `backend/app/config.py:356` | `SHM_CONFIG`（4MB / 4 区域） |
| `backend/app/services/fault_diagnosis.py:31` | `FAULT_MEMBER_COUNT=400` / `FAULTS_PER_MEMBER=6` / `FDE_COUNT=100` |
| `backend/app/services/config_management.py:41` | `CONFIG_MEMBER_COUNT=400` / `RECEIVE_INTERVAL=5.0` / `RECEIVE_BATCH=15` |
| `backend/app/core/icd_time_cycle.py:38` | `TIME_CYCLE_MSG`（28 字节 / 400ms / VL 21162 / 端口 31696） |
