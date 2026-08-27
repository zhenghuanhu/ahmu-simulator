"""
维护模式控制服务
================================
模拟 AHMU 通过 ARINC664 总线接收 RDCU 转换后的离散量消息:
- 维护开关信号 (Switch_Ground_Test / Switch_Data_Load / Switch_Normal)
- 轮载信号 (All_Gear_WOW, True=地 / False=空)
- 空速信号 (Voted_Calibrated_Airspeed, kts)

模式判定逻辑:
- 维护模式条件 (三者同时满足且持续超过30s):
    1. "空/地"信号 = "地" (All_Gear_WOW = True and valid)
    2. 飞机空速 < 80 kts
    3. 维护开关 = "地面测试" 或 "数据加载"
  进入维护模式后, 用户在前端仅可对地面测试及数据加载业务进行操作。
- 正常模式条件 (三者同时满足):
    1. "空/地"信号 = "空" (All_Gear_WOW = False and valid)
    2. 飞机空速 > 80 kts
    3. 维护开关 = "正常"
  进入正常模式后, 可进行除地面测试和数据加载外的其它业务。
"""
import asyncio
from datetime import datetime
from enum import Enum
from loguru import logger

from app.core.websocket_manager import ws_manager
from app.config import MAINTENANCE_MODE_CONDITIONS, NORMAL_MODE_CONDITIONS


class SystemMode(str, Enum):
    NORMAL = "normal"
    MAINTENANCE = "maintenance"
    TRANSITIONING = "transitioning"


