"""
飞机状态消息 接收/回传 闭环测试

覆盖:
  T1 维护模式参数注入: 发送维护开关, 校验回传 A664 帧阶段=15 + 终止标志=False
  T2 巡航模式参数注入: 发送空中巡航参数, 校验回传 A664 帧阶段=7

前置: 后端已启动 (AHMU 仿真器, 监听 8443 / UDP 34820)
运行: cd backend && venv/Scripts/python.exe tests/test_aircraft_status_rx.py
"""
import socket
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.simulate_aircraft_event import AircraftEventSender, build_params  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def recv_after_send(sender, params, wait=1.6):
    """发送参数并等待 A664 帧回传 (先清空 socket 残留, 只读最新)"""
    sender.sock.settimeout(0.05)
    try:
        while True:
            sender.sock.recvfrom(65535)
    except socket.timeout:
        pass
    sender.sock.settimeout(5.0)
    sender.send_event(params)
    time.sleep(wait)
    return sender.recv_status()


def main():
    results = []
    print("=" * 64)
    print("飞机状态消息 接收/回传 闭环测试")
    print("=" * 64)

    sender = AircraftEventSender()
    try:
        # T1 维护模式 → A664 帧阶段=15, 终止标志=False
        print("\n[T1] 维护模式参数注入")
        msg = recv_after_send(sender, build_params("maintenance"))
        ok = msg.get("flight_phase") == 15 and msg.get("global_abort_flag") is False
        results.append(assert_cond(ok, f"A664帧阶段={msg.get('flight_phase')}(应15) "
                                       f"终止标志={msg.get('global_abort_flag')}(应False)"))

        # T2 巡航模式 → A664 帧阶段=7
        print("\n[T2] 巡航模式参数注入")
        msg = recv_after_send(sender, build_params("cruise"))
        ok = msg.get("flight_phase") == 7
        results.append(assert_cond(ok, f"A664帧阶段={msg.get('flight_phase')}(应7 巡航)"))
    finally:
        sender.close()

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
