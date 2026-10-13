"""
模拟测试台/成员系统周期发送飞机状态消息 (维护模式控制 —— 接收侧)

用途:
    模拟其他设备(测试台/成员系统)经 AFDX(A664) 向 AHMU 仿真器周期发送
    飞机状态消息, 用于验证 AHMU 接收侧维护模式控制逻辑。
    对齐 ETest《维护模式控制.py》: flight_phase=15(maintenance) 触发维护模式,
    1~14 为正常飞行阶段; 维护模式需持续 30s 才生效。

用法 (后端已启动):
    cd backend

    # 单阶段持续发送 (维护阶段, 每 1s 一次, 共 35 次)
    ./venv/Scripts/python.exe tests/simulate_aircraft_status_sender.py \
        --device PAA_L3 --phase maintenance --period 1.0 --count 35

    # 阶段序列 (先正常 engine_start 10s, 再维护 maintenance 35s)
    ./venv/Scripts/python.exe tests/simulate_aircraft_status_sender.py \
        --sequence engine_start:10,maintenance:35

    # 仅看关键日志
    ./venv/Scripts/python.exe tests/simulate_aircraft_status_sender.py --quiet

飞行阶段 (数字 1~15 或名称):
    1 power_on / 2 engine_start / 3 taxi_off / 4 takeoff_roll / 5 initial_climb
    6 climb / 7 cruise / 8 descent / 9 approach / 10 go_around / 11 flare
    12 rollout / 13 taxi_in / 14 engine_shutdown / 15 maintenance
"""
import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional

# 默认目标 (AHMU 后端)
DEFAULT_BASE = "http://127.0.0.1:8443/api/v1"

# 飞行阶段映射 (对齐 ETest __get_event_chase_number)
FLIGHT_PHASES = {
    1: "power_on", 2: "engine_start", 3: "taxi_off", 4: "takeoff_roll",
    5: "initial_climb", 6: "climb", 7: "cruise", 8: "descent",
    9: "approach", 10: "go_around", 11: "flare", 12: "rollout",
    13: "taxi_in", 14: "engine_shutdown", 15: "maintenance",
}
PHASE_NUMBERS = {v: k for k, v in FLIGHT_PHASES.items()}

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sim-sender")


# ---------------- HTTP 请求 ----------------

def call(method: str, path: str, body: Optional[dict] = None, base: str = DEFAULT_BASE) -> dict:
    """发送 HTTP 请求并返回 JSON 响应 (网络/HTTP 错误返回 {status:error})"""
    url = base + path
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"status": "error", "message": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"网络错误: {e.reason}"}


# ---------------- 飞行阶段解析 ----------------

def parse_phase(value: str) -> int:
    """解析飞行阶段参数: 数字(1~15) 或名称(power_on/.../maintenance)"""
    v = value.strip()
    if v.isdigit():
        n = int(v)
        if 1 <= n <= 15:
            return n
        raise ValueError(f"飞行阶段越界: {n} (合法范围 1~15)")
    name = v.lower()
    if name in PHASE_NUMBERS:
        return PHASE_NUMBERS[name]
    raise ValueError(f"未知飞行阶段名: {v} (可选 {list(PHASE_NUMBERS)})")


def parse_sequence(value: str):
    """解析阶段序列 "phase:sec,phase:sec" → [(phase:int, sec:float), ...]"""
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


# ---------------- 消息构造 ----------------

