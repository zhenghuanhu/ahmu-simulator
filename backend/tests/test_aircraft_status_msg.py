"""
飞机状态消息功能测试 (基于《飞机事件信息.py》对向发送语义)
================================================================

验证 AHMU 仿真器「飞机状态消息」功能的正确性, 覆盖四个维度:

  Part 1 (纯单元, 无需后端) —— 消息构造与字段填充 + 对向解析 + 边界
      P1.1  消息长度 = 68 字节
      P1.2  字段填充: Flight_Leg (SINT16 大端 @8)
      P1.3  字段填充: Flight_Phase (UBNR16 大端 @12)
      P1.4  字段填充: UTC 时间 (UBNR8 @16~22)
      P1.5  字段填充: ICAO_Code (UINT24 大端 @24)
      P1.6  字段填充: Global_Abort_Flag (BOOL @64)
      P1.7  字段填充: 注册号倒序交错 (ISO-5 @32~43)
      P1.8  字段填充: 航班号+机场倒序交错 (ISO-5 @44~63)
      P1.9  解析: unpack 往返一致
      P1.10 边界: 空字符串身份 / 超长截断 / 异常 ICAO / 全零帧 / 长度不足帧

  Part 2 (端到端, 需后端) —— 触发条件/流程 + 对向接收 + 边界
      P2.1  维护模式触发 → 回传 A664 帧阶段=15 + 终止标志=False
      P2.2  巡航模式触发 → 回传 A664 帧阶段=7
      P2.3  事件参数交替 (FMS_IC_1_PARA 1↔0, 对齐 3 轮循环语义)
      P2.4  1Hz 周期发布 (连续两帧阶段稳定)
      P2.5  对向接收解析 (68 字节二进制帧 → unpack 字段合法)
      P2.6  边界: 空数据 (空 params 不崩溃, 用默认值回传)
      P2.7  边界: 重复发送 (阶段稳定 + 事件报告不重复记录)
      P2.8  边界: 异常字段 (airspeed="abc" 拒绝注入, 不崩溃)

运行:
  # 仅 Part 1 (无需后端)
  cd backend && venv/Scripts/python.exe tests/test_aircraft_status_msg.py --unit
  # 全部 (Part 1 + Part 2, 需先启动后端)
  cd backend && venv/Scripts/python.exe tests/test_aircraft_status_msg.py
"""
import json
import socket
import struct
import sys
import os
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.core.icd_aircraft_status import (  # noqa: E402
    AIRCRAFT_STATUS_MSG, pack_aircraft_status, unpack_aircraft_status,
    _encode_registration, _encode_flight_airport, _parse_icao,
)
from tests.simulate_aircraft_event import AircraftEventSender, build_params  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"
UTC_SAMPLE = (22, 10, 26, 14, 30, 45)  # 22日 10月 26年 14:30:45


# ==================== 结果收集 ====================

class ResultCollector:
    def __init__(self):
        self.results = []

    def check(self, cond, label, detail=""):
        self.results.append(bool(cond))
        tag = "PASS" if cond else "FAIL"
        suffix = f" | {detail}" if detail else ""
        print(f"  [{tag}] {label}{suffix}")
        return bool(cond)

    def summary(self, title):
        passed = sum(self.results)
        total = len(self.results)
        print("\n" + "=" * 64)
        print(f"{title}: {passed}/{total} 通过")
        print("=" * 64)
        return passed == total


# ==================== Part 1: 消息构造与字段填充 (纯单元) ====================

