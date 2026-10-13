"""
模拟 air_icd_start: 通过 A664/AFDX UDP 端口发送 Aircraft_Status_Msg (维护模式控制)

用途:
    对齐 ETest《维护模式控制.py》中 AircraftStatusMessage.air_icd_start 的发送链路:
        air_icd_start(protocol_name, protocol_data, period)
          → air_send_msg: e_protocol.pack(data) 打包为二进制
          → afdx_send.write_buff(binary, {"port_id": port_id}) 经 UDP 端口发送

    本脚本模拟该链路: 构造 68 字节 Aircraft_Status_Msg (对齐
    EoICD_Publisher_Table_v6_1_1.xlsx: 端口 34896 / VL 23212 / 68 字节 / 1Hz),
    通过 UDP 发送给 AHMU, 由 AHMU 接收侧解析 flight_phase 并驱动维护模式。

用法 (后端已启动, 需先启动 AHMU 的 UDP 接收器):
    cd backend
    ./venv/Scripts/python.exe tests/simulate_aircraft_status_icd.py \
        --phase maintenance --period 1.0 --count 35

    阶段序列 (先正常 10s 再维护 35s):
    ./venv/Scripts/python.exe tests/simulate_aircraft_status_icd.py \
        --sequence engine_start:10,maintenance:35

参数:
    --host      目标地址 (默认 127.0.0.1)
    --port      目标 UDP 端口 (默认 34896 = Aircraft_Status_Msg)
    --period    发送周期秒数 (默认 1.0, 对齐 ICD 1000ms)
    --phase     飞行阶段 数字 1~15 或名称 power_on/.../maintenance (默认 15=maintenance)
    --count     发送次数 (0=无限)
    --duration  持续秒数 (0=无限)
    --sequence  阶段序列 "phase:sec,phase:sec"
    --leg       飞行航段 (默认 0)
    --icao/--reg/--flight/--dep/--dest  飞机身份信息
    --quiet     仅输出警告及以上
"""
import argparse
import logging
import os
import socket
import sys
import time

# 使脚本可独立运行时 import app.core 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.icd_aircraft_status import (  # noqa: E402
    pack_aircraft_status, AIRCRAFT_STATUS_MSG,
    DEFAULT_IDENTITY,
)

# 飞行阶段映射 (对齐 ETest __get_event_chase_number)
FLIGHT_PHASES = {
    1: "power_on", 2: "engine_start", 3: "taxi_off", 4: "takeoff_roll",
    5: "initial_climb", 6: "climb", 7: "cruise", 8: "descent",
    9: "approach", 10: "go_around", 11: "flare", 12: "rollout",
    13: "taxi_in", 14: "engine_shutdown", 15: "maintenance",
}
PHASE_NUMBERS = {v: k for k, v in FLIGHT_PHASES.items()}

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("icd-sender")


def parse_phase(value: str) -> int:
    v = value.strip()
    if v.isdigit():
        n = int(v)
        if 1 <= n <= 15:
            return n
        raise ValueError(f"飞行阶段越界: {n} (合法范围 1~15)")
    name = v.lower()
    if name in PHASE_NUMBERS:
        return PHASE_NUMBERS[name]
    raise ValueError(f"未知飞行阶段名: {v}")


def parse_sequence(value: str):
    seq = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"序列项格式错误(应为 phase:sec): {part}")
        phase_str, sec_str = part.split(":", 1)
        seq.append((parse_phase(phase_str), float(sec_str)))
    if not seq:
        raise ValueError("阶段序列为空")
    return seq


def build_payload(args, phase: int) -> bytes:
    """构造 68 字节 Aircraft_Status_Msg 载荷 (复用 ICD 编解码, 对齐权威字节定义)"""
    return pack_aircraft_status(
        flight_phase=phase,
        global_abort_flag=(phase != 15),   # 维护阶段=False, 其它=True
        flight_leg=args.leg,
        icao_code=args.icao,
        registration=args.reg,
        flight_number=args.flight,
        departure_airport=args.dep,
        destination_airport=args.dest,
        # utc 默认 None → 使用当前 UTC 时间 (对齐 ICD UBNR 数字编码)
    )


