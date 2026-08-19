"""
参数监控服务
- 参数配置引擎 (名称/类型/采样率/精度/单位)
- 有效性校验 (unavailable/out_of_range/invalid)
- 1Hz实时推送参数值到前端
- 快捷访问列表管理 (100+张列表, 每张50+参数)
"""
import asyncio
import random
import json
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, ParamSnapshot, QuickAccessList
from app.core.websocket_manager import ws_manager
from app.config import SIMULATION_CONFIG


class ParamMonitorService:
    """参数监控服务"""

    def __init__(self):
        self._running = False
        self._monitored_params: dict[str, dict] = {}  # param_name -> config
        self._quick_lists: dict[int, list[str]] = {}  # list_id -> param names

    async def start(self):
        """启动参数监控服务"""
        self._running = True
        self._init_default_params()
        logger.info("参数监控服务已启动")
        asyncio.create_task(self._param_push_loop())

    async def stop(self):
        self._running = False
        logger.info("参数监控服务已停止")

    def _init_default_params(self):
        """初始化默认监控参数"""
        default_params = [
            {"name": "temperature_1", "type": "float", "unit": "C", "ata": "21-01",
             "min": -50, "max": 150, "rate": 1},
            {"name": "temperature_2", "type": "float", "unit": "C", "ata": "21-02",
             "min": -50, "max": 150, "rate": 1},
            {"name": "voltage_1", "type": "float", "unit": "V", "ata": "24-01",
             "min": 0, "max": 30, "rate": 1},
            {"name": "voltage_2", "type": "float", "unit": "V", "ata": "24-02",
             "min": 0, "max": 30, "rate": 1},
            {"name": "hydraulic_pressure_1", "type": "float", "unit": "PSI", "ata": "29-01",
             "min": 0, "max": 5000, "rate": 1},
            {"name": "hydraulic_pressure_2", "type": "float", "unit": "PSI", "ata": "29-02",
             "min": 0, "max": 5000, "rate": 1},
            {"name": "fuel_quantity", "type": "float", "unit": "KG", "ata": "28-01",
             "min": 0, "max": 50000, "rate": 1},
            {"name": "engine_rpm_1", "type": "float", "unit": "RPM", "ata": "73-01",
             "min": 0, "max": 10000, "rate": 1},
            {"name": "engine_rpm_2", "type": "float", "unit": "RPM", "ata": "73-02",
             "min": 0, "max": 10000, "rate": 1},
            {"name": "altitude", "type": "float", "unit": "FT", "ata": "34-01",
             "min": 0, "max": 51000, "rate": 1},
            {"name": "airspeed", "type": "float", "unit": "KTS", "ata": "34-02",
             "min": 0, "max": 600, "rate": 1},
            {"name": "heading", "type": "float", "unit": "DEG", "ata": "34-03",
             "min": 0, "max": 360, "rate": 1},
        ]
        for p in default_params:
            self._monitored_params[p["name"]] = p

    async def _param_push_loop(self):
        """1Hz参数推送循环"""
        while self._running:
            try:
                param_data = {}
                for name, config in self._monitored_params.items():
                    # Mock: 生成模拟参数值
                    min_val = config.get("min", 0)
                    max_val = config.get("max", 100)
                    value = random.uniform(min_val, min_val + (max_val - min_val) * 0.3 + 10)

                    # 有效性校验
                    validity = "valid"
                    if random.random() < 0.02:
                        validity = random.choice(["unavailable", "out_of_range", "invalid"])
                        if validity == "out_of_range":
                            value = max_val + 100  # 超出范围

                    param_data[name] = {
                        "value": round(value, 2),
                        "unit": config["unit"],
                        "validity": validity,
                        "ata": config["ata"],
                        "type": config["type"],
                        "timestamp": datetime.utcnow().isoformat(),
                    }

                    # 存储到数据库 (每10秒存一次, 避免数据量过大)
                    if random.random() < 0.1:
                        self._save_snapshot(name, value, config, validity)

                # WebSocket推送
                await ws_manager.broadcast("param_update", param_data)

            except Exception as e:
                logger.error(f"参数推送异常: {e}")
            await asyncio.sleep(1.0)  # 1Hz

    def _save_snapshot(self, name: str, value: float, config: dict, validity: str):
        """保存参数快照到数据库"""
        db = SessionLocal()
        try:
            snapshot = ParamSnapshot(
                param_name=name,
                param_value=value,
                param_unit=config["unit"],
                param_type=config["type"],
                ata_chapter=config["ata"],
                validity=validity,
                sample_rate=config["rate"],
                is_displayed=True,
                is_recorded=True,
            )
            db.add(snapshot)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"参数快照存储失败: {e}")
        finally:
            db.close()

    def get_param_list(self, db: Session, ata: Optional[str] = None) -> list:
        """获取参数列表"""
        params = []
        for name, config in self._monitored_params.items():
            if ata and config["ata"] != ata:
                continue
            params.append({
                "name": name,
                "type": config["type"],
                "unit": config["unit"],
                "ata": config["ata"],
                "min": config.get("min"),
                "max": config.get("max"),
                "rate": config["rate"],
            })
        return params

    def get_param_history(self, db: Session, name: str, limit: int = 100) -> list:
        """获取参数历史数据"""
        items = db.query(ParamSnapshot).filter(
            ParamSnapshot.param_name == name
        ).order_by(ParamSnapshot.timestamp.desc()).limit(limit).all()
        return [{
            "value": s.param_value,
            "unit": s.param_unit,
            "validity": s.validity,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
        } for s in items]

    def create_quick_list(self, db: Session, name: str, params: list[str]) -> int:
        """创建快捷访问列表"""
        ql = QuickAccessList(
            list_name=name,
            param_names=json.dumps(params),
        )
        db.add(ql)
        db.commit()
        db.refresh(ql)
        self._quick_lists[ql.id] = params
        logger.info(f"快捷列表已创建: ID={ql.id}, 名称={name}, 参数数={len(params)}")
        return ql.id

    def get_quick_lists(self, db: Session) -> list:
        """获取所有快捷访问列表"""
        items = db.query(QuickAccessList).order_by(QuickAccessList.id).all()
        return [{
            "id": q.id,
            "name": q.list_name,
            "params": json.loads(q.param_names) if q.param_names else [],
            "created_at": q.created_at.isoformat() if q.created_at else None,
        } for q in items]

    def get_quick_list_params(self, db: Session, list_id: int) -> list:
        """获取指定快捷列表的参数"""
        ql = db.query(QuickAccessList).filter(QuickAccessList.id == list_id).first()
        if ql:
            return json.loads(ql.param_names) if ql.param_names else []
        return []


# 全局单例
param_service = ParamMonitorService()
