"""
OHMS 通信安全防护服务 (技术方案 4.3.5)
- 链路数据完整性校验 (CRC32): 数据包/文件末尾追加 CRC, 接收错误则丢弃
- 异常数据包处理: 通信管理应用丢弃错误数据包
- VPN 加密隧道: 对无线链路建立加密隧道, 防止数据泄露/外部攻击
- WiFi 白名单 + PSK 认证: 白名单存可信 SSID + 预共享密钥, 扫描比对, PSK 认证建立连接
"""
import zlib
import random
from datetime import datetime
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, WiFiWhitelist, CommSecurityLog


# 模拟附近 WiFi 网络池 (扫描时返回, 含可信与不可信)
NEARBY_WIFI_NETWORKS = [
    # (ssid, 是否可信 -> 由白名单比对决定)
    "AHMU-MAINT-NET",
    "OEM-OPS-WIFI",
    "AIRLINE-MX-5G",
    "STARBUCKS-WIFI",
    "FREE-WIFI",
    "GUEST-NET",
    "NEIGHBOR-5G",
    "UNKNOWN-HOTSPOT",
]

# 预置可信 WiFi 白名单 (SSID, PSK, 描述)
DEFAULT_WHITELIST = [
    ("AHMU-MAINT-NET", "ahmu-psk-2026", "AHMU 维护网络"),
    ("OEM-OPS-WIFI", "oem-ops-key", "OEM 运营网络"),
]


