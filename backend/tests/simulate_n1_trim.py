"""
模拟 FCS/EEC 发送 N1_Trim 信号 (对向测试端)

模拟机载 FCS(飞控模块)/EEC(发动机电子控制器) 经 AFDX(A664) 向 AHMU 发送
N1_Trim 信号 (Mock 环境用 UDP 承载 JSON)。

用法:
    cd backend
    ./venv/Scripts/python.exe tests/simulate_n1_trim.py --engine 1 --applied 1.5
    ./venv/Scripts/python.exe tests/simulate_n1_trim.py --engine 2 --disable 1
    ./venv/Scripts/python.exe tests/simulate_n1_trim.py --engine 3 --failure 1
"""
import argparse
import json
import socket

AHMU_HOST = "127.0.0.1"
AHMU_PORT = 34860  # 对齐 ENGINE_TRIM_RX_CONFIG.recv_port


def send_n1_trim(engine_id: int, signals: dict,
                 host: str = AHMU_HOST, port: int = AHMU_PORT) -> int:
    """发送 N1_Trim 信号到 AHMU, 返回发送字节数"""
    payload = {
        "member_system": "HF_FCM_1",
        "engine_id": engine_id,
        "signals": signals,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return sock.sendto(data, (host, port))
    finally:
        sock.close()


def build_signals(applied=None, available=None, command_left=None, command_right=None,
                  disable=None, lower=None, upper=None, failure=None) -> dict:
    """按需构建 N1_Trim 信号字典 (仅包含非 None 项)"""
    signals = {}
    if applied is not None:
        signals["N1_Trim_Applied"] = applied
    if available is not None:
        signals["N1_Trim_Available"] = available
    if command_left is not None:
        signals["N1_Trim_Command_Left"] = command_left
    if command_right is not None:
        signals["N1_Trim_Command_Right"] = command_right
    if disable is not None:
        signals["N1_Trim_Disable"] = disable
    if lower is not None:
        signals["N1_Trim_Lower_Limit"] = lower
    if upper is not None:
        signals["N1_Trim_Upper_Limit"] = upper
    if failure is not None:
        signals["Engine_Trim_Failure"] = failure
    return signals


def main():
    parser = argparse.ArgumentParser(description="模拟 FCS/EEC 发送 N1_Trim 信号")
    parser.add_argument("--engine", type=int, default=1, help="发动机编号 (1~4)")
    parser.add_argument("--applied", type=float, help="N1_Trim_Applied (-2.5~+2.5)")
    parser.add_argument("--available", type=int, choices=[0, 1], help="N1_Trim_Available")
    parser.add_argument("--command-left", type=float, dest="command_left", help="N1_Trim_Command_Left")
    parser.add_argument("--command-right", type=float, dest="command_right", help="N1_Trim_Command_Right")
    parser.add_argument("--disable", type=int, choices=[0, 1], help="N1_Trim_Disable")
    parser.add_argument("--lower", type=float, help="N1_Trim_Lower_Limit")
    parser.add_argument("--upper", type=float, help="N1_Trim_Upper_Limit")
    parser.add_argument("--failure", type=int, choices=[0, 1], help="Engine_Trim_Failure")
    args = parser.parse_args()

    signals = build_signals(
        applied=args.applied, available=args.available,
        command_left=args.command_left, command_right=args.command_right,
        disable=args.disable, lower=args.lower, upper=args.upper,
        failure=args.failure,
    )
    if not signals:
        print("[模拟端] 未提供任何信号, 请指定 --applied/--disable 等参数")
        return 1

    n = send_n1_trim(args.engine, signals)
    print(f"[模拟端] 已发送 N1_Trim 信号到 AHMU ({AHMU_HOST}:{AHMU_PORT}): "
          f"engine={args.engine} {signals} ({n} 字节)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
