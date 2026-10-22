"""
飞机状态消息 接收/回传 闭环测试 (飞机事件信息)

覆盖:
  T1 维护模式事件注入: 发送 FMS_IC_1_PARA=1 + 维护开关, 校验回传 A664 帧阶段=15 + 终止标志=False
  T2 事件参数翻转: 发送 FMS_IC_1_PARA=0, 校验事件参数更新 (HTTP 查询)
  T3 巡航模式事件注入: 发送空中巡航参数, 校验回传 A664 帧阶段=7
  T4 事件参数纳入消息 (HTTP 查询含 fms_ic_1_para)

前置: 后端已启动 (AHMU 仿真器, 监听 8443 / UDP 34820)
运行: cd backend && venv/Scripts/python.exe tests/test_aircraft_status_rx.py
"""
import json
import socket
import sys
import os
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.simulate_aircraft_event import AircraftEventSender, build_params  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def recv_after_send(sender, params, fms, wait=1.6):
    """发送事件并等待 A664 帧回传 (先清空 socket 残留, 只读最新)"""
    sender.sock.settimeout(0.05)
    try:
        while True:
            sender.sock.recvfrom(65535)
    except socket.timeout:
        pass
    sender.sock.settimeout(5.0)
    sender.send_event(params, fms_ic_1_para=fms)
    time.sleep(wait)
    return sender.recv_status()


def query_aircraft_status():
    """HTTP 查询最新飞机状态消息 (含扩展字段: event_params/air_ground/params)"""
    with urllib.request.urlopen(BASE + "/aircraft-status", timeout=10) as r:
        return json.loads(r.read().decode())


def main():
    results = []
    print("=" * 64)
    print("飞机状态消息 接收/回传 闭环测试")
    print("=" * 64)

    sender = AircraftEventSender()
    try:
        # T1 维护模式 + FMS=1 → A664 帧阶段=15, 终止标志=False
        print("\n[T1] 维护模式事件注入 (FMS_IC_1_PARA=1)")
        msg = recv_after_send(sender, build_params("maintenance"), 1)
        ok = msg.get("flight_phase") == 15 and msg.get("global_abort_flag") is False
        results.append(assert_cond(ok, f"A664帧阶段={msg.get('flight_phase')}(应15) "
                                       f"终止标志={msg.get('global_abort_flag')}(应False)"))

        # T2 事件参数翻转 FMS=0 (HTTP 查询 event_params)
        print("\n[T2] 事件参数翻转 (FMS_IC_1_PARA=0)")
        recv_after_send(sender, build_params("maintenance"), 0)
        d = query_aircraft_status()
        ep = d.get("message", {}).get("event_params", {})
        ok = ep.get("fms_ic_1_para") == 0
        results.append(assert_cond(ok, f"事件参数={ep} (应 fms_ic_1_para=0)"))

        # T3 巡航模式 → A664 帧阶段=7
        print("\n[T3] 巡航模式事件注入")
        msg = recv_after_send(sender, build_params("cruise"), 1)
        ok = msg.get("flight_phase") == 7
        results.append(assert_cond(ok, f"A664帧阶段={msg.get('flight_phase')}(应7 巡航)"))
    finally:
        sender.close()

    # T4 事件参数纳入消息 (HTTP 查询)
    print("\n[T4] 事件参数纳入消息")
    try:
        d = query_aircraft_status()
        msg = d.get("message", {})
        has_event = "event_params" in msg and "fms_ic_1_para" in msg.get("event_params", {})
        results.append(assert_cond(has_event, f"最新消息含事件参数: {msg.get('event_params')}"))
    except Exception as e:
        results.append(assert_cond(False, f"查询失败: {e}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
