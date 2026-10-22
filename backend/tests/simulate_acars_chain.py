"""
模拟 ACARS 下游链路 (合并 CPM + RDCU + CMU) — 用于验证 AHMU 仿真器的 ACARS 下行功能

本程序合并 AHMU 仿真器之外的三个下游节点, 完整模拟 14 步下行数据流中的步骤 5~14:

  CPM (ACARS 接口应用):
    [5]  解析 AHMU 的 A664 帧, 提取结构化维护数据包
    [6]  将结构化维护数据转换为 ARINC619 协议报文
    [7]  将 ARINC619 报文重新封装成新的 A664 帧
    [8]  通过 A664 总线向下发送 (载荷=ARINC619 报文)
  RDCU (429↔664 转换):
    [9]  解析 A664 帧头, 提取 ARINC619 报文
    [10] 执行协议转换: ARINC664 -> ARINC429
    [11] 通过 A429 总线转发给 CMU
  CMU (通信终端):
    [12] 返回 A429 链路确认(ACK) 给 RDCU
    [13] RDCU 返回 A664 确认(ACK) 给 CPM
    [14] CPM 返回 A664 确认(ACK) 给 AHMU

端口 (对齐 ACARS_CONFIG):
  下游监听 34811 (AHMU 下行发送端口), 发送到 34810 (AHMU 上行接收端口)

场景 (--mode, 用于下行测试):
  normal       正常响应: 逐级 ACK, 最终 CPM 回 A664 ACK 给 AHMU
  ack-timeout  下游丢失: 不回 A664 ACK, 验证 AHMU 超时 + 链路丢失处理
  bad-response 异常响应: 回错误帧(数据帧而非 ACK), 验证 AHMU 丢弃非匹配帧后超时

用法:
  python tests/simulate_acars_chain.py                     # 持续监听 AHMU 下行
  python tests/simulate_acars_chain.py --once              # 处理一次下行后退出
  python tests/simulate_acars_chain.py --uplink DNL_ACMF101/0/EOR --once  # 发上行请求触发下行
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.icd_acars import (  # noqa: E402
    encode_a664_frame, decode_a664_frame,
    A664_MSG_DATA, A664_MSG_EOR, A664_MSG_ACK,
    encode_data_frame, decode_data_frame,  # ARINC619 (CPM 封装用)
)

DOWNSTREAM_RECV_PORT = 34811   # 下游监听 (AHMU 下行发送到该端口)
DOWNSTREAM_SEND_PORT = 34810   # 下游发送 (AHMU 上行接收该端口)


class ACARSDownstream:
    """合并 CPM + RDCU + CMU 的下游链路模拟"""

    def __init__(self, recv_port: int = DOWNSTREAM_RECV_PORT,
                 send_port: int = DOWNSTREAM_SEND_PORT, timeout: float = 10.0):
        self.recv_port = recv_port
        self.send_port = send_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.recv_port))
        self.sock.settimeout(timeout)
        self._ldu = 0
        self._file = 0

    def _send(self, data: bytes):
        self.sock.sendto(data, ("127.0.0.1", self.send_port))

    def _recv(self) -> bytes:
        data, _ = self.sock.recvfrom(65535)
        return data

    def close(self):
        self.sock.close()

    # ---------- 下行处理 (步骤 5~14) ----------

    def handle_downlink(self, mode: str = "normal") -> dict:
        """接收 AHMU 下行 A664 帧, 模拟 CPM+RDCU+CMU 处理, 最终回 A664 ACK"""
        # [5] CPM 解析 AHMU 的 A664 帧, 提取维护数据包
        data = self._recv()
        frame = decode_a664_frame(data)
        if not frame or frame["msg_type"] != A664_MSG_DATA:
            return {"ok": False, "error": f"期望维护数据帧, 实际 {frame}"}
        payload = frame["payload"]
        print(f"[CPM] 步骤5: 解析 A664 帧, 提取结构化维护数据包 ({len(payload)} 字节)")

        # [6] CPM 封装 ARINC619
        self._ldu += 1
        self._file += 1
        arinc619 = encode_data_frame(payload, self._ldu, self._file)
        print(f"[CPM] 步骤6: 将维护数据转换为 ARINC619 报文 ({len(arinc619)} 字节)")

        # [7][8] CPM 再封 A664 帧并发送
        a664_frame = encode_a664_frame(A664_MSG_DATA, arinc619)
        print("[CPM] 步骤7-8: 将 ARINC619 报文封装为 A664 帧并向下发送")

        # [9] RDCU 解析 664 提取 619
        inner = decode_a664_frame(a664_frame)
        arinc619_extracted = inner["payload"] if inner else b""
        print(f"[RDCU] 步骤9: 解析 A664 帧头, 提取 ARINC619 报文 ({len(arinc619_extracted)} 字节)")

        # [10] RDCU 664 -> 429 转换 (模拟: 提取 619 载荷, 校验 CRC)
        d619 = decode_data_frame(arinc619_extracted)
        crc_ok = d619["crc_ok"] if d619 else False
        print(f"[RDCU] 步骤10: 执行 664->429 协议转换 (619 CRC 校验: {crc_ok})")

        # [11] RDCU 经 429 发 CMU
        print("[RDCU] 步骤11: 通过 A429 总线转发给 CMU")

        # [12] CMU 回 429 ACK -> RDCU
        print("[CMU] 步骤12: 返回 A429 链路确认(ACK) 给 RDCU")

        # [13] RDCU 回 664 ACK -> CPM
        print("[RDCU] 步骤13: 返回 A664 总线确认(ACK) 给 CPM")

        # 按场景响应
        if mode == "ack-timeout":
            # 下游丢失: 不回 A664 ACK
            print("[CPM] 模拟下游丢失: 不回 A664 ACK 给 AHMU")
            return {"ok": False, "error": "已模拟 ACK 超时 (不回 A664 ACK)"}
        if mode == "bad-response":
            # 异常响应: 回错误帧(数据帧)而非 ACK
            self._send(encode_a664_frame(A664_MSG_DATA, b"bad"))
            print("[CPM] 模拟异常响应: 回错误帧(数据帧)而非 ACK")
            return {"ok": False, "error": "已回错误帧而非 A664 ACK"}

        # [14] CPM 回 664 ACK -> AHMU
        self._send(encode_a664_frame(A664_MSG_ACK, b"ACK"))
        print("[CPM] 步骤14: 返回 A664 总线确认(ACK) 给 AHMU 仿真器")
        return {"ok": True, "crc_ok": crc_ok, "payload_size": len(payload)}

    # ---------- 上行请求 (触发 AHMU 下行) ----------

    def send_uplink_eor(self, eor: str):
        """模拟上行链路 (CMU->RDCU->CPM), 最终发 A664 EOR 请求给 AHMU"""
        print("[CMU] 发 ARINC619 上行链路请求")
        print("[RDCU] 步骤: 执行 429->664 协议转换")
        print(f"[CPM] 步骤: 解析 ARINC619 请求, 转发 EOR 请求 {eor} 给 AHMU")
        self._send(encode_a664_frame(A664_MSG_EOR, eor.encode("utf-8")))


def main():
    parser = argparse.ArgumentParser(description="模拟 ACARS 下游链路 (合并 CPM+RDCU+CMU)")
    parser.add_argument("--once", action="store_true", help="处理一次后退出")
    parser.add_argument("--mode", default="normal",
                        choices=["normal", "ack-timeout", "bad-response"],
                        help="下行响应场景")
    parser.add_argument("--uplink", default=None, help="发送上行 EOR 请求 (触发 AHMU 下行)")
    parser.add_argument("--recv-port", type=int, default=DOWNSTREAM_RECV_PORT)
    parser.add_argument("--send-port", type=int, default=DOWNSTREAM_SEND_PORT)
    args = parser.parse_args()

    chain = ACARSDownstream(args.recv_port, args.send_port)
    print("=" * 64)
    print(f"模拟 ACARS 下游链路 (CPM+RDCU+CMU) 已启动 (监听 127.0.0.1:{args.recv_port})")
    print("=" * 64)

    try:
        if args.uplink:
            # 发上行请求触发下行, 然后接收 AHMU 的下行
            chain.send_uplink_eor(args.uplink)
            result = chain.handle_downlink(args.mode)
            print(f"[下游] 下行处理完成: {result}")
        elif args.once:
            result = chain.handle_downlink(args.mode)
            print(f"[下游] 下行处理完成: {result}")
        else:
            # 持续监听
            while True:
                try:
                    result = chain.handle_downlink(args.mode)
                    print(f"[下游] 下行处理完成: {result}")
                except socket.timeout:
                    print("[下游] 等待 AHMU 下行超时, 继续监听...")
                except KeyboardInterrupt:
                    print("\n[下游] 已退出")
                    break
    except socket.timeout:
        print("[下游] 超时: 未收到 AHMU 下行 (请确认 AHMU 仿真器已启动)")
    finally:
        chain.close()


if __name__ == "__main__":
    main()
