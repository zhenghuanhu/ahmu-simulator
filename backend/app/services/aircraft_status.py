"""
飞机状态消息生成与发布服务 (4.3.7 飞机状态消息)

功能概述 (依据《技术方案-20260818》4.3.7 节 + OMS技术交底 + 成员系统规范):
  1. 消息发布: 以 1Hz 频率周期性发布 "OHMS飞机状态" 消息
  2. 数据来源: 飞行航段/阶段/全局终止标志由 OHMS 自身计算;
     UTC时间/ICAO码/注册号/航班号/目的地/出发机场由其他系统提供
  3. 接收信号: 起落架系统(轮载) / 大气系统(空速) / 飞管系统(尾号+航班号)
  4. 双源选择: 8个参数各有左源/右源, 左源有效时基于左源, 左源失效切换右源
  5. 阶段判定: 飞行阶段 1~15 + 维护模式逻辑计算表
  6. 编码: A664 / A429 / A825 参照成员系统规范

数据流:
  起落架/大气/飞管/发动机/刹车/控制板 (其他系统)
        │ (ARINC 总线: 轮载/空速/尾号/航班号等)
        ▼
    AHMU 飞机状态服务 ── 双源选择 ── 阶段计算(1~15) ── 航段/终止标志 ── 1Hz 发布 ──▶ A664/A429/A825
        │
        ▼ (WebSocket 推送)
    前端实时展示

飞行阶段判定逻辑计算表 (1~15, 对齐 ETest __get_event_chase_number):
  1  power_on 上电           空地=地 & 油门≤怠速 & 空速<滑行阈值 (初始)
  2  engine_start 发动机启动  空地=地 & 怠速<油门<起飞推力
  3  taxi_off 滑出           空地=地 & 地速>滑行阈值 & 空速<滑行阈值 & 刹车释放
  4  takeoff_roll 起飞滑跑    空地=地 & 油门≥起飞推力 & 空速<离地速度
  5  initial_climb 初始爬升   空地=空 & 空速≥离地速度 & 高度变化率>0
  6  climb 爬升              空地=空 & 高度变化率>爬升阈值 & 高度<巡航高度
  7  cruise 巡航             空地=空 & 高度≈巡航高度 & |高度变化率|<巡航容差
  8  descent 下降            空地=空 & 高度变化率<-下降阈值 & 高度>进近高度
  9  approach 进近           空地=空 & 高度≤进近高度 & 高度下降
  10 go_around 复飞          空地=空 & 上一阶段下降/进近/拉平 & 高度变化率重新向上
  11 flare 拉平              空地=空 & 高度低 & 下降 & 空速<离地速度
  12 rollout 着陆滑跑        空地=地 & 空速>0 & 上一阶段空中 (刚接地)
  13 taxi_in 滑入            空地=地 & 地速>滑行阈值 & 空速<滑行阈值 (落地后)
  14 engine_shutdown 关车     空地=地 & 油门≤怠速 & 空速<滑行阈值 (滑入后)
  15 maintenance 维护        维护开关∈{地面测试,数据加载}
"""
import asyncio
import json
import socket
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple
from loguru import logger

from app.database import SessionLocal, AircraftStatusRecord, EventReport
from app.core.websocket_manager import ws_manager
from app.core.icd_aircraft_status import pack_aircraft_status
from app.config import AIRCRAFT_STATUS_CONFIG, AIRCRAFT_STATUS_RX_CONFIG


class FlightPhase(int, Enum):
    """飞行阶段 1~15 (对齐 ETest __get_event_chase_number)"""
    POWER_ON = 1          # power_on 上电
    ENGINE_START = 2      # engine_start 发动机启动
    TAXI_OFF = 3          # taxi_off 滑出
    TAKEOFF_ROLL = 4      # takeoff_roll 起飞滑跑
    INITIAL_CLIMB = 5     # initial_climb 初始爬升
    CLIMB = 6             # climb 爬升
    CRUISE = 7            # cruise 巡航
    DESCENT = 8           # descent 下降
    APPROACH = 9          # approach 进近
    GO_AROUND = 10        # go_around 复飞
    FLARE = 11            # flare 拉平
    ROLLOUT = 12          # rollout 着陆滑跑
    TAXI_IN = 13          # taxi_in 滑入
    ENGINE_SHUTDOWN = 14  # engine_shutdown 关车
    MAINTENANCE = 15      # maintenance 维护


