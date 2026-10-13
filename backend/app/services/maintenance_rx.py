"""
维护模式控制 —— 接收侧逻辑 (AHMU 接收其他设备发送的飞机状态消息)
====================================================================

依据 ETest 测试脚本《维护模式控制.py》及 AircraftStatusMessage 类梳理的实现。

背景:
  《维护模式控制.py》描述的是【其他设备(测试台/成员系统)】发送给 AHMU 的操作逻辑:
      air_status = AircraftStatusMessage(test_type="AHMU_equipment_system_mix_test",
                                          cpm_equipment_test_list=["PAA_L3"])
      air_status.normal_mode('engine_start',   flight_data={})   # 正常模式 (飞行阶段=2)
      air_status.maintenance_mode('maintenance', flight_data={}) # 维护模式 (飞行阶段=15)

  即: 其他设备通过 AFDX(A664) 通道向 AHMU 周期发送"飞机状态消息", 消息中携带
  飞行阶段字段(flight_phase), AHMU 接收后据此进入/退出维护模式。

消息格式与字段含义 (梳理自 public_function.py / __get_event_chase_number):
  - flight_phase (飞行阶段, 1~15): 维护模式判定的直接依据
      {1: power_on, 2: engine_start, 3: taxi_off, 4: takeoff_roll, 5: initial_climb,
       6: climb, 7: cruise, 8: descent, 9: approach, 10: go_around, 11: flare,
       12: rollout, 13: taxi_in, 14: engine_shutdown, 15: maintenance}
  - global_abort_flag (全局终止标志): maintenance 阶段=False, 其它阶段=True
  - 其它字段: icao_code / reg_num(注册号) / flight_num(航班号) / dep_arpt(出发机场)
              dest_arpt(目的地机场) / utc_date / utc_time / flight_leg(航段)

状态处理规则 (梳理自 __check_event_chase):
  1. event_chase == "maintenance" → global_abort_flag = False; 否则 = True
  2. 维护模式需持续超过 30 秒才符合要求 (30秒内进入维护模式视为不符合要求)
  3. normal_mode 使 AHMU 恢复为正常模式 (飞行阶段 1~14)

接收流程:
  接收飞机状态消息 → 解析 flight_phase → 判定维护/正常 → 驱动维护模式状态机
  (复用 MaintenanceModeService 的 30s 持续计时) → 维护 global_abort_flag → 响应

说明: ETest 将 maintenance 定义为飞行阶段 15 (共 15 档), 与 AHMU 生成侧
aircraft_status.py 的 FlightPhase 枚举(MAINTENANCE=14) 编号不同。本接收侧模块
严格对齐 ETest 原始定义 (maintenance=15), 保证"与原逻辑保持一致"。
"""
import socket
import threading
from datetime import datetime
from loguru import logger

from app.core.icd_aircraft_status import unpack_aircraft_status, AIRCRAFT_STATUS_MSG
from app.core.websocket_manager import ws_manager
from app.services.maintenance_mode import maintenance_service, SystemMode


# ETest 飞行阶段映射 (对齐 __get_event_chase_number)
FLIGHT_PHASE_NAMES = {
    1: "power_on",
    2: "engine_start",
    3: "taxi_off",
    4: "takeoff_roll",
    5: "initial_climb",
    6: "climb",
    7: "cruise",
    8: "descent",
    9: "approach",
    10: "go_around",
    11: "flare",
    12: "rollout",
    13: "taxi_in",
    14: "engine_shutdown",
    15: "maintenance",
}

# 字符串名 → 编号 反向映射
FLIGHT_PHASE_NUMBERS = {v: k for k, v in FLIGHT_PHASE_NAMES.items()}

MAINTENANCE_PHASE = 15          # 维护模式对应的飞行阶段
PHASE_MIN, PHASE_MAX = 1, 15    # 飞行阶段合法范围
MAINTENANCE_HOLD_SEC = 30       # 维护模式需持续的时间 (对齐 maintenance_service.HOLD_DURATION)


