"""
构型管理服务
- 周期性接收成员系统构型报告
- 与基本构型报告比对, 不一致生成错误报告
- 支持400个成员系统构型报告批量验证
"""
import asyncio
import random
import json
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, ConfigReport
from app.core.websocket_manager import ws_manager
from app.config import SIMULATION_CONFIG


class ConfigManagementService:
    """构型管理服务"""

    def __init__(self):
        self._running = False
        self._base_config: dict[str, dict] = {}  # 基本构型: member -> {item: value}

    async def start(self):
        """启动构型管理服务"""
        self._running = True
        self._init_base_config()
        logger.info("构型管理服务已启动")
        asyncio.create_task(self._config_receive_loop())

    async def stop(self):
        self._running = False
        logger.info("构型管理服务已停止")

    def _init_base_config(self):
        """初始化基本构型报告 (400个成员系统)"""
        config_types = ["hardware", "software", "database"]
        config_items = ["PN", "SN", "HW_REV", "SW_REV", "DB_REV", "MOD_STATUS"]
        for i in range(1, 401):
            member = f"MEM{i:03d}"
            self._base_config[member] = {}
            for item in config_items:
                self._base_config[member][item] = f"{item}_{i:04d}"
            self._base_config[member]["type"] = random.choice(config_types)
        logger.info(f"基本构型报告已初始化: {len(self._base_config)}个成员系统")

    async def _config_receive_loop(self):
        """周期性接收构型报告 (每10秒处理一批)"""
        while self._running:
            try:
                # Mock: 随机选择10个成员系统发送构型报告
                members = random.sample(
                    list(self._base_config.keys()),
                    min(10, len(self._base_config))
                )
                for member in members:
                    await self.process_config_report(member)
            except Exception as e:
                logger.error(f"构型接收循环异常: {e}")
            await asyncio.sleep(10.0)

    async def process_config_report(self, member_system: str) -> dict:
        """处理构型报告 - 比对并存储"""
        base = self._base_config.get(member_system, {})
        if not base:
            return {"status": "error", "message": f"成员系统 {member_system} 基本构型不存在"}

        results = []
        has_mismatch = False
        db = SessionLocal()
        try:
            for item, expected_value in base.items():
                if item == "type":
                    continue
                # Mock: 1%概率不一致
                actual_value = expected_value
                is_match = True
                if random.random() < 0.01:
                    actual_value = f"{item}_MISMATCH"
                    is_match = False
                    has_mismatch = True

                cr = ConfigReport(
                    member_system=member_system,
                    config_item=item,
                    config_value=actual_value,
                    expected_value=expected_value,
                    is_match=is_match,
                    config_type=base.get("type", "hardware"),
                )
                db.add(cr)
                results.append({
                    "item": item,
                    "value": actual_value,
                    "expected": expected_value,
                    "match": is_match,
                })

            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"构型报告存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        if has_mismatch:
            await ws_manager.broadcast("config_mismatch", {
                "member_system": member_system,
                "details": results,
                "timestamp": datetime.utcnow().isoformat(),
            })
            logger.warning(f"构型不一致: {member_system}")

        return {"status": "ok", "member": member_system, "has_mismatch": has_mismatch}

    def get_config_report(self, db: Session, member: Optional[str] = None,
                          page: int = 1, size: int = 20) -> dict:
        """获取构型报告列表"""
        query = db.query(ConfigReport)
        if member:
            query = query.filter(ConfigReport.member_system == member)
        query = query.order_by(ConfigReport.updated_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": r.id,
                "member_system": r.member_system,
                "config_item": r.config_item,
                "config_value": r.config_value,
                "expected_value": r.expected_value,
                "is_match": r.is_match,
                "config_type": r.config_type,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            } for r in items],
        }

    def batch_verify(self, db: Session, count: int = 400) -> dict:
        """批量构型验证"""
        results = {"total": count, "pass": 0, "mismatch": 0, "details": []}
        for i in range(1, count + 1):
            member = f"MEM{i:03d}"
            base = self._base_config.get(member, {})
            for item, expected in base.items():
                if item == "type":
                    continue
                cr = db.query(ConfigReport).filter(
                    ConfigReport.member_system == member,
                    ConfigReport.config_item == item,
                ).first()
                if cr and not cr.is_match:
                    results["mismatch"] += 1
                    results["details"].append({
                        "member": member,
                        "item": item,
                        "value": cr.config_value,
                        "expected": cr.expected_value,
                    })
                else:
                    results["pass"] += 1
        return results


# 全局单例
config_service = ConfigManagementService()
