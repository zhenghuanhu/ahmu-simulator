"""
故障历史检索 TFTP 上传客户端 (对齐 OHMS 故障历史报告获取664.py 的 TFTPServer)

ARINC 615A 数据加载协议以 TFTP (Trivial File Transfer Protocol) 为底层承载。
检索任务完成后, AHMU 作为 TFTP 客户端向测试端 TFTPServer 上传故障历史文件。

采用标准 TFTP (RFC 1350 + RFC 2347 blksize 选项):
  上传流程 (客户端 WRQ -> 服务器 ACK(0)/OACK -> 客户端 DATA 分块 -> 服务器逐块 ACK):
    1. 客户端发 WRQ (opcode=0x0002, 含 octet + blksize 选项)
    2. 服务器用新 TID 回 ACK(0) 或 OACK(0x0006) -> 再回 ACK(0)
    3. 客户端逐块发 DATA (opcode=0x0003, block 从 1 递增), 服务器逐块回 ACK(0x0004)
    4. 最后一块 (< blksize) 发送后结束
"""
import socket
from typing import Optional
from loguru import logger

from app.config import FAULT_HISTORY_TFTP_CONFIG

OPCODE_RRQ = b"\x00\x01"
OPCODE_WRQ = b"\x00\x02"
OPCODE_DATA = b"\x00\x03"
OPCODE_ACK = b"\x00\x04"
OPCODE_ERROR = b"\x00\x05"
OPCODE_OACK = b"\x00\x06"


class TftpClient:
    """标准 TFTP 客户端 (全局单例)"""

    def __init__(self, host: str = None, port: int = None,
                 blksize: int = None, timeout: float = None):
        cfg = FAULT_HISTORY_TFTP_CONFIG
        self.host = host or cfg.get("host", "127.0.0.1")
        self.port = port or cfg.get("port", 59)
        self.blksize = blksize or cfg.get("blksize", 2048)
        self.timeout = timeout or cfg.get("timeout", 3.0)
        self.enabled = cfg.get("enabled", True)

    def _recv_packet(self, sock: socket.socket, timeout: float = None):
        """接收一个 TFTP 包, 返回 (data, addr)"""
        sock.settimeout(timeout or self.timeout)
        data, addr = sock.recvfrom(65535)
        return data, addr

    def upload(self, filename: str, data: bytes,
               host: str = None, port: int = None) -> dict:
        """上传文件到 TFTP 服务器 (WRQ -> ACK(0)/OACK -> DATA 分块 -> ACK)

        返回 {status, blocks, bytes} 或 {status: error, message}
        """
        host = host or self.host
        port = port or self.port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 1. WRQ (含 octet + blksize 选项)
            wrq = (OPCODE_WRQ + filename.encode("utf-8") + b"\x00"
                   + b"octet" + b"\x00" + b"blksize" + b"\x00"
                   + str(self.blksize).encode() + b"\x00")
            sock.sendto(wrq, (host, port))

            # 2. 等 ACK(0) 或 OACK
            pkt, server_addr = self._recv_packet(sock)
            opcode = pkt[0:2]
            if opcode == OPCODE_ERROR:
                return {"status": "error",
                        "message": pkt[4:].decode(errors="replace")}
            if opcode == OPCODE_OACK:
                # OACK: 确认选项, 再等 ACK(0)
                pkt, server_addr = self._recv_packet(sock)
                if pkt[0:2] != OPCODE_ACK:
                    return {"status": "error", "message": "OACK 后未收到 ACK"}
            elif opcode != OPCODE_ACK:
                return {"status": "error",
                        "message": f"未预期的响应 opcode={opcode.hex()}"}

            # 3. 逐块发 DATA, 逐块等 ACK
            block = 0
            offset = 0
            total = len(data)
            while offset < total:
                block += 1
                chunk = data[offset:offset + self.blksize]
                pkt = OPCODE_DATA + block.to_bytes(2, "big") + chunk
                sock.sendto(pkt, server_addr)
                ack, server_addr = self._recv_packet(sock)
                if ack[0:2] != OPCODE_ACK:
                    return {"status": "error", "message": "DATA 后未收到 ACK"}
                offset += self.blksize
            return {"status": "ok", "blocks": block, "bytes": total}
        except (socket.timeout, OSError) as e:
            return {"status": "error", "message": f"TFTP 上传失败: {e}"}
        finally:
            sock.close()

    def upload_fault_history(self, member: str, history: list) -> dict:
        """上传某成员的故障历史数据文件 (JSON 内容)"""
        import json
        filename = f"{member}.LUI"
        payload = json.dumps({"member_system": member, "items": history},
                             ensure_ascii=False).encode("utf-8")
        if not self.enabled:
            return {"status": "ok", "message": "TFTP 上传已禁用", "filename": filename}
        result = self.upload(filename, payload)
        if result.get("status") == "ok":
            logger.info(f"[FaultHistoryTFTP] 已上传 {filename} "
                        f"({result['bytes']} 字节, {result['blocks']} 块)")
        else:
            logger.warning(f"[FaultHistoryTFTP] 上传 {filename} 失败: {result.get('message')}")
        return {**result, "filename": filename}


# 全局单例
tftp_client = TftpClient()