class MaintenanceModeReceiver:
    """AHMU 维护模式接收器 (接收其他设备的飞机状态消息并驱动维护模式)"""

    def __init__(self):
        self._latest_rx: dict = {}
        self._rx_count = 0
        self._global_abort_flag = True   # 全局终止标志 (维护=False)
        self._last_phase = None          # 上次接收的飞行阶段 (用于避免重复驱动信号)

    # ---------------- 字段解析 ----------------

    @staticmethod
    def _extract_flight_phase(payload: dict):
        """
        从消息中提取飞行阶段字段, 兼容多种字段命名:
          - flight_phase / phase (通用)
          - ds1_flight_phase (AHMU A664 DS1 分组编码)
          - event_chase / flight_phase_name / phase_name (字符串名)
        返回 (flight_phase:int, source_field:str) 或 (None, reason)
        """
        if payload is None or not isinstance(payload, dict):
            return None, "payload 必须为字典类型"

        # 1) 直接数字字段
        for field in ("flight_phase", "ds1_flight_phase", "phase", "flight_phase_number"):
            if field in payload and payload[field] is not None:
                raw = payload[field]
                if isinstance(raw, bool):
                    return None, f"字段 {field} 类型错误 (布尔值)"
                if isinstance(raw, int):
                    return raw, field
                if isinstance(raw, str):
                    s = raw.strip()
                    if s.isdigit():
                        return int(s), field
                    return None, f"字段 {field} 非数字字符串: {raw}"
                if isinstance(raw, float) and raw.is_integer():
                    return int(raw), field
                return None, f"字段 {field} 类型错误: {type(raw).__name__}"

        # 2) 字符串名字段
        for field in ("event_chase", "flight_phase_name", "phase_name"):
            if field in payload and payload[field]:
                name = str(payload[field]).strip().lower()
                if name in FLIGHT_PHASE_NUMBERS:
                    return FLIGHT_PHASE_NUMBERS[name], field
                return None, f"未知飞行阶段名: {payload[field]}"

        return None, "消息中缺少飞行阶段字段 (flight_phase/phase/event_chase 等)"

    # ---------------- 接收处理 ----------------

    def _apply_phase(self, phase: int, source: str = "flight_phase") -> dict:
        """
        同步核心逻辑: 校验 + 驱动维护模式 + 记录, 返回结果 dict (不含 WebSocket 广播)

        供 HTTP API 接收路径与 UDP 接收路径复用, 保证两路行为一致。
        """
        self._rx_count += 1

        # 边界校验 (1~15)
        if phase < PHASE_MIN or phase > PHASE_MAX:
            logger.warning(f"维护模式接收: 飞行阶段越界 {phase} (合法范围 {PHASE_MIN}~{PHASE_MAX})")
            return self._error_response(
                f"飞行阶段越界: {phase} (合法范围 {PHASE_MIN}~{PHASE_MAX})")

        phase_name = FLIGHT_PHASE_NAMES.get(phase, "unknown")

        # 全局终止标志 (维护阶段=False, 其它=True)
        self._global_abort_flag = (phase != MAINTENANCE_PHASE)

        # 驱动维护模式状态机 (复用 MaintenanceModeService 的 30s 持续计时)
        # 仅在飞行阶段变化时驱动信号: 周期重复发送同一阶段时不再重复 set_signals,
        # 否则每次接收都会重置 30s 计时(hold_start), 导致计时永远无法累计。
        if phase == MAINTENANCE_PHASE:
            mode_action = SystemMode.MAINTENANCE.value
        else:
            mode_action = SystemMode.NORMAL.value

        if phase != self._last_phase:
            if phase == MAINTENANCE_PHASE:
                # 维护条件等价信号: 维护开关=地面测试 + 地 + 空速0(<80)
                maintenance_service.set_signals({
                    "maintenance_switch": "ground_test",
                    "all_gear_wow": True,
                    "airspeed": 0.0,
                })
            else:
                # 正常条件等价信号: 维护开关=正常 + 空 + 空速120(>80)
                maintenance_service.set_signals({
                    "maintenance_switch": "normal",
                    "all_gear_wow": False,
                    "airspeed": 120.0,
                })
            self._last_phase = phase

        # 记录最新接收消息
        self._latest_rx = {
            "flight_phase": phase,
            "phase_name": phase_name,
            "global_abort_flag": self._global_abort_flag,
            "source_field": source,
            "received_at": datetime.utcnow().isoformat(),
        }

        # 组装响应 (体现 30s 持续计时状态)
        mode_info = maintenance_service.get_mode_info()
        hold_remaining = mode_info.get("hold_remaining", 0) \
            if phase == MAINTENANCE_PHASE else 0

        logger.info(
            f"维护模式接收: flight_phase={phase}({phase_name}) "
            f"global_abort_flag={self._global_abort_flag} mode_action={mode_action} "
            f"当前模式={mode_info['mode']} 剩余计时={hold_remaining}s")

        return {
            "status": "ok",
            "flight_phase": phase,
            "phase_name": phase_name,
            "global_abort_flag": self._global_abort_flag,
            "mode_action": mode_action,
            "current_mode": mode_info["mode"],
            "hold_remaining": round(hold_remaining, 1),
            "entered_maintenance": (
                phase == MAINTENANCE_PHASE
                and mode_info["mode"] == SystemMode.MAINTENANCE.value),
            "message": self._result_message(phase, mode_info),
        }

    async def receive_aircraft_status(self, payload: dict) -> dict:
        """
        HTTP API 接收路径: 解析飞行阶段并驱动维护模式 (供调试/界面调用)

        返回: {status, flight_phase, phase_name, global_abort_flag,
               mode_action, current_mode, hold_remaining, ...}
        """
        # 解析飞行阶段
        phase, src = self._extract_flight_phase(payload)
        if phase is None:
            self._rx_count += 1
            logger.warning(f"维护模式接收: 飞行阶段解析失败 - {src}")
            return self._error_response(f"飞行阶段解析失败: {src}")

        result = self._apply_phase(phase, src)

        # WebSocket 推送接收事件 (供前端展示)
        if result.get("status") == "ok":
            await ws_manager.broadcast("maintenance_rx", {
                "flight_phase": result["flight_phase"],
                "phase_name": result["phase_name"],
                "global_abort_flag": result["global_abort_flag"],
                "mode_action": result["mode_action"],
                "current_mode": result["current_mode"],
                "hold_remaining": result["hold_remaining"],
                "timestamp": datetime.utcnow().isoformat(),
            })
        return result

    # ---------------- UDP 接收 (对齐 ICD Aircraft_Status_Msg 端口 34896) ----------------

    def start_udp_receiver(self, host: str = "0.0.0.0",
                           port: int = AIRCRAFT_STATUS_MSG["port"]):
        """启动 UDP 接收线程, 监听 Aircraft_Status_Msg (默认 34896)

        对齐 ETest air_icd_start 经 AFDX 端口发送 68 字节 Aircraft_Status_Msg 的场景:
        本接收器接收 UDP 数据报 → unpack 为字段 → 驱动维护模式。
        """
        if getattr(self, "_udp_running", False):
            logger.warning("维护模式 UDP 接收器已在运行")
            return
        self._udp_running = True
        self._udp_thread = threading.Thread(
            target=self._udp_recv_loop, args=(host, port), daemon=True,
            name="maintenance-rx-udp")
        self._udp_thread.start()
        logger.info(f"维护模式 UDP 接收器已启动: {host}:{port} (Aircraft_Status_Msg)")

    def stop_udp_receiver(self):
        """停止 UDP 接收线程"""
        self._udp_running = False
        logger.info("维护模式 UDP 接收器已停止")

    def _udp_recv_loop(self, host: str, port: int):
        """UDP 接收循环 (后台线程)"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as e:
            logger.error(f"UDP 端口 {host}:{port} 绑定失败: {e}")
            self._udp_running = False
            sock.close()
            return

        while self._udp_running:
            try:
                sock.settimeout(1.0)
                data, addr = sock.recvfrom(AIRCRAFT_STATUS_MSG["size"] * 2)
                msg = unpack_aircraft_status(data)
                logger.info(f"UDP 收到 Aircraft_Status_Msg 来自 {addr}: "
                            f"flight_phase={msg['flight_phase']} "
                            f"leg={msg['flight_leg']}")
                # 驱动维护模式 (与 HTTP 路径复用同一核心逻辑)
                self._apply_phase(msg["flight_phase"], source="udp_flight_phase")
            except socket.timeout:
                continue
            except ValueError as e:
                logger.warning(f"UDP 消息解析失败: {e}")
            except Exception as e:
                logger.error(f"UDP 接收异常: {e}")
        sock.close()
        logger.info("维护模式 UDP 接收线程已退出")

    # ---------------- 辅助 ----------------

    def _result_message(self, phase: int, mode_info: dict) -> str:
        if phase == MAINTENANCE_PHASE:
            if mode_info["mode"] == SystemMode.MAINTENANCE.value:
                return "已进入维护模式 (飞行阶段=maintenance)"
            return (f"收到维护模式指令, 维护条件需持续 {MAINTENANCE_HOLD_SEC}s "
                    f"后方生效 (当前剩余 {mode_info.get('hold_remaining', 0)}s)")
        return f"收到正常模式指令 (飞行阶段={FLIGHT_PHASE_NAMES.get(phase)})"

    @staticmethod
    def _error_response(message: str) -> dict:
        return {"status": "error", "message": message}

    # ---------------- 查询接口 ----------------


    def get_rx_status(self) -> dict:
        """获取接收侧状态"""
        mode_info = maintenance_service.get_mode_info()
        return {
            "rx_count": self._rx_count,
            "latest_rx": self._latest_rx,
            "global_abort_flag": self._global_abort_flag,
            "maintenance_phase": MAINTENANCE_PHASE,
            "phase_range": [PHASE_MIN, PHASE_MAX],
            "phase_names": FLIGHT_PHASE_NAMES,
            "current_mode": mode_info["mode"],
            "hold_remaining": mode_info.get("hold_remaining", 0),
        }


# 全局单例
maintenance_rx = MaintenanceModeReceiver()
