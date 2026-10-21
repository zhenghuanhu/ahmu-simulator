"""
模拟打印机 (GIPC Printer) — AHMU 打印功能验证设备

用途:
    独立运行的「模拟打印机」设备, 监听 UDP 34801, 接收 AHMU 经 AFDX 发送的
    ARINC744A 打印数据, 模拟真实打印机的接收与处理流程, 用于验证 AHMU 打印功能。

消息发送与接收方式:
    - 接收: UDP 监听 127.0.0.1:34801 (对应 AFDX Printer_GIPC_Data, VL 23247),
      接收 AHMU 发送的 156 字节打印消息 (URQ / DATA)。
      消息格式: 字节0=optCode(0=URQ,1=DATA), 字节1-2=blockNum(uint16 大端),
                字节3-155=payload(URQ=文件名, DATA=数据块)。
    - 发送: UDP 发送到 127.0.0.1:34802, 回复 ACK(块号) 与作业状态 STATUS。
      (Mock 以 JSON over UDP 模拟 A429 字, 见 app/core/icd_print.py)

打印任务模拟处理流程:
    1. 接收 URQ(optCode=0): 解析文件名 + 总块数, 回 ACK(block_number=0);
    2. 接收 DATA(optCode=1): 解析块号 + 数据块, 回 ACK(block_number=块号), 收集数据块;
    3. 收满全部块(块号 >= 总块数): 拼接数据块, 保存为 ps 文件, 回 STATUS(JobStatus=0 成功);
    4. 重置状态, 等待下一个打印任务 (循环, 直到 --once 或 Ctrl+C)。

验证判断标准:
    - 打印成功: 完整收到 URQ + 全部 DATA 块(块号连续 1..N), 已逐块回 ACK,
      已回 STATUS(成功), 保存的 ps 文件内容与前端提交内容一致,
      AHMU 侧打印任务状态为 completed 且生成 ps 文件。
    - 打印失败: URQ 超时未收到 / DATA 块号不连续或缺失 / 保存内容与提交内容不一致。

用法:
    # 持续监听 (配合前端手动点击"提交打印", Ctrl+C 退出)
    venv\\Scripts\\python.exe tests\\simulate_printer.py

    # 处理单个打印任务后退出 (自动化验证)
    venv\\Scripts\\python.exe tests\\simulate_printer.py --once
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.icd_print import (  # noqa: E402
    decode_print_message, decode_feedback, encode_ack, encode_status_report,
    encode_cts, OPT_URQ, OPT_DATA, JOB_STATUS_SUCCESS,
)


class SimulatePrinter:
    """模拟打印机设备: UDP 接收 AHMU 打印数据, 回 ACK/状态, 保存 ps 文件"""

    def __init__(self, host: str, recv_port: int, send_port: int, output_dir: str,
                 recv_timeout: float = 60.0):
        self.host = host
        self.recv_port = recv_port
        self.send_port = send_port
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.bind((host, recv_port))
        self.recv_sock.settimeout(recv_timeout)
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _recv(self) -> tuple:
        """接收一条打印消息 (156 字节)"""
        return self.recv_sock.recvfrom(4096)

    def _send_ack(self, block_number: int):
        """回复打印块 ACK"""
        self.send_sock.sendto(encode_ack(block_number), (self.host, self.send_port))

    def _send_status(self, job_status: int = JOB_STATUS_SUCCESS):
        """回复作业状态"""
        self.send_sock.sendto(encode_status_report(job_status), (self.host, self.send_port))

    def handle_one_job(self) -> dict:
        """处理一个打印任务, 自动识别文件传输(URQ)或块传输(RTS), 返回处理结果"""
        data, addr = self._recv()
        # 先尝试二进制解析 (URQ/DATA, 文件传输模式)
        msg = decode_print_message(data)
        if msg and msg["opt_code"] == OPT_URQ:
            return self._handle_file_transfer(msg)
        # 再尝试 JSON 解析 (RTS, 块传输模式)
        feedback = decode_feedback(data)
        if feedback and feedback.get("type") == "RTS":
            return self._handle_block_transfer(feedback)
        return {"ok": False, "error": f"未知消息类型: {data[:50]!r}"}

    def _handle_file_transfer(self, urq_msg: dict) -> dict:
        """文件传输模式: URQ -> DATA×N(每块ACK) -> STATUS"""
        filename = urq_msg["payload"].rstrip(b"\x00").decode("utf-8", errors="replace")
        total_blocks = urq_msg["block_num"]
        self._send_ack(0)
        print(f"[模拟打印机] 收到 URQ: 文件={filename} 总块数={total_blocks} → 已回 ACK(0)")

        # DATA 分块
        blocks = []
        last_block = 0
        while True:
            data, addr = self._recv()
            msg = decode_print_message(data)
            if not msg or msg["opt_code"] != OPT_DATA:
                return {"ok": False, "error": f"应为 DATA, 实际: {msg}"}
            block_num = msg["block_num"]
            if block_num != last_block + 1:
                return {"ok": False, "error": f"块号不连续: 期望 {last_block + 1}, 实际 {block_num}"}
            blocks.append(msg["payload"])
            self._send_ack(block_num)
            print(f"[模拟打印机] 收到 DATA 块 {block_num}/{total_blocks} "
                  f"({len(msg['payload'])} 字节) → 已回 ACK({block_num})")
            last_block = block_num
            if block_num >= total_blocks:
                break

        return self._finish(filename, blocks)

    def _handle_block_transfer(self, rts_msg: dict) -> dict:
        """块传输模式: RTS -> CTS -> DATA×N(每块ACK) -> STATUS"""
        total_blocks = rts_msg.get("record_count", 0)
        filename = f"print_block_{total_blocks}.ps"
        self._send_cts()
        print(f"[模拟打印机] 收到 RTS: RECORD 数={total_blocks} → 已回 CTS")

        # DATA 分块
        blocks = []
        last_block = 0
        while True:
            data, addr = self._recv()
            msg = decode_print_message(data)
            if not msg or msg["opt_code"] != OPT_DATA:
                return {"ok": False, "error": f"应为 DATA, 实际: {msg}"}
            block_num = msg["block_num"]
            if block_num != last_block + 1:
                return {"ok": False, "error": f"块号不连续: 期望 {last_block + 1}, 实际 {block_num}"}
            blocks.append(msg["payload"])
            self._send_ack(block_num)
            print(f"[模拟打印机] 收到 DATA 块 {block_num}/{total_blocks} "
                  f"({len(msg['payload'])} 字节) → 已回 ACK({block_num})")
            last_block = block_num
            if block_num >= total_blocks:
                break

        return self._finish(filename, blocks)

    def _finish(self, filename: str, blocks: list) -> dict:
        """拼接数据块, 保存 ps 文件, 回作业状态 STATUS"""
        content = b"".join(blocks).rstrip(b"\x00")
        save_path = os.path.join(self.output_dir, filename)
        with open(save_path, "wb") as f:
            f.write(content)
        self._send_status(JOB_STATUS_SUCCESS)
        print(f"[模拟打印机] 打印完成: 保存 {save_path} ({len(content)} 字节) → 已回 STATUS(成功)")
        return {"ok": True, "filename": filename, "total_blocks": len(blocks),
                "save_path": save_path, "size": len(content)}

    def _send_cts(self):
        """回复 CTS (允许打印)"""
        self.send_sock.sendto(encode_cts(), (self.host, self.send_port))

    def run(self, once: bool = False):
        """持续监听并处理打印任务"""
        print("=" * 64)
        print(f"模拟打印机已启动 (监听 {self.host}:{self.recv_port}, 反馈 {self.host}:{self.send_port})")
        print("等待 AHMU 打印数据... (前端点击「提交打印」后触发)")
        print("=" * 64)
        while True:
            try:
                result = self.handle_one_job()
                if result["ok"]:
                    print(f"[模拟打印机] 任务处理成功: {result['filename']} ({result['size']} 字节)")
                else:
                    print(f"[模拟打印机] 任务处理失败: {result['error']}")
            except socket.timeout:
                print("[模拟打印机] 等待打印数据超时, 无任务")
                if once:
                    break
            except KeyboardInterrupt:
                print("\n[模拟打印机] 已停止")
                break
            if once:
                break
        self.close()

    def close(self):
        for sock in (self.recv_sock, self.send_sock):
            try:
                sock.close()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="模拟打印机 (AHMU 打印功能验证设备)")
    parser.add_argument("--host", default="127.0.0.1", help="监听/目标地址")
    parser.add_argument("--port", type=int, default=34801, help="接收端口 (AHMU 打印数据)")
    parser.add_argument("--send-port", type=int, default=34802, help="发送端口 (回 ACK/状态)")
    parser.add_argument("--output-dir", default=None, help="打印文件保存目录")
    parser.add_argument("--once", action="store_true", help="处理单个任务后退出")
    args = parser.parse_args()

    output_dir = args.output_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "print_received")
    printer = SimulatePrinter(args.host, args.port, args.send_port, output_dir)
    try:
        printer.run(once=args.once)
    finally:
        printer.close()


if __name__ == "__main__":
    main()
