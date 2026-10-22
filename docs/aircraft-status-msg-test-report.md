# 飞机状态消息功能测试报告

## 一、测试标题

AHMU 仿真器「飞机状态消息」功能测试（基于《飞机事件信息.py》对向发送语义）

## 二、测试目的

验证 AHMU 仿真器飞机状态消息功能的正确性，覆盖以下四个维度：

| 维度 | 验证目标 |
| --- | --- |
| ① 消息构造与字段填充 | `pack_aircraft_status` 编码 68 字节 A664 帧的字段偏移、大小端、倒序交错字符串布局是否正确 |
| ② 发送触发条件与流程 | 维护模式/巡航模式触发、事件参数（FMS_IC_1_PARA）交替、1Hz 周期发布是否符合预期 |
| ③ 对向接收解析 | 对向端（《飞机事件信息.py》经 e_icd AFDX 解码）能否正确接收 68 字节二进制帧并解析 |
| ④ 边界情况处理 | 空数据、重复发送、异常字段、空字符串、超长截断、异常 ICAO 等是否容错不崩溃 |

## 三、测试环境与前置条件

| 项 | 说明 |
| --- | --- |
| 被测对象 | `backend/app/services/aircraft_status.py`（飞机状态消息生成/发布服务） |
| 编解码模块 | `backend/app/core/icd_aircraft_status.py`（Aircraft_Status_Msg 68 字节 A664 帧） |
| 对向模拟端 | `backend/tests/simulate_aircraft_event.py`（模拟《飞机事件信息.py》发送事件 + 收 A664 帧） |
| 测试脚本 | `backend/tests/test_aircraft_status_msg.py`（22 项，Part 1 纯单元 + Part 2 端到端） |
| 运行环境 | Windows / Python 3.13（venv）/ Uvicorn 端口 8443 |
| 通信端口 | 事件注入 UDP 34820（测试端→AHMU），回传 UDP 34821（AHMU→测试端） |

## 四、测试步骤

### 4.1 仅运行 Part 1（纯单元，无需后端）

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
venv\Scripts\python.exe tests\test_aircraft_status_msg.py --unit
```

### 4.2 运行完整测试（Part 1 + Part 2，需先启动后端）

```cmd
:: 1. 启动后端（另开终端）
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8443

:: 2. 运行测试
venv\Scripts\python.exe tests\test_aircraft_status_msg.py
```

## 五、测试项与预期/实际结果

### 5.1 Part 1：消息构造与字段填充 + 对向解析 + 边界（纯单元）

| 编号 | 测试项 | 预期结果 | 实际结果 |
| --- | --- | --- | --- |
| P1.1 | 消息长度 | 68 字节 | ✅ 68 字节 |
| P1.2 | Flight_Leg（SINT16 大端 @8~9） | flight_leg=-5 → `0xFFFB` | ✅ `fffb` |
| P1.3 | Flight_Phase（UBNR16 大端 @12~13） | flight_phase=15 → `0x000F` | ✅ `000f` |
| P1.4 | UTC 时间（UBNR8 @16~22，含 2 字节 PAD） | days/months/years/PAD/secs/mins/hours/PAD 正确填充 | ✅ `160a1a002d1e0e00` |
| P1.5 | ICAO_Code（UINT24 大端 @24~26） | `0x780123` | ✅ `780123` |
| P1.6 | Global_Abort_Flag（BOOL @64） | True→1 / False→0 | ✅ |
| P1.7 | 注册号倒序交错（ISO-5 @32~43） | Char_2/Char_1/StrLen/Char_6/5/4/3 布局正确 | ✅ `2d42000641313030...` |
| P1.8 | 航班号+机场倒序交错（ISO-5 @44~63） | Flight_Num/Dest/Dep 交错布局正确 | ✅ |
| P1.9 | unpack 往返一致 | 阶段/航段/ICAO/身份/终止/UTC 一致 | ✅ |
| P1.10a | 空字符串身份 | unpack 返回空串 | ✅ |
| P1.10b | 超长注册号截断 | 截断为 9 字符 | ✅ `'ABCDEFGHI'` |
| P1.10c | 异常 ICAO 字符 | 解析为 0 | ✅ |
| P1.10d | 全零帧 unpack | 阶段 0 / 身份空串 | ✅ |
| P1.10e | 长度不足帧（67 字节） | 抛 ValueError 正确拒绝 | ✅ |

### 5.2 Part 2：触发条件/流程 + 对向接收 + 边界（端到端）

| 编号 | 测试项 | 预期结果 | 实际结果 |
| --- | --- | --- | --- |
| P2.1 | 维护模式触发（FMS_IC_1_PARA=1） | 阶段 15 + 终止标志 False | ✅ phase=15, abort=False |
| P2.2 | 巡航模式触发 | 阶段 7 | ✅ phase=7 |
| P2.3 | 事件参数交替（1→0→1） | 事件参数与注入值一致 | ✅ 最终 fms_ic_1_para=1 |
| P2.4 | 1Hz 周期发布 | 连续两帧阶段稳定 | ✅ 15 vs 15 |
| P2.5 | 对向接收解析 | 阶段 1~15 / ICAO 6 位 / 航段 int | ✅ phase=15, icao=780123, leg=0 |
| P2.6 | 空数据（空 params） | 不崩溃，用默认值回传 | ✅ |
| P2.7 | 重复发送 | 阶段稳定 + 事件报告不重复记录 | ✅ EventReport 23→23 |
| P2.8 | 异常字段（airspeed="abc"） | 拒绝注入，不崩溃 | ✅ phase=7 |

## 六、测试结论

| 项目 | 结果 |
| --- | --- |
| 本测试脚本（test_aircraft_status_msg.py） | **22/22 通过** ✅ |
| 完整回归（含既有 9 套测试） | **109 项通过**（11+4+22+7+14+15+12+13+8+3） ✅ |
| 结论 | 飞机状态消息功能的消息构造、字段填充、触发流程、对向接收解析及边界处理均符合预期，测试通过 |

### 附带改造说明

| 改动 | 说明 |
| --- | --- |
| `aircraft_status.py` `_send_status_message` | 回传由 JSON 改为 68 字节 A664 二进制帧（对齐对向端 e_icd AFDX 解码） |
| `aircraft_status.py` `_inject_param` | 提取公共参数注入方法（字段名+枚举+数值归一化），修复异常字段导致的 TypeError 崩溃 |
| `icd_aircraft_status.py` | 注册号/航班号 String_Length 截断用 `min(len,9/8)`，超长时长度字段与实际字符数一致 |

## 七、相关文件

| 文件 | 说明 |
| --- | --- |
| `backend/tests/test_aircraft_status_msg.py` | 本测试脚本（Part 1 纯单元 + Part 2 端到端） |
| `backend/tests/simulate_aircraft_event.py` | 对向发送端模拟器（发送事件 + 收 A664 帧） |
| `backend/tests/test_aircraft_status.py` | 既有飞机状态测试（HTTP 注入，11 项） |
| `backend/tests/test_aircraft_status_rx.py` | 既有接收/回传闭环测试（4 项） |
| `backend/app/services/aircraft_status.py` | 飞机状态消息生成/发布服务 |
| `backend/app/core/icd_aircraft_status.py` | Aircraft_Status_Msg 编解码模块 |
| `机载航电/OHMS/test_case/飞机事件信息.py` | 对向发送参考文件（本测试的语义来源） |
