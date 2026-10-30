"""
故障诊断 - getParameter 查询接口 + ICD 故障/失效报告接收测试 (改动1 + 改动2)

对齐 OHMS test_case/故障与失效报告批量创建.txt 与 fault_diag api_libs.AhmuApi:
  测试端经 AFDX 批量注入故障/失效 ICD 消息, 经 POST /getParameter(messageID 区分)查询验证:
    - 失效报告批量: get_existing_failures(0x0106) totalCount 达标
    - 故障报告批量: get_inbound_leg_failures_all(0x0103) totalCount 达标

覆盖:
  T1 ICD 失效报告批量注入 -> get_existing_failures totalCount 增加
  T2 ICD 故障报告批量注入 -> get_inbound_leg_failures_all totalCount 增加
  T3 getParameter 各 messageID 返回结构 (0x0101/0x0102/0x0103/0x0106/0x0108)
  T4 get_failure_history 按航段/ATA 筛选
  T5 未知 messageID -> 空结果不报错

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_fault_diag_664.py
"""
import json
import sys
import time
import urllib.request

from simulate_fault_inject import batch_send

BASE = "http://127.0.0.1:8443/api/v1"


def post(path, data=None):
    url = BASE + path
    req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get_parameter(message_id, **extra):
    payload = {"messageID": message_id, "sessionID": 2, "status": 1, **extra}
    return post("/getParameter", payload)


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    results = []
    print("=" * 64)
    print("故障诊断 - getParameter 接口 + ICD 故障/失效接收测试")
    print("=" * 64)

    # T1 ICD 失效报告批量注入
    print("\n[T1] ICD 失效报告批量注入 (get_existing_failures 0x0106)")
    before = get_parameter(262)[0]["totalCount"]
    batch_send("failure", 50, member="EEC_1", prefix="EEC_1-FAILURE")
    time.sleep(2.0)  # 等待 UDP 接收处理
    after = get_parameter(262)[0]["totalCount"]
    ok = after >= before + 50
    results.append(assert_cond(ok, f"失效报告 totalCount {before} -> {after} (应增加>=50)"))

    # T2 ICD 故障报告批量注入
    print("\n[T2] ICD 故障报告批量注入 (get_inbound_leg_failures_all 0x0103)")
    before = get_parameter(259)[0]["totalCount"]
    batch_send("fault", 50, member="EEC_1", prefix="EEC_1-FAULT")
    time.sleep(2.0)
    after = get_parameter(259)[0]["totalCount"]
    ok = after >= before + 50
    results.append(assert_cond(ok, f"本航段故障 totalCount {before} -> {after} (应增加>=50)"))

    # T3 getParameter 各 messageID 返回结构
    print("\n[T3] getParameter 各 messageID 返回结构")
    checks = [
        (257, "inboundLegFDEStructList"),    # 0x0101 本航段 FDE
        (258, "existingFDEStructList"),      # 0x0102 现有 FDE
        (259, "inboundLegFailureAllStructList"),  # 0x0103 本航段失效
        (262, "existingFailureStructList"),  # 0x0106 现有失效
        (264, "failureHistoryStructList"),   # 0x0108 故障历史
    ]
    ok = True
    for mid, field in checks:
        resp = get_parameter(mid)
        has = field in resp[0] and "totalCount" in resp[0]
        ok = ok and has
        if not has:
            print(f"    [FAIL] messageID {mid} 缺 {field} 或 totalCount: {list(resp[0].keys())}")
    results.append(assert_cond(ok, "5 个 messageID 均含 totalCount + 对应列表字段"))

    # T4 get_failure_history 按航段/ATA 筛选
    print("\n[T4] get_failure_history 按航段筛选 (0x0108)")
    resp = get_parameter(264, minLeg=0, maxLeg=0, ataList="")
    ok = "totalCount" in resp[0] and "failureHistoryStructList" in resp[0]
    results.append(assert_cond(ok, f"故障历史 totalCount={resp[0]['totalCount']}"))

    # T5 未知 messageID
    print("\n[T5] 未知 messageID -> 空结果不报错")
    resp = get_parameter(9999)
    ok = isinstance(resp, list) and resp[0].get("totalCount", -1) == 0
    results.append(assert_cond(ok, f"未知 messageID 返回 {resp[0].get('message', 'totalCount=0')}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
