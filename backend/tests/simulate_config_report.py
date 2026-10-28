"""
模拟成员系统发送构型报告 (阶段二: 构型报告 AFDX 接收的对向发送端)

对齐 OHMS test_case/构型信息.py 的发送语义:
  测试端经 AFDX 发送 Config_Report_Dataset(载荷 Configuration_report) 给 AHMU。
  Mock 环境用 UDP 单播承载 JSON 构型报告 (四类字段: 硬件/特定/软件/软件部件),
  AHMU 收到后解析入库并与基本构型比对。

用法:
  python tests/simulate_config_report.py --member EEC_1 --equip 3038 --mismatch
  python tests/simulate_config_report.py --member EEC_1 --equip 3038   # 匹配基本构型
"""
import argparse
import json
import socket

SEND_HOST = "127.0.0.1"
SEND_PORT = 34840  # 对齐 CONFIG_REPORT_RX_CONFIG.recv_port


def build_report(member_system: str, equip_id: int, mismatch: bool = False) -> dict:
    """构造构型报告 JSON (mismatch=True 时硬PartNumber/序列号偏离基本构型)"""
    hard = {
        "hardPartNumber": f"{member_system}-999-99" if mismatch else f"{member_system}-100-01",
        "hardPartDescription": f"{member_system} 设备",
        "hardSerialNumber": f"SN-{member_system}-9999" if mismatch else f"SN-{member_system}-0001",
        "hardModificationStatus": "99" if mismatch else "01",
    }
    soft = {
        "softLocationID": member_system,
        "softLocationDescription": f"{member_system} Software Location",
    }
    specific = [f"{member_system}-SPEC-01", f"{member_system}-SPEC-02"]
    soft_parts = [
        {"softPartNumber": f"{member_system}-SW-01",
         "softPartDescription": f"{member_system} Software Part 01"},
        {"softPartNumber": f"{member_system}-SW-02",
         "softPartDescription": f"{member_system} Software Part 02"},
    ]
    return {
        "member_system": member_system,
        "equip_id": equip_id,
        "hard": hard,
        "specific": specific,
        "soft": soft,
        "soft_parts": soft_parts,
    }


def send_report(report: dict):
    """经 UDP 发送构型报告给 AHMU"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(json.dumps(report, ensure_ascii=False).encode("utf-8"),
                (SEND_HOST, SEND_PORT))
    sock.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--member", default="EEC_1")
    ap.add_argument("--equip", type=int, default=3038)
    ap.add_argument("--mismatch", action="store_true", help="构型偏离基本构型 (触发构型错误报告)")
    args = ap.parse_args()

    report = build_report(args.member, args.equip, mismatch=args.mismatch)
    send_report(report)
    kind = "不匹配" if args.mismatch else "匹配"
    print(f"[发送端] 已发送构型报告({kind}): {args.member} equipID={args.equip} "
          f"hardPartNumber={report['hard']['hardPartNumber']}")


if __name__ == "__main__":
    main()