def send_one(sock: socket.socket, target, args, phase: int, seq_no: int):
    payload = build_payload(args, phase)
    phase_name = FLIGHT_PHASES.get(phase, "unknown")
    sock.sendto(payload, target)
    logger.info("[%03d] 发送 %s(flight_phase=%d) → %s:%d (%d 字节)",
                seq_no, phase_name, phase, target[0], target[1], len(payload))


def run_single(args, sock, target):
    phase = args.phase
    seq_no = 0
    start = time.time()
    try:
        while True:
            send_one(sock, target, args, phase, seq_no + 1)
            seq_no += 1
            if args.count > 0 and seq_no >= args.count:
                break
            if args.duration > 0 and time.time() - start >= args.duration:
                break
            time.sleep(args.period)
    except KeyboardInterrupt:
        logger.info("收到中断, 停止发送")
    logger.info("发送结束: 共发送 %d 条", seq_no)


def run_sequence(args, sock, target):
    seq_no = 0
    try:
        for phase, secs in args.sequence:
            phase_name = FLIGHT_PHASES.get(phase, str(phase))
            logger.info(">>> 进入阶段 %s(%d), 持续 %.1fs <<<", phase_name, phase, secs)
            end = time.time() + secs
            while time.time() < end:
                send_one(sock, target, args, phase, seq_no + 1)
                seq_no += 1
                time.sleep(args.period)
    except KeyboardInterrupt:
        logger.info("收到中断, 停止发送")
    logger.info("发送结束: 共发送 %d 条", seq_no)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="模拟 air_icd_start 经 UDP 发送 Aircraft_Status_Msg")
    p.add_argument("--host", default="127.0.0.1", help="目标地址 (默认 127.0.0.1)")
    p.add_argument("--port", type=int, default=AIRCRAFT_STATUS_MSG["port"],
                   help=f"目标 UDP 端口 (默认 {AIRCRAFT_STATUS_MSG['port']})")
    p.add_argument("--period", type=float, default=1.0, help="发送周期秒数 (默认 1.0)")
    p.add_argument("--phase", type=parse_phase, default=15,
                   help="飞行阶段 数字 1~15 或名称 (默认 15=maintenance)")
    p.add_argument("--count", type=int, default=0, help="发送次数 (0=无限)")
    p.add_argument("--duration", type=float, default=0, help="持续秒数 (0=无限)")
    p.add_argument("--sequence", type=parse_sequence, default=None,
                   help='阶段序列 "phase:sec,phase:sec" (如 engine_start:10,maintenance:35)')
    p.add_argument("--leg", type=int, default=0, help="飞行航段 (默认 0)")
    p.add_argument("--icao", default=DEFAULT_IDENTITY["icao_code"], help="ICAO 应答机码")
    p.add_argument("--reg", default=DEFAULT_IDENTITY["registration"], help="注册号")
    p.add_argument("--flight", default=DEFAULT_IDENTITY["flight_number"], help="航班号")
    p.add_argument("--dep", default=DEFAULT_IDENTITY["departure_airport"], help="出发机场")
    p.add_argument("--dest", default=DEFAULT_IDENTITY["destination_airport"], help="目的地机场")
    p.add_argument("--quiet", action="store_true", help="仅输出警告及以上日志")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    target = (args.host, args.port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

    logger.info("=== 模拟 air_icd_start 启动 ===")
    logger.info("目标=%s:%d (Aircraft_Status_Msg, VL=%d, %d字节, %dms)",
                args.host, args.port, AIRCRAFT_STATUS_MSG["vl_id"],
                AIRCRAFT_STATUS_MSG["size"], AIRCRAFT_STATUS_MSG["period_ms"])
    logger.info("周期=%.1fs", args.period)

    try:
        if args.sequence:
            logger.info("模式=阶段序列 (%d 段)", len(args.sequence))
            run_sequence(args, sock, target)
        else:
            logger.info("模式=单阶段 flight_phase=%d(%s)",
                        args.phase, FLIGHT_PHASES.get(args.phase, "unknown"))
            run_single(args, sock, target)
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
