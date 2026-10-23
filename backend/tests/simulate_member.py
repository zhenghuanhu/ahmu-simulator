"""
模拟成员系统 (启动测试对向端) — 用于验证 AHMU 启动测试功能

角色: 模拟成员系统, 经 AFDX(Mock UDP) 接收 AHMU 启动测试指令并自动回响应。

端口 (对齐 STARTUP_TEST_CONFIG):
  监听 34831 (AHMU 发送指令端口)
  发送到 34830 (AHMU 接收成员响应端口)

场景:
  interactive          交互式正常: RUN->IN_PROGRESS->DISPLAY_PAGE->(CONTINUE)->IN_PROGRESS->完成
  interactive_suppressed 交互式抑制: CONTINUE 后回 TEST_SUPPRESSED (测试过程中被抑制)
  non_interactive      非交互式: RUN->IN_PROGRESS->测试完成(带故障报告可选)

用法:
  python tests/simulate_member.py --scenario interactive
  python tests/simulate_member.py --scenario interactive_suppressed
  python tests/simulate_member.py --scenario non_interactive --fault
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.icd_startup_test import (  # noqa: E402
    encode_message, decode_message, message_name,
    MSG_RUN_TEST, MSG_TERMINATE_TEST, MSG_CONTINUE_TEST, MSG_ACK,
    MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED, MSG_DISPLAY_PAGE,
    MSG_TEST_TERMINATED, MSG_MEMBER_STATUS,
)

SEND_PORT = 34830   # 成员系统发送响应到 AHMU 接收端口
RECV_PORT = 34831   # 成员系统监听 AHMU 指令端口


class SimulateMember:
    """模拟成员系统: 接收 AHMU 指令并自动回响应"""

    def __init__(self, member_system: str = "PAA_L3", scenario: str = "interactive",
                 with_fault: bool = False, complete_delay: float = 5.0,
                 recv_port: int = RECV_PORT, send_port: int = SEND_PORT):
        self.member_system = member_system
        self.scenario = scenario
        self.with_fault = with_fault
        self.complete_delay = complete_delay  # 继续测试后到完成之间的时长(秒), 给终止窗口
        self.recv_port = recv_port
        self.send_port = send_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.recv_port))
        self.sock.settimeout(0.5)
        self._running = False
        self._thread = None
        self.log = []

    def _send(self, msg_type: str, **fields):
        """发送响应到 AHMU"""
        data = encode_message(msg_type, member_system=self.member_system, **fields)
        self.sock.sendto(data, ("127.0.0.1", self.send_port))
        self.log.append(msg_type)
        print(f"[成员 {self.member_system}] 发送 {message_name(msg_type)}")

    def _handle(self, msg: dict):
        """处理 AHMU 指令 (非阻塞: 延迟响应用独立定时器, 保证主循环可及时接收终止指令)"""
        msg_type = msg.get("type")
        test_id = msg.get("test_id")

        if msg_type == MSG_RUN_TEST:
            print(f"[成员 {self.member_system}] 收到运行测试指令, 回测试进行中响应")
            self._send(MSG_TEST_IN_PROGRESS, test_id=test_id)
            if self.scenario in ("interactive", "interactive_suppressed"):
                # 交互式: 回显示页面响应
                threading.Timer(0.3, lambda: self._send(
                    MSG_DISPLAY_PAGE, test_id=test_id, page_id="PAGE_1")).start()
            elif self.scenario == "non_interactive":
                # 非交互式: 执行直到完成, 回测试完成标志 + 故障报告
                faults = ([{"name": "EEC 通道A 故障", "active": True}]
                          if self.with_fault else [])
                threading.Timer(0.3, lambda: self._send(
                    MSG_MEMBER_STATUS, test_id=test_id, inhibited=False,
                    test_completed=True, fault_reports=faults)).start()

        elif msg_type == MSG_CONTINUE_TEST:
            print(f"[成员 {self.member_system}] 收到继续测试指令, 响应={msg.get('response')}")
            self._send(MSG_TEST_IN_PROGRESS, test_id=test_id)
            if self.scenario == "interactive_suppressed":
                # 测试过程中被抑制
                threading.Timer(0.5, lambda: self._send(
                    MSG_TEST_SUPPRESSED, test_id=test_id,
                    reason="抑制条件激活 (维护舱门开启)")).start()
            else:
                # 继续执行一段时间 (给维护人员观察/终止窗口), 之后自然完成
                print(f"[成员 {self.member_system}] 继续执行测试中... ({self.complete_delay}s 后完成)")
                faults = ([{"name": "EEC 通道A 故障", "active": True}]
                          if self.with_fault else [])
                threading.Timer(self.complete_delay, lambda: self._send(
                    MSG_MEMBER_STATUS, test_id=test_id, inhibited=False,
                    test_completed=True, fault_reports=faults)).start()

        elif msg_type == MSG_TERMINATE_TEST:
            print(f"[成员 {self.member_system}] 收到终止指令, 回终止响应")
            self._send(MSG_TEST_TERMINATED, test_id=test_id)

    def start(self):
        """启动接收循环 (后台线程)"""
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self):
        while self._running:
            try:
                data, _ = self.sock.recvfrom(65535)
                msg = decode_message(data)
                if msg and msg.get("type") != MSG_ACK:
                    self._handle(msg)
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._running = False
        try:
            self.sock.close()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description="模拟成员系统 (启动测试对向端)")
    parser.add_argument("--member", default="PAA_L3", help="成员系统名")
    parser.add_argument("--scenario", default="interactive",
                        choices=["interactive", "interactive_suppressed", "non_interactive"],
                        help="场景")
    parser.add_argument("--fault", action="store_true", help="测试完成时带故障报告")
    args = parser.parse_args()

    member = SimulateMember(args.member, args.scenario, args.fault).start()
    print("=" * 64)
    print(f"模拟成员系统已启动: {args.member} (场景={args.scenario}, 故障={args.fault})")
    print(f"监听 AHMU 指令端口 {member.recv_port}, 发送响应到 {member.send_port}")
    print("=" * 64)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        member.stop()
        print("\n已退出")


if __name__ == "__main__":
    main()
