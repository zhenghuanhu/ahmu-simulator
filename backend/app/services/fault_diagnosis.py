"""
故障诊断服务 (技术方案 4.3.6 故障诊断功能)
- 接收成员系统以不同频率(0.25/0.5/1/2Hz)周期性上报的故障报告
- 数据正确性校验 + 故障方程级联过滤 + FDE关联修正
- 失效报告生成 + 与/或逻辑运算定位根源故障
- 历史航段失效报告(-128~127) + 故障整合到单个LRU
- 成员系统启用/禁用失效报告 + ICD故障注入(控制持续时间)
"""
import asyncio
import random
import json
import time
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import (
    SessionLocal, FaultReport, FailureReport,
    FaultModelConfig, FailureFaultRelation, FDE, LRUFailureReport,
)
from app.core.websocket_manager import ws_manager
from app.services.lifecycle import MEMBER_SYSTEMS, MEMBER_SYSTEM_MAP

# 成员系统总数 (真实48 + 虚拟补足, 满足"不少于400个成员系统")
FAULT_MEMBER_COUNT = 400
# 每成员系统故障模型条目数
FAULTS_PER_MEMBER = 6
# FDE 消息总数 (不少于100条)
FDE_COUNT = 100

# 故障上报频率 (Hz)
REPORT_FREQUENCIES = [0.25, 0.5, 1.0, 2.0]

# FDE 驾驶舱效应描述模板
FDE_TEXTS = [
    "ENG 1 FAIL", "ENG 2 FAIL", "HYD SYS 1 LO PR", "HYD SYS 2 LO PR",
    "AIL SERVO FAULT", "RUD TRIM FAULT", "ELEC GEN 1 FAULT", "ELEC GEN 2 FAULT",
    "BLEED LEAK", "CABIN ALT HI", "FUEL PUMP 1 FAULT", "FUEL PUMP 2 FAULT",
    "NAV IR1 FAULT", "NAV IR2 FAULT", "TCAS FAULT", "WX RADAR FAULT",
    "APU FAULT", "LG GEAR DISAGREE", "FLT CTRL COMPUTER FAULT", "PACK 1 FAULT",
    "AUTO BRAKE FAULT", "ANTI-ICE FAULT", "PITOT HEAT FAULT", "PRESS SYS FAULT",
    "VHF 1 FAULT", "HF 1 FAULT", "ACARS FAULT", "SATCOM FAULT",
]


