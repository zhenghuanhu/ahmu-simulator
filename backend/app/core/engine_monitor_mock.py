"""
发动机监视装置 (Engine Monitoring Unit, EMU) Mock

模拟 EMU 通过 ARINC 总线向 AHMU 上报发动机配平数据:
- N1 / N2 转速
- EGT 排气温度
- 燃油流量 Fuel Flow
- 推力额定 Thrust Rating
- 当前配平值 Trim Value

生产环境由真实发动机监视装置板卡 SDK 通过 A664/A429 总线替代,
本 Mock 供本地开发与仿真集成测试使用。

接口设计:
- start_monitoring(callback): 启动周期上报, 每个上报周期调用 callback(engine_id, data)
- apply_trim(engine_id, target_trim): 下发配平指令, EMU 模拟执行并返回是否接受
- get_engine_data(engine_id) / get_all_engine_data(): 查询当前发动机配平数据
"""
import asyncio
import random
from datetime import datetime
from typing import Callable, Optional
from loguru import logger

from app.config import ENGINE_MONITOR_CONFIG


class EngineMonitorMock:
    """发动机监视装置 Mock (全局单例)

    每台发动机维护一组独立的实时配平数据, 并周期性(默认1Hz)通过回调上报。
    配平指令下发后, EMU 模拟执行: 在若干上报周期内逐步逼近目标配平值。
    """

    def __init__(self):
        cfg = ENGINE_MONITOR_CONFIG
        self._engine_count = cfg["engine_count"]
        self._n1_range = cfg["n1_range"]
        self._n2_range = cfg["n2_range"]
        self._egt_range = cfg["egt_range"]
        self._ff_range = cfg["fuel_flow_range"]
        self._thrust_range = cfg["thrust_rating_range"]
        self._report_period_ms = 1000  # 上报周期, 由服务注入覆盖
        self._fail_prob = cfg["emu_validity_fail_prob"]

        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._callback: Optional[Callable] = None

        # 发动机实时状态: engine_id -> dict
        self._engines: dict[int, dict] = {
            eid: self._init_engine(eid) for eid in range(1, self._engine_count + 1)
        }

    # ---------------- 内部初始化 ----------------

    def _init_engine(self, engine_id: int) -> dict:
        """初始化单台发动机状态"""
        return {
            "engine_id": engine_id,
            "n1": round(random.uniform(60.0, 95.0), 2),
            "n2": round(random.uniform(60.0, 95.0), 2),
            "egt": round(random.uniform(600.0, 850.0), 1),
            "fuel_flow": round(random.uniform(1500.0, 3500.0), 1),
            "thrust_rating": round(random.uniform(60.0, 95.0), 2),
            "trim_value": 0.0,          # 当前配平值
            "trim_target": None,         # 目标配平值 (无指令时为 None)
            "trim_status": "monitoring",  # monitoring/applying/applied
            "validity": "valid",
        }

    # ---------------- 生命周期 ----------------

    def set_report_period(self, period_ms: int):
        """设置上报周期 (毫秒)"""
        self._report_period_ms = period_ms

    async def start_monitoring(self, callback: Callable):
        """启动周期上报, callback(engine_id, data) 异步回调"""
        self._callback = callback
        self._running = True
        self._task = asyncio.create_task(self._report_loop())
        logger.info(f"发动机监视装置 Mock 已启动: {self._engine_count} 台发动机, "
                    f"上报周期 {self._report_period_ms}ms")

    async def stop_monitoring(self):
        """停止周期上报"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("发动机监视装置 Mock 已停止")

    async def _report_loop(self):
        """周期上报循环: 刷新数据 -> 回调推送"""
        while self._running:
            try:
                for engine_id, eng in self._engines.items():
                    self._refresh_engine(eng)
                    if self._callback:
                        # 深拷贝快照, 避免外部修改内部状态
                        await self._callback(engine_id, dict(eng))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"发动机监视装置上报异常: {e}")
            await asyncio.sleep(self._report_period_ms / 1000.0)

    def _refresh_engine(self, eng: dict):
        """刷新单台发动机数据 (模拟传感器波动 + 配平执行)"""
        # 模拟数据有效性 (小概率失效)
        if random.random() < self._fail_prob:
            eng["validity"] = "unavailable"
        else:
            eng["validity"] = "valid"

        # 参数小范围波动
        eng["n1"] = self._clamp(
            round(eng["n1"] + random.uniform(-0.8, 0.8), 2), self._n1_range)
        eng["n2"] = self._clamp(
            round(eng["n2"] + random.uniform(-0.8, 0.8), 2), self._n2_range)
        eng["egt"] = self._clamp(
            round(eng["egt"] + random.uniform(-5.0, 5.0), 1), self._egt_range)
        eng["fuel_flow"] = self._clamp(
            round(eng["fuel_flow"] + random.uniform(-20.0, 20.0), 1), self._ff_range)

        # 配平执行: 当前值向目标值逐步逼近
        if eng["trim_target"] is not None:
            eng["trim_status"] = "applying"
            step = 0.3  # 每周期逼近步长 (%)
            diff = eng["trim_target"] - eng["trim_value"]
            if abs(diff) <= step:
                eng["trim_value"] = eng["trim_target"]
                eng["trim_status"] = "applied"
            else:
                eng["trim_value"] = round(eng["trim_value"] + (step if diff > 0 else -step), 2)

            # 配平影响推力额定
            eng["thrust_rating"] = self._clamp(
                round(eng["thrust_rating"] + diff * 0.05, 2), self._thrust_range)
        else:
            eng["trim_status"] = "monitoring"

    @staticmethod
    def _clamp(value: float, rng: list) -> float:
        """限制数值范围"""
        lo, hi = rng[0], rng[1]
        return max(lo, min(hi, value))

    # ---------------- 配平指令执行 ----------------

    def apply_trim(self, engine_id: int, target_trim: float) -> dict:
        """下发配平指令, EMU 接受后模拟执行 (返回确认结果)

        返回: {"accepted": bool, "message": str}
        """
        eng = self._engines.get(engine_id)
        if eng is None:
            return {"accepted": False, "message": f"发动机 {engine_id} 不存在"}

        # 模拟 EMU 拒绝概率 (数据失效或忙时拒绝)
        if eng["validity"] == "unavailable" and random.random() < 0.5:
            return {"accepted": False, "message": "EMU 数据失效, 拒绝配平指令"}

        eng["trim_target"] = target_trim
        eng["trim_status"] = "applying"
        logger.info(f"[EMU] 接受配平指令: 发动机 {engine_id} -> {target_trim}%")
        return {"accepted": True, "message": "指令已接受, 正在执行"}

    # ---------------- 数据查询 ----------------

    def get_engine_data(self, engine_id: int) -> Optional[dict]:
        """查询单台发动机当前配平数据"""
        eng = self._engines.get(engine_id)
        if eng is None:
            return None
        return dict(eng)

    def get_all_engine_data(self) -> list[dict]:
        """查询所有发动机当前配平数据"""
        return [dict(eng) for eng in self._engines.values()]

    @property
    def engine_count(self) -> int:
        return self._engine_count


# 全局单例
engine_monitor = EngineMonitorMock()