def part1_unit(r: ResultCollector):
    print("\n" + "-" * 64)
    print("Part 1  消息构造与字段填充 + 对向解析 + 边界 (纯单元, 无需后端)")
    print("-" * 64)

    # P1.1 消息长度
    data = pack_aircraft_status(flight_phase=7, utc=UTC_SAMPLE)
    r.check(len(data) == AIRCRAFT_STATUS_MSG["size"],
            "P1.1 消息长度 = 68 字节", f"实际 {len(data)}")

    # P1.2 Flight_Leg SINT16 大端 @8~9
    data = pack_aircraft_status(flight_phase=7, flight_leg=-5, utc=UTC_SAMPLE)
    r.check(struct.unpack_from(">h", data, 8)[0] == -5,
            "P1.2 Flight_Leg SINT16 大端 @8~9", f"bytes={data[8:10].hex()}")

    # P1.3 Flight_Phase UBNR16 大端 @12~13
    data = pack_aircraft_status(flight_phase=15, utc=UTC_SAMPLE)
    r.check(struct.unpack_from(">H", data, 12)[0] == 15,
            "P1.3 Flight_Phase UBNR16 大端 @12~13", f"bytes={data[12:14].hex()}")

    # P1.4 UTC 时间 @16~22 (days/months/years/PAD/secs/mins/hours/PAD)
    data = pack_aircraft_status(flight_phase=7, utc=UTC_SAMPLE)
    d, m, y, _pad, s, mi, h, _pad2 = UTC_SAMPLE[0], UTC_SAMPLE[1], UTC_SAMPLE[2], 0, UTC_SAMPLE[5], UTC_SAMPLE[4], UTC_SAMPLE[3], 0
    ok = (data[16] == d and data[17] == m and data[18] == y
          and data[20] == s and data[21] == mi and data[22] == h
          and data[19] == 0 and data[23] == 0)
    r.check(ok, "P1.4 UTC 时间 UBNR8 @16~22 (含 2 字节 PAD)",
            f"bytes={data[16:24].hex()}")

    # P1.5 ICAO_Code UINT24 大端 @24~26
    data = pack_aircraft_status(flight_phase=7, icao_code="780123", utc=UTC_SAMPLE)
    r.check(int.from_bytes(data[24:27], "big") == 0x780123,
            "P1.5 ICAO_Code UINT24 大端 @24~26", f"bytes={data[24:27].hex()}")

    # P1.6 Global_Abort_Flag BOOL @64
    data_true = pack_aircraft_status(flight_phase=7, global_abort_flag=True, utc=UTC_SAMPLE)
    data_false = pack_aircraft_status(flight_phase=15, global_abort_flag=False, utc=UTC_SAMPLE)
    r.check(data_true[64] == 1 and data_false[64] == 0,
            "P1.6 Global_Abort_Flag BOOL @64 (True=1/False=0)")

    # P1.7 注册号倒序交错 @32~43
    reg = _encode_registration("B-001A")
    # Char_2/Char_1/StrLen(2)/Char_6/5/4/3/PAD/Char_9/8/7
    ok = (reg[0] == ord("-") and reg[1] == ord("B")
          and int.from_bytes(reg[2:4], "big") == 6
          and reg[4] == ord("A") and reg[5] == ord("1")
          and reg[6] == ord("0") and reg[7] == ord("0"))
    r.check(ok, "P1.7 注册号倒序交错 @32~43", f"bytes={reg.hex()}")

    # P1.8 航班号+机场倒序交错 @44~63
    fa = _encode_flight_airport("CXF001", "ZSPD", "ZBAA")
    # Flight_Num_Char_2/1, StrLen, Char_6/5/4/3, Dest_Char_2/1, ...
    ok = (fa[0] == ord("X") and fa[1] == ord("C")
          and int.from_bytes(fa[2:4], "big") == 6
          and fa[8] == ord("B") and fa[9] == ord("Z"))  # Dest_Char_2='B', Dest_Char_1='Z'
    r.check(ok, "P1.8 航班号+机场倒序交错 @44~63", f"bytes={fa.hex()}")

    # P1.9 unpack 往返一致
    data = pack_aircraft_status(flight_phase=7, global_abort_flag=True, flight_leg=-5,
                                icao_code="780123", registration="B-001A",
                                flight_number="CXF001", departure_airport="ZSPD",
                                destination_airport="ZBAA", utc=UTC_SAMPLE)
    d = unpack_aircraft_status(data)
    ok = (d["flight_phase"] == 7 and d["flight_leg"] == -5
          and d["global_abort_flag"] is True and d["icao_code"] == "780123"
          and d["registration"] == "B-001A" and d["flight_number"] == "CXF001"
          and d["departure_airport"] == "ZSPD" and d["destination_airport"] == "ZBAA")
    r.check(ok, "P1.9 unpack 往返一致 (阶段/航段/ICAO/身份/终止/UTC)",
            f"icao={d['icao_code']} reg={d['registration']} fn={d['flight_number']}")

    # P1.10 边界情况 (纯函数)
    print("\n  -- 边界情况 (纯函数) --")
    # 空字符串身份
    data = pack_aircraft_status(flight_phase=1, registration="", flight_number="",
                                departure_airport="", destination_airport="", utc=UTC_SAMPLE)
    d = unpack_aircraft_status(data)
    r.check(d["registration"] == "" and d["flight_number"] == "" and d["departure_airport"] == "",
            "P1.10a 空字符串身份 → unpack 返回空串")
    # 超长字符串截断
    data = pack_aircraft_status(flight_phase=1, registration="ABCDEFGHIJK", utc=UTC_SAMPLE)
    d = unpack_aircraft_status(data)
    r.check(d["registration"] == "ABCDEFGHI",
            "P1.10b 超长注册号截断为 9 字符", f"reg={d['registration']!r}")
    # 异常 ICAO
    r.check(_parse_icao("XYZ") == 0,
            "P1.10c 异常 ICAO 字符 → 解析为 0")
    # 全零帧 unpack
    d = unpack_aircraft_status(bytes(AIRCRAFT_STATUS_MSG["size"]))
    r.check(d["flight_phase"] == 0 and d["registration"] == "",
            "P1.10d 全零帧 unpack → 阶段0/身份空串")
    # 长度不足帧抛 ValueError
    try:
        unpack_aircraft_status(bytes(67))
        r.check(False, "P1.10e 长度不足帧应抛 ValueError")
    except ValueError:
        r.check(True, "P1.10e 长度不足帧抛 ValueError (正确拒绝)")


