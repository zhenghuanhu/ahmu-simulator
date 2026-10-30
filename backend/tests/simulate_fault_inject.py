"""
模拟测试端批量注入故障/失效 ICD 报告 (改动2: ICD 故障/失效报告接收的对向发送端)

对齐 OHMS test_case/故障与失效报告批量创建.txt 的发送语义:
  测试端经 AFDX 发送故障/失效 ICD 消息(return_dp_dict_all_val val=0/1 生成两种状态值),
  AHMU 收到后解析生成故障/失效报告。Mock 环境用 UDP 承载 JSON。

用法:
  python tests/simulate_fault_inject.py --type failure --count 100 --member EEC_1
  python tests/simulate_fault_inject.py --type fault --count 100
"""
import argparse
import json
import socket

SEND_HOST = "127.0.0.1"
SEND_PORT = 34850  # 对齐 FAULT_RX_CONFIG.recv_port


def send_one(sock: socket.socket, kind: str, member: str, code: str,
             severity: str = "minor", value: int = 1):
    """发送一条故障/失效报告"""
    msg = {"member_system": member, "severity": severity, "value": value}
    msg["fault_code" if kind == "fault" else "failure_code"] = code
    sock.sendto(json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                (SEND_HOST, SEND_PORT))


def batch_send(kind: str, count: int, member: str = "EEC_1", prefix: str = None):
    """批量发送 count 条故障/失效报告

    kind=fault:   用故障模型代码 {member}-001~006 循环 (触发级联失效报告)
    kind=failure: 用任意失效代码 (直接入库失效报告)
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for i in range(count):
        if kind == "fault":
            # 故障模型代码: 用 5 个非级联故障 (EEC_1-001~005) 循环复用,
            # 第 6 个(EEC_1-006)为级联故障会被抑制, 批量注入避开以确保全部生成失效报告
            code = f"{member}-{(i % 5) + 1:03d}"
        else:
            prefix = prefix or f"{member}-FAILURE"
            code = f"{prefix}-{i:04d}"
        send_one(sock, kind, member, code)
    sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", default="failure", choices=["fault", "failure"])
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--member", default="EEC_1")
    args = ap.parse_args()

    batch_send(args.type, args.count, args.member)
    print(f"[发送端] 已发送 {args.count} 条 {args.type} 报告 (member={args.member})")


if __name__ == "__main__":
    main()