def build_message(args, phase: int) -> dict:
    """构造一条飞机状态消息 (字段对齐 OHMS 飞机状态消息)"""
    return {
        "device": args.device,                    # 设备标识 (测试台/成员系统)
        "flight_phase": phase,                     # 飞行阶段 (1~15)
        "flight_leg": args.leg,                    # 飞行航段
        "icao_code": args.icao,                    # ICAO 应答机码
        "registration": args.reg,                  # 注册号
        "flight_number": args.flight,              # 航班号
        "departure_airport": args.dep,             # 出发机场
        "destination_airport": args.dest,          # 目的地机场
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---------------- 发送 ----------------

def send_one(base: str, msg: dict, seq_no: int) -> dict:
    """发送一条消息并打印清晰日志, 返回响应"""
    phase = msg["flight_phase"]
    phase_name = FLIGHT_PHASES.get(phase, "unknown")
    resp = call("POST", "/maintenance/receive-aircraft-status", msg, base)

    if resp.get("status") == "ok":
        current = resp.get("current_mode", "?")
        hold = resp.get("hold_remaining")
        entered = resp.get("entered_maintenance")
        extra = f"当前模式={current}"
        if hold:
            extra += f", 维护剩余={hold}s"
        if entered:
            extra += " (已进入维护模式)"
        logger.info(
            "[%03d] 发送 flight_phase=%d(%s) → ok | %s",
            seq_no, phase, phase_name, extra,
        )
    else:
        logger.warning(
            "[%03d] 发送 flight_phase=%d(%s) → 失败: %s",
            seq_no, phase, phase_name, resp.get("message", resp),
        )
    return resp


def run_single(args, base: str):
    """单阶段持续发送 (--phase)"""
    phase = args.phase
    seq_no = 0
    start = time.time()
    try:
        while True:
            msg = build_message(args, phase)
            send_one(base, msg, seq_no + 1)
            seq_no += 1

            # 停止条件
            if args.count > 0 and seq_no >= args.count:
                break
            if args.duration > 0 and time.time() - start >= args.duration:
                break
            time.sleep(args.period)
    except KeyboardInterrupt:
        logger.info("收到中断, 停止发送")
    logger.info("发送结束: 共发送 %d 条", seq_no)


def run_sequence(args, base: str):
    """阶段序列发送 (--sequence)"""
    seq = args.sequence
    seq_no = 0
    try:
        for phase, secs in seq:
            phase_name = FLIGHT_PHASES.get(phase, str(phase))
            logger.info(">>> 进入阶段 %s(%d), 持续 %.1fs <<<", phase_name, phase, secs)
            end = time.time() + secs
            while time.time() < end:
                msg = build_message(args, phase)
                send_one(base, msg, seq_no + 1)
                seq_no += 1
                time.sleep(args.period)
    except KeyboardInterrupt:
        logger.info("收到中断, 停止发送")
    logger.info("发送结束: 共发送 %d 条", seq_no)


# ---------------- 主入口 ----------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="模拟测试台/成员系统周期发送飞机状态消息 (维护模式控制接收侧)")
    p.add_argument("--host", default="127.0.0.1:8443",
                   help="AHMU 后端地址 host:port (默认 127.0.0.1:8443)")
    p.add_argument("--period", type=float, default=1.0,
                   help="发送周期秒数 (默认 1.0)")
    p.add_argument("--device", default="PAA_L3",
                   help="设备标识 (默认 PAA_L3, 对齐 ETest cpm_equipment_test_list)")
    p.add_argument("--phase", type=parse_phase, default=15,
                   help="飞行阶段: 数字 1~15 或名称 power_on/.../maintenance (默认 15=maintenance)")
    p.add_argument("--count", type=int, default=0,
                   help="发送次数 (0=无限, 默认 0)")
    p.add_argument("--duration", type=float, default=0,
                   help="持续发送秒数 (0=无限, 默认 0)")
    p.add_argument("--sequence", type=parse_sequence, default=None,
                   help='阶段序列 "phase:sec,phase:sec" (如 engine_start:10,maintenance:35, 优先于 --phase)')
    p.add_argument("--leg", type=int, default=0, help="飞行航段 (默认 0)")
    p.add_argument("--icao", default="780123", help="ICAO 应答机码")
    p.add_argument("--reg", default="B-001A", help="注册号")
    p.add_argument("--flight", default="CXF001", help="航班号")
    p.add_argument("--dep", default="ZSPD", help="出发机场")
    p.add_argument("--dest", default="ZBAA", help="目的地机场")
    p.add_argument("--quiet", action="store_true", help="仅输出警告及以上日志")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    base = f"http://{args.host}/api/v1"

    # 连通性预检
    probe = call("GET", "/system/mode", base=base)
    if probe.get("status") == "error":
        logger.error("无法连接 AHMU 后端 %s: %s", args.host, probe.get("message", "未知错误"))
        return 1
    logger.info("=== 模拟发送器启动 ===")
    logger.info("目标=%s 设备=%s 周期=%.1fs", base, args.device, args.period)

    if args.sequence:
        logger.info("模式=阶段序列 (%d 段)", len(args.sequence))
        run_sequence(args, base)
    else:
        phase_name = FLIGHT_PHASES.get(args.phase, "unknown")
        logger.info("模式=单阶段 flight_phase=%d(%s)", args.phase, phase_name)
        run_single(args, base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
