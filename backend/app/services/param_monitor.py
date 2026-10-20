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
import json
import asyncio
import math
import random
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import SessionLocal, ParamConfig, ParamSnapshot, ParamReport, QuickAccessList
from app.core.websocket_manager import ws_manager
from app.core.icd_parser import icd_parser
from app.services.lifecycle import MEMBER_SYSTEMS
from app.config import DATA_DIR

# 参数报告下传默认目录
PARAM_REPORT_DIR = DATA_DIR / "param_reports"

# 成员系统标识 → ATA 映射 (用于给参数分配来源成员系统)
_MEMBER_ATA_MAP: dict[str, str] = {}
for _code, _name, _ata in MEMBER_SYSTEMS:
    _MEMBER_ATA_MAP.setdefault(_ata, _code)

# ==================== 参数数据来源标识 (统一) ====================
# real      - 真实数据: 通过消息(PAA 转发)实际接收到的参数数据
# simulated - 模拟数据: 程序内部随机生成 (已移除, 仅保留标识供区分)
DATA_SOURCE_REAL = "real"
DATA_SOURCE_SIMULATED = "simulated"


class ParamMonitorService:
    """参数监控服务"""

    def __init__(self):
        self._running = False
        self._params: dict[str, dict] = {}                 # param_name -> 完整配置
        self._monitor_services: dict[str, bool] = {}       # 成员系统 -> 监控服务是否启用
        self._latest_values: dict[str, dict] = {}          # param_name -> 最近一次真实值(data_source=real)
        self._sim_values: dict[str, dict] = {}             # param_name -> 模拟值(data_source=simulated)
        self._simulation_enabled = False                   # 模拟数据开关
        self._sim_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动参数监控服务"""
        self._running = True
        self._init_default_params()
        self._start_simulation_loop()
        logger.info("参数监控服务已启动 (真实数据来自 PAA 转发, 模拟数据由开关控制)")

    async def stop(self):
        self._running = False
        self._stop_simulation_loop()
        logger.info("参数监控服务已停止")

    # ==================== 参数配置初始化 ====================

    @staticmethod
    def _map_param_type(data_format_type: str, resolution) -> str:
        """ICD DataFormatType → 参数类型 (int/float/bool/string)"""
        dft = (data_format_type or "").upper()
        if dft in ("BOOL", "BIT", "DIS"):
            return "bool"
        if dft in ("ISO-5", "ISO5", "CHAR", "STRING"):
            return "string"
        if dft in ("SINT", "UBNR", "UINT", "INT"):
            # 有非 1 分辨率视为浮点, 否则整数
            return "float" if (resolution and resolution != 1.0) else "int"
        if dft in ("BNR", "FLOAT", "DOUBLE"):
            return "float"
        return "float"

    @staticmethod
    def _precision_from_resolution(resolution) -> int:
        """由分辨率推导采样精度 (小数位)"""
        if resolution is None:
            return 2
        if resolution >= 1.0:
            return 0
        s = f"{resolution:.10f}".rstrip("0").rstrip(".")
        return len(s.split(".")[1]) if "." in s else 0

    @staticmethod
    def _rate_from_interval(interval) -> int:
        """由 ICD 发送周期(ms)推导采样频率(Hz), 限制在 1~10Hz"""
        if not interval or interval <= 0:
            return 1
        return max(1, min(10, round(1000 / interval)))

    @staticmethod
    def _member_ata(member: str) -> str:
        """成员系统 → ATA 章节 (优先名录映射, 未知归 '00')"""
        for code, _name, ata in MEMBER_SYSTEMS:
            if code == member:
                return ata
        return "00"

    def _extract_params_from_icd(self) -> list[dict]:
        """从 ICD 提取参数监控参数定义 (OHMSAttribute 含 Parameter 的信号)

        对齐真实 ICD: 参数名称/类型/单位/范围/分辨率/采样频率均来自 ICD 信号定义,
        每个成员系统最多取 3 个纯 Parameter 信号 (不足补 Parameter,Fault), 覆盖真实航电参数。
        """
        entries = icd_parser.icd_entries
        if not entries:
            return []

        # 纯 Parameter 优先, Parameter,Fault 次之
        pure = [e for e in entries if e.get("ohms_attribute") == "Parameter"]
        mixed = [e for e in entries if e.get("ohms_attribute") == "Parameter,Fault"]
        pool = pure + mixed

        # 每成员最多 3 个
        per_member: dict[str, list[dict]] = {}
        for e in pool:
            member = e.get("member_system") or "UNKNOWN"
            per_member.setdefault(member, [])
            if len(per_member[member]) < 3:
                per_member[member].append(e)

        params = []
        seen: set = set()
        for member, lst in per_member.items():
            for e in lst:
                name = e["signal_name"]
                if name in seen:  # 跨消息同名信号去重
                    continue
                seen.add(name)
                resolution = e.get("resolution")
                ptype = self._map_param_type(e.get("data_format_type", ""), resolution)
                precision = self._precision_from_resolution(resolution)
                rate = self._rate_from_interval(e.get("transmission_interval"))
                params.append({
                    "param_name": name,
                    "param_type": ptype,
                    "sample_rate": rate,
                    "sample_precision": precision,
                    "param_unit": e.get("unit") or "-",
                    "is_recorded": True,
                    "is_displayed": True,
                    "record_precision": precision,
                    "record_rate": 1,
                    "record_start_logic": "电源接通且参数有效",
                    "record_stop_logic": "断电或参数无效",
                    "ata_chapter": e.get("ata") or self._member_ata(member),
                    "source_member": member,
                    "min_val": e.get("min_val"),
                    "max_val": e.get("max_val"),
                })
        return params

    def _init_default_params(self):
        """初始化默认监控参数 (含完整配置项), 覆盖多个 ATA 章节"""
        db = SessionLocal()
        try:
            existing = db.query(ParamConfig).count()
            if existing > 0:
                self._load_params(db)
                logger.info(f"参数配置已存在: {existing}个, 跳过初始化")
                return

            # 优先从 ICD 提取参数 (使用 ICD 定义, 对齐真实航电 ICD)
            icd_params = self._extract_params_from_icd()
            if icd_params:
                for p in icd_params:
                    db.add(ParamConfig(**p))
                db.commit()
                logger.info(f"参数配置已初始化: {len(icd_params)}个参数 (来源: ICD)")
                self._load_params(db)
                return

            # 回退: ICD 无 Parameter 信号时使用内置默认参数
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
            logger.info(f"参数配置已初始化: {len(default_params)}个参数 (来源: 内置默认)")
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

    # ==================== 参数配置管理 (供应商配置工具) ====================

    def _cfg_full_dict(self, cfg: ParamConfig) -> dict:
        """ParamConfig ORM → 完整配置字典 (含来源/审计字段)"""
        return {
            **self._cfg_to_dict(cfg),
            "config_source": cfg.config_source or "icd",
            "updated_at": cfg.updated_at.isoformat() if cfg.updated_at else None,
            "updated_by": cfg.updated_by or "TEST",
        }

    def get_param_config_list(self, db: Session, ata: Optional[str] = None,
                              recorded: Optional[bool] = None,
                              source: Optional[str] = None,
                              keyword: Optional[str] = None,
                              page: int = 1, size: int = 50) -> dict:
        """参数配置列表 (按 ATA/是否记录/来源筛选 + 关键字搜索 + 分页)"""
        query = db.query(ParamConfig)
        if ata:
            query = query.filter(ParamConfig.ata_chapter.startswith(ata))
        if recorded is not None:
            query = query.filter(ParamConfig.is_recorded == recorded)
        if source:
            query = query.filter(ParamConfig.config_source == source)
        if keyword:
            query = query.filter(ParamConfig.param_name.ilike(f"%{keyword}%"))
        total = query.count()
        items = (query.order_by(ParamConfig.ata_chapter, ParamConfig.param_name)
                 .offset((page - 1) * size).limit(size).all())
        return {"total": total, "page": page, "size": size,
                "items": [self._cfg_full_dict(c) for c in items]}

    def get_param_config_detail(self, db: Session, param_name: str) -> dict:
        """单个参数配置详情"""
        cfg = db.query(ParamConfig).filter(ParamConfig.param_name == param_name).first()
        if not cfg:
            return {"status": "error", "message": f"参数 {param_name} 不存在"}
        return {"status": "ok", "config": self._cfg_full_dict(cfg)}

    @staticmethod
    def _validate_config(fields: dict) -> Optional[str]:
        """校验配置项, 返回错误信息 (None 表示通过)"""
        ptype = fields.get("param_type")
        if ptype and ptype not in ("int", "float", "bool", "string"):
            return f"参数类型非法: {ptype} (仅支持 int/float/bool/string)"
        for key in ("sample_rate", "record_rate"):
            v = fields.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not (1 <= v <= 10)):
                return f"{key} 需为 1~10 的整数"
        for key in ("sample_precision", "record_precision"):
            v = fields.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 6)):
                return f"{key} 需为 0~6 的整数"
        return None

    def update_param_config(self, db: Session, param_name: str, fields: dict,
                            operator: str = "TEST") -> dict:
        """修改参数配置项 (基础 + 记录, 含校验与是否记录联动)"""
        cfg = db.query(ParamConfig).filter(ParamConfig.param_name == param_name).first()
        if not cfg:
            return {"status": "error", "message": f"参数 {param_name} 不存在"}

        allowed = ("param_type", "sample_rate", "sample_precision", "param_unit",
                   "is_recorded", "is_displayed", "record_precision", "record_rate",
                   "record_start_logic", "record_stop_logic", "min_val", "max_val")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return {"status": "error", "message": "无有效更新字段"}

        err = self._validate_config(updates)
        if err:
            return {"status": "error", "message": err}

        try:
            for k, v in updates.items():
                setattr(cfg, k, v)
            # 是否记录联动: 关闭记录时清空记录逻辑
            if not cfg.is_recorded:
                cfg.record_start_logic = None
                cfg.record_stop_logic = None
            cfg.config_source = "manual"
            cfg.updated_by = operator
            cfg.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(cfg)
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": f"保存失败: {e}"}

        # 同步内存缓存
        self._params[param_name] = self._cfg_to_dict(cfg)
        logger.info(f"参数配置已更新: {param_name} (操作人 {operator})")
        return {"status": "ok", "config": self._cfg_full_dict(cfg)}

    def batch_update_param_config(self, db: Session, fields: dict,
                                  operator: str = "TEST") -> dict:
        """批量修改参数配置 (如批量设置是否记录/是否显示/采样频率)"""
        batch_allowed = ("is_recorded", "is_displayed", "sample_rate", "record_rate")
        updates = {k: v for k, v in fields.items() if k in batch_allowed and v is not None}
        if not updates:
            return {"status": "error", "message": "无有效批量更新字段"}
        err = self._validate_config(updates)
        if err:
            return {"status": "error", "message": err}
        try:
            count = 0
            for cfg in db.query(ParamConfig).all():
                for k, v in updates.items():
                    setattr(cfg, k, v)
                if not cfg.is_recorded:
                    cfg.record_start_logic = None
                    cfg.record_stop_logic = None
                cfg.config_source = "manual"
                cfg.updated_by = operator
                cfg.updated_at = datetime.utcnow()
                count += 1
            db.commit()
            self._load_params(db)
            logger.info(f"批量更新参数配置: {count} 个参数 (操作人 {operator})")
            return {"status": "ok", "updated": count}
        except Exception as e:
            db.rollback()
            return {"status": "error", "message": f"批量更新失败: {e}"}

    def export_param_config(self, db: Session) -> dict:
        """导出参数配置为 JSON 文件"""
        try:
            configs = [self._cfg_full_dict(c) for c in
                       db.query(ParamConfig).order_by(ParamConfig.ata_chapter).all()]
            payload = {
                "exportedAt": datetime.now().isoformat(),
                "paramCount": len(configs),
                "params": configs,
            }
            PARAM_REPORT_DIR.mkdir(parents=True, exist_ok=True)
            file_path = PARAM_REPORT_DIR / f"param_config_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"参数配置已导出: {file_path}")
            return {"status": "ok", "paramCount": len(configs), "file_path": str(file_path)}
        except Exception as e:
            logger.error(f"导出参数配置失败: {e}")
            return {"status": "error", "message": str(e)}

    # ==================== 参数查询 ====================

    def get_param_list(self, db: Session, ata: Optional[str] = None) -> list:
        """获取参数列表 (支持按 ATA 章节查询, 章节号前缀匹配如 "34" 匹配 "34-01")"""
        display = self._build_display_view()
        params = []
        for name, config in self._params.items():
            if ata and not config["ata"].startswith(ata):
                continue
            latest = display.get(name, {})
            params.append({**config, "value": latest.get("value"),
                           "validity": latest.get("validity", "valid"),
                           "data_source": latest.get("data_source", "")})
        return params

    def get_ata_chapters(self) -> list:
        """获取参数涉及的 ATA 章节列表 (去重)"""
        atas = sorted({c["ata"].split("-")[0] for c in self._params.values()})
        return [{"ata": a, "paramCount": sum(
            1 for c in self._params.values() if c["ata"].split("-")[0] == a)} for a in atas]

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

    # ==================== 参数报告与历史记录 ====================

    def get_param_report(self, db: Session, ata: Optional[str] = None,
                         operator: str = "manual") -> dict:
        """生成参数报告并自动保存为历史记录

        包含: 参数报告标识/报告名称/参数个数/参数明细/参数摘要/获取状态。
        每次获取均写入 param_reports 历史记录, 供历史列表查看、重新查看与导出。
        """
        now = datetime.now()
        report_id = f"PR-{now.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
        display = self._build_display_view()

        params = []
        valid = out = invalid = 0
        sources: set = set()
        atas: set = set()
        for name, config in self._params.items():
            if ata and not config["ata"].startswith(ata):
                continue
            latest = display.get(name, {})
            validity = latest.get("validity", "valid")
            if validity == "valid":
                valid += 1
            elif validity == "out_of_range":
                out += 1
            else:
                invalid += 1
            ds = latest.get("data_source", "")
            if ds:
                sources.add(ds)
            atas.add(config["ata"].split("-")[0])
            params.append({
                "paramName": name,
                "paramType": config["type"],
                "paramValue": latest.get("value"),
                "paramUnit": config["unit"],
                "recordTime": latest.get("timestamp"),
                "ata": config["ata"],
                "validity": validity,
                "dataSource": ds,
            })

        # 数据来源标识
        if len(sources) > 1:
            data_source = "mixed"
        else:
            data_source = (sources.pop() if sources else "")

        ata_scope = sorted(atas)
        scope_label = f"ATA{ata}" if ata else "全量"
        report_name = f"参数报告-{scope_label}-{now.strftime('%m-%d %H:%M')}"

        # 参数摘要
        src_label = ""
        if data_source == "mixed":
            src_label = " · 来源: 真实+模拟"
        elif data_source == "real":
            src_label = " · 来源: 真实"
        elif data_source == "simulated":
            src_label = " · 来源: 模拟"
        summary = (f"共 {len(params)} 个参数 · 覆盖 {len(ata_scope)} 个 ATA 章节"
                   f"({'/'.join(ata_scope)}) · 有效 {valid} / 越限 {out} / 无效 {invalid}"
                   f"{src_label}")

        report = {
            "reportId": report_id,
            "reportName": report_name,
            "paramCount": len(params),
            "validCount": valid,
            "outOfRangeCount": out,
            "invalidCount": invalid,
            "ataScope": ata_scope,
            "dataSource": data_source,
            "summary": summary,
            "generatedAt": now.isoformat(),
            "params": params,
        }

        # 自动保存历史记录
        try:
            rec = ParamReport(
                report_id=report_id,
                report_name=report_name,
                param_count=len(params),
                valid_count=valid,
                out_of_range_count=out,
                invalid_count=invalid,
                ata_scope=json.dumps(ata_scope, ensure_ascii=False),
                data_source=data_source,
                summary=summary,
                status="success",
                export_status="none",
                operator=operator,
                data_json=json.dumps(report, ensure_ascii=False),
                download_status="stored",
                get_time=now,
            )
            db.add(rec)
            db.commit()
            logger.info(f"参数报告已获取并保存历史: {report_id} ({len(params)} 参数)")
        except Exception as e:
            db.rollback()
            logger.error(f"参数报告存储失败: {e}")
            report["status"] = "failed"

        return report

    @staticmethod
    def _report_to_summary(r: ParamReport) -> dict:
        """ParamReport ORM → 历史列表摘要"""
        return {
            "reportId": r.report_id,
            "reportName": r.report_name or r.report_id,
            "paramCount": r.param_count or 0,
            "validCount": r.valid_count or 0,
            "outOfRangeCount": r.out_of_range_count or 0,
            "invalidCount": r.invalid_count or 0,
            "ataScope": json.loads(r.ata_scope) if r.ata_scope else [],
            "dataSource": r.data_source or "",
            "summary": r.summary or "",
            "status": r.status or "success",
            "exportStatus": r.export_status or "none",
            "operator": r.operator or "manual",
            "getTime": r.get_time.isoformat() if r.get_time else (
                r.created_at.isoformat() if r.created_at else None),
        }

    def get_report_history(self, db: Session, start: Optional[str] = None,
                           end: Optional[str] = None, keyword: Optional[str] = None,
                           page: int = 1, size: int = 20) -> dict:
        """获取参数报告历史列表 (时间范围 + 关键字筛选/搜索 + 分页)

        start/end: ISO 时间字符串 (按 get_time 过滤, 支持仅起/仅止)
        keyword: 模糊匹配 report_name / report_id
        """
        query = db.query(ParamReport)
        if start:
            # 纯日期 (YYYY-MM-DD) 自动补全为当天起始
            if len(start) == 10:
                start = f"{start} 00:00:00"
            query = query.filter(ParamReport.get_time >= start)
        if end:
            # 纯日期自动补全为当天末尾
            if len(end) == 10:
                end = f"{end} 23:59:59.999999"
            query = query.filter(ParamReport.get_time <= end)
        if keyword:
            like = f"%{keyword}%"
            query = query.filter(or_(
                ParamReport.report_name.ilike(like),
                ParamReport.report_id.ilike(like),
            ))
        total = query.count()
        items = (query.order_by(ParamReport.get_time.desc())
                 .offset((page - 1) * size).limit(size).all())
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [self._report_to_summary(r) for r in items],
        }

    def get_report_history_detail(self, db: Session, report_id: str) -> dict:
        """查看历史报告详情 (从 data_json 快照加载, 不重新生成)"""
        rec = db.query(ParamReport).filter(ParamReport.report_id == report_id).first()
        if not rec:
            return {"status": "error", "message": f"参数报告 {report_id} 不存在"}
        payload = json.loads(rec.data_json) if rec.data_json else {"params": []}
        payload["reportName"] = rec.report_name or rec.report_id
        payload["summary"] = rec.summary or ""
        payload["status"] = rec.status or "success"
        payload["exportStatus"] = rec.export_status or "none"
        payload["operator"] = rec.operator or "manual"
        payload["getTime"] = rec.get_time.isoformat() if rec.get_time else None
        return {"status": "ok", "report": payload}

    def delete_all_reports(self, db: Session) -> dict:
        """删除全部参数报告历史记录"""
        try:
            deleted = db.query(ParamReport).delete()
            db.commit()
            logger.info(f"已删除全部参数报告历史记录: {deleted} 条")
            return {"status": "ok", "deleted": deleted}
        except Exception as e:
            db.rollback()
            logger.error(f"删除参数报告历史失败: {e}")
            return {"status": "error", "message": str(e)}

    def download_param_report(self, db: Session, report_id: Optional[str] = None) -> dict:
        """下传/导出参数报告: 生成 JSON 文件到磁盘, 并更新导出状态"""
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

            # 更新下传/导出状态
            rec = db.query(ParamReport).filter(ParamReport.report_id == rid).first()
            if rec:
                rec.download_status = "downloaded"
                rec.export_status = "exported"
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
        if not enabled:
            # 禁用时清理该成员的模拟展示值, 保持状态一致
            for name, cfg in self._params.items():
                if cfg.get("source_member") == member_system:
                    self._sim_values.pop(name, None)
        logger.info(f"成员系统 {member_system} 参数监控服务: {'启用' if enabled else '禁用'}")
        return {"status": "ok", "member_system": member_system, "enabled": enabled}

    def _member_status(self, code: str, name: str, ata: str, display: dict) -> dict:
        """构建单个成员系统的监控运行状态"""
        enabled = self._monitor_services.get(code, False)
        member_params = [n for n, c in self._params.items() if c.get("source_member") == code]
        real_count = sum(1 for n in member_params if n in self._latest_values)
        sim_count = sum(1 for n in member_params if n in self._sim_values)
        last_update = None
        for n in member_params:
            v = display.get(n)
            ts = v.get("timestamp") if v else None
            if ts and (last_update is None or ts > last_update):
                last_update = ts
        return {
            "memberSystem": code,
            "memberName": name,
            "ata": ata,
            "enabled": enabled,
            "paramCount": len(member_params),
            "realCount": real_count,
            "simCount": sim_count,
            "lastUpdate": last_update,
        }

    def get_monitor_services(self) -> dict:
        """获取成员系统监控服务状态 (含名录成员 + ICD 参数来源成员)

        名录成员来自 MEMBER_SYSTEMS, 另补充 param_configs 中实际出现的
        ICD 成员系统 (如 HF_EEC_L_CHA), 使 AFDX/ICD 数据来源的成员也可启停监控。
        """
        display = self._build_display_view()
        members = []
        seen: set = set()
        for code, name, ata in MEMBER_SYSTEMS:
            seen.add(code)
            members.append(self._member_status(code, name, ata, display))

        # 补充 ICD 参数来源成员
        for name, cfg in self._params.items():
            sm = cfg.get("source_member") or ""
            if not sm or sm in seen:
                continue
            seen.add(sm)
            ata = (cfg.get("ata") or "").split("-")[0] if cfg.get("ata") else ""
            members.append(self._member_status(sm, sm, ata, display))

        return {
            "memberList": members,
            "summary": {
                "totalMembers": len(members),
                "totalEnabled": sum(1 for m in members if m["enabled"]),
                "realParams": len(self._latest_values),
                "simParams": len(self._sim_values),
                "simulationEnabled": self._simulation_enabled,
            },
        }

    # ==================== PAA 参数转发接收 ====================

    async def receive_paa_params(self, params: list[dict]) -> dict:
        """接收来自 PAA 转发的真实参数数据, 用于飞机运行数据存储与实时显示

        params: [{"name":..., "value":..., "unit":..., "ata":..., "source_member":..., "validity":...}, ...]

        数据来源标识: 本接口接收的参数均为"真实数据"(data_source=real),
        区别于程序内部模拟生成的数据(已移除)。
        仅"已启动监控服务"的成员系统参数会被接收、推送与显示;
        监控服务默认关闭, 故启动前不会出现任何参数数据。
        """
        db = SessionLocal()
        saved = 0
        skipped = 0
        try:
            for p in params:
                name = p.get("name") or p.get("paramName")
                if not name:
                    continue
                value = p.get("value")
                if value is None:
                    continue
                cfg = self._params.get(name, {})
                # 来源成员系统: 优先显式字段, 其次配置, 否则空
                src = p.get("source_member") or cfg.get("source_member") or ""

                # 监控启动状态判断: 来源成员监控服务未启动时丢弃
                if src and not self._monitor_services.get(src, False):
                    skipped += 1
                    logger.info(f"[Param] 成员系统 {src} 监控服务未启动, 丢弃参数 {name}")
                    continue

                num_value = float(value) if isinstance(value, (int, float)) else 0.0
                unit = p.get("unit") or cfg.get("unit")
                ata = p.get("ata") or cfg.get("ata")
                validity = p.get("validity", "valid")
                ptype = cfg.get("type", "float")
                ts = datetime.utcnow().isoformat()

                # 更新最近值 (标记真实数据来源)
                self._latest_values[name] = {
                    "value": num_value,
                    "unit": unit,
                    "validity": validity,
                    "ata": ata,
                    "type": ptype,
                    "timestamp": ts,
                    "data_source": DATA_SOURCE_REAL,
                    "source_member": src,
                }

                # 快照入库
                snapshot = ParamSnapshot(
                    param_name=name,
                    param_value=num_value,
                    param_unit=unit,
                    param_type=ptype,
                    ata_chapter=ata,
                    validity=validity,
                    sample_rate=cfg.get("sample_rate", 1),
                    is_displayed=True,
                    is_recorded=True,
                )
                db.add(snapshot)
                saved += 1

            db.commit()
            # 广播完整展示视图 (真实优先, 模拟补充), 保证前端展示与后端状态一致
            await ws_manager.broadcast("param_update", self._build_display_view())
            logger.info(f"PAA 转发真实参数: 接收 {saved}个, 丢弃 {skipped}个 (监控未启动)")
            return {"status": "ok", "saved": saved, "skipped": skipped,
                    "data_source": DATA_SOURCE_REAL}
        except Exception as e:
            db.rollback()
            logger.error(f"PAA 参数接收失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

    # ==================== AFDX 数据接收 (参数监控) ====================

    async def ingest_afdx_signals(self, member_system: str, decoded_signals: dict) -> dict:
        """接收 AFDX 通道解码后的信号, 更新参数监控 (真实数据路径)

        member_system: 来源成员系统 (ICD 成员名, 如 HF_EEC_L_CHA)
        decoded_signals: icd_afdx.decode_frame 返回的信号字典 {信号名: {value/unit/validity/...}}
        复用 receive_paa_params 完成监控启停判断、快照入库与 WebSocket 广播。
        """
        params = []
        for name, s in decoded_signals.items():
            if not isinstance(s, dict):
                continue
            value = s.get("value")
            if value is None:
                continue
            params.append({
                "name": name,
                "value": value,
                "unit": s.get("unit"),
                "validity": s.get("validity", "valid"),
                "source_member": member_system,
            })
        if not params:
            return {"status": "ok", "saved": 0, "skipped": 0, "data_source": DATA_SOURCE_REAL}
        return await self.receive_paa_params(params)

    # ==================== 模拟数据开关与生成 ====================

    def set_simulation_mode(self, enabled: bool) -> dict:
        """设置参数监控模拟数据开关 (开启后为已启动监控服务的成员生成模拟数据)"""
        self._simulation_enabled = bool(enabled)
        if not self._simulation_enabled:
            # 关闭模拟数据时清空模拟展示值
            self._sim_values.clear()
        logger.info(f"参数监控模拟数据: {'开启' if self._simulation_enabled else '关闭'}")
        return {"status": "ok", "simulation_enabled": self._simulation_enabled}

    def get_simulation_config(self) -> dict:
        """获取参数监控模拟数据配置"""
        return {"simulation_enabled": self._simulation_enabled}

    def _start_simulation_loop(self):
        """启动模拟数据生成循环 (幂等)"""
        if self._sim_task and not self._sim_task.done():
            return
        self._sim_task = asyncio.create_task(self._simulation_loop())

    def _stop_simulation_loop(self):
        """停止模拟数据生成循环"""
        if self._sim_task and not self._sim_task.done():
            self._sim_task.cancel()
        self._sim_task = None

    async def _simulation_loop(self):
        """模拟数据生成循环 (1Hz): 开关开启时, 为已启动监控服务的成员周期生成模拟参数

        真实数据始终优先展示; 模拟数据仅用于在无真实数据时渲染前端显示效果。
        """
        while self._running:
            try:
                if self._simulation_enabled:
                    changed = self._generate_simulation_frame()
                    if changed:
                        await ws_manager.broadcast("param_update", self._build_display_view())
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"模拟数据生成异常: {e}")
            await asyncio.sleep(1.0)

    def _generate_simulation_frame(self) -> bool:
        """为所有已启动监控服务的成员系统生成一帧模拟参数数据

        返回是否有数据变化 (用于决定是否广播)。
        """
        enabled_members = {m for m, on in self._monitor_services.items() if on}
        if not enabled_members:
            return False

        changed = False
        ts = datetime.utcnow().isoformat()
        for name, config in self._params.items():
            member = config.get("source_member") or ""
            if member not in enabled_members:
                continue
            value = self._gen_sim_value(config)
            self._sim_values[name] = {
                "value": value,
                "unit": config.get("unit"),
                "validity": "valid",
                "ata": config.get("ata"),
                "type": config.get("type"),
                "timestamp": ts,
                "data_source": DATA_SOURCE_SIMULATED,
                "source_member": member,
            }
            changed = True
        return changed

    def _gen_sim_value(self, config: dict):
        """基于参数配置生成模拟值 (范围正弦波动 + 随机扰动, 不同参数相位错开)"""
        ptype = config.get("type", "float")
        if ptype == "bool":
            return float(random.choice([0, 1]))
        if ptype == "string":
            return "SIM"
        mn = config.get("min")
        mx = config.get("max")
        if mn is None or mx is None:
            mn, mx = 0.0, 100.0
        base = (mn + mx) / 2.0
        amp = (mx - mn) / 2.0
        if amp <= 0:
            amp = 1.0
        # 用参数名哈希作相位偏移, 使各参数波形不同步
        phase = (sum(ord(c) for c in config.get("name", "")) % 360) * math.pi / 180.0
        value = base + amp * math.sin(time.time() / 5.0 + phase)
        value += random.uniform(-amp * 0.05, amp * 0.05)
        precision = config.get("sample_precision") or 2
        return round(value, precision)

    def _build_display_view(self) -> dict:
        """构建当前展示视图: 真实数据优先, 无真实数据时用模拟数据补充"""
        view = dict(self._sim_values)
        view.update(self._latest_values)
        return view


# 全局单例
param_service = ParamMonitorService()