# ==================== Part 2: 触发条件/流程 + 对向接收 + 边界 (端到端) ====================

def backend_ready():
    try:
        urllib.request.urlopen(BASE + "/aircraft-status", timeout=3)
        return True
    except Exception:
        return False


def query_aircraft_status():
    with urllib.request.urlopen(BASE + "/aircraft-status", timeout=10) as r:
        return json.loads(r.read().decode())


def count_event_reports():
    """查询 EventReport 表中 fms_ic_1_para_change 记录数 (供重复发送去重校验)"""
    from app.database import SessionLocal, EventReport
    db = SessionLocal()
    try:
        return db.query(EventReport).filter(
            EventReport.event_type == "fms_ic_1_para_change").count()
    finally:
        db.close()


def clear_socket(sock):
    sock.settimeout(0.05)
    try:
        while True:
            sock.recvfrom(65535)
    except socket.timeout:
        pass


def recv_after_send(sender, params, fms=None, wait=1.6):
    """发送事件并等待 A664 帧回传 (清残留, 只读最新)"""
    clear_socket(sender.sock)
    sender.sock.settimeout(5.0)
    sender.send_event(params, fms_ic_1_para=fms)
    time.sleep(wait)
    return sender.recv_status()


def part2_e2e(r: ResultCollector):
    print("\n" + "-" * 64)
    print("Part 2  触发条件/流程 + 对向接收 + 边界 (端到端, 需后端)")
    print("-" * 64)

    if not backend_ready():
        r.check(False, "后端未就绪", "请先启动 AHMU 仿真器 (uvicorn app.main:app --port 8443)")
        return

    sender = AircraftEventSender()
    try:
        # P2.1 维护模式触发 → 阶段=15 + 终止=False
        print("\n[P2.1] 维护模式触发 (FMS_IC_1_PARA=1)")
        msg = recv_after_send(sender, build_params("maintenance"), 1)
        ok = msg.get("flight_phase") == 15 and msg.get("global_abort_flag") is False
        r.check(ok, "P2.1 维护模式 → 阶段15 + 终止标志False",
                f"phase={msg.get('flight_phase')} abort={msg.get('global_abort_flag')}")

        # P2.2 巡航模式触发 → 阶段=7
        print("\n[P2.2] 巡航模式触发")
        msg = recv_after_send(sender, build_params("cruise"), 1)
        r.check(msg.get("flight_phase") == 7, "P2.2 巡航模式 → 阶段7",
                f"phase={msg.get('flight_phase')}")

        # P2.3 事件参数交替 (1→0→1, 对齐 3 轮循环语义)
        print("\n[P2.3] 事件参数交替 (FMS_IC_1_PARA 1→0→1)")
        ok = True
        for expect in (1, 0, 1):
            recv_after_send(sender, build_params("maintenance"), expect)
            ep = query_aircraft_status().get("message", {}).get("event_params", {})
            if ep.get("fms_ic_1_para") != expect:
                ok = False
        r.check(ok, "P2.3 事件参数交替 1↔0 与注入值一致", f"最终 event_params={ep}")

        # P2.4 1Hz 周期发布 (连续两帧阶段稳定)
        print("\n[P2.4] 1Hz 周期发布 (连续两帧)")
        recv_after_send(sender, build_params("maintenance"), 1)
        clear_socket(sender.sock)
        sender.sock.settimeout(5.0)
        f1 = sender.recv_status()
        f2 = sender.recv_status()
        r.check(f1.get("flight_phase") == f2.get("flight_phase"),
                "P2.4 连续两帧阶段稳定", f"{f1.get('flight_phase')} vs {f2.get('flight_phase')}")

        # P2.5 对向接收解析 (68 字节二进制帧字段合法)
        print("\n[P2.5] 对向接收解析 (二进制帧字段合法性)")
        recv_after_send(sender, build_params("maintenance"), 1)
        clear_socket(sender.sock)
        sender.sock.settimeout(5.0)
        d = sender.recv_status()
        ok = (1 <= d.get("flight_phase", 0) <= 15
              and len(d.get("icao_code", "")) == 6
              and isinstance(d.get("flight_leg", None), int))
        r.check(ok, "P2.5 二进制帧解析字段合法 (阶段1~15/ICAO 6位/航段int)",
                f"phase={d.get('flight_phase')} icao={d.get('icao_code')} leg={d.get('flight_leg')}")

        # P2.6 边界: 空数据
        print("\n[P2.6] 边界: 空数据 (空 params)")
        msg = recv_after_send(sender, {}, None)
        ok = msg.get("flight_phase") is not None
        r.check(ok, "P2.6 空数据不崩溃, 用默认值回传", f"phase={msg.get('flight_phase')}")

        # P2.7 边界: 重复发送 (阶段稳定 + 事件报告不重复记录)
        print("\n[P2.7] 边界: 重复发送")
        before = count_event_reports()
        for _ in range(3):
            recv_after_send(sender, build_params("maintenance"), 1)
        after = count_event_reports()
        msg = recv_after_send(sender, build_params("maintenance"), 1)
        r.check(after == before and msg.get("flight_phase") == 15,
                "P2.7 重复发送阶段稳定 + 事件报告不重复",
                f"EventReport {before}->{after}, phase={msg.get('flight_phase')}")

        # P2.8 边界: 异常字段 (airspeed="abc" 拒绝注入不崩溃)
        print("\n[P2.8] 边界: 异常字段 (airspeed=\"abc\")")
        bad = dict(build_params("cruise"))
        bad["airspeed"] = "abc"
        msg = recv_after_send(sender, bad, 1)
        ok = msg.get("flight_phase") is not None
        r.check(ok, "P2.8 异常数值字段拒绝注入, 不崩溃", f"phase={msg.get('flight_phase')}")
    finally:
        sender.close()


# ==================== 入口 ====================

def main():
    import argparse
    parser = argparse.ArgumentParser(description="飞机状态消息功能测试")
    parser.add_argument("--unit", action="store_true", help="仅运行 Part 1 (纯单元, 无需后端)")
    args = parser.parse_args()

    print("=" * 64)
    print("飞机状态消息功能测试 (基于《飞机事件信息.py》对向发送语义)")
    print("=" * 64)

    r = ResultCollector()
    part1_unit(r)
    if not args.unit:
        part2_e2e(r)

    ok = r.summary("测试结果")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
