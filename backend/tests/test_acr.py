"""
构型管理 - ACR 构型请求接收与响应测试 (阶段三)

对齐 OHMS test_case/构型参数发送.py 的发送语义:
  测试端经 AFDX 发送 ACR 构型请求(Deal_Type 1=A429/2=A664, 关键字段 MSID),
  AHMU 收到后解析 MSID 定位成员, 回传该成员的构型数据(四类字段)。

覆盖:
  T1 ACR 请求(Deal_Type=2/A664) -> 收到响应, 含 member_system + 四类字段
  T2 ACR 请求(Deal_Type=1/A429) -> 收到响应
  T3 响应字段完整性 (hard/specific/soft/soft_parts)
  T4 不存在的成员 -> 无响应 (超时)

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_acr.py
"""
import socket
import sys

from simulate_acr_request import send_acr_request


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    results = []
    print("=" * 64)
    print("构型管理 - ACR 构型请求接收与响应测试 (阶段三)")
    print("=" * 64)

    # T1 ACR 请求 (A664)
    print("\n[T1] ACR 请求 (Deal_Type=2/A664, MSID=EEC_1)")
    resp = send_acr_request("EEC_1", deal_type=2, recv_port=34842, timeout=3.0)
    ok = (resp.get("status") == "ok" and resp.get("member_system") == "EEC_1"
          and resp.get("deal_type") == 2)
    results.append(assert_cond(ok, f"status={resp.get('status')} "
                                   f"member={resp.get('member_system')}"))

    # T2 ACR 请求 (A429)
    print("\n[T2] ACR 请求 (Deal_Type=1/A429, MSID=HF_FCM_1)")
    resp = send_acr_request("HF_FCM_1", deal_type=1, recv_port=34843, timeout=3.0)
    ok = resp.get("status") == "ok" and resp.get("deal_type") == 1
    results.append(assert_cond(ok, f"status={resp.get('status')} "
                                   f"member={resp.get('member_system')} deal_type={resp.get('deal_type')}"))

    # T3 响应字段完整性
    print("\n[T3] 响应字段完整性 (四类字段)")
    resp = send_acr_request("EEC_1", deal_type=2, recv_port=34844, timeout=3.0)
    ok = (resp.get("status") == "ok"
          and "hard" in resp and "hardPartNumber" in resp["hard"]
          and "specific" in resp and isinstance(resp["specific"], list)
          and "soft" in resp and "softLocationID" in resp["soft"]
          and "soft_parts" in resp and isinstance(resp["soft_parts"], list))
    results.append(assert_cond(ok, f"hard={resp.get('hard',{}).get('hardPartNumber')} "
                                   f"soft={resp.get('soft',{}).get('softLocationID')}"))

    # T4 不存在的成员 -> 无响应 (超时)
    print("\n[T4] 不存在的成员 -> 无响应 (超时)")
    try:
        resp = send_acr_request("UNKNOWN_MEMBER", deal_type=2, recv_port=34845, timeout=2.0)
        ok = False  # 不应收到响应
        results.append(assert_cond(ok, f"意外收到响应: {resp}"))
    except (socket.timeout, TimeoutError):
        results.append(assert_cond(True, "未收到响应 (符合预期)"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
