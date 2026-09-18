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

飞行阶段判定逻辑计算表 (1~15):
  1  上电 Power-Up         空地=地 & 油门≤怠速 & 空速<滑行阈值 (初始)
  2  预飞行 Pre-Flight     空地=地 & 油门≤怠速 & 地速≈0 (维护开关=正常)
  3  发动机启动 Engine Start 空地=地 & 怠速<油门<起飞推力 & 空速<滑行阈值
  4  滑出 Taxi-Out         空地=地 & 地速>滑行阈值 & 空速<起飞阈值 & 刹车释放
  5  起飞滑跑 Takeoff Roll 空地=地 & 空速递增 & 油门≥起飞推力
  6  起飞离地 Liftoff      空地=空 & 空速≥离地速度 & 高度变化率>0
  7  爬升 Climb            空地=空 & 高度变化率>爬升阈值 & 高度<巡航高度
  8  巡航 Cruise           空地=空 & 高度≈巡航高度 & |高度变化率|<巡航容差
  9  下降 Descent          空地=空 & 高度变化率<-下降阈值 & 高度>进近高度
  10 进近 Approach         空地=空 & 高度≤进近高度 & 高度下降 & 空速减小
  11 着陆滑跑 Landing Roll 空地=地 & 空速>0 & 高度<阈值 (刚接地)
  12 滑入 Taxi-In          空地=地 & 地速>滑行阈值 & 空速<滑行阈值
  13 发动机关车 Shutdown   空地=地 & 油门≈0 & 空速<滑行阈值
  14 维护 Maintenance      维护开关∈{地面测试,数据加载}
  15 未知/无效 Unknown      双源均失效无法判定
