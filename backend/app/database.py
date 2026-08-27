"""
AHMU 仿真器 - 数据库初始化与ORM模型
SQLite + WAL模式, 支持并发读 + 单写
"""
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, Boolean,
    Float, Index, JSON, ForeignKey, LargeBinary, text
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from datetime import datetime
import uuid

from app.config import DATABASE_URL, LOG_DIR

# 启用WAL模式, 支持并发读+单写
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# 启用WAL模式
with engine.connect() as conn:
    conn.execute(text("PRAGMA journal_mode=WAL"))
    conn.execute(text("PRAGMA synchronous=NORMAL"))
    conn.execute(text("PRAGMA cache_size=-64000"))  # 64MB cache
    conn.execute(text("PRAGMA temp_store=MEMORY"))
    conn.commit()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """数据库会话依赖注入"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database():
    """初始化数据库, 创建所有表"""
    Base.metadata.create_all(bind=engine)


# ==================== ORM Models ====================

class FaultReport(Base):
    """故障报告表 - 支持25000+失效报告/50000+故障报告"""
    __tablename__ = "fault_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)  # 成员系统
    fault_code = Column(String(50), nullable=False, index=True)      # 故障代码
    fault_text = Column(Text)                                         # 故障描述
    severity = Column(String(20), default="minor")                    # 严重程度: minor/major/critical
    status = Column(String(20), default="active")                    # active/resolved/suppressed
    ata_chapter = Column(String(10), index=True)                      # ATA章节号
    flight_phase = Column(Integer, default=0)                          # 飞行阶段(1~15)
    flight_segment = Column(Integer, default=0, index=True)           # 航段号(-128~127)
    is_cascaded = Column(Boolean, default=False)                       # 是否级联故障
    parent_fault_id = Column(String(36), ForeignKey("fault_reports.id"), nullable=True)
    fde_code = Column(String(50), nullable=True)                      # 关联FDE代码
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    resolved_at = Column(DateTime, nullable=True)
    raw_data = Column(Text)                                            # 原始数据(JSON)

    __table_args__ = (
        Index("idx_fault_member_seg", "member_system", "flight_segment"),
        Index("idx_fault_code_status", "fault_code", "status"),
    )


class FailureReport(Base):
    """失效报告表 (Failure Report - 与故障报告关联)"""
    __tablename__ = "failure_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    failure_code = Column(String(50), nullable=False, index=True)
    failure_text = Column(Text)
    fault_report_id = Column(String(36), ForeignKey("fault_reports.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    raw_data = Column(Text)


class ConfigReport(Base):
    """构型报告表 - 支持400个成员系统构型报告批量验证"""
    __tablename__ = "config_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    config_item = Column(String(100), nullable=False)
    config_value = Column(Text)
    expected_value = Column(Text)
    is_match = Column(Boolean, default=True)
    config_type = Column(String(30), default="hardware")  # hardware/software/database
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class LifecycleData(Base):
    """生命周期数据表 (旧版, 保留兼容)"""
    __tablename__ = "lifecycle_data"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    power_on_time = Column(Integer, default=0)      # 上电运行时间(秒)
    power_cycle_count = Column(Integer, default=0)   # 上电循环计数
    last_retrieved = Column(DateTime, default=datetime.utcnow, index=True)
    retrieval_status = Column(String(20), default="success")  # success/failed/timeout


class TCEquipment(Base):
    """时间周期(生命周期)设备表
    对应机载航电 MEMBER_SYSTEM + SYS_LRU + MEMBER_SYSTEM_DF 数据结构
    支持ATA分类查询、设备列表查询、时间周期状态查询
    """
    __tablename__ = "tc_equipments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    equip_id = Column(String(50), nullable=False, index=True)       # 设备ID (如 LRU001)
    equip_name = Column(String(100))                                  # 设备名称
    ata_code = Column(String(10), index=True)                         # ATA章节号 (如 "21")
    ata_name = Column(String(100))                                    # ATA章节名称
    member_system = Column(String(50), index=True)                   # 关联成员系统 (如 MEM001)
    ip_address = Column(String(50))                                   # IP地址
    rx_port = Column(Integer, default=0)                              # 接收端口
    tx_port = Column(Integer, default=0)                             # 发送端口
    is_available = Column(Integer, default=1)                         # 可用性: 0=异常 1=可获取 2=不可点击
    power_on_time = Column(Integer, default=0)                        # 上电运行时间(秒)
    power_cycle_count = Column(Integer, default=0)                    # 上电循环计数
    status_string = Column(String(20))                                # 状态字符串 "HH:MM:SS"
    last_retrieved = Column(DateTime, nullable=True, index=True)     # 最近获取时间
    retrieval_status = Column(String(20), default="pending")          # pending/success/failed/timeout

    __table_args__ = (
        Index("idx_tc_ata_equip", "ata_code", "equip_id"),
    )


class ParamSnapshot(Base):
    """参数快照表 - 参数监控功能"""
    __tablename__ = "param_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    param_name = Column(String(100), nullable=False, index=True)
    param_value = Column(Float)
    param_unit = Column(String(20))
    param_type = Column(String(30))  # int/float/bool/string
    ata_chapter = Column(String(10), index=True)
    validity = Column(String(30), default="valid")  # valid/unavailable/out_of_range/invalid
    sample_rate = Column(Integer, default=1)  # 采样频率(Hz)
    is_displayed = Column(Boolean, default=True)
    is_recorded = Column(Boolean, default=True)
    quick_list_id = Column(Integer, nullable=True)  # 快捷访问列表ID
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class QuickAccessList(Base):
    """参数快捷访问列表 - 支持100+张列表, 每张50+参数"""
    __tablename__ = "quick_access_lists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    list_name = Column(String(100), nullable=False)
    user = Column(String(50), default="TEST")
    param_names = Column(Text)  # JSON array of param names
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class StartupTest(Base):
    """启动测试记录表"""
    __tablename__ = "startup_tests"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    test_type = Column(String(30), default="interactive")  # interactive/non_interactive
    status = Column(String(30), default="idle")  # idle/running/waiting_ack/completed/suppressed
    result = Column(String(30), default="pending")  # pass/fail/abort/pending
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    test_detail = Column(Text)  # JSON test steps and results
    suppression_reason = Column(Text, nullable=True)


class DataLoadTask(Base):
    """数据加载任务表 - ARINC615A, 支持300个成员系统串行加载"""
    __tablename__ = "data_load_tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    file_name = Column(String(200))
    file_size = Column(Integer, default=0)
    progress = Column(Float, default=0.0)  # 0.0 ~ 100.0
    status = Column(String(30), default="pending")  # pending/loading/completed/failed
    load_mode = Column(String(30), default="serial")  # serial/concurrent
    start_time = Column(DateTime, default=datetime.utcnow)
    end_time = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)


class NVMData(Base):
    """NVM数据表 - 数据下载管理"""
    __tablename__ = "nvm_data"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    member_system = Column(String(50), nullable=False, index=True)
    data_type = Column(String(50))  # fault_snapshot/config_snapshot/life_cycle
    data_content = Column(LargeBinary)
    data_size = Column(Integer, default=0)
    retrieved_at = Column(DateTime, default=datetime.utcnow, index=True)
    download_status = Column(String(20), default="stored")  # stored/downloaded


class EventReport(Base):
    """事件报告表"""
    __tablename__ = "event_reports"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_type = Column(String(50), nullable=False, index=True)
    member_system = Column(String(50), nullable=True)
    description = Column(Text)
    related_fault_ids = Column(Text)  # JSON array of fault IDs
    related_params = Column(Text)     # JSON of param snapshot
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class ICDEntry(Base):
    """ICD条目表 - ICD解析结果存储"""
    __tablename__ = "icd_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(100), nullable=False, index=True)  # 成员系统-数据库-消息帧
    member_system = Column(String(50), index=True)
    database_name = Column(String(50))
    frame_name = Column(String(100))
    protocol = Column(String(20))  # A664/A429/A825
    port_id = Column(Integer, nullable=True)
    label_id = Column(Integer, nullable=True)
    can_id = Column(Integer, nullable=True)
    vl_id = Column(Integer, nullable=True)
    signal_name = Column(String(100))
    bit_offset = Column(Integer, default=0)
    bit_length = Column(Integer, default=1)
    resolution = Column(Float, default=1.0)
    offset = Column(Float, default=0.0)
    unit = Column(String(20))
    min_val = Column(Float, nullable=True)
    max_val = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_icd_msg_sig", "message_id", "signal_name"),
    )


class SystemState(Base):
    """系统状态表 - 维护模式、飞行阶段等"""
    __tablename__ = "system_state"

    id = Column(Integer, primary_key=True, autoincrement=True)
    state_key = Column(String(50), nullable=False, unique=True)
    state_value = Column(String(200))
    state_type = Column(String(30))  # string/int/float/bool
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class PrintJob(Base):
    """打印任务表"""
    __tablename__ = "print_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_type = Column(String(30), default="file_transfer")  # file_transfer/block_transfer
    content = Column(Text)
    status = Column(String(20), default="queued")  # queued/sending/completed/failed
    printer_status = Column(String(20), default="ready")  # ready/busy/open/error
    created_at = Column(DateTime, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class ACARSMessage(Base):
    """ACARS消息表"""
    __tablename__ = "acars_messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    direction = Column(String(10), default="downlink")  # uplink/downlink
    message_type = Column(String(50))  # fault_report/failure_report/fde/event_report/config_data
    priority = Column(Integer, default=0)  # 0=low, 1=normal, 2=high
    content = Column(Text)
    link_status = Column(String(20), default="idle")  # idle/busy/lost
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    sent_at = Column(DateTime, nullable=True)
