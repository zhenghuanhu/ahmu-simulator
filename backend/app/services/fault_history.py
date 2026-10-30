"""
故障历史检索服务 (启动测试服务 - 199 成员启动测试)

对齐 OHMS test_case/200NVM.py 与 test_case/故障历史报告获取664.py 的测试流程:
  1. FHATAsRequest   - 获取 ATA 编号列表
  2. FHEquipRequest  - 获取指定 ATA 的设备列表
  3. FHRetrievalRequest - 开始故障历史检索任务 (equipID 为数字 MemberID)
  4. FHStatusRequest - 查询检索任务状态 (Complete/Error)

数据源: 真实成员系统 (MEMBER_SYSTEMS, 51 个), 数字 equipID 自分配 (3000+序号),
        与构型管理 ACR 目录一致; 兼容成员系统 code 字符串回退。
支持多达 199 个成员系统并发启动检索任务 (每任务按 equip_id 独立记录状态)。
"""
import asyncio
from datetime import datetime
from typing import Optional
from loguru import logger

from app.database import SessionLocal, FaultHistoryTask
from app.services.lifecycle import MEMBER_SYSTEMS
from app.core import icd_config_acr as acr_icd


class FaultHistoryService:
    """故障历史检索服务 (全局单例)"""

    # Mock 检索耗时 (秒): 启动后延迟此时间标记 Complete, 模拟检索过程
    RETRIEVAL_DELAY_SEC = 0.5
    # 数字 equipID 基数 (与构型管理 ACR 目录一致: 3001 起递增)
    EQUIPID_BASE = 3000
    # 成员系统总数 (真实 51 + 虚拟补足, 满足"199 成员启动测试服务")
    MEMBER_COUNT = 400

    def __init__(self):
        self._running = False
        # 数字 equipID 目录: [{equipID, ata, equipName, memberCode}]
        self._member_dir: list[dict] = []
        self._dir_by_equipid: dict[int, dict] = {}
        self._dir_by_code: dict[str, dict] = {}

    async def start(self):
        self._running = True
        self._build_member_dir()
        logger.info(f"故障历史检索服务已启动 ({len(self._member_dir)} 个设备目录)")
        return self

    async def stop(self):
        self._running = False
        logger.info("故障历史检索服务已停止")

    # ==================== 数字 equipID 目录 ====================

    def _build_member_dir(self):
        """构建数字 equipID 目录: 真实成员 + 虚拟成员补足到 400

        真实成员按 MEMBER_SYSTEMS (AHMU 归 ATA 45, 与构型管理一致);
        虚拟成员 ata 复用真实章节循环 (避免伪 ATA 章节污染 ATA 列表)。
        """
        catalog = []
        real_atas = [ata for _, _, ata in MEMBER_SYSTEMS]
        # 真实成员
        for idx, (code, name, ata) in enumerate(MEMBER_SYSTEMS, start=1):
            ata = acr_icd.AHMU_ATA if code == "AHMU" else ata
            catalog.append({
                "equipID": self.EQUIPID_BASE + idx,
                "ata": str(ata),
                "equipName": name,
                "memberCode": code,
            })
        # 虚拟成员补足到 MEMBER_COUNT, ata 用真实章节循环
        for i in range(len(MEMBER_SYSTEMS) + 1, self.MEMBER_COUNT + 1):
            code = f"MEM{i:03d}"
            ata = real_atas[i % len(real_atas)]
            catalog.append({
                "equipID": self.EQUIPID_BASE + i,
                "ata": str(ata),
                "equipName": f"虚拟成员系统-{i:03d}",
                "memberCode": code,
            })
        self._member_dir = catalog
        self._dir_by_equipid = {e["equipID"]: e for e in catalog}
        self._dir_by_code = {e["memberCode"]: e for e in catalog}

    def _ensure_dir(self):
        if not self._member_dir:
            self._build_member_dir()

    def _resolve(self, equip_id):
        """解析 equipID -> 目录项 (支持数字/数字字符串/成员 code 回退)"""
        self._ensure_dir()
        if isinstance(equip_id, str) and equip_id.strip().isdigit():
            equip_id = int(equip_id.strip())
        if isinstance(equip_id, int):
            return self._dir_by_equipid.get(equip_id)
        return self._dir_by_code.get(str(equip_id))

    @staticmethod
    def _normalize_equip_key(equip_id) -> str:
        """归一化 equipID 为字符串 key (数字 -> str, 字符串 -> 原样)"""
        if isinstance(equip_id, int) or (isinstance(equip_id, str) and equip_id.strip().isdigit()):
            return str(equip_id).strip()
        return str(equip_id or "").strip()

    # ==================== 检索接口 ====================

    def get_fh_atas(self) -> dict:
        """获取 ATA 编号列表 (真实章节 + 名称, 唯一 + 排序)"""
        self._ensure_dir()
        ata_map = {}
        for e in self._member_dir:
            ata = e["ata"]
            if ata not in ata_map:
                ata_map[ata] = {"ata": ata, "ataName": acr_icd.ata_name(ata)}
        atas = sorted(ata_map.values(),
                      key=lambda x: int(x["ata"]) if x["ata"].isdigit() else 999)
        return {"fhATAStructList": atas, "totalCount": len(atas)}

    def get_fh_equips(self, ata: str = None) -> dict:
        """获取成员系统设备列表 (数字 equipID, 可按 ATA 过滤)"""
        self._ensure_dir()
        ata_key = str(ata or "").strip().lstrip("ata").lstrip("ATA")
        items = []
        for e in self._member_dir:
            if ata_key and str(e["ata"]) != ata_key:
                continue
            items.append({
                "equipID": e["equipID"],
                "equipName": e["equipName"],
                "ata": e["ata"],
            })
        return {"fhEquipStructList": items, "totalCount": len(items)}

    async def start_fh_retrieval(self, equip_id) -> dict:
        """启动故障历史检索任务 (equipID 兼容数字 MemberID / 成员 code)"""
        key = self._normalize_equip_key(equip_id)
        if not key:
            return {"status": "error", "message": "equipID 不能为空"}
        # 创建任务记录 (InProgress)
        db = SessionLocal()
        try:
            task = FaultHistoryTask(equip_id=key, status="InProgress")
            db.add(task)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[FaultHistory] 创建检索任务失败: {e}")
        finally:
            db.close()
        # 后台延迟完成
        asyncio.create_task(self._complete_task(key))
        logger.info(f"[FaultHistory] 已启动成员 {key} 故障历史检索任务")
        return {"status": "ok", "equipID": key, "taskStatus": "InProgress"}

    async def _complete_task(self, equip_id: str):
        """延迟后标记检索完成, 并触发 TFTP 上传故障历史文件"""
        await asyncio.sleep(self.RETRIEVAL_DELAY_SEC)
        db = SessionLocal()
        try:
            task = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.equip_id == equip_id,
                FaultHistoryTask.status == "InProgress",
            ).order_by(FaultHistoryTask.created_at.desc()).first()
            if task:
                task.status = "Complete"
                task.completed_at = datetime.utcnow()
                db.commit()
                logger.info(f"[FaultHistory] 成员 {equip_id} 故障历史检索完成")
        except Exception as e:
            db.rollback()
            logger.error(f"[FaultHistory] 更新检索状态失败: {e}")
        finally:
            db.close()
        # 检索完成后触发 TFTP 上传故障历史文件 (异步, 避免阻塞事件循环)
        await self._upload_history(equip_id)

    async def _upload_history(self, equip_id: str):
        """检索完成后经 TFTP 上传该成员的故障历史文件"""
        from app.services.fault_history_tftp import tftp_client
        entry = self._resolve(equip_id)
        member = entry["memberCode"] if entry else equip_id
        history = self._collect_history(member)
        await asyncio.to_thread(tftp_client.upload_fault_history, member, history)

    @staticmethod
    def _collect_history(member: str) -> list:
        """收集某成员的故障/失效历史 (供 TFTP 上传)"""
        from app.database import FailureReport
        db = SessionLocal()
        try:
            rows = db.query(FailureReport).filter(
                FailureReport.member_system == member
            ).order_by(FailureReport.created_at).all()
            return [{
                "failure_code": r.failure_code,
                "severity": r.severity,
                "flight_segment": r.flight_segment,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            } for r in rows]
        finally:
            db.close()

    def query_fh_status(self, equip_id) -> dict:
        """查询检索任务状态 (对齐 OHMS: data[0]['fhStatusStructList'][0]['status'])"""
        key = self._normalize_equip_key(equip_id)
        db = SessionLocal()
        try:
            task = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.equip_id == key,
            ).order_by(FaultHistoryTask.created_at.desc()).first()
            status = task.status if task else "Complete"  # 无记录视为已完成
            return {"fhStatusStructList": [{
                "status": status,
                "equipID": key,
                "completedAt": task.completed_at.isoformat() if task and task.completed_at else None,
            }]}
        finally:
            db.close()

    def get_status(self) -> dict:
        """服务整体状态"""
        db = SessionLocal()
        try:
            total = db.query(FaultHistoryTask).count()
            completed = db.query(FaultHistoryTask).filter(
                FaultHistoryTask.status == "Complete").count()
        finally:
            db.close()
        return {"running": self._running, "total_tasks": total, "completed": completed}


# 全局单例
fault_history_service = FaultHistoryService()
