"""
参数监控服务 (Parameter Monitoring)
对应技术方案 4.3.12 参数监控功能:
  - 地面人机界面获取参数报告 (参数报告标识/参数个数/参数名称/参数类型/参数数值/参数单位/参数记录时间/参数所属ATA), 支持下传
  - 创建/存储参数快捷访问列表, 支持对列表中参数增/删/改
  - 成员系统启动或禁用飞机参数监控服务
  - 参数基本配置项 (名称/类型/采样频率/采样精度/单位/是否记录/是否显示) + 记录配置项 (记录精度/记录频率/开始/结束记录逻辑)
  - 参数有效性校验 (valid/unavailable/out_of_range/invalid), 按 ATA 章节查询
  - 接收 PAA 转发的参数用于飞机运行数据存储
"""
import asyncio
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, ParamConfig, ParamSnapshot, ParamReport, QuickAccessList
from app.core.websocket_manager import ws_manager
from app.services.lifecycle import MEMBER_SYSTEMS
from app.config import DATA_DIR

# 参数报告下传默认目录
PARAM_REPORT_DIR = DATA_DIR / "param_reports"

# 成员系统标识 → ATA 映射 (用于给参数分配来源成员系统)
_MEMBER_ATA_MAP: dict[str, str] = {}
for _code, _name, _ata in MEMBER_SYSTEMS:
    _MEMBER_ATA_MAP.setdefault(_ata, _code)