PHASE_NAMES = {
    1: "power_on", 2: "engine_start", 3: "taxi_off", 4: "takeoff_roll",
    5: "initial_climb", 6: "climb", 7: "cruise", 8: "descent",
    9: "approach", 10: "go_around", 11: "flare", 12: "rollout",
    13: "taxi_in", 14: "engine_shutdown", 15: "maintenance",
}

# ICD 信号名 -> 内部参数名 映射 (对齐 Member_System_all_sender_icd_v6_1_1.xml)
# 8 个双源参数在 ICD 中为标准信号名, 接收侧需兼容映射。
# 注意: engine_thrust_lever_angle(油门推力角度) 在 ICD 中无精确数值信号
#       (仅有 Engine_TLA_Not_TOGA / TLA_Not_At_Idle_During_Start 两个 DIS 位),
#       与 FMS_IC_1_PARA 类似属测试端私有参数, 故未纳入别名映射。
ICD_SIGNAL_ALIASES = {
    "All_Gear_WOW": "air_ground_status",              # LGCU 轮载 DIS 1bit (1=地/0=空)
    "Voted_Calibrated_Airspeed": "airspeed",          # FCM FLOAT 32 knots
    "Ground_Speed": "ground_speed",                   # AMU BCD knots
    "L012_Ground_Speed": "ground_speed",
    "Voted_Altitude_Rate": "fcm_corrected_altitude_rate",  # FCM FLOAT 32 ft/min
    "Voted_Pressure_Altitude": "flight_altitude",     # FCM FLOAT 32 ft
    "Brake_Pressure": "brake_status",                 # ABCU UBNR 12 PSI
    "Maintenance_Switch": "maintenance_switch_position",  # 驾驶舱 DIS
}