class FaultDiagnosisService:
    """故障诊断引擎"""

    def __init__(self):
        self._running = False
        self._member_catalog: list[dict] = []      # [{code, name, ata}]
        self._member_freq: dict[str, float] = {}    # member -> 上报频率(Hz)
        self._failure_enabled: dict[str, bool] = {} # member -> 是否启用失效报告
        self._fault_model: dict[str, dict] = {}     # "member:fault_code" -> model dict
        self._failure_relations: dict[str, list] = {}  # failure_code -> [(fault_code, logic)]
        self._fde_pool: dict[str, dict] = {}        # fde_code -> fde dict
        self._next_report: dict[str, float] = {}    # member -> next report monotonic time
        self._active_faults: set = set()            # "member:fault_code" 活跃故障集合
        self._injections: dict[str, asyncio.Task] = {}  # injection_id -> task
        self._current_segment = 0
        self._total_faults = 0
        self._inj_seq = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._build_member_catalog()
        self._build_fde_pool()
        self._build_fault_model()
        logger.info(f"故障诊断服务已启动 (成员系统{len(self._member_catalog)}, "
                    f"故障模型{len(self._fault_model)}, FDE{len(self._fde_pool)})")
        asyncio.create_task(self._receive_loop())

    async def stop(self):
        self._running = False
        for t in self._injections.values():
            t.cancel()
        logger.info("故障诊断服务已停止")

    def _build_member_catalog(self):
        catalog = [{"code": code, "name": name, "ata": ata}
                   for code, name, ata in MEMBER_SYSTEMS]
        for i in range(1, FAULT_MEMBER_COUNT - len(catalog) + 1):
            code = f"MEM{i:03d}"
            catalog.append({"code": code, "name": f"虚拟成员系统-{i:03d}",
                            "ata": f"{(i % 60) + 1:02d}"})
        self._member_catalog = catalog
        for m in catalog:
            self._member_freq[m["code"]] = random.choice(REPORT_FREQUENCIES)
            self._failure_enabled[m["code"]] = True

    def _build_fde_pool(self):
        """初始化 FDE 驾驶舱效应消息池 (100条)"""
        db = SessionLocal()
        try:
            existing = db.query(FDE.fde_code).all()
            existing_codes = {r[0] for r in existing}
            for i in range(1, FDE_COUNT + 1):
                code = f"FDE-{i:03d}"
                if code in existing_codes:
                    continue
                db.add(FDE(
                    fde_code=code,
                    fde_text=f"{FDE_TEXTS[(i - 1) % len(FDE_TEXTS)]} [{i}]",
                    severity=random.choice(["minor", "minor", "major", "critical"]),
                    ata_chapter=f"{(i % 60) + 1:02d}",
                    lru_code=None,
                ))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"FDE 池初始化失败(可能已存在): {e}")
        finally:
            db.close()
        # 从 DB 加载 FDE 到内存
        db2 = SessionLocal()
        try:
            for r in db2.query(FDE).all():
                self._fde_pool[r.fde_code] = {
                    "fde_code": r.fde_code, "fde_text": r.fde_text,
                    "severity": r.severity, "ata_chapter": r.ata_chapter,
                }
        finally:
            db2.close()

    def _build_fault_model(self):
        """构建故障模型 (故障方程): 故障报告定义 + 级联 + FDE关联 + LRU + 逻辑关系"""
        db = SessionLocal()
        try:
            existing = db.query(FaultModelConfig.fault_code).all()
            existing_codes = {r[0] for r in existing}
            if len(existing_codes) >= len(self._member_catalog) * FAULTS_PER_MEMBER:
                # 已初始化, 直接从 DB 加载
                self._load_model_from_db(db)
                return

            fde_codes = list(self._fde_pool.keys())
            models = []
            relations = []
            for m in self._member_catalog:
                member = m["code"]
                name = m["name"]
                ata = m["ata"]
                for i in range(1, FAULTS_PER_MEMBER + 1):
                    fault_code = f"{member}-{i:03d}"
                    if fault_code in existing_codes:
                        continue
                    severity = ["minor", "minor", "major", "critical"][i % 4]
                    lru_code = f"{member}-LRU{(i % 2) + 1}"
                    # FDE 关联: 前3个故障关联 FDE
                    fde_code = fde_codes[(self._hash(member) + i) % len(fde_codes)] if i <= 3 else None
                    # 级联: 第6个故障级联自第5个
                    cascade_parents = [f"{member}-005"] if i == 6 else []
                    # 失效报告关联: 每个故障触发1个失效报告
                    failure_codes = [f"{member}-FR-{i:03d}"]
                    models.append(FaultModelConfig(
                        member_system=member,
                        fault_code=fault_code,
                        fault_text=f"{name} 故障-{i:03d} ({ata}章)",
                        severity=severity,
                        ata_chapter=ata,
                        lru_code=lru_code,
                        fde_code=fde_code,
                        cascade_parents=cascade_parents,
                        failure_codes=failure_codes,
                    ))
                    # 逻辑关系: 该失效报告 <- 该故障 (or)
                    relations.append(FailureFaultRelation(
                        member_system=member,
                        failure_code=f"{member}-FR-{i:03d}",
                        fault_code=fault_code,
                        logic_type="or",
                    ))
                # 追加一个"与"逻辑: FR-007 关联 004 与 005 (and)
                relations.append(FailureFaultRelation(
                    member_system=member,
                    failure_code=f"{member}-FR-007",
                    fault_code=f"{member}-004",
                    logic_type="and",
                ))
                relations.append(FailureFaultRelation(
                    member_system=member,
                    failure_code=f"{member}-FR-007",
                    fault_code=f"{member}-005",
                    logic_type="and",
                ))
            if models:
                db.add_all(models)
            if relations:
                db.add_all(relations)
            db.commit()
            self._load_model_from_db(db)
        except Exception as e:
            db.rollback()
            logger.error(f"故障模型构建失败: {e}")
        finally:
            db.close()

    def _load_model_from_db(self, db: Session):
        self._fault_model.clear()
        self._failure_relations.clear()
        for r in db.query(FaultModelConfig).all():
            self._fault_model[f"{r.member_system}:{r.fault_code}"] = {
                "member_system": r.member_system,
                "fault_code": r.fault_code,
                "fault_text": r.fault_text,
                "severity": r.severity,
                "ata_chapter": r.ata_chapter,
                "lru_code": r.lru_code,
                "fde_code": r.fde_code,
                "cascade_parents": r.cascade_parents or [],
                "failure_codes": r.failure_codes or [],
            }
        for r in db.query(FailureFaultRelation).all():
            self._failure_relations.setdefault(r.failure_code, []).append(
                (r.fault_code, r.logic_type))

    @staticmethod
    def _hash(s: str) -> int:
        return sum(ord(c) for c in s)

    # ==================== 周期接收 (不同频率) ====================

    async def _receive_loop(self):
        """不同频率周期接收成员系统故障报告"""
        TICK = 0.25  # 4Hz 调度粒度, 覆盖 0.25/0.5/1/2Hz 上报
        while self._running:
            now = time.monotonic()
            for m in self._member_catalog:
                code = m["code"]
                next_t = self._next_report.get(code)
                if next_t is None:
                    self._next_report[code] = now + 1.0 / self._member_freq[code]
                    continue
                if now >= next_t:
                    self._next_report[code] = now + 1.0 / self._member_freq[code]
                    await self._report_fault(code)
            await asyncio.sleep(TICK)

    async def _report_fault(self, member: str):
        """成员系统自发上报故障 (低概率, 演示周期上报)"""
        if random.random() > 0.005:  # 0.5% 概率, 避免故障爆炸
            return
        faults = [m for m in self._fault_model.values()
                  if m["member_system"] == member]
        if not faults:
            return
        f = random.choice(faults)
        await self.process_fault_report(member, f["fault_code"], f["severity"])

    # ==================== 核心: 故障报告处理 ====================

    async def process_fault_report(self, member_system: str, fault_code: str,
                                   severity: str = "minor", segment: Optional[int] = None) -> dict:
        """处理接收到的故障报告: 数据校验 -> 级联过滤 -> FDE关联 -> 失效报告生成 -> 存储"""
        # 1. 数据正确性校验
        if not member_system or not fault_code:
            return {"status": "error", "message": "故障数据校验失败"}
        seg = segment if segment is not None else self._current_segment
        if not (-128 <= seg <= 127):
            seg = 0  # 航段越界修正

        model = self._fault_model.get(f"{member_system}:{fault_code}")
        fault_text = model["fault_text"] if model else f"成员系统{member_system}故障: {fault_code}"
        ata = model["ata_chapter"] if model else "45"
        lru_code = model["lru_code"] if model else None
        fde_code = model["fde_code"] if model else None
        parent_codes = model["cascade_parents"] if model else []

        # 2. 级联过滤: 若为级联故障且父故障活跃, 过滤(suppressed)
        suppressed = False
        if parent_codes:
            for pc in parent_codes:
                if f"{member_system}:{pc}" in self._active_faults:
                    suppressed = True
                    break

        # 3. 存储故障报告
        fault = FaultReport(
            member_system=member_system,
            fault_code=fault_code,
            fault_text=fault_text,
            severity=severity,
            status="suppressed" if suppressed else "active",
            ata_chapter=ata,
            flight_phase=0,
            flight_segment=seg,
            is_cascaded=bool(parent_codes),
            parent_fault_id=None,
            fde_code=fde_code,
            lru_code=lru_code,
            raw_data=json.dumps({"fault_code": fault_code, "severity": severity}),
        )
        db = SessionLocal()
        failure_codes = []
        fault_id = None
        try:
            db.add(fault)
            db.commit()
            db.refresh(fault)
            fault_id = fault.id
            self._total_faults += 1

            # 4. 生成失效报告 (成员启用失效报告 且 未被级联过滤)
            if not suppressed and self._failure_enabled.get(member_system, True) and model:
                for fc in model.get("failure_codes", []):
                    # 与/或逻辑运算定位根源故障
                    root, generate = self._resolve_root(member_system, fc, fault_code)
                    if not generate:
                        continue
                    fr = FailureReport(
                        member_system=member_system,
                        failure_code=fc,
                        failure_text=f"失效报告 {fc}",
                        severity=severity,
                        status="active",
                        fault_report_id=fault_id,
                        root_fault_code=root,
                        logic_type=self._logic_of(fc),
                        lru_code=lru_code,
                        flight_segment=seg,
                        raw_data=json.dumps({"failure_code": fc}),
                    )
                    db.add(fr)
                    failure_codes.append(fc)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"故障存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        if not suppressed:
            self._active_faults.add(f"{member_system}:{fault_code}")

        # 5. WebSocket 推送
        await ws_manager.broadcast("fault_new", {
            "id": fault_id,
            "member_system": member_system,
            "fault_code": fault_code,
            "fault_text": fault_text,
            "severity": severity,
            "status": "suppressed" if suppressed else "active",
            "ata_chapter": ata,
            "flight_segment": seg,
            "is_cascaded": bool(parent_codes),
            "fde_code": fde_code,
            "lru_code": lru_code,
            "failure_codes": failure_codes,
            "timestamp": datetime.utcnow().isoformat(),
        })

        logger.info(f"故障处理: {member_system}/{fault_code} suppressed={suppressed} "
                    f"failures={len(failure_codes)}")
        return {"status": "ok", "fault_id": fault_id, "suppressed": suppressed,
                "failure_codes": failure_codes}

    def _logic_of(self, failure_code: str) -> str:
        rels = self._failure_relations.get(failure_code, [])
        return rels[0][1] if rels else "or"

    def _resolve_root(self, member: str, failure_code: str, current_fault: str):
        """与/或逻辑运算: 定位失效报告的根源故障
        返回 (root_fault_code, generate)
        """
        rels = self._failure_relations.get(failure_code, [])
        if not rels:
            return current_fault, True
        logic = rels[0][1]
        fault_codes = [fc for fc, _ in rels]
        if logic == "and":
            # 需要所有关联故障都活跃才产生该失效报告
            if all(f"{member}:{fc}" in self._active_faults or fc == current_fault
                   for fc in fault_codes):
                return ",".join(fault_codes), True
            return None, False
        # or: 任一关联故障活跃即为根源
        return current_fault, True

    # ==================== ICD 故障注入 (控制持续时间) ====================

    async def inject_fault(self, member: str, fault_code: str, severity: Optional[str] = None,
                           duration: int = 0, segment: Optional[int] = None) -> dict:
        """成员系统模拟 ICD 故障注入: 设置故障有效, 周期发送, 控制持续时间"""
        model = self._fault_model.get(f"{member}:{fault_code}")
        if not model:
            return {"status": "error", "message": f"故障 {fault_code} 未在故障模型中定义"}
        sev = severity or model["severity"]
        result = await self.process_fault_report(member, fault_code, sev, segment)

        if duration > 0:
            self._inj_seq += 1
            inj_id = f"INJ-{self._inj_seq}"
            task = asyncio.create_task(
                self._injection_loop(inj_id, member, fault_code, sev, duration, segment))
            self._injections[inj_id] = task
            result["injection_id"] = inj_id
            result["duration"] = duration
        return result

    async def _injection_loop(self, inj_id: str, member: str, fault_code: str,
                              severity: str, duration: int, segment: Optional[int]):
        freq = self._member_freq.get(member, 1.0)
        interval = 1.0 / freq
        end = time.monotonic() + duration
        while time.monotonic() < end:
            await asyncio.sleep(interval)
            if inj_id not in self._injections:
                return
            await self.process_fault_report(member, fault_code, severity, segment)
        self._injections.pop(inj_id, None)

    def stop_injection(self, injection_id: str) -> dict:
        task = self._injections.pop(injection_id, None)
        if task:
            task.cancel()
            return {"status": "ok", "message": f"注入 {injection_id} 已停止"}
        return {"status": "error", "message": f"注入 {injection_id} 不存在"}

    def active_injections(self) -> list:
        return [{"injection_id": k, "member": getattr(v, "member", None),
                 "fault_code": getattr(v, "fault_code", None)}
                for k, v in self._injections.items()]

    # ==================== 成员系统启用/禁用失效报告 ====================

    def set_failure_enabled(self, member: str, enabled: bool) -> dict:
        if member not in self._member_freq:
            return {"status": "error", "message": f"成员系统 {member} 不存在"}
        self._failure_enabled[member] = enabled
        return {"status": "ok", "member_system": member, "failure_enabled": enabled}

    # ==================== 查询接口 ====================

    def get_member_systems(self) -> dict:
        """成员系统列表 (含上报频率/失效报告启停状态)"""
        return {
            "total": len(self._member_catalog),
            "memberList": [{
                "memberSystem": m["code"],
                "memberName": m["name"],
                "ata": m["ata"],
                "frequency": self._member_freq.get(m["code"], 1.0),
                "failureEnabled": self._failure_enabled.get(m["code"], True),
            } for m in self._member_catalog],
        }

    def get_fault_model(self, member: Optional[str] = None, page: int = 1, size: int = 20) -> dict:
        """读取故障模型配置 (故障报告定义 + 级联 + FDE关联 + 失效报告关联)"""
        db = SessionLocal()
        try:
            query = db.query(FaultModelConfig)
            if member:
                query = query.filter(FaultModelConfig.member_system == member)
            total = query.count()
            rows = query.order_by(FaultModelConfig.member_system, FaultModelConfig.fault_code) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "member_system": r.member_system,
                    "fault_code": r.fault_code,
                    "fault_text": r.fault_text,
                    "severity": r.severity,
                    "ata_chapter": r.ata_chapter,
                    "lru_code": r.lru_code,
                    "fde_code": r.fde_code,
                    "cascade_parents": r.cascade_parents,
                    "failure_codes": r.failure_codes,
                } for r in rows],
            }
        finally:
            db.close()

    def get_failure_relations(self, failure_code: Optional[str] = None,
                              page: int = 1, size: int = 20) -> dict:
        """读取失效报告与故障报告的逻辑关系"""
        db = SessionLocal()
        try:
            query = db.query(FailureFaultRelation)
            if failure_code:
                query = query.filter(FailureFaultRelation.failure_code == failure_code)
            total = query.count()
            rows = query.order_by(FailureFaultRelation.failure_code) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "failure_code": r.failure_code,
                    "fault_code": r.fault_code,
                    "logic_type": r.logic_type,
                } for r in rows],
            }
        finally:
            db.close()

    def get_fde_list(self, active_only: bool = False, page: int = 1, size: int = 20) -> dict:
        """获取 FDE 列表 (active_only=True 时仅返回活跃故障关联的 FDE)"""
        db = SessionLocal()
        try:
            if active_only:
                codes = db.query(FaultReport.fde_code).filter(
                    FaultReport.status == "active",
                    FaultReport.fde_code.isnot(None),
                ).distinct().all()
                active_codes = {c[0] for c in codes}
                items = [self._fde_pool[c] for c in active_codes if c in self._fde_pool]
                return {"total": len(items), "items": items}
            query = db.query(FDE)
            total = query.count()
            rows = query.order_by(FDE.fde_code).offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "fde_code": r.fde_code, "fde_text": r.fde_text,
                    "severity": r.severity, "ata_chapter": r.ata_chapter,
                } for r in rows],
            }
        finally:
            db.close()

    def get_fault_list(self, db: Session, page: int = 1, size: int = 20,
                       member: Optional[str] = None, status: Optional[str] = None) -> dict:
        """获取故障报告列表"""
        query = db.query(FaultReport)
        if member:
            query = query.filter(FaultReport.member_system == member)
        if status:
            query = query.filter(FaultReport.status == status)
        query = query.order_by(FaultReport.created_at.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {"total": total, "page": page, "size": size,
                "items": [self._fault_to_dict(f) for f in items]}

    def get_failure_list(self, member: Optional[str] = None, segment: Optional[int] = None,
                         page: int = 1, size: int = 20) -> dict:
        """获取失效报告列表"""
        db = SessionLocal()
        try:
            query = db.query(FailureReport)
            if member:
                query = query.filter(FailureReport.member_system == member)
            if segment is not None:
                query = query.filter(FailureReport.flight_segment == segment)
            total = query.count()
            rows = query.order_by(FailureReport.created_at.desc()) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "id": r.id,
                    "member_system": r.member_system,
                    "failure_code": r.failure_code,
                    "failure_text": r.failure_text,
                    "severity": r.severity,
                    "status": r.status,
                    "root_fault_code": r.root_fault_code,
                    "logic_type": r.logic_type,
                    "lru_code": r.lru_code,
                    "flight_segment": r.flight_segment,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in rows],
            }
        finally:
            db.close()

    def get_failure_history(self, segment: int) -> dict:
        """历史航段失效报告 (-128~127)"""
        if not (-128 <= segment <= 127):
            return {"segment": segment, "total": 0, "items": [],
                    "message": "航段号超出范围(-128~127)"}
        db = SessionLocal()
        try:
            rows = db.query(FailureReport).filter(
                FailureReport.flight_segment == segment
            ).order_by(FailureReport.created_at.desc()).all()
            return {
                "segment": segment, "total": len(rows),
                "items": [{
                    "member_system": r.member_system,
                    "failure_code": r.failure_code,
                    "failure_text": r.failure_text,
                    "severity": r.severity,
                    "root_fault_code": r.root_fault_code,
                    "logic_type": r.logic_type,
                    "lru_code": r.lru_code,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in rows],
            }
        finally:
            db.close()

    def get_fault_history(self, db: Session, segment: int) -> list:
        """历史故障查询 (按航段)"""
        items = db.query(FaultReport).filter(
            FaultReport.flight_segment == segment
        ).order_by(FaultReport.created_at.desc()).all()
        return [self._fault_to_dict(f) for f in items]

    def get_lru_reports(self, segment: Optional[int] = None) -> dict:
        """故障整合到单个LRU: 按LRU聚合失效报告"""
        db = SessionLocal()
        try:
            query = db.query(FailureReport)
            if segment is not None:
                query = query.filter(FailureReport.flight_segment == segment)
            rows = query.all()
            lru_map = {}
            for r in rows:
                key = r.lru_code or f"{r.member_system}-UNKNOWN"
                if key not in lru_map:
                    lru_map[key] = {
                        "lru_code": key,
                        "member_system": r.member_system,
                        "failure_codes": set(),
                        "root_fault_codes": set(),
                    }
                lru_map[key]["failure_codes"].add(r.failure_code)
                if r.root_fault_code:
                    lru_map[key]["root_fault_codes"].update(
                        r.root_fault_code.split(","))
            items = []
            for k, v in lru_map.items():
                items.append({
                    "lru_code": k,
                    "member_system": v["member_system"],
                    "failure_count": len(v["failure_codes"]),
                    "fault_count": len(v["root_fault_codes"]),
                    "failure_codes": sorted(v["failure_codes"]),
                    "root_fault_codes": sorted(v["root_fault_codes"]),
                })
            items.sort(key=lambda x: -x["failure_count"])
            return {"total": len(items), "items": items}
        finally:
            db.close()

    def locate_root_faults(self, failure_code: str, segment: Optional[int] = None) -> dict:
        """与/或逻辑运算定位失效报告的根源故障"""
        rels = self._failure_relations.get(failure_code, [])
        if not rels:
            return {"failure_code": failure_code, "logic": None, "root_faults": []}
        logic = rels[0][1]
        fault_codes = [fc for fc, _ in rels]
        active = self._get_active_codes(segment)
        if logic == "and":
            roots = fault_codes if all(fc in active for fc in fault_codes) else []
        else:
            roots = [fc for fc in fault_codes if fc in active]
        return {"failure_code": failure_code, "logic": logic,
                "root_faults": roots}

    def _get_active_codes(self, segment: Optional[int] = None) -> set:
        db = SessionLocal()
        try:
            q = db.query(FaultReport.fault_code).filter(FaultReport.status == "active")
            if segment is not None:
                q = q.filter(FaultReport.flight_segment == segment)
            return {r[0] for r in q.all()}
        finally:
            db.close()

    def resolve_fault(self, db: Session, fault_id: str) -> bool:
        fault = db.query(FaultReport).filter(FaultReport.id == fault_id).first()
        if fault:
            fault.status = "resolved"
            fault.resolved_at = datetime.utcnow()
            db.commit()
            self._active_faults.discard(f"{fault.member_system}:{fault.fault_code}")
            return True
        return False

    def set_flight_context(self, segment: int, phase: int):
        self._current_segment = segment if -128 <= segment <= 127 else 0

    def _fault_to_dict(self, fault: FaultReport) -> dict:
        return {
            "id": fault.id,
            "member_system": fault.member_system,
            "fault_code": fault.fault_code,
            "fault_text": fault.fault_text,
            "severity": fault.severity,
            "status": fault.status,
            "ata_chapter": fault.ata_chapter,
            "flight_phase": fault.flight_phase,
            "flight_segment": fault.flight_segment,
            "is_cascaded": fault.is_cascaded,
            "fde_code": fault.fde_code,
            "lru_code": fault.lru_code,
            "created_at": fault.created_at.isoformat() if fault.created_at else None,
            "resolved_at": fault.resolved_at.isoformat() if fault.resolved_at else None,
        }

    @property
    def total_faults(self) -> int:
        return self._total_faults


# 全局单例
fault_service = FaultDiagnosisService()
