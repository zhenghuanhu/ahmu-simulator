# 维护模式控制测试指南

本文档说明 AHMU 仿真器「维护模式控制」功能的测试方法，包括前置条件、操作步骤、预期结果与常见问题排查。

## 1. 功能概述

AHMU 仿真器接收其他设备（测试台/成员系统）发送的飞机状态消息，解析其中的飞行阶段字段（`flight_phase`），据此进入或退出维护模式。

- **维护模式**：`flight_phase = 15`（maintenance），需持续 **30 秒**方生效；
- **正常模式**：`flight_phase = 1~14`（如 `2` = engine_start），立即恢复；
- **全局终止标志**（`global_abort_flag`）：维护阶段为 `False`，其他阶段为 `True`。

AHMU 提供两条等价接收入口：

| 入口 | 通道 | 端点 | 用途 |
|------|------|------|------|
| HTTP API | HTTP | `POST /api/v1/maintenance/receive-aircraft-status` | 调试/界面调用 |
| UDP (ICD) | A664/AFDX UDP | `0.0.0.0:34896`（Aircraft_Status_Msg） | 对齐真实 ICD 物理链路 |

## 2. 前置条件

1. **后端已启动**：双击项目根目录 `start.bat`，或执行
   ```cmd
   cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator
   start.bat
   ```
2. **HTTP 服务就绪**（返回 200）：
   ```cmd
   curl http://localhost:8443/api/v1/system/mode
   ```
3. **UDP 接收器就绪**（随后端自动监听 34896）：
   ```cmd
   netstat -ano | findstr 34896
   ```
   预期看到一行 `UDP  0.0.0.0:34896  *:*  <PID>`。
4. **初始模式为正常**：上述 `curl` 返回的 JSON 中 `"mode":"normal"`。
5. **测试脚本就位**：`backend/tests/` 下的两个脚本：
   - `simulate_aircraft_status_sender.py`（HTTP 发送）
   - `simulate_aircraft_status_icd.py`（UDP/ICD 发送）

## 3. 关键配置与参数

### 3.1 ICD 消息参数（对齐 Aircraft_Status_Msg）

| 参数 | 值 |
|------|-----|
| 消息名 | Aircraft_Status_Msg |
| UDP 端口 | 34896 |
| VL ID | 23212 |
| 载荷大小 | 68 字节 |
| 发送周期 | 1000 ms（1Hz） |
| Flight_Phase 字节位置 | 12~13（UBNR 16 位大端） |
| Flight_Leg 字节位置 | 8~9（SINT 16 位大端） |
| Global_Abort_Flag 字节位置 | 64（BOOL） |

### 3.2 维护判定阈值

| 参数 | 值 |
|------|-----|
| 维护空速阈值 | < 80 kts |
| 维护条件持续 | ≥ 30 秒 |
| 维护阶段 flight_phase | 15（maintenance） |

### 3.3 发送脚本参数（`simulate_aircraft_status_icd.py`）

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | 127.0.0.1 | 目标地址 |
| `--port` | 34896 | 目标 UDP 端口 |
| `--period` | 1.0 | 发送周期（秒） |
| `--phase` | 15 | 飞行阶段：数字 1~15 或名称（`power_on`/`engine_start`/.../`maintenance`） |
| `--count` | 0（无限） | 发送条数 |
| `--duration` | 0（无限） | 持续秒数 |
| `--sequence` | 无 | 阶段序列 `"engine_start:10,maintenance:35"` |
| `--leg` | 0 | 飞行航段 |

## 4. 测试步骤

所有命令均在 `backend` 目录下执行：

```cmd
cd C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\ahmu-simulator\backend
```

### 4.1 HTTP 接口进入/退出维护模式

**进入维护模式**：

```cmd
curl -X POST http://localhost:8443/api/v1/maintenance/receive-aircraft-status -H "Content-Type: application/json" -d "{\"flight_phase\":15}"
```

**退出维护模式**：

```cmd
curl -X POST http://localhost:8443/api/v1/maintenance/receive-aircraft-status -H "Content-Type: application/json" -d "{\"flight_phase\":2}"
```

### 4.2 UDP（ICD）进入/退出维护模式

**进入维护模式**（周期发送 maintenance，持续 35 秒超过 30 秒阈值）：

```cmd
venv\Scripts\python.exe tests\simulate_aircraft_status_icd.py --phase maintenance --period 1.0 --count 35
```

