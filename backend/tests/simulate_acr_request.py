"""
模拟测试端发送 ACR 构型请求 (阶段三: ACR 请求接收与响应的对向发送端)

对齐 OHMS test_case/构型参数发送.py 的发送语义:
  测试端经 AFDX 发送 ACR 构型请求(Deal_Type 1=A429/2=A664, 关键字段 MSID),
  AHMU 收到后解析 MSID 定位成员, 回传该成员的构型数据(四类字段)。

用法:
  python tests/simulate_acr_request.py --msid EEC_1 --deal-type 2
"""
import argparse
import json
import socket

SEND_HOST = "127.0.0.1"
SEND_PORT = 34841  # 对齐 CONFIG_REPORT_RX_CONFIG.acr_request_port (AHMU 接收)


def send_acr_request(msid: str, deal_type: int = 2, hwpn: str = "",
                     ahwsn: str = "", action: str = "A",
                     recv_port: int = 34842, timeout: float = 3.0) -> dict:
    """发送 ACR 请求并接收 AHMU 回传的构型数据响应"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((SEND_HOST, recv_port))  # 绑定接收端口 (AHMU 回传到此处)
    sock.settimeout(timeout)
    msg = {
        "deal_type": deal_type, "msid": msid,
        "hwpn": hwpn, "ahwsn": ahwsn, "action": action,
    }
    sock.sendto(json.dumps(msg, ensure_ascii=False).encode("utf-8"),
                (SEND_HOST, SEND_PORT))
    try:
        data, addr = sock.recvfrom(65535)
        return json.loads(data.decode("utf-8"))
    finally:
        sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--msid", default="EEC_1")
    ap.add_argument("--deal-type", type=int, default=2, choices=[1, 2])
    args = ap.parse_args()

    resp = send_acr_request(args.msid, deal_type=args.deal_type)
    print(f"[发送端] 收到 ACR 响应: status={resp.get('status')} "
          f"member={resp.get('member_system')} deal_type={resp.get('deal_type')}")
    if resp.get("status") == "ok":
        print(f"    hardPartNumber={resp['hard'].get('hardPartNumber')} "
              f"softLocationID={resp['soft'].get('softLocationID')}")


if __name__ == "__main__":
    main()