class CommSecurityService:
    """OHMS 通信安全防护服务"""

    def __init__(self):
        self._running = False
        self._vpn_tunnel_active = False
        self._vpn_tunnel_info: Optional[dict] = None
        # 数据包统计
        self._packets_received = 0
        self._packets_passed = 0
        self._packets_dropped = 0

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._init_whitelist()
        logger.info("通信安全防护服务已启动")

    async def stop(self):
        self._running = False
        logger.info("通信安全防护服务已停止")

    def _init_whitelist(self):
        """初始化可信 WiFi 白名单"""
        db = SessionLocal()
        try:
            existing = {r.ssid for r in db.query(WiFiWhitelist.ssid).all()}
            for ssid, psk, desc in DEFAULT_WHITELIST:
                if ssid in existing:
                    continue
                db.add(WiFiWhitelist(ssid=ssid, psk=psk, description=desc))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"WiFi 白名单初始化失败: {e}")
        finally:
            db.close()

    # ==================== CRC 完整性校验 ====================

    @staticmethod
    def _crc32(data: str) -> str:
        """计算 CRC32 (8位十六进制)"""
        return format(zlib.crc32(data.encode("utf-8")) & 0xFFFFFFFF, "08X")

    def append_crc(self, payload: str) -> dict:
        """发送方: 在数据包/文件末尾追加 CRC 校验值"""
        crc = self._crc32(payload)
        packet = f"{payload}|CRC={crc}"
        return {"payload": payload, "crc": crc, "packet": packet}

    def receive_packet(self, payload: str, crc: Optional[str] = None) -> dict:
        """接收方: 校验数据包 CRC, 错误则丢弃"""
        self._packets_received += 1
        expected_crc = self._crc32(payload)
        is_valid = (crc is not None and crc.upper() == expected_crc)

        if is_valid:
            self._packets_passed += 1
            self._log("crc_check", f"数据包 CRC 校验通过 (payload={payload[:40]})", "success")
            return {"status": "ok", "valid": True,
                    "message": "CRC 校验通过, 数据包接收", "crc": expected_crc}
        # CRC 错误 -> 通信管理应用丢弃该包
        self._packets_dropped += 1
        self._log("packet_drop", f"数据包 CRC 校验失败, 丢弃 (期望 {expected_crc}, 实际 {crc})", "dropped")
        return {"status": "error", "valid": False,
                "message": "CRC 校验失败, 数据包已丢弃", "expected_crc": expected_crc,
                "received_crc": crc}

    def verify_file(self, content: str, crc: Optional[str] = None) -> dict:
        """文件完整性校验 (文件末尾 CRC)"""
        expected = self._crc32(content)
        if crc and crc.upper() == expected:
            self._log("crc_check", f"文件 CRC 校验通过 ({len(content)} 字节)", "success")
            return {"status": "ok", "valid": True, "crc": expected,
                    "message": "文件完整性校验通过"}
        self._log("packet_drop", "文件 CRC 校验失败", "dropped")
        return {"status": "error", "valid": False, "crc": expected,
                "message": "文件完整性校验失败"}

    # ==================== VPN 加密隧道 ====================

    def establish_vpn_tunnel(self) -> dict:
        """建立 VPN 加密隧道 (防止无线链路数据泄露/外部攻击)"""
        if self._vpn_tunnel_active:
            return {"status": "error", "message": "VPN 隧道已建立"}
        self._vpn_tunnel_active = True
        self._vpn_tunnel_info = {
            "protocol": "IPsec/IKEv2",
            "cipher": "AES-256-GCM",
            "auth": "SHA-256",
            "established_at": datetime.utcnow().isoformat(),
        }
        self._log("vpn_establish", "VPN 加密隧道已建立 (AES-256-GCM)", "success")
        return {"status": "ok", "tunnel_active": True, "tunnel": self._vpn_tunnel_info}

    def teardown_vpn_tunnel(self) -> dict:
        """断开 VPN 隧道"""
        if not self._vpn_tunnel_active:
            return {"status": "error", "message": "VPN 隧道未建立"}
        self._vpn_tunnel_active = False
        self._vpn_tunnel_info = None
        self._log("vpn_teardown", "VPN 加密隧道已断开", "success")
        return {"status": "ok", "tunnel_active": False}

    @property
    def vpn_status(self) -> dict:
        return {"tunnel_active": self._vpn_tunnel_active,
                "tunnel": self._vpn_tunnel_info}

    # ==================== WiFi 白名单 ====================

    def add_whitelist(self, ssid: str, psk: str, description: str = "") -> dict:
        """添加可信 WiFi 到白名单"""
        if not ssid or not psk:
            return {"status": "error", "message": "SSID 和 PSK 不能为空"}
        db = SessionLocal()
        try:
            if db.query(WiFiWhitelist).filter(WiFiWhitelist.ssid == ssid).first():
                return {"status": "error", "message": f"SSID {ssid} 已在白名单中"}
            db.add(WiFiWhitelist(ssid=ssid, psk=psk, description=description))
            db.commit()
        finally:
            db.close()
        self._log("wifi_scan", f"白名单添加 SSID {ssid}", "success")
        return {"status": "ok", "ssid": ssid, "message": "已加入白名单"}

    def remove_whitelist(self, ssid: str) -> dict:
        """从白名单移除 WiFi"""
        db = SessionLocal()
        try:
            row = db.query(WiFiWhitelist).filter(WiFiWhitelist.ssid == ssid).first()
            if not row:
                return {"status": "error", "message": f"SSID {ssid} 不在白名单中"}
            db.delete(row)
            db.commit()
        finally:
            db.close()
        return {"status": "ok", "ssid": ssid, "message": "已从白名单移除"}

    def get_whitelist(self) -> dict:
        """获取白名单列表"""
        db = SessionLocal()
        try:
            rows = db.query(WiFiWhitelist).order_by(WiFiWhitelist.created_at).all()
            return {"total": len(rows), "items": [{
                "ssid": r.ssid, "psk": r.psk, "description": r.description,
            } for r in rows]}
        finally:
            db.close()

    # ==================== WiFi 扫描 + PSK 认证 ====================

    def scan_wifi(self) -> dict:
        """WiFi 扫描: 扫描附近网络, 与白名单逐一比对"""
        db = SessionLocal()
        try:
            trusted_ssids = {r.ssid for r in db.query(WiFiWhitelist.ssid).all()}
        finally:
            db.close()

        # 模拟扫描结果 (随机返回附近网络 + 信号强度)
        scanned = random.sample(NEARBY_WIFI_NETWORKS, k=random.randint(4, len(NEARBY_WIFI_NETWORKS)))
        results = []
        for ssid in scanned:
            trusted = ssid in trusted_ssids
            results.append({
                "ssid": ssid,
                "signal": random.randint(-80, -30),   # RSSI (dBm)
                "trusted": trusted,
            })
        trusted_count = sum(1 for r in results if r["trusted"])
        self._log("wifi_scan", f"WiFi 扫描完成: 共 {len(results)} 个网络, {trusted_count} 个可信", "success")
        return {"total": len(results), "trusted_count": trusted_count, "networks": results}

    def authenticate(self, ssid: str, psk: str) -> dict:
        """PSK 身份验证: 与白名单比对预共享密钥, 认证成功建立连接"""
        db = SessionLocal()
        try:
            row = db.query(WiFiWhitelist).filter(WiFiWhitelist.ssid == ssid).first()
        finally:
            db.close()

        if not row:
            self._log("psk_auth", f"认证失败: SSID {ssid} 不在白名单", "failed")
            return {"status": "error", "authenticated": False,
                    "message": f"SSID {ssid} 不在白名单, 认证失败"}
        if row.psk != psk:
            self._log("psk_auth", f"认证失败: SSID {ssid} PSK 不匹配", "failed")
            return {"status": "error", "authenticated": False,
                    "message": "预共享密钥不匹配, 认证失败"}
        # 双方持有相同 PSK -> 认证成功, 连接建立
        self._log("psk_auth", f"认证成功: SSID {ssid} 连接建立", "success")
        return {"status": "ok", "authenticated": True,
                "message": f"认证成功, 已连接 {ssid}", "ssid": ssid}

    # ==================== 日志与统计 ====================

    def _log(self, event_type: str, detail: str, result: str):
        db = SessionLocal()
        try:
            db.add(CommSecurityLog(event_type=event_type, detail=detail, result=result))
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"通信安全日志记录失败: {e}")
        finally:
            db.close()

    def get_logs(self, event_type: Optional[str] = None,
                 page: int = 1, size: int = 20) -> dict:
        """查询通信安全日志"""
        db = SessionLocal()
        try:
            query = db.query(CommSecurityLog)
            if event_type:
                query = query.filter(CommSecurityLog.event_type == event_type)
            total = query.count()
            rows = query.order_by(CommSecurityLog.created_at.desc()) \
                .offset((page - 1) * size).limit(size).all()
            return {
                "total": total, "page": page, "size": size,
                "items": [{
                    "event_type": r.event_type, "detail": r.detail,
                    "result": r.result,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                } for r in rows],
            }
        finally:
            db.close()

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "vpn_tunnel_active": self._vpn_tunnel_active,
            "packets_received": self._packets_received,
            "packets_passed": self._packets_passed,
            "packets_dropped": self._packets_dropped,
        }


# 全局单例
comm_security_service = CommSecurityService()
