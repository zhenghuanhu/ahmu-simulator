"""
飞控模块生命周期数据模拟发送脚本 (simulate_fcm_time_cycle.py)
================================================================

模拟飞控模块 HF_FCM_1 通过 A664(AFDX) 向 AHMU(OHMS) 周期发送生命周期数据:

    FCS_OutputData_TO_OMS_BLK_5.DS1.Time_Cycle_Status (160位 BLK 块)

对齐 ICD (Member_System_all_sender_icd_v6_1_1.xml + 数据加载.yml):
    - 成员系统: HF_FCM_1 (NameDef=HF_FLIGHT_CONTROL_MODULE_CLASS)
    - 消息名:   FCS_OutputData_TO_OMS_BLK_5
    - VLID:     21162
    - 消息大小: 28 字节
    - 周期:     400 ms
    - DstUDP:   31696

用法示例:
    # 周期发送 (默认 400ms, 10 次)
    venv\\Scripts\\python.exe tests\\simulate_fcm_time_cycle.py

    # 指定初始上电时间/循环计数 + 发送次数
    venv\\Scripts\\python.exe tests\\simulate_fcm_time_cycle.py \\
        --power-on-time 3600 --cycle-count 12 --count 20 --period 0.4

    # 无限发送 (Ctrl+C 停止)
    venv\\Scripts\\python.exe tests\\simulate_fcm_time_cycle.py --count 0

    # 指令响应模式: 监听 AHMU 生命周期获取指令, 收到后响应 (用于"真实数据"链路)
    venv\\Scripts\\python.exe tests\\simulate_fcm_time_cycle.py --listen

    # 指令响应模式 + 指定初始值
    venv\\Scripts\\python.exe tests\\simulate_fcm_time_cycle.py --listen --power-on-time 12345 --cycle-count 99
"""
import argparse
import socket
import sys
import time
from datetime import datetime

# 允许直接以脚本方式运行时导入工程内的编解码模块
sys.path.insert(0, ".") if "." not in sys.path else None
from app.core.icd_time_cycle import pack_time_cycle_message, format_power_on_time, TIME_CYCLE_MSG  # noqa: E402

# 飞控模块指令监听端口 (AHMU 下发"生命周期获取指令", 飞控模块收到后响应)
FCM_LISTEN_PORT = 31697


def parse_args():
    p = argparse.ArgumentParser(description="模拟飞控模块发送生命周期数据 (Time_Cycle_Status)")
    p.add_argument("--host", default="127.0.0.1", help="AHMU 地址 (默认 127.0.0.1)")
    p.add_argument("--port", type=int, default=TIME_CYCLE_MSG["port"], help=f"UDP 端口 (默认 {TIME_CYCLE_MSG['port']})")
    p.add_argument("--period", type=float, default=TIME_CYCLE_MSG["period_ms"] / 1000.0, help="发送周期(秒, 默认 0.4)")
    p.add_argument("--count", type=int, default=10, help="发送次数 (0=无限, 默认 10)")
    p.add_argument("--power-on-time", type=int, default=3600, help="初始上电运行时间(秒, 默认 3600)")
    p.add_argument("--cycle-count", type=int, default=12, help="初始上电循环计数(默认 12)")
    p.add_argument("--step", type=int, default=0, help="每次周期上电时间增量(秒, 默认=按周期取整, 至少1)")
    p.add_argument("--quiet", action="store_true", help="仅输出警告及以上")
    p.add_argument("--listen", action="store_true", help="指令响应模式: 监听 AHMU 获取指令后响应")
    p.add_argument("--listen-port", type=int, default=FCM_LISTEN_PORT, help=f"指令监听端口 (默认 {FCM_LISTEN_PORT})")
    return p.parse_args()


def listen_mode(args):
    """指令响应模式: 监听 AHMU 生命周期获取指令, 收到后向 AHMU 响应 Time_Cycle_Status

    对齐真实链路: AHMU 下发"生命周期获取指令"(UDP 31697) → 飞控模块响应真实数据(UDP 31696)。
    """
    member = TIME_CYCLE_MSG["member_system"]
    power_on_time = args.power_on_time
    power_cycle_count = args.cycle_count
    step = args.step if args.step > 0 else max(1, int(args.period))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.listen_port))

    print(f"=== 飞控模块 {member} 指令响应模式 (真实数据) ===")
    print(f"监听指令: {args.host}:{args.listen_port} | 响应目标: {args.host}:{args.port}")
    print(f"初始: 上电时间 {format_power_on_time(power_on_time)} ({power_on_time}s), 循环计数 {power_cycle_count}")

    try:
        while True:
            data, addr = sock.recvfrom(1024)
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[{ts}] 收到 AHMU 生命周期获取指令 来自 {addr} ({data.hex()})")
            # 上电时间按真实墙钟累加 (每次响应递增 step 秒)
            power_on_time += step
            payload = pack_time_cycle_message(power_on_time, power_cycle_count)
            sock.sendto(payload, (args.host, args.port))
            print(f"       → 响应 {member} Time_Cycle_Status → {args.host}:{args.port} "
                  f"上电时间 {format_power_on_time(power_on_time)}, 循环计数 {power_cycle_count}")
    except KeyboardInterrupt:
        print("\n已停止监听")
    finally:
        sock.close()


def main():
    args = parse_args()
    if args.listen:
        listen_mode(args)
        return

    member = TIME_CYCLE_MSG["member_system"]
    power_on_time = args.power_on_time
    power_cycle_count = args.cycle_count
    step = args.step if args.step > 0 else max(1, int(args.period))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0

    print(f"=== 飞控模块 {member} 生命周期数据模拟发送 ===")
    print(f"目标: {args.host}:{args.port} | 消息: {TIME_CYCLE_MSG['name']} "
          f"(VL {TIME_CYCLE_MSG['vl_id']}, {TIME_CYCLE_MSG['size']}字节, {TIME_CYCLE_MSG['period_ms']}ms)")
    print(f"初始: 上电时间 {format_power_on_time(power_on_time)} ({power_on_time}s), 循环计数 {power_cycle_count}")

    try:
        while args.count == 0 or sent < args.count:
            payload = pack_time_cycle_message(power_on_time, power_cycle_count)
            sock.sendto(payload, (args.host, args.port))
            sent += 1
            if not args.quiet:
                ts = datetime.now().strftime("%H:%M:%S")
                print(f"[{ts}] #{sent} 发送 {member} Time_Cycle_Status → "
                      f"{args.host}:{args.port} ({len(payload)}字节) "
                      f"上电时间 {format_power_on_time(power_on_time)}, 循环计数 {power_cycle_count}")
            # 模拟累加: 每次周期上电时间递增, 循环计数不变
            power_on_time += step
            time.sleep(args.period)
    except KeyboardInterrupt:
        print("\n已停止发送")
    finally:
        sock.close()

    print(f"发送完成: 共 {sent} 条")


if __name__ == "__main__":
    main()