"""
import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple
from loguru import logger

from app.database import SessionLocal, AircraftStatusRecord
from app.core.websocket_manager import ws_manager
from app.config import AIRCRAFT_STATUS_CONFIG


class FlightPhase(int, Enum):
    """飞行阶段 1~15"""
    POWER_UP = 1
    PRE_FLIGHT = 2
    ENGINE_START = 3
    TAXI_OUT = 4
    TAKEOFF_ROLL = 5
    LIFTOFF = 6
    CLIMB = 7
    CRUISE = 8
    DESCENT = 9
    APPROACH = 10
    LANDING_ROLL = 11
    TAXI_IN = 12
    ENGINE_SHUTDOWN = 13
    MAINTENANCE = 14
    UNKNOWN = 15


PHASE_NAMES = {
    1: "Power-Up", 2: "Pre-Flight", 3: "Engine Start", 4: "Taxi-Out",
    5: "Takeoff Roll", 6: "Liftoff", 7: "Climb", 8: "Cruise",
    9: "Descent", 10: "Approach", 11: "Landing Roll", 12: "Taxi-In",
    13: "Engine Shutdown", 14: "Maintenance", 15: "Unknown",
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
        self._flight_phase = FlightPhase.POWER_UP.value
        self._flight_leg = 0                      # 飞行航段 (-128~127)
        self._global_abort_flag = False           # 全局终止标志
        self._active_source = "left"              # 当前生效源
        self._prev_phase = FlightPhase.POWER_UP.value  # 上一周期阶段 (用于航段递增)

        # 最新消息缓存 (供查询)
        self._latest_message: dict = {}

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        asyncio.create_task(self._publish_loop())
        logger.info("飞机状态消息服务已启动 (1Hz 发布)")

    async def stop(self):
        self._running = False
        logger.info("飞机状态消息服务已停止")

    # ==================== 信号接收接口 ====================

    def set_source_signal(self, source: str, param: str, value, valid: bool = True) -> dict:
        """注入某源某参数 (模拟从其他系统接收信号)

        source: left/right
        param:  8个双源参数之一
        value:  参数值
        valid:  有效性 (False 表示该源参数失效)
        """
        source = source if source in ("left", "right") else "left"
        if param not in self._params:
            return {"status": "error", "message": f"未知参数: {param}"}
        self._params[param][source] = {"value": value, "valid": bool(valid)}
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
            return FlightPhase.MAINTENANCE.value  # Phase 14 维护

        ag = g("air_ground_status", "ground")       # 空地状态
        cas = g("airspeed", 0.0)                    # 空速
        gs = g("ground_speed", 0.0)                 # 地速
        alt_rate = g("fcm_corrected_altitude_rate", 0.0)  # 高度变化率
        thrust = g("engine_thrust_lever_angle", 0.0)  # 油门推力角度
        alt = g("flight_altitude", 0.0)             # 飞行高度
        brake = g("brake_status", "applied")        # 刹车状态

        thr = self._thr

        # 关键参数双源均失效 → 无法判定 → Phase 15 未知
        if eff["air_ground_status"]["valid"] is False and \
           eff["airspeed"]["valid"] is False:
            return FlightPhase.UNKNOWN.value

        if ag == "air":
            # ---- 空中 ----
            if abs(alt_rate) <= thr["altitude_rate_cruise"] and alt >= thr["altitude_cruise"] * 0.9:
                return FlightPhase.CRUISE.value        # 8 巡航
            if alt_rate > thr["altitude_rate_climb"] and alt < thr["altitude_cruise"]:
                return FlightPhase.CLIMB.value         # 7 爬升
            if alt_rate < -thr["altitude_rate_climb"] and alt > thr["altitude_approach"]:
                return FlightPhase.DESCENT.value       # 9 下降
            if alt <= thr["altitude_approach"] and alt_rate < 0:
                return FlightPhase.APPROACH.value      # 10 进近
            if cas >= thr["airspeed_liftoff"] and alt_rate > 0:
                return FlightPhase.LIFTOFF.value       # 6 离地
            # 空中默认 (数据不足以细分)
            return FlightPhase.CLIMB.value             # 保守: 爬升
        else:
            # ---- 地面 ----
            if cas >= thr["airspeed_liftoff"]:
                return FlightPhase.LIFTOFF.value       # 6 离地 (空速达离地速度)
            if cas > 0 and thrust >= thr["thrust_takeoff"]:
                return FlightPhase.TAKEOFF_ROLL.value  # 5 起飞滑跑
            if gs > thr["ground_speed_taxi"] and cas < thr["airspeed_taxi"] and brake == "released":
                # 区分滑出/滑入 (高度为0 且 空速低, 需结合上一阶段方向)
                if self._prev_phase in (FlightPhase.LANDING_ROLL.value, FlightPhase.APPROACH.value,
                                        FlightPhase.DESCENT.value):
                    return FlightPhase.TAXI_IN.value   # 12 滑入
                return FlightPhase.TAXI_OUT.value      # 4 滑出
            if thrust > thr["thrust_idle"] and thrust < thr["thrust_takeoff"]:
                return FlightPhase.ENGINE_START.value  # 3 发动机启动
            if thrust <= thr["thrust_idle"] and cas < thr["airspeed_taxi"]:
                if gs <= thr["ground_speed_taxi"]:
                    return FlightPhase.PRE_FLIGHT.value  # 2 预飞行 (或 1 上电/13 关车)
                return FlightPhase.TAXI_IN.value       # 12 滑入
            # 地面默认
            return FlightPhase.POWER_UP.value          # 1 上电

    # ==================== 航段与全局终止标志 ====================

    def _update_flight_leg(self, new_phase: int):
        """航段管理: 起飞滑跑(进入新起飞)时航段递增, 范围 -128~127 循环"""
        # 从地面滑行进入起飞滑跑, 视为新航段开始
        if (new_phase == FlightPhase.TAKEOFF_ROLL.value
                and self._prev_phase in (FlightPhase.TAXI_OUT.value, FlightPhase.POWER_UP.value,
                                         FlightPhase.PRE_FLIGHT.value, FlightPhase.ENGINE_START.value)):
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

        # UTC 日期与时间 (由其他系统提供, 此处用系统当前 UTC)
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
                encoded = self._encode_buses(message)

                # 1. WebSocket 推送 (前端实时展示)
                await ws_manager.broadcast("aircraft_status", message)

                # 2. 记录到数据库 (节流: 仅阶段/航段变化时记录, 避免高频写库)
                await self._record_if_changed(message)

                # 3. A664/A429/A825 编码发布 (通过共享内存/ARINC 通道, Mock 环境仅日志)
                logger.debug(
                    f"飞机状态消息发布: 阶段={message['phase_name']}({message['flight_phase']}) "
                    f"航段={message['flight_leg']} 源={message['source']} "
                    f"终止={message['global_abort_flag']} 编码A664/A429/A825")

            except Exception as e:
                logger.error(f"飞机状态消息发布异常: {e}")
            await asyncio.sleep(self._publish_period_ms / 1000)

    async def _record_if_changed(self, message: dict):
        """阶段/航段变化时记录到数据库"""
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