**退出维护模式**：

```cmd
venv\Scripts\python.exe tests\simulate_aircraft_status_icd.py --phase engine_start --period 0.5 --count 3
```

### 4.3 异常与边界测试

```cmd
rem 越界阶段 (应报错)
venv\Scripts\python.exe tests\simulate_aircraft_status_icd.py --phase 16

rem 未知阶段名 (应报错)
venv\Scripts\python.exe tests\simulate_aircraft_status_icd.py --phase fly_to_moon
```

### 4.4 阶段序列测试（一次脚本内先正常后维护）

```cmd
venv\Scripts\python.exe tests\simulate_aircraft_status_icd.py --sequence engine_start:10,maintenance:35
```

## 5. 预期结果

| 测试项 | 预期结果 |
|--------|----------|
| 进入维护（HTTP/UDP） | 返回 `phase_name=maintenance`、`global_abort_flag=false`；30 秒内 `mode` 保持 `normal` 且 `hold_remaining` 递减，30 秒后 `mode=maintenance` |
| 退出维护 | 返回 `mode_action=normal`、`global_abort_flag=true`，`mode` 立即恢复 `normal` |
| 信号联动 | 维护时维护开关 `ground_test`、轮载 `True`、空速 `0.0`；正常时开关 `normal`、轮载 `False` |
| 异常拦截 | 越界/未知阶段返回 `status:error` 及明确提示，不影响当前模式 |
| UDP 接收 | `GET /api/v1/maintenance/rx-status` 的 `rx_count` 随发送递增，`latest.phase_name` 正确 |

## 6. 验证方法

### 6.1 命令行验证

```cmd
rem 查看当前模式与 30s 计时
curl http://localhost:8443/api/v1/system/mode

rem 查看接收侧统计
curl http://localhost:8443/api/v1/maintenance/rx-status
```

### 6.2 界面验证

登录 `http://localhost:8443`（账号 `TEST` / `123456`）：
- 顶部导航栏模式徽章在 `NORMAL MODE` 与 `MAINTENANCE MODE` 间切换；
- 维护模式下，导航仅「启动测试 / 数据加载 / 数据重置 / 数据下载 / 生命周期」可操作，其余标签被禁用。

### 6.3 WebSocket 事件验证

订阅 `ws://localhost:8443/ws/ahmu`，可观察：
- `maintenance_rx`：收到每条飞机状态消息；
- `mode_transition`：维护条件首次满足（30s 计时开始）；
- `mode_change`：30s 后切换维护模式，或恢复正常模式。

## 7. 常见问题排查

| 现象 | 可能原因 | 解决 |
|------|----------|------|
| `curl` 连接失败 | 后端未启动 | 双击 `start.bat` 重新启动后端 |
| UDP 发送后 `rx_count` 不增长 | UDP 端口 34896 未监听 | 确认后端已启动，`netstat -ano \| findstr 34896` 有监听 |
| 持续发送但 30 秒后仍未进入维护模式 | 发送脚本中途重启导致计时重置 | 单次脚本持续发送超过 30 秒，或使用 `--sequence` |
| `curl` 在 PowerShell 无输出 | `curl` 是 `Invoke-WebRequest` 别名 | 改用 `curl.exe` 或 `Invoke-RestMethod` |
| 端口 8443 被占用导致启动失败 | 存在残留后端进程 | `netstat -ano \| findstr 8443` 找到 PID 后 `taskkill /F /PID <PID>` |
| 脚本报 `ModuleNotFoundError: app` | 未在 `backend` 目录执行 | 先 `cd` 到 `backend` 目录再运行脚本 |

## 8. 相关文件

| 文件 | 说明 |
|------|------|
| `backend/app/services/maintenance_mode.py` | 维护模式状态机（信号驱动 + 30s 持续计时） |
| `backend/app/services/maintenance_rx.py` | 接收侧（HTTP API + UDP 接收器） |
| `backend/app/core/icd_aircraft_status.py` | Aircraft_Status_Msg 编解码（对齐 XML 字节定义） |
| `backend/tests/simulate_aircraft_status_sender.py` | HTTP 发送模拟脚本 |
| `backend/tests/simulate_aircraft_status_icd.py` | UDP/ICD 发送模拟脚本（模拟 air_icd_start） |
| `backend/tests/verify_icd_against_xml.py` | ICD 编解码 ↔ XML 字节定义比对校验 |