class AircraftStatusService:
    """飞机状态消息生成与发布服务 (全局单例)"""

    def __init__(self):
        cfg = AIRCRAFT_STATUS_CONFIG
        self._running = False
        self._publish_period_ms = cfg["publish_period_ms"]
        self._port_a664 = cfg["port_a664"]
        self._port_a429 = cfg["port_a429"]
        self._port_a825 = cfg["port_a825"]

        # 飞机身份信息 (由其他系统提供)
        self._identity = {
            "icao_code": cfg["icao_code"],
            "registration": cfg["registration"],
            "flight_number": cfg["flight_number"],
            "departure_airport": cfg["departure_airport"],
            "destination_airport": cfg["destination_airport"],
        }

        # 判定阈值
        self._thr = {
            "airspeed_liftoff": cfg["airspeed_liftoff_kts"],
            "airspeed_taxi": cfg["airspeed_taxi_kts"],
            "ground_speed_taxi": cfg["ground_speed_taxi_kts"],
            "altitude_cruise": cfg["altitude_cruise_ft"],
            "altitude_approach": cfg["altitude_approach_ft"],
            "altitude_rate_climb": cfg["altitude_rate_climb_fpm"],
            "altitude_rate_cruise": cfg["altitude_rate_cruise_fpm"],
            "thrust_takeoff": cfg["thrust_takeoff_deg"],
            "thrust_idle": cfg["thrust_idle_deg"],
        }

        # 双源参数: 每个参数有 left / right, 各含 value + valid
        # 默认值 (飞机初始状态: 地, 空速0, 维护开关正常)
        param_defaults = {
            "air_ground_status": "ground",        # ground/air
            "airspeed": 0.0,                       # kts
            "ground_speed": 0.0,                   # kts
            "fcm_corrected_altitude_rate": 0.0,    # ft/min
            "engine_thrust_lever_angle": 0.0,      # 度
            "flight_altitude": 0.0,                # ft
            "brake_status": "applied",             # applied/released
            "maintenance_switch_position": "normal",  # normal/ground_test/data_load
        }
        self._params = {
            p: {
                "left": {"value": param_defaults.get(p), "valid": True},
                "right": {"value": param_defaults.get(p), "valid": True},
            }
            for p in cfg["dual_source_params"]
        }

        # OHMS 自身计算的状态
        self._flight_phase = FlightPhase.POWER_ON.value
        self._flight_leg = 0                      # 飞行航段 (-128~127)
        self._global_abort_flag = False           # 全局终止标志
        self._active_source = "left"              # 当前生效源
        self._prev_phase = FlightPhase.POWER_ON.value  # 上一周期阶段 (用于航段递增)

        # 最新消息缓存 (供查询)
        self._latest_message: dict = {}

        # 飞机事件信息 接收/回传通道 (Mock UDP 模拟 A664)
        rx_cfg = AIRCRAFT_STATUS_RX_CONFIG
        self._rx_host = rx_cfg["recv_host"]
        self._rx_port = rx_cfg["recv_port"]
        self._send_port = rx_cfg["send_port"]
        self._rx_sock: Optional[socket.socket] = None
        self._send_sock: Optional[socket.socket] = None

        # 事件关联参数 (飞机事件信息中的事件参数, 如 FMS_IC_1_PARA)
        self._event_params: dict[str, object] = {
            p: 0 for p in rx_cfg.get("event_params", ["fms_ic_1_para"])
        }
        self._received_utc_time: Optional[str] = None   # 接收到的 UTC_TIME (缺省用系统时间)

        # 变化检测: 记录上一周期 (phase, leg) 用于节流写库
        self._last_recorded_key: Optional[tuple] = None

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._setup_udp_sockets()
        asyncio.create_task(self._publish_loop())
        asyncio.create_task(self._rx_loop())
        logger.info("飞机状态消息服务已启动 (1Hz 发布 + 飞机事件信息接收)")

    async def stop(self):
        self._running = False
        self._close_udp_sockets()
        logger.info("飞机状态消息服务已停止")

    # ==================== UDP 收发 (Mock A664) ====================

    def _setup_udp_sockets(self):
        """创建 UDP 收发 socket (Mock A664 模拟飞机事件信息收发)"""
        try:
            self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._rx_sock.bind((self._rx_host, self._rx_port))
            self._rx_sock.setblocking(False)
            self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            logger.info(f"[AircraftStatus] UDP 接收已监听: {self._rx_host}:{self._rx_port}, "
                        f"回传: 127.0.0.1:{self._send_port}")
        except OSError as e:
            logger.error(f"[AircraftStatus] UDP socket 初始化失败: {e}")
            self._close_udp_sockets()

    def _close_udp_sockets(self):
        for sock in (self._rx_sock, self._send_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self._rx_sock = None
        self._send_sock = None

    async def _rx_loop(self):
        """UDP 接收循环: 接收飞机事件信息, 解码参数并注入"""
        loop = asyncio.get_event_loop()
        while self._running and self._rx_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._rx_sock, 65535)
                self._handle_rx_message(data, addr)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.5)

    @staticmethod
    def _normalize_param_name(param: str) -> str:
        """ICD 信号名 -> 内部参数名 (兼容映射)"""
        return ICD_SIGNAL_ALIASES.get(param, param)

    @staticmethod
    def _normalize_param_value(param: str, value):
        """按 ICD 语义归一化枚举值"""
        if param == "air_ground_status":
            # All_Gear_WOW: 1=地(WOW)/0=空
            if value in (1, True, "1", "true", "True"):
                return "ground"
            if value in (0, False, "0", "false", "False"):
                return "air"
            return value
        if param == "brake_status":
            # Brake_Pressure PSI: >0 施加, <=0 释放
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return "released" if value <= 0 else "applied"
            return value
        return value

    def _handle_rx_message(self, data: bytes, addr):
        """处理接收到的飞机事件信息 (Mock JSON): 解码参数并注入"""
        try:
            msg = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            logger.warning(f"[AircraftStatus] 无法解析飞机事件信息: {data[:80]!r}")
            return
        if not isinstance(msg, dict):
            return

        # 1. 8 个双源参数 (params 子对象, 兼容 ICD 信号名)
        params = msg.get("params") or msg.get("signals") or {}
        if isinstance(params, dict):
            for param, value in params.items():
                name = self._normalize_param_name(param)
                if name in self._params:
                    # 双源参数: 注入左源 (valid=True), 枚举/数值归一化 (异常值拒绝注入)
                    self._inject_param(name, value, "left", True)
                elif name in self._event_params:
                    # 事件关联参数
                    self._event_params[name] = value

        # 2. 事件参数 (顶层或 params 内)
        for p in self._event_params:
            if p in msg:
                self._event_params[p] = msg[p]

        # 3. UTC 时间 (由测试端提供)
        if msg.get("utc_time"):
            self._received_utc_time = str(msg["utc_time"])

        # 4. 飞机身份信息
        for key in ("icao_code", "registration", "flight_number",
                    "departure_airport", "destination_airport"):
            if key in msg:
                self._identity[key] = str(msg[key])

        # 5. 事件参数变化 -> 记录事件报告 (飞机状态监测事件)
        self._record_event_if_changed()
        logger.debug(f"[AircraftStatus] 收到飞机事件信息: params={list(params.keys()) if isinstance(params, dict) else '?'} "
                     f"事件参数={self._event_params}")

    def _record_event_if_changed(self):
        """事件参数 (FMS_IC_1_PARA) 变化时写入 EventReport 事件报告"""
        fms = self._event_params.get("fms_ic_1_para")
        if fms is None:
            return
        db = SessionLocal()
        try:
            last = db.query(EventReport).filter(
                EventReport.event_type == "fms_ic_1_para_change"
            ).order_by(EventReport.created_at.desc()).first()
            prev = None
            if last and last.description:
                try:
                    prev = json.loads(last.description).get("value")
                except (ValueError, TypeError):
                    prev = None
            if prev == fms:
                return  # 未变化, 不重复记录
            db.add(EventReport(
                event_type="fms_ic_1_para_change",
                member_system="FMS_IC_1",
                description=json.dumps({"value": fms}, ensure_ascii=False),
                related_params=json.dumps(self._event_params, ensure_ascii=False),
            ))
            db.commit()
            logger.info(f"[AircraftStatus] 事件参数 FMS_IC_1_PARA 变化: {prev} -> {fms}")
        except Exception as e:
            db.rollback()
            logger.error(f"[AircraftStatus] 事件报告记录失败: {e}")
        finally:
            db.close()

    # ==================== 信号接收接口 ====================

    def _inject_param(self, param: str, value, source: str = "left", valid: bool = True) -> bool:
        """注入单个参数 (字段名归一化 + 枚举归一化 + 数值归一化), 返回是否成功

        非法数值 (如 airspeed="abc") 拒绝注入, 避免阶段判定比较时抛 TypeError。
        """
        name = self._normalize_param_name(param)
        if name not in self._params:
            return False
        value = self._normalize_param_value(name, value)
        numeric_params = {"airspeed", "ground_speed", "fcm_corrected_altitude_rate",
                          "engine_thrust_lever_angle", "flight_altitude"}
        if name in numeric_params and value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                return False
        self._params[name][source] = {"value": value, "valid": bool(valid)}
        return True

    def set_source_signal(self, source: str, param: str, value, valid: bool = True) -> dict:
        """注入某源某参数 (模拟从其他系统接收信号)

        source: left/right
        param:  8个双源参数之一
        value:  参数值
        valid:  有效性 (False 表示该源参数失效)
        """
        source = source if source in ("left", "right") else "left"
        param = self._normalize_param_name(param)
        if param not in self._params:
            return {"status": "error", "message": f"未知参数: {param}"}
        if not self._inject_param(param, value, source, valid):
            return {"status": "error", "message": f"参数 {param} 需为数值"}
        logger.info(f"飞机状态信号注入: {source}源 {param}={value} valid={valid}")
        return {"status": "ok", "source": source, "param": param}

    def set_aircraft_identity(self, payload: dict) -> dict:
        """设置飞机身份信息 (由飞管系统/ATC提供)"""
        for key in ("icao_code", "registration", "flight_number",
                    "departure_airport", "destination_airport"):
            if key in payload:
                self._identity[key] = str(payload[key])
        return {"status": "ok", "identity": self._identity}

    # ==================== 双源选择 ====================

    def _select_source_value(self, param: str) -> Tuple[object, bool, str]:
        """双源选择: 左源有效用左源, 左源失效切右源, 均失效返回 (None, False)"""
        p = self._params[param]
        left, right = p["left"], p["right"]
        if left["valid"]:
            return left["value"], True, "left"
        if right["valid"]:
            return right["value"], True, "right"
        return None, False, "none"

    def _collect_effective_params(self) -> dict:
        """收集当前生效参数 (双源选择后), 附来源与有效性"""
        result = {}
        for param in self._params:
            value, valid, source = self._select_source_value(param)
            result[param] = {"value": value, "valid": valid, "source": source}
        return result

    # ==================== 飞行阶段计算 ====================

    def _calc_flight_phase(self, eff: dict) -> int:
        """按飞行阶段逻辑计算表判定阶段 1~15

        优先判定维护模式 (维护开关位置), 再按空地/空速/高度等判定飞行阶段
        """
        def g(param, default=None):
            e = eff.get(param)
            return e["value"] if e and e["valid"] else default

        # 维护开关位置
        msw = g("maintenance_switch_position", "normal")
        if msw in ("ground_test", "data_load"):
            return FlightPhase.MAINTENANCE.value  # 15 维护

        ag = g("air_ground_status", "ground")       # 空地状态
        cas = g("airspeed", 0.0)                    # 空速
        gs = g("ground_speed", 0.0)                 # 地速
        alt_rate = g("fcm_corrected_altitude_rate", 0.0)  # 高度变化率
        thrust = g("engine_thrust_lever_angle", 0.0)  # 油门推力角度
        alt = g("flight_altitude", 0.0)             # 飞行高度
        brake = g("brake_status", "applied")        # 刹车状态

        thr = self._thr

        # 关键参数双源均失效 → 无法判定, 保持上一阶段 (无上一阶段回退上电)
        if eff["air_ground_status"]["valid"] is False and \
           eff["airspeed"]["valid"] is False:
            return self._prev_phase if self._prev_phase else FlightPhase.POWER_ON.value

        # air_ground 失效但空速有效时, 用空速推断空地状态 (空速≥离地速度视为空中)
        if eff["air_ground_status"]["valid"] is False and eff["airspeed"]["valid"] is True:
            ag = "air" if (cas or 0.0) >= thr["airspeed_liftoff"] else "ground"

        if ag == "air":
            # ---- 空中 ----
            # 复飞: 上一阶段下降/进近/拉平/复飞, 高度变化率重新向上
            if alt_rate > thr["altitude_rate_climb"] and self._prev_phase in (
                    FlightPhase.DESCENT.value, FlightPhase.APPROACH.value,
                    FlightPhase.FLARE.value, FlightPhase.GO_AROUND.value):
                return FlightPhase.GO_AROUND.value    # 10 复飞
            # 拉平: 低高度 + 下降 + 空速低于离地速度 (着陆前)
            if alt <= thr["altitude_approach"] and alt_rate < 0 and cas < thr["airspeed_liftoff"]:
                return FlightPhase.FLARE.value        # 11 拉平
            # 初始爬升: 高度低(<进近高度), 高度变化率>0, 空速≥离地速度 (刚离地)
            if alt < thr["altitude_approach"] and alt_rate > 0 and cas >= thr["airspeed_liftoff"]:
                return FlightPhase.INITIAL_CLIMB.value  # 5 初始爬升
            # 巡航: 高度≈巡航高度, |高度变化率|≤容差
            if abs(alt_rate) <= thr["altitude_rate_cruise"] and alt >= thr["altitude_cruise"] * 0.9:
                return FlightPhase.CRUISE.value       # 7 巡航
            # 爬升: 高度变化率>爬升阈值, 高度<巡航
            if alt_rate > thr["altitude_rate_climb"] and alt < thr["altitude_cruise"]:
                return FlightPhase.CLIMB.value        # 6 爬升
            # 下降: 高度变化率<-下降阈值, 高度>进近
            if alt_rate < -thr["altitude_rate_climb"] and alt > thr["altitude_approach"]:
                return FlightPhase.DESCENT.value      # 8 下降
            # 进近: 高度≤进近高度, 高度下降
            if alt <= thr["altitude_approach"] and alt_rate < 0:
                return FlightPhase.APPROACH.value     # 9 进近
            # 空中默认: 爬升
            return FlightPhase.CLIMB.value            # 6 爬升
        else:
            # ---- 地面 ----
            # 起飞滑跑: 油门≥起飞推力, 空速<离地速度
            if thrust >= thr["thrust_takeoff"] and cas < thr["airspeed_liftoff"]:
                return FlightPhase.TAKEOFF_ROLL.value  # 4 起飞滑跑
            # 着陆滑跑: 刚接地(上一阶段空中), 空速>0
            if cas > 0 and self._prev_phase in (FlightPhase.FLARE.value, FlightPhase.APPROACH.value,
                                                FlightPhase.GO_AROUND.value):
                return FlightPhase.ROLLOUT.value      # 12 着陆滑跑
            # 滑出/滑入: 地速>滑行阈值, 空速低, 刹车释放 (结合上一阶段方向)
            if gs > thr["ground_speed_taxi"] and cas < thr["airspeed_taxi"] and brake == "released":
                if self._prev_phase in (FlightPhase.ROLLOUT.value, FlightPhase.APPROACH.value,
                                        FlightPhase.FLARE.value, FlightPhase.DESCENT.value):
                    return FlightPhase.TAXI_IN.value  # 13 滑入
                return FlightPhase.TAXI_OFF.value     # 3 滑出
            # 发动机启动: 怠速<油门<起飞推力
            if thrust > thr["thrust_idle"] and thrust < thr["thrust_takeoff"]:
                return FlightPhase.ENGINE_START.value  # 2 发动机启动
            # 关车/上电: 油门≤怠速, 空速低
            if thrust <= thr["thrust_idle"] and cas < thr["airspeed_taxi"]:
                if self._prev_phase in (FlightPhase.TAXI_IN.value, FlightPhase.ROLLOUT.value,
                                        FlightPhase.ENGINE_SHUTDOWN.value):
                    return FlightPhase.ENGINE_SHUTDOWN.value  # 14 关车
                return FlightPhase.POWER_ON.value     # 1 上电
            # 地面默认: 上电
            return FlightPhase.POWER_ON.value         # 1 上电

    # ==================== 航段与全局终止标志 ====================

    def _update_flight_leg(self, new_phase: int):
        """航段管理: 起飞滑跑(进入新起飞)时航段递增, 范围 -128~127 循环"""
        # 从地面滑行/上电/发动机启动进入起飞滑跑, 视为新航段开始
        if (new_phase == FlightPhase.TAKEOFF_ROLL.value
                and self._prev_phase in (FlightPhase.TAXI_OFF.value, FlightPhase.POWER_ON.value,
                                         FlightPhase.ENGINE_START.value)):
            self._flight_leg += 1
            if self._flight_leg > 127:
                self._flight_leg = -128
            logger.info(f"进入新航段: 飞行航段 = {self._flight_leg}")

    def _calc_global_abort_flag(self, new_phase: int) -> bool:
        """全局终止标志: 维护阶段=False, 其它阶段=True (参照成员系统规范)"""
        return False if new_phase == FlightPhase.MAINTENANCE.value else True

    # ==================== 消息生成 ====================

    def _build_message(self) -> dict:
        """生成 OHMS 飞机状态消息 (9 个字段)"""
        eff = self._collect_effective_params()
        phase = self._calc_flight_phase(eff)
        self._update_flight_leg(phase)
        self._global_abort_flag = self._calc_global_abort_flag(phase)
        self._prev_phase = phase

        # 关键参数 (空地/空速) 双源均失效 → 阶段判定降级 (保持上一阶段), 显式标记
        key_degraded = (eff["air_ground_status"]["valid"] is False and
                        eff["airspeed"]["valid"] is False)

        # UTC 日期与时间: 优先用接收到的 UTC_TIME (测试端提供), 缺省用系统当前 UTC
        if self._received_utc_time:
            try:
                now = datetime.fromisoformat(self._received_utc_time.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                now = datetime.now(timezone.utc)
        else:
            now = datetime.now(timezone.utc)

        # 双源选择结果 (当前生效源)
        source = "left"
        for e in eff.values():
            if e["source"] == "right":
                source = "right"
                break
        self._active_source = source
        self._flight_phase = phase

        message = {
            "flight_leg": self._flight_leg,                    # OHMS 飞行航段
            "flight_phase": phase,                              # OHMS 阶段 (1~15)
            "phase_name": PHASE_NAMES.get(phase, "Unknown"),
            "utc_date": now.strftime("%Y-%m-%d"),               # UTC 日期
            "utc_time": now.strftime("%H:%M:%S"),               # UTC 时间
            "icao_code": self._identity["icao_code"],           # ICAO 应答机码
            "registration": self._identity["registration"],     # 注册号
            "flight_number": self._identity["flight_number"],   # 航班号
            "departure_airport": self._identity["departure_airport"],   # 出发机场
            "destination_airport": self._identity["destination_airport"],  # 目的地机场
            "global_abort_flag": self._global_abort_flag,       # 全局终止标志
            "source": source,                                    # 数据源
            "air_ground": eff["air_ground_status"]["value"],
            "airspeed": eff["airspeed"]["value"],
            # 8 个双源参数当前生效值/有效性/来源, 显式标记 valid=False (失效≠默认值)
            "params": {k: {"value": v["value"], "valid": v["valid"], "source": v["source"]}
                       for k, v in eff.items()},
            "phase_degraded": key_degraded,                      # 关键参数双源失效导致阶段降级
            "event_params": dict(self._event_params),            # 事件关联参数 (FMS_IC_1_PARA 等)
            "timestamp": now.isoformat(),
        }
        self._latest_message = message
        return message

    def _encode_buses(self, message: dict) -> dict:
        """按成员系统规范编码 (A664 / A429 / A825)

        A664: DS1 分组 (flight_phase, global_abort_flag, flight_leg)
        A429: DS5/DS6 分组 (flight_leg, flight_phase, global_abort_flag)
        A825: 备份通道 (完整消息)
        """
        return {
            "a664": {
                "port": self._port_a664,
                "vl_id": 1,
                "ds1_flight_phase": message["flight_phase"],
                "ds1_global_abort_flag": int(message["global_abort_flag"]),
                "ds1_flight_leg": message["flight_leg"],
            },
            "a429": {
                "port": self._port_a429,
                "label": 227,   # OHMS 飞机状态 A429 Label (示例)
                "ds5_flight_leg": message["flight_leg"],
                "ds6_flight_phase": message["flight_phase"],
                "ds6_global_abort_flag": int(message["global_abort_flag"]),
            },
            "a825": {
                "can_id": self._port_a825,
                "payload": message,
            },
        }

    # ==================== 周期发布 ====================

    async def _publish_loop(self):
        """1Hz 周期发布飞机状态消息"""
        while self._running:
            try:
                message = self._build_message()
                self._encode_buses(message)

                # 1. WebSocket 推送 (前端实时展示)
                await ws_manager.broadcast("aircraft_status", message)

                # 2. 记录到数据库 (节流: 仅阶段/航段变化时记录, 避免高频写库)
                self._record_if_changed(message)

                # 3. A664 回传飞机状态消息给测试端 (Mock UDP)
                self._send_status_message(message)

            except Exception as e:
                logger.error(f"飞机状态消息发布异常: {e}")
            await asyncio.sleep(self._publish_period_ms / 1000)

    def _send_status_message(self, message: dict):
        """通过 UDP 回传 A664 飞机状态消息 (68 字节二进制帧) 给测试端/对向端

        对齐《飞机事件信息.py》: 对向端经 AFDX 接收 68 字节 Aircraft_Status_Msg 二进制帧
        (而非 JSON), 由 e_icd 解码后经 check_air_status_massage_para 校验阶段/终止标志/UTC 等。
        """
        if not self._send_sock:
            return
        try:
            data = pack_aircraft_status(
                flight_phase=message["flight_phase"],
                global_abort_flag=message["global_abort_flag"],
                flight_leg=message["flight_leg"],
                icao_code=message["icao_code"],
                registration=message["registration"],
                flight_number=message["flight_number"],
                departure_airport=message["departure_airport"],
                destination_airport=message["destination_airport"],
                utc=self._message_utc_tuple(message),
            )
            self._send_sock.sendto(data, ("127.0.0.1", self._send_port))
        except OSError as e:
            logger.error(f"[AircraftStatus] 回传飞机状态消息失败: {e}")

    @staticmethod
    def _message_utc_tuple(message: dict):
        """从消息的 utc_date/utc_time 解析为 (days, months, years, hours, minutes, seconds)"""
        try:
            d = datetime.strptime(message["utc_date"], "%Y-%m-%d")
            t = datetime.strptime(message["utc_time"], "%H:%M:%S").time()
            return (d.day, d.month, d.year % 100, t.hour, t.minute, t.second)
        except (KeyError, ValueError):
            now = datetime.now(timezone.utc)
            return (now.day, now.month, now.year % 100, now.hour, now.minute, now.second)

    def _record_if_changed(self, message: dict):
        """仅阶段/航段变化时记录到数据库 (节流, 避免高频写库)"""
        key = (message["flight_phase"], message["flight_leg"])
        if key == self._last_recorded_key:
            return
        self._last_recorded_key = key
        db = SessionLocal()
        try:
            rec = AircraftStatusRecord(
                flight_leg=message["flight_leg"],
                flight_phase=message["flight_phase"],
                global_abort_flag=message["global_abort_flag"],
                source=message["source"],
                icao_code=message["icao_code"],
                registration=message["registration"],
                flight_number=message["flight_number"],
                departure_airport=message["departure_airport"],
                destination_airport=message["destination_airport"],
                phase_name=message["phase_name"],
                air_ground=message["air_ground"],
                airspeed=message["airspeed"],
            )
            db.add(rec)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()

    # ==================== 查询接口 ====================

    def get_latest_message(self) -> dict:
        """获取最新飞机状态消息"""
        if not self._latest_message:
            self._build_message()
        return {
            "status": "ok",
            "message": self._latest_message,
            "flight_phase": self._flight_phase,
            "flight_leg": self._flight_leg,
            "global_abort_flag": self._global_abort_flag,
            "active_source": self._active_source,
            "params": self._collect_effective_params(),
            "identity": self._identity,
        }

    def get_status(self) -> dict:
        """获取飞机状态服务整体状态"""
        return {
            "running": self._running,
            "publish_period_ms": self._publish_period_ms,
            "flight_phase": self._flight_phase,
            "phase_name": PHASE_NAMES.get(self._flight_phase, "Unknown"),
            "flight_leg": self._flight_leg,
            "global_abort_flag": self._global_abort_flag,
            "active_source": self._active_source,
            "params": self._collect_effective_params(),
        }


# 全局单例
aircraft_status_service = AircraftStatusService()
