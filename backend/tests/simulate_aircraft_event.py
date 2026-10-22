"""
模拟飞机事件信息发送端 (ETEST 测试端) — 用于验证 AHMU 飞机状态消息接收/回传

角色: 模拟测试端, 经 AFDX(Mock UDP) 向 AHMU 发送「飞机事件信息」(含事件参数 FMS_IC_1_PARA
与 8 个双源参数), 并接收 AHMU 回传的飞机状态消息。

端口 (对齐 AIRCRAFT_STATUS_RX_CONFIG):
  发送到 34820 (AHMU 接收飞机事件信息端口)
  监听   34821 (AHMU 回传飞机状态消息端口)

用法:
  python tests/simulate_aircraft_event.py                 # 发送一次事件并收一次回传
  python tests/simulate_aircraft_event.py --fms 0 --phase normal  # 指定事件参数与阶段
"""
import argparse
import json
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SEND_PORT = 34820   # 发送到 AHMU 接收端口
RECV_PORT = 34821   # 监听 AHMU 回传端口


class AircraftEventSender:
    """模拟测试端: 发送飞机事件信息 + 接收飞机状态消息回传"""

    def __init__(self, send_port: int = SEND_PORT, recv_port: int = RECV_PORT,
                 timeout: float = 5.0):
        self.send_port = send_port
        self.recv_port = recv_port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", self.recv_port))
        self.sock.settimeout(timeout)

    def send_event(self, params: dict, fms_ic_1_para: int = 1, utc_time: str = None):
        """发送飞机事件信息 (含双源参数 + 事件参数 FMS_IC_1_PARA)"""
        msg = {"type": "aircraft_event", "params": params,
               "fms_ic_1_para": fms_ic_1_para}
        if utc_time:
            msg["utc_time"] = utc_time
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        self.sock.sendto(data, ("127.0.0.1", self.send_port))

    def recv_status(self) -> dict:
        """接收 AHMU 回传的 A664 飞机状态消息 (68 字节二进制帧) 并解析"""
        from app.core.icd_aircraft_status import unpack_aircraft_status
        data, _ = self.sock.recvfrom(65535)
        return unpack_aircraft_status(data)

    def close(self):
        self.sock.close()


def build_params(phase: str = "maintenance") -> dict:
    """按飞行阶段构造 8 个双源参数"""
    if phase == "maintenance":
        return {
            "air_ground_status": "ground",
            "airspeed": 0.0,
            "ground_speed": 0.0,
            "fcm_corrected_altitude_rate": 0.0,
            "engine_thrust_lever_angle": 0.0,
            "flight_altitude": 0.0,
            "brake_status": "applied",
            "maintenance_switch_position": "ground_test",  # 维护模式
        }
    if phase == "cruise":
        return {
            "air_ground_status": "air",
            "airspeed": 460.0,
            "ground_speed": 450.0,
            "fcm_corrected_altitude_rate": 0.0,
            "engine_thrust_lever_angle": 35.0,
            "flight_altitude": 35000.0,
            "brake_status": "released",
            "maintenance_switch_position": "normal",
        }
    # 默认: 正常模式 地面 预飞行
    return {
        "air_ground_status": "ground",
        "airspeed": 0.0,
        "ground_speed": 0.0,
        "fcm_corrected_altitude_rate": 0.0,
        "engine_thrust_lever_angle": 0.0,
        "flight_altitude": 0.0,
        "brake_status": "applied",
        "maintenance_switch_position": "normal",
    }


def main():
    parser = argparse.ArgumentParser(description="模拟飞机事件信息发送端")
    parser.add_argument("--fms", type=int, default=1, help="FMS_IC_1_PARA 事件参数值")
    parser.add_argument("--phase", default="maintenance", help="飞行阶段(maintenance/cruise/normal)")
    args = parser.parse_args()

    sender = AircraftEventSender()
    print("=" * 64)
    print(f"模拟飞机事件信息发送端 (发送到 127.0.0.1:{sender.send_port}, 监听 {sender.recv_port})")
    print(f"事件参数 FMS_IC_1_PARA={args.fms}, 阶段={args.phase}")
    print("=" * 64)

    try:
        sender.send_event(build_params(args.phase), fms_ic_1_para=args.fms)
        print("[发送端] 已发送飞机事件信息, 等待 AHMU 回传飞机状态消息...")
        msg = sender.recv_status()
        print("[发送端] 收到 A664 飞机状态消息回传 (解析后):")
        for k in ("flight_phase", "flight_leg", "global_abort_flag",
                  "icao_code", "registration", "flight_number",
                  "departure_airport", "destination_airport"):
            print(f"    {k} = {msg.get(k)}")
        print(f"    utc = {msg.get('utc')}")
    except socket.timeout:
        print("[发送端] 超时: 未收到 AHMU 回传 (请确认 AHMU 仿真器已启动)")
    finally:
        sender.close()


if __name__ == "__main__":
    main()
