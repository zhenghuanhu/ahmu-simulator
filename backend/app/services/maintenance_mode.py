"""
维护模式控制服务
- 多条件状态机: 空/地 + 空速 + 维护开关
- 条件持续≥30s进入维护模式
- 正常模式↔维护模式切换
"""
import asyncio
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger

from app.core.websocket_manager import ws_manager
from app.config import MAINTENANCE_MODE_CONDITIONS, NORMAL_MODE_CONDITIONS


class SystemMode(str, Enum):
    NORMAL = "normal"
    MAINTENANCE = "maintenance"
    TRANSITIONING = "transitioning"


class MaintenanceModeService:
    """维护模式控制器"""

    def __init__(self):
        self._running = False
        self._mode = SystemMode.NORMAL
        self._condition_hold_start = None  # 条件持续时间起始
        self._hold_duration = MAINTENANCE_MODE_CONDITIONS["hold_duration_sec"]

        # 当前信号状态 (Mock)
        self._signals = {
            "air_ground": "ground",  # ground / air
            "airspeed": 0.0,        # kts
            "maintenance_switch": "normal",  # normal / ground_test / data_load
            "gear_wow": True,       # True = on ground
        }

    async def start(self):
        self._running = True
        logger.info(f"维护模式服务已启动, 当前模式: {self._mode.value}")
        asyncio.create_task(self._mode_monitor_loop())

    async def stop(self):
        self._running = False
        logger.info("维护模式服务已停止")

    async def _mode_monitor_loop(self):
        """持续监控模式切换条件"""
        while self._running:
            try:
                # 更新Mock信号
                self._update_mock_signals()

                # 检查维护模式条件
                maint_ok = self._check_maintenance_conditions()
                # 检查正常模式条件
                normal_ok = self._check_normal_conditions()

                if maint_ok and self._mode != SystemMode.MAINTENANCE:
                    if self._condition_hold_start is None:
                        self._condition_hold_start = datetime.utcnow()
                        logger.info(f"维护模式条件满足, 开始计时...")
                        await ws_manager.broadcast("mode_transition", {
                            "from": self._mode.value,
                            "to": SystemMode.TRANSITIONING.value,
                            "reason": "maintenance_conditions_met",
                            "hold_remaining": self._hold_duration,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    elif (datetime.utcnow() - self._condition_hold_start).total_seconds() >= self._hold_duration:
                        old_mode = self._mode
                        self._mode = SystemMode.MAINTENANCE
                        self._condition_hold_start = None
                        logger.info(f"已进入维护模式")
                        await ws_manager.broadcast("mode_change", {
                            "mode": SystemMode.MAINTENANCE.value,
                            "previous": old_mode.value,
                            "timestamp": datetime.utcnow().isoformat(),
                        })

                elif normal_ok and self._mode != SystemMode.NORMAL:
                    old_mode = self._mode
                    self._mode = SystemMode.NORMAL
                    self._condition_hold_start = None
                    logger.info(f"已切换到正常模式")
                    await ws_manager.broadcast("mode_change", {
                        "mode": SystemMode.NORMAL.value,
                        "previous": old_mode.value,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

                elif not maint_ok and self._mode != SystemMode.NORMAL and self._condition_hold_start:
                    # 条件不再满足, 取消切换
                    self._condition_hold_start = None
                    await ws_manager.broadcast("mode_transition_cancelled", {
                        "reason": "conditions_no_longer_met",
                        "timestamp": datetime.utcnow().isoformat(),
                    })

            except Exception as e:
                logger.error(f"模式监控异常: {e}")
            await asyncio.sleep(1.0)

    def _update_mock_signals(self):
        """更新Mock信号"""
        import random
        if self._mode == SystemMode.MAINTENANCE:
            # 维护模式下: 地面, 低速
            self._signals["air_ground"] = "ground"
            self._signals["airspeed"] = random.uniform(0, 60)
            self._signals["maintenance_switch"] = "ground_test"
            self._signals["gear_wow"] = True
        else:
            # 正常模式下: 随机
            self._signals["airspeed"] = random.uniform(0, 200)
            self._signals["air_ground"] = "air" if self._signals["airspeed"] > 90 else "ground"
            self._signals["gear_wow"] = self._signals["air_ground"] == "ground"
            self._signals["maintenance_switch"] = "normal"

    def _check_maintenance_conditions(self) -> bool:
        """检查维护模式条件: 空/地=地 + 空速≤70kts + 维护开关=地面测试, 三者同时满足"""
        return (
            self._signals["gear_wow"] == MAINTENANCE_MODE_CONDITIONS["all_gear_wow"] and
            self._signals["airspeed"] <= MAINTENANCE_MODE_CONDITIONS["voted_calibrated_airspeed"] and
            self._signals["maintenance_switch"] in ("ground_test", "data_load")
        )

    def _check_normal_conditions(self) -> bool:
        """检查正常模式条件: 空速≥90kts + 维护开关=正常"""
        return (
            self._signals["airspeed"] >= NORMAL_MODE_CONDITIONS["airspeed"] and
            self._signals["maintenance_switch"] == NORMAL_MODE_CONDITIONS["maintenance_switch"]
        )

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
        return {
            "mode": self._mode.value,
            "hold_elapsed": hold_elapsed,
            "hold_remaining": max(0, self._hold_duration - hold_elapsed),
            "signals": self._signals,
        }


# 全局单例
maintenance_service = MaintenanceModeService()
