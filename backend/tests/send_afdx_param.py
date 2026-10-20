"""
AFDX / ARINC 664 参数发送测试脚本

参考 ETest 测试用例《参数监控报告.py》的 send_para_data:
  通过 AFDX 通道周期发送 ICD 参数信号 (如 Selected_N1_Indicated 递增)。
数据来源于 ICD: 从 ICD 加载消息帧定义, 按信号位段编码为原始报文, 经 UDP 单播发送。

用法示例:
  # 默认: 发送 Selected_N1_Indicated 从 20 开始, 步进 1, 共 35 次, 1Hz, 到 127.0.0.1:34800
  python tests/send_afdx_param.py

  # 自定义信号/值/次数/周期/端口
  python tests/send_afdx_param.py --signal Selected_N1_Indicated --start 20 --step 1 --count 10 --period 500
  python tests/send_afdx_param.py --member HF_EEC_L_CHA --frame A664_Msg_EEC_EICAS --vl 23275 --port 34800
"""
import argparse
import os
import socket
import sys
import time

# 确保可导入 backend/app 包 (脚本位于 backend/tests/ 下)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.icd_parser import icd_parser  # noqa: E402
from app.core.icd_afdx import resolve_frame, encode_frame  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="AFDX/A664 参数发送测试脚本")
    parser.add_argument("--member", default="HF_EEC_L_CHA", help="成员系统 (ICD 成员名)")
    parser.add_argument("--frame", default="A664_Msg_EEC_EICAS", help="ICD 消息帧名")
    parser.add_argument("--vl", type=int, default=23275, help="虚拟链路 VL ID")
    parser.add_argument("--signal", default="Selected_N1_Indicated", help="要发送的信号名")
    parser.add_argument("--start", type=float, default=20.0, help="信号起始值")
    parser.add_argument("--step", type=float, default=1.0, help="每次步进值")
    parser.add_argument("--count", type=int, default=35, help="发送次数")
    parser.add_argument("--period", type=int, default=1000, help="发送周期 (毫秒)")
    parser.add_argument("--host", default="127.0.0.1", help="目标主机 (AHMU 仿真器)")
    parser.add_argument("--port", type=int, default=34800, help="目标 UDP 端口")
    args = parser.parse_args()

    # 加载 ICD (优先缓存)
    if not icd_parser.is_loaded:
        if not icd_parser.load_cache():
            print("[错误] ICD 未加载: 请先导入 ICD 或启动一次后端生成缓存")
            sys.exit(1)

    # 定位消息帧
    frame = resolve_frame(args.member, args.frame, args.vl)
    if not frame:
        print(f"[错误] 未找到消息帧: {args.member}/{args.frame} (VL {args.vl})")
        sys.exit(1)

    if args.signal not in frame.signals:
        available = list(frame.signals.keys())
        print(f"[错误] 信号 {args.signal} 不存在于帧 {frame.frame_name}")
        print(f"       该帧可用信号 (前 30 个): {available[:30]}")
        sys.exit(1)

    sig = frame.signals[args.signal]
    print("=" * 64)
    print("AFDX 参数发送")
    print(f"  成员系统 : {frame.member_system}")
    print(f"  消息帧   : {frame.frame_name} (协议 {frame.protocol}, VL {frame.vl_id})")
    print(f"  消息大小 : {frame.message_size} 字节")
    print(f"  信号     : {args.signal} (类型 {sig.data_format_type or sig.data_type}, "
          f"位偏移 {sig.bit_offset}, 位长 {sig.bit_length}, 分辨率 {sig.resolution})")
    print(f"  目标     : {args.host}:{args.port}")
    print(f"  发送策略 : 起始 {args.start}, 步进 {args.step}, 次数 {args.count}, 周期 {args.period}ms")
    print("=" * 64)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    value = args.start
    try:
        for i in range(args.count):
            data = encode_frame(frame, {args.signal: value})
            sock.sendto(data, (args.host, args.port))
            print(f"[{i + 1:>3}/{args.count}] {args.signal} = {value:g}  ({len(data)} 字节)")
            value += args.step
            if i < args.count - 1:
                time.sleep(args.period / 1000.0)
    finally:
        sock.close()
    print("发送完成")


if __name__ == "__main__":
    main()