class ParamMonitorService:
    """参数监控服务"""

    def __init__(self):
        self._running = False
        self._params: dict[str, dict] = {}                 # param_name -> 完整配置
        self._monitor_services: dict[str, bool] = {}       # 成员系统 -> 监控服务是否启用
        self._latest_values: dict[str, dict] = {}          # param_name -> 最近一次值(供报告使用)

    async def start(self):
        """启动参数监控服务"""
        self._running = True
        self._init_default_params()
        logger.info("参数监控服务已启动")
        asyncio.create_task(self._param_push_loop())

    async def stop(self):
        self._running = False
        logger.info("参数监控服务已停止")

    # ==================== 参数配置初始化 ====================

    def _init_default_params(self):
        """初始化默认监控参数 (含完整配置项), 覆盖多个 ATA 章节"""
        db = SessionLocal()
        try:
            existing = db.query(ParamConfig).count()
            if existing > 0:
                self._load_params(db)
                logger.info(f"参数配置已存在: {existing}个, 跳过初始化")
                return

            # (名称, 类型, 单位, ATA, min, max, 采样频率, 采样精度, 是否记录, 是否显示, 记录精度, 记录频率, 开始记录逻辑, 结束记录逻辑)
            default_params = [
                ("cabin_temp", "float", "C", "21-01", -50, 150, 1, 2, True, True, 2, 1, "电源接通且空调组件工作", "断电或空调组件关闭"),
                ("pack_discharge_temp", "float", "C", "21-02", -50, 150, 1, 2, True, True, 2, 1, "空调组件工作", "组件关闭"),
                ("zone_temp", "float", "C", "21-03", -50, 150, 2, 2, True, True, 2, 1, "空调组件工作", "组件关闭"),
                ("autopilot_mode", "string", "-", "22-01", None, None, 1, 0, True, True, 0, 1, "AP接通", "AP断开"),
                ("fd_pitch_cmd", "float", "DEG", "22-02", -30, 30, 2, 2, True, True, 2, 2, "FD接通", "FD断开"),
                ("hf_tx_power", "float", "W", "23-01", 0, 400, 1, 1, True, True, 1, 1, "HF发射", "HF待机"),
                ("vhf_frequency", "float", "MHz", "23-02", 118, 137, 1, 3, True, True, 3, 1, "VHF上电", "VHF断电"),
                ("gen_voltage_1", "float", "V", "24-01", 0, 130, 1, 2, True, True, 2, 1, "发电机1工作", "发电机1关闭"),
                ("gen_voltage_2", "float", "V", "24-02", 0, 130, 1, 2, True, True, 2, 1, "发电机2工作", "发电机2关闭"),
                ("bus_voltage", "float", "V", "24-03", 0, 130, 1, 2, True, True, 2, 1, "电源总线带电", "总线断电"),
                ("aileron_position", "float", "DEG", "27-01", -30, 30, 4, 2, True, True, 2, 4, "液压可用", "液压失效"),
                ("elevator_position", "float", "DEG", "27-02", -30, 30, 4, 2, True, True, 2, 4, "液压可用", "液压失效"),
                ("rudder_position", "float", "DEG", "27-03", -30, 30, 4, 2, True, True, 2, 4, "液压可用", "液压失效"),
                ("fuel_quantity", "float", "KG", "28-01", 0, 50000, 1, 1, True, True, 1, 1, "加油完成", "燃油耗尽"),
                ("fuel_flow", "float", "KG/H", "28-02", 0, 20000, 1, 1, True, True, 1, 1, "发动机运转", "发动机关车"),
                ("hyd_pressure_1", "float", "PSI", "29-01", 0, 5000, 2, 1, True, True, 1, 2, "液压泵1工作", "液压泵1关闭"),
                ("hyd_pressure_2", "float", "PSI", "29-02", 0, 5000, 2, 1, True, True, 1, 2, "液压泵2工作", "液压泵2关闭"),
                ("wing_anti_ice_temp", "float", "C", "30-01", -50, 200, 1, 2, True, True, 2, 1, "防冰接通", "防冰断开"),
                ("clock_time", "string", "UTC", "31-01", None, None, 1, 0, True, True, 0, 1, "航电上电", "航电断电"),
                ("gear_position", "string", "-", "32-01", None, None, 2, 0, True, True, 0, 2, "起落架系统上电", "断电"),
                ("brake_temp", "float", "C", "32-02", 0, 1000, 1, 1, True, True, 1, 1, "刹车系统上电", "断电"),
                ("altitude", "float", "FT", "34-01", 0, 51000, 2, 1, True, True, 1, 2, "IRS对准完成", "IRS失效"),
                ("airspeed", "float", "KTS", "34-02", 0, 600, 2, 1, True, True, 1, 2, "空速管可用", "空速管失效"),
                ("heading", "float", "DEG", "34-03", 0, 360, 2, 1, True, True, 1, 2, "IRS对准完成", "IRS失效"),
                ("vertical_speed", "float", "FT/MIN", "34-04", -6000, 6000, 2, 0, True, True, 0, 2, "IRS对准完成", "IRS失效"),
                ("mach", "float", "-", "34-05", 0, 1.0, 2, 3, True, True, 3, 2, "空速管可用", "空速管失效"),
                ("bleed_pressure", "float", "PSI", "36-01", 0, 100, 1, 1, True, True, 1, 1, "引气活门打开", "引气活门关闭"),
                ("apu_egt", "float", "C", "49-01", 0, 1200, 1, 1, True, True, 1, 1, "APU运转", "APU关车"),
                ("apu_rpm", "float", "RPM", "49-02", 0, 50000, 1, 0, True, True, 0, 1, "APU运转", "APU关车"),
                ("n1_1", "float", "RPM", "71-01", 0, 10000, 2, 1, True, True, 1, 2, "发动机1运转", "发动机1关车"),
                ("n1_2", "float", "RPM", "71-02", 0, 10000, 2, 1, True, True, 1, 2, "发动机2运转", "发动机2关车"),
                ("egt_1", "float", "C", "71-03", 0, 1000, 2, 1, True, True, 1, 2, "发动机1运转", "发动机1关车"),
                ("egt_2", "float", "C", "71-04", 0, 1000, 2, 1, True, True, 1, 2, "发动机2运转", "发动机2关车"),
                ("oil_temp", "float", "C", "79-01", 0, 200, 1, 1, True, True, 1, 1, "发动机运转", "发动机停车"),
                ("oil_pressure", "float", "PSI", "79-02", 0, 200, 1, 1, True, True, 1, 1, "发动机运转", "发动机停车"),
            ]

            for (name, ptype, unit, ata, mn, mx, rate, precision,
                 recorded, displayed, rec_prec, rec_rate, start_l, stop_l) in default_params:
                cfg = ParamConfig(
                    param_name=name,
                    param_type=ptype,
                    sample_rate=rate,
                    sample_precision=precision,
                    param_unit=unit,
                    is_recorded=recorded,
                    is_displayed=displayed,
                    record_precision=rec_prec,
                    record_rate=rec_rate,
                    record_start_logic=start_l,
                    record_stop_logic=stop_l,
                    ata_chapter=ata,
                    source_member=_MEMBER_ATA_MAP.get(ata.split("-")[0]),
                    min_val=mn,
                    max_val=mx,
                )
                db.add(cfg)

            db.commit()
            logger.info(f"参数配置已初始化: {len(default_params)}个参数")
            self._load_params(db)
        except Exception as e:
            db.rollback()
            logger.error(f"参数配置初始化失败: {e}")
        finally:
            db.close()

    def _load_params(self, db: Session):
        """从数据库加载参数配置到内存"""
        for cfg in db.query(ParamConfig).all():
            self._params[cfg.param_name] = self._cfg_to_dict(cfg)

    @staticmethod
    def _cfg_to_dict(cfg: ParamConfig) -> dict:
        return {
            "name": cfg.param_name,
            "type": cfg.param_type,
            "unit": cfg.param_unit,
            "ata": cfg.ata_chapter,
            "source_member": cfg.source_member,
            "min": cfg.min_val,
            "max": cfg.max_val,
            "sample_rate": cfg.sample_rate,
            "sample_precision": cfg.sample_precision,
            "is_recorded": cfg.is_recorded,
            "is_displayed": cfg.is_displayed,
            "record_precision": cfg.record_precision,
            "record_rate": cfg.record_rate,
            "record_start_logic": cfg.record_start_logic,
            "record_stop_logic": cfg.record_stop_logic,
        }

    # ==================== 实时推送 ====================

    async def _param_push_loop(self):
        """1Hz 参数推送循环"""
        while self._running:
            try:
                param_data = {}
                for name, config in self._params.items():
                    if not config.get("is_displayed", True):
                        continue
                    # 成员系统服务禁用时跳过
                    src = config.get("source_member")
                    if src and src in self._monitor_services and not self._monitor_services[src]:
                        continue

                    value = self._gen_value(config)

                    # 有效性校验
                    validity = "valid"
                    if random.random() < 0.02:
                        validity = random.choice(["unavailable", "out_of_range", "invalid"])
                        if validity == "out_of_range" and config.get("max") is not None:
                            value = config["max"] + 100

                    precision = config.get("sample_precision", 2)
                    value = round(value, precision)

                    ts = datetime.utcnow().isoformat()
                    param_data[name] = {
                        "value": value,
                        "unit": config["unit"],
                        "validity": validity,
                        "ata": config["ata"],
                        "type": config["type"],
                        "timestamp": ts,
                    }
                    self._latest_values[name] = param_data[name]

                    # 按记录频率存储快照 (记录且满足记录频率)
                    if config.get("is_recorded", True) and random.random() < 0.1:
                        self._save_snapshot(name, value, config, validity)

                await ws_manager.broadcast("param_update", param_data)
            except Exception as e:
                logger.error(f"参数推送异常: {e}")
            await asyncio.sleep(1.0)

    def _gen_value(self, config: dict) -> float:
        """生成模拟参数值"""
        if config.get("type") == "string":
            return 0.0  # 字符串类型在快照层不存数值
        mn = config.get("min") or 0
        mx = config.get("max") or 100
        base = mn + (mx - mn) * 0.3
        return random.uniform(mn, base + 10)

    def _save_snapshot(self, name: str, value: float, config: dict, validity: str):
        db = SessionLocal()
        try:
            snapshot = ParamSnapshot(
                param_name=name,
                param_value=value,
                param_unit=config["unit"],
                param_type=config["type"],
                ata_chapter=config["ata"],
                validity=validity,
                sample_rate=config.get("sample_rate", 1),
                is_displayed=config.get("is_displayed", True),
                is_recorded=config.get("is_recorded", True),
            )
            db.add(snapshot)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"参数快照存储失败: {e}")
        finally:
            db.close()

    # ==================== 参数查询 ====================

    def get_param_list(self, db: Session, ata: Optional[str] = None) -> list:
        """获取参数列表 (支持按 ATA 章节查询)"""
        params = []
        for name, config in self._params.items():
            if ata and config["ata"] != ata:
                continue
            params.append({**config, "value": self._latest_values.get(name, {}).get("value"),
                           "validity": self._latest_values.get(name, {}).get("validity", "valid")})
        return params

    def get_ata_chapters(self) -> list:
        """获取参数涉及的 ATA 章节列表 (去重)"""
        atas = sorted({c["ata"].split("-")[0] for c in self._params.values()})
        return [{"ata": a, "paramCount": sum(1 for c in self._params.values() if c["ata"].startswith(a + "-"))} for a in atas]

    def get_param_history(self, db: Session, name: str, limit: int = 100) -> list:
        """获取参数历史数据"""
        items = db.query(ParamSnapshot).filter(
            ParamSnapshot.param_name == name
        ).order_by(ParamSnapshot.timestamp.desc()).limit(limit).all()
        return [{
            "value": s.param_value,
            "unit": s.param_unit,
            "validity": s.validity,
            "ata": s.ata_chapter,
            "timestamp": s.timestamp.isoformat() if s.timestamp else None,
        } for s in items]

    # ==================== 参数报告 ====================

    def get_param_report(self, db: Session, ata: Optional[str] = None) -> dict:
        """生成参数报告
        包含: 参数报告标识/参数个数/参数名称/参数类型/参数数值/参数单位/参数记录时间/参数所属ATA
        """
        report_id = f"PR-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        params = []
        for name, config in self._params.items():
            if ata and config["ata"] != ata:
                continue
            latest = self._latest_values.get(name, {})
            params.append({
                "paramName": name,
                "paramType": config["type"],
                "paramValue": latest.get("value"),
                "paramUnit": config["unit"],
                "recordTime": latest.get("timestamp"),
                "ata": config["ata"],
                "validity": latest.get("validity", "valid"),
            })

        report = {
            "reportId": report_id,
            "paramCount": len(params),
            "generatedAt": datetime.now().isoformat(),
            "params": params,
        }

        # 存储报告
        try:
            rec = ParamReport(
                report_id=report_id,
                param_count=len(params),
                data_json=json.dumps(report, ensure_ascii=False),
                download_status="stored",
            )
            db.add(rec)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"参数报告存储失败: {e}")

        return report

    def download_param_report(self, db: Session, report_id: Optional[str] = None) -> dict:
        """下传参数报告: 生成 JSON 文件到磁盘"""
        try:
            if report_id:
                rec = db.query(ParamReport).filter(ParamReport.report_id == report_id).first()
                if rec and rec.data_json:
                    payload = json.loads(rec.data_json)
                    rid = rec.report_id
                else:
                    return {"status": "error", "message": f"参数报告 {report_id} 不存在"}
            else:
                payload = self.get_param_report(db)
                rid = payload["reportId"]

            PARAM_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            file_path = PARAM_REPORT_DIR / f"param_report_{rid}.json"
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            # 更新下传状态
            rec = db.query(ParamReport).filter(ParamReport.report_id == rid).first()
            if rec:
                rec.download_status = "downloaded"
                rec.file_path = str(file_path)
                db.commit()

            logger.info(f"参数报告已下传: {file_path}")
            return {"status": "ok", "report_id": rid, "file_path": str(file_path)}
        except Exception as e:
            logger.error(f"参数报告下传失败: {e}")
            return {"status": "error", "message": f"下传失败: {e}"}

    # ==================== 快捷访问列表 ====================

    def create_quick_list(self, db: Session, name: str, params: list[str]) -> int:
        """创建快捷访问列表"""
        ql = QuickAccessList(list_name=name, param_names=json.dumps(params))
        db.add(ql)
        db.commit()
        db.refresh(ql)
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
            "updated_at": q.updated_at.isoformat() if q.updated_at else None,
        } for q in items]

    def update_quick_list(self, db: Session, list_id: int, name: Optional[str] = None,
                          params: Optional[list[str]] = None) -> dict:
        """修改快捷访问列表 (名称/参数)"""
        ql = db.query(QuickAccessList).filter(QuickAccessList.id == list_id).first()
        if not ql:
            return {"status": "error", "message": f"快捷列表 {list_id} 不存在"}
        if name is not None:
            ql.list_name = name
        if params is not None:
            ql.param_names = json.dumps(params)
        db.commit()
        return {"status": "ok", "id": list_id, "name": ql.list_name,
                "params": json.loads(ql.param_names) if ql.param_names else []}

    def delete_quick_list(self, db: Session, list_id: int) -> dict:
        """删除快捷访问列表"""
        ql = db.query(QuickAccessList).filter(QuickAccessList.id == list_id).first()
        if not ql:
            return {"status": "error", "message": f"快捷列表 {list_id} 不存在"}
        db.delete(ql)
        db.commit()
        return {"status": "ok", "id": list_id}

    def add_param_to_list(self, db: Session, list_id: int, param_name: str) -> dict:
        """向快捷列表增加参数"""
        ql = db.query(QuickAccessList).filter(QuickAccessList.id == list_id).first()
        if not ql:
            return {"status": "error", "message": f"快捷列表 {list_id} 不存在"}
        if param_name not in self._params:
            return {"status": "error", "message": f"参数 {param_name} 不存在"}
        names = json.loads(ql.param_names) if ql.param_names else []
        if param_name not in names:
            names.append(param_name)
            ql.param_names = json.dumps(names)
            db.commit()
        return {"status": "ok", "id": list_id, "params": names}

    def remove_param_from_list(self, db: Session, list_id: int, param_name: str) -> dict:
        """从快捷列表删除参数"""
        ql = db.query(QuickAccessList).filter(QuickAccessList.id == list_id).first()
        if not ql:
            return {"status": "error", "message": f"快捷列表 {list_id} 不存在"}
        names = json.loads(ql.param_names) if ql.param_names else []
        if param_name in names:
            names.remove(param_name)
            ql.param_names = json.dumps(names)
            db.commit()
        return {"status": "ok", "id": list_id, "params": names}

    # ==================== 成员系统监控服务启停 ====================

    def set_monitor_service(self, member_system: str, enabled: bool) -> dict:
        """成员系统启动或禁用飞机参数监控服务"""
        self._monitor_services[member_system] = enabled
        logger.info(f"成员系统 {member_system} 参数监控服务: {'启用' if enabled else '禁用'}")
        return {"status": "ok", "member_system": member_system, "enabled": enabled}

    def get_monitor_services(self) -> dict:
        """获取成员系统监控服务状态"""
        return {
            "memberList": [
                {"memberSystem": code, "memberName": name, "ata": ata,
                 "enabled": self._monitor_services.get(code, True)}
                for code, name, ata in MEMBER_SYSTEMS
            ]
        }

    # ==================== PAA 参数转发接收 ====================

    def receive_paa_params(self, params: list[dict]) -> dict:
        """接收来自 PAA 转发的参数, 用于飞机运行数据存储
        params: [{"name":..., "value":..., "unit":..., "ata":...}, ...]
        """
        db = SessionLocal()
        saved = 0
        try:
            for p in params:
                name = p.get("name") or p.get("paramName")
                if not name:
                    continue
                value = p.get("value")
                if value is None:
                    continue
                cfg = self._params.get(name, {})
                snapshot = ParamSnapshot(
                    param_name=name,
                    param_value=float(value) if isinstance(value, (int, float)) else 0.0,
                    param_unit=p.get("unit") or cfg.get("unit"),
                    param_type=cfg.get("type", "float"),
                    ata_chapter=p.get("ata") or cfg.get("ata"),
                    validity=p.get("validity", "valid"),
                    sample_rate=cfg.get("sample_rate", 1),
                    is_displayed=True,
                    is_recorded=True,
                )
                db.add(snapshot)
                saved += 1
            db.commit()
            logger.info(f"PAA 转发参数已存储: {saved}个")
            return {"status": "ok", "saved": saved}
        except Exception as e:
            db.rollback()
            logger.error(f"PAA 参数存储失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()


# 全局单例
param_service = ParamMonitorService()
