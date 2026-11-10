# AHMU 仿真器功能覆盖核对报告

> 核对对象：技术文档「4.3 AHMU 仿真器」章节全部功能要求（共 4.3.1 ~ 4.3.20 二十个子功能）
> 对照基准：AHMU 仿真器当前代码实现（`ahmu-simulator/backend` + `ahmu-simulator/frontend`）
> 更新日期：2026-11-09

## 一、覆盖总结清单

| 状态 | 数量 | 功能编号 |
| --- | --- | --- |
| ✅ 已覆盖 | 17 项 | 4.3.1、4.3.2、4.3.3、4.3.4、4.3.5、4.3.6、4.3.7、4.3.8、4.3.9、4.3.10、4.3.11、4.3.12、4.3.13、4.3.14、4.3.16、4.3.19、4.3.20 |
| ⚠️ 部分覆盖 | 3 项 | 4.3.15（维护接口板，硬件项）、4.3.17（人机界面，缺「数据上传」「连续参数」独立页面）、4.3.18（配置文件读取与模型解析，仅 nvm_reset_config.json） |
| ❌ 未覆盖 | 0 项 | — |

## 二、逐项核对明细

| 编号 | 文档功能要求 | 代码实现位置 | 覆盖状态 |
| --- | --- | --- | --- |
| 4.3.1 | 共享内存 / API | `backend/app/core/shared_memory.py` | ✅ 已覆盖 |
| 4.3.2 | 打印管理 | `backend/app/services/print_mgr.py`、`backend/app/core/icd_print.py`、`frontend/src/views/Print.vue` | ✅ 已覆盖 |
| 4.3.3 | 地面人机界面 | `frontend/src/views/FaultDiagnosis.vue`、`backend/app/services/fault_diagnosis.py` | ✅ 已覆盖 |
| 4.3.4 | 维护模式 | `backend/app/services/maintenance_mode.py`、`backend/app/services/maintenance_rx.py` | ✅ 已覆盖 |
| 4.3.5 | 通信安全 | `backend/app/services/comm_security.py`、`frontend/src/views/CommSecurity.vue` | ✅ 已覆盖 |
| 4.3.6 | 故障诊断 | `backend/app/services/fault_diagnosis.py`、`frontend/src/views/FaultDiagnosis.vue` | ✅ 已覆盖 |
| 4.3.7 | 飞机状态消息 | `backend/app/services/aircraft_status.py`、`backend/app/core/icd_aircraft_status.py`、`frontend/src/views/AircraftStatus.vue` | ✅ 已覆盖 |
| 4.3.8 | 启动测试 | `backend/app/services/startup_test.py`、`backend/app/core/icd_startup_test.py`、`frontend/src/views/StartupTest.vue` | ✅ 已覆盖 |
| 4.3.9 | 发动机配平 | `backend/app/services/engine_trim.py`、`backend/app/core/engine_monitor_mock.py`、`frontend/src/views/EngineTrim.vue` | ✅ 已覆盖 |
| 4.3.10 | 数据重置 | `backend/app/services/nvm_reset.py`、`frontend/src/views/NVMReset.vue` | ✅ 已覆盖 |
| 4.3.11 | 生命周期 | `backend/app/services/lifecycle.py`、`backend/app/core/icd_time_cycle.py`、`frontend/src/views/Lifecycle.vue` | ✅ 已覆盖 |
| 4.3.12 | 参数监控 | `backend/app/services/param_monitor.py`、`frontend/src/views/ParamMonitor.vue` | ✅ 已覆盖 |
| 4.3.13 | 构型管理 | `backend/app/services/config_management.py`、`backend/app/core/icd_config_acr.py`、`frontend/src/views/ConfigManagement.vue` | ✅ 已覆盖 |
| 4.3.14 | 数据下载 | `backend/app/services/nvm_download.py`、`frontend/src/views/NVMDownload.vue` | ✅ 已覆盖 |
| 4.3.15 | 维护接口板 | 硬件项（千兆以太网口），无独立软件实现 | ⚠️ 部分覆盖 |
| 4.3.16 | ACARS | `backend/app/services/acars.py`、`backend/app/core/icd_acars.py`、`frontend/src/views/ACARS.vue` | ✅ 已覆盖 |
| 4.3.17 | 人机界面 | 前端各页面（`frontend/src/views/*.vue`）；缺「数据上传」独立页面，「连续参数」并入 ParamMonitor 需明确 | ⚠️ 部分覆盖 |
| 4.3.18 | 配置文件读取与模型解析 | `backend/app/services/nvm_reset.py`（仅 `nvm_reset_config.json`）；其余模型代码内置 | ⚠️ 部分覆盖 |
| 4.3.19 | ICD 导入 | `backend/app/core/icd_parser.py`、`backend/app/core/icd_xml_parser.py`、`frontend/src/views/ICDManagement.vue` | ✅ 已覆盖 |
| 4.3.20 | 功能模型生成 | 代码内置模型生成逻辑 | ✅ 已覆盖 |

## 三、核心缺口与不一致

1. **4.3.18 配置文件加载链路不完整**：故障诊断模型、启动测试模型、构型报告、生命周期数据模型均为代码内置生成，未从 OHMS 维护模型配置工具输出的配置文件加载。当前仅数据重置（`nvm_reset_config.json`）实现了从配置文件读取。

2. **4.3.17 缺少「数据上传」独立页面**：「连续参数」可能已并入 ParamMonitor，但需明确对应关系；「数据上传」无独立页面。

3. **4.3.15 维护接口板为硬件项**：千兆以太网口属工控机硬件配置，需交付确认工控机配置，无独立软件实现。

## 四、额外实现（超出 4.3 编号范围）

| 功能 | 代码实现位置 |
| --- | --- |
| 数据加载（ARINC615A） | `backend/app/services/data_load.py`、`frontend/src/views/DataLoad.vue` |
| 故障历史检索 + TFTP 下载 | `backend/app/services/fault_history.py`、`backend/app/services/fault_history_tftp.py` |
| 事件报告页面 | `frontend/src/views/EventReports.vue` |

## 五、结论与后续建议

AHMU 仿真器已覆盖技术文档「4.3 AHMU 仿真器」章节 20 个子功能中的 17 项，另有 3 项部分覆盖，无完全未覆盖项。

建议后续优先处理：

1. **4.3.18 配置文件加载链路**：统一故障诊断 / 启动测试 / 构型报告 / 生命周期数据模型为从 OHMS 维护模型配置工具输出的配置文件加载，替换代码内置生成。
2. **4.3.17 人机界面**：补齐「数据上传」独立页面，明确「连续参数」与 ParamMonitor 的对应关系。
3. **4.3.15 维护接口板**：确认工控机千兆以太网口配置交付项。