class MaintenanceModeService:
    """维护模式控制器 (信号驱动状态机)"""

    # 阈值
    AIRSPEED_THRESHOLD = 80.0          # 空速判定阈值 (维护:<80, 正常:>80)
    HOLD_DURATION = 30                 # 维护条件需持续的时间 (秒)

    def __init__(self):
        self._running = False
        self._mode = SystemMode.NORMAL
        self._condition_hold_start = None  # 维护条件持续计时起始

        # ARINC664 离散量信号状态 (Mock, 由信号模拟接口注入)
        # 每个信号含 value + valid (有效性)
        self._signals = {
            "maintenance_switch": "normal",   # normal / ground_test / data_load
            "switch_valid": True,
            "all_gear_wow": True,             # True = 地 (Weight on Wheels)
            "wow_valid": True,
            "airspeed": 0.0,                  # Voted_Calibrated_Airspeed (kts)
            "airspeed_valid": True,
        }

        self._hold_duration = MAINTENANCE_MODE_CONDITIONS.get(
            "hold_duration_sec", self.HOLD_DURATION)

    # ---------------- 生命周期 ----------------

    async def start(self):
        self._running = True
        logger.info(f"维护模式服务已启动, 当前模式: {self._mode.value}")
        asyncio.create_task(self._mode_monitor_loop())

    async def stop(self):
        self._running = False
        logger.info("维护模式服务已停止")

    # ---------------- 状态机主循环 ----------------

    async def _mode_monitor_loop(self):
        """持续监控模式切换条件 (1s周期, 模拟ARINC664离散量消息刷新)"""
        while self._running:
            try:
                maint_ok = self._check_maintenance_conditions()
                normal_ok = self._check_normal_conditions()

                if maint_ok and self._mode != SystemMode.MAINTENANCE:
                    # 维护条件满足 → 计时, 持续30s后进入维护模式
                    if self._condition_hold_start is None:
                        self._condition_hold_start = datetime.utcnow()
                        logger.info("维护模式条件满足 (地 + 空速<80 + 开关=地面测试/数据加载), 开始30s计时")
                        await ws_manager.broadcast("mode_transition", {
                            "from": self._mode.value,
                            "to": SystemMode.TRANSITIONING.value,
                            "reason": "maintenance_conditions_met",
                            "hold_remaining": self._hold_duration,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    else:
                        elapsed = (datetime.utcnow() - self._condition_hold_start).total_seconds()
                        if elapsed >= self._hold_duration:
                            old_mode = self._mode
                            self._mode = SystemMode.MAINTENANCE
                            self._condition_hold_start = None
                            logger.info("维护条件已持续30s, AHMU进入维护模式 (仅可操作地面测试与数据加载)")
                            await ws_manager.broadcast("mode_change", {
                                "mode": SystemMode.MAINTENANCE.value,
                                "previous": old_mode.value,
                                "timestamp": datetime.utcnow().isoformat(),
                            })

                elif normal_ok and self._mode != SystemMode.NORMAL:
                    # 正常条件满足 → 立即恢复正常模式
                    old_mode = self._mode
                    self._mode = SystemMode.NORMAL
                    self._condition_hold_start = None
                    logger.info("正常模式条件满足 (空 + 空速>80 + 开关=正常), 恢复正常模式")
                    await ws_manager.broadcast("mode_change", {
                        "mode": SystemMode.NORMAL.value,
                        "previous": old_mode.value,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

                elif self._condition_hold_start is not None and not maint_ok:
                    # 维护条件不再满足 → 取消计时
                    self._condition_hold_start = None
                    logger.info("维护模式条件不再满足, 取消30s计时")
                    await ws_manager.broadcast("mode_transition_cancelled", {
                        "reason": "conditions_no_longer_met",
                        "timestamp": datetime.utcnow().isoformat(),
                    })

            except Exception as e:
                logger.error(f"模式监控异常: {e}")
            await asyncio.sleep(1.0)

    # ---------------- 条件判定 ----------------

    def _check_maintenance_conditions(self) -> bool:
        """维护模式条件: 空/地=地 + 空速<80 + 开关∈{地面测试,数据加载}, 且信号全部有效"""
        s = self._signals
        return (
            s["wow_valid"] and s["all_gear_wow"] is True
            and s["airspeed_valid"] and s["airspeed"] < self.AIRSPEED_THRESHOLD
            and s["switch_valid"] and s["maintenance_switch"] in ("ground_test", "data_load")
        )

    def _check_normal_conditions(self) -> bool:
        """正常模式条件: 空/地=空 + 空速>80 + 开关=正常, 且信号全部有效"""
        s = self._signals
        return (
            s["wow_valid"] and s["all_gear_wow"] is False
            and s["airspeed_valid"] and s["airspeed"] > self.AIRSPEED_THRESHOLD
            and s["switch_valid"] and s["maintenance_switch"] == "normal"
        )

    def _condition_detail(self) -> dict:
        """各条件明细 (供调试面板显示)"""
        s = self._signals
        maint = {
            "wow_is_ground": bool(s["wow_valid"] and s["all_gear_wow"] is True),
            "airspeed_below_80": bool(s["airspeed_valid"] and s["airspeed"] < self.AIRSPEED_THRESHOLD),
            "switch_in_test_position": bool(
                s["switch_valid"] and s["maintenance_switch"] in ("ground_test", "data_load")),
        }
        normal = {
            "wow_is_air": bool(s["wow_valid"] and s["all_gear_wow"] is False),
            "airspeed_above_80": bool(s["airspeed_valid"] and s["airspeed"] > self.AIRSPEED_THRESHOLD),
            "switch_is_normal": bool(s["switch_valid"] and s["maintenance_switch"] == "normal"),
        }
        return {
            "maintenance": {**maint, "all_met": all(maint.values())},
            "normal": {**normal, "all_met": all(normal.values())},
        }

    # ---------------- 信号设置 (调试/模拟接口) ----------------

    def set_signals(self, payload: dict) -> dict:
        """设置模拟信号, 支持测试用例信号名与内部命名:
        - Switch_Ground_Test / Switch_Data_Load / Switch_Normal / maintenance_switch
        - All_Gear_WOW / all_gear_wow
        - Voted_Calibrated_Airspeed / airspeed
        - *_valid 后缀可控制有效性
        """
        applied = {}
        s = self._signals

        # 维护开关 (互斥三选一)
        switch = None
        if payload.get("Switch_Ground_Test") is True:
            switch = "ground_test"
        elif payload.get("Switch_Data_Load") is True:
            switch = "data_load"
        elif payload.get("Switch_Normal") is True:
            switch = "normal"
        elif "maintenance_switch" in payload:
            switch = str(payload["maintenance_switch"])
        if switch is not None:
            if switch not in ("normal", "ground_test", "data_load"):
                return {"status": "error", "message": f"无效的开关位置: {switch}"}
            applied["maintenance_switch"] = switch
            s["maintenance_switch"] = switch

        # 轮载 (True=地, False=空)
        wow = None
        if "All_Gear_WOW" in payload:
            wow = bool(payload["All_Gear_WOW"])
        elif "all_gear_wow" in payload:
            wow = bool(payload["all_gear_wow"])
        if wow is not None:
            applied["all_gear_wow"] = wow
            s["all_gear_wow"] = wow

        # 空速 (kts)
        airspeed = None
        if "Voted_Calibrated_Airspeed" in payload:
            airspeed = float(payload["Voted_Calibrated_Airspeed"])
        elif "airspeed" in payload:
            airspeed = float(payload["airspeed"])
        if airspeed is not None:
            if airspeed < 0 or airspeed > 600:
                return {"status": "error", "message": "空速超出范围 (0-600 kts)"}
            applied["airspeed"] = airspeed
            s["airspeed"] = airspeed

        # 有效性标志
        for key, skey in [
            ("switch_valid", "switch_valid"),
            ("wow_valid", "wow_valid"),
            ("airspeed_valid", "airspeed_valid"),
        ]:
            if key in payload:
                applied[skey] = bool(payload[key])
                s[skey] = bool(payload[key])

        # 信号变化后重置维护条件计时, 由主循环重新判定
        self._condition_hold_start = None
        logger.info(f"模拟信号更新: {applied}")
        return {"status": "ok", "applied": applied}

    def set_switch(self, position: str) -> dict:
        """设置维护开关位置 (兼容旧接口)"""
        return self.set_signals({"maintenance_switch": position})

    def force_mode(self, mode: str) -> dict:
        """强制切换模式 (测试用, 同步设置匹配的信号状态)"""
        old = self._mode.value
        if mode == "maintenance":
            self._mode = SystemMode.MAINTENANCE
            self._signals.update({
                "maintenance_switch": "ground_test",
                "all_gear_wow": True,
                "airspeed": 0.0,
            })
        elif mode == "normal":
            self._mode = SystemMode.NORMAL
            self._signals.update({
                "maintenance_switch": "normal",
                "all_gear_wow": False,
                "airspeed": 120.0,
            })
        else:
            return {"status": "error", "message": f"无效模式: {mode}"}
        self._condition_hold_start = None
        logger.info(f"模式强制切换: {old} → {mode}")
        return {"status": "ok", "mode": mode, "previous": old}

    # ---------------- 状态查询 ----------------

    @property
    def current_mode(self) -> str:
        return self._mode.value

    @property
    def signals(self) -> dict:
        return self._signals.copy()

    def get_mode_info(self) -> dict:
        hold_elapsed = 0
        if self._condition_hold_start:
            hold_elapsed = (datetime.utcnow() - self._condition_hold_start).total_seconds()
        s = self._signals
        return {
            "mode": self._mode.value,
            "hold_elapsed": round(hold_elapsed, 1),
            "hold_remaining": round(max(0, self._hold_duration - hold_elapsed), 1),
            "airspeed_threshold": self.AIRSPEED_THRESHOLD,
            "hold_duration": self._hold_duration,
            "signals": {
                "Switch_Ground_Test": s["maintenance_switch"] == "ground_test",
                "Switch_Data_Load": s["maintenance_switch"] == "data_load",
                "Switch_Normal": s["maintenance_switch"] == "normal",
                "maintenance_switch": s["maintenance_switch"],
                "switch_valid": s["switch_valid"],
                "All_Gear_WOW": s["all_gear_wow"],
                "wow_valid": s["wow_valid"],
                "Voted_Calibrated_Airspeed": s["airspeed"],
                "airspeed": s["airspeed"],
                "airspeed_valid": s["airspeed_valid"],
            },
            "conditions": self._condition_detail(),
        }


# 全局单例
maintenance_service = MaintenanceModeService()
