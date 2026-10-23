"""
启动测试功能测试 (ARINC624 启动测试交互通信)

覆盖:
  T1 前置条件检查: 非地面/非维护开关位置 → 拒绝发起
  T2 交互式正常场景: RUN->进行中->显示页面->维护人员响应->继续->完成(pass)
  T3 交互式抑制场景: 测试过程中被抑制 → suppressed
  T4 非交互式场景(带故障): 测试完成 + 故障报告 → fail
  T5 非交互式场景(无故障): 测试完成 → pass
  T6 初始抑制 → 清除 → 运行: 初始被抑制 → 清除后继续 → 完成
  T7 终止指令: 终止进行中的测试 → terminated

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_startup_test.py
"""
import json
import sys
import os
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.simulate_member import SimulateMember  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"
MEMBER = "PAA_L3"


def call(method, path, data=None):
    """HTTP 调用辅助"""
    url = BASE + path
    if data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method=method)
    else:
        req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def post(path):
    return call("POST", path)


def get(path):
    return call("GET", path)


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def force_mode(mode="maintenance"):
    return post(f"/system/force-mode?mode={mode}")


def inject_member_status(inhibited=False, test_completed=False, fault=False):
    faults = json.dumps([{"name": "EEC 通道A 故障", "active": True}]) if fault else "[]"
    return post(f"/groundtest/member-status?member={MEMBER}&inhibited="
                f"{str(inhibited).lower()}&test_completed={str(test_completed).lower()}"
                f"&fault_reports={urllib.parse.quote(faults)}")


def start_test(test_type="interactive"):
    return post(f"/groundtest/start?member={MEMBER}&test_type={test_type}")


def poll_test(test_id, timeout=20.0):
    """轮询测试记录直到结束, 返回记录 dict"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = get(f"/groundtest/list?page=1&size=50&member={MEMBER}")
        for item in d.get("items", []):
            if item["id"] == test_id:
                if item["status"] in ("completed", "suppressed", "terminated", "aborted"):
                    return item
        time.sleep(0.5)
    return None


def main():
    import urllib.parse
    results = []
    print("=" * 64)
    print("启动测试功能测试 (ARINC624 启动测试交互)")
    print("=" * 64)

    # T1 前置条件检查 (强制正常模式: 空中/开关正常 → 不满足)
    print("\n[T1] 前置条件检查 (非地面/开关非测试位置 → 拒绝)")
    force_mode("normal")
    time.sleep(0.3)
    r = start_test("interactive")
    ok = r.get("status") == "error" and "前置条件" in r.get("message", "")
    results.append(assert_cond(ok, f"发起被拒绝: {r.get('message')}"))

    # 恢复维护模式 (地面 + 开关=地面测试)
    force_mode("maintenance")
    time.sleep(0.3)

    # T2 交互式正常场景
    print("\n[T2] 交互式正常场景 (RUN->进行中->显示页面->响应->继续->完成)")
    member = SimulateMember(MEMBER, scenario="interactive").start()
    r = start_test("interactive")
    assert r.get("status") == "ok", f"发起失败: {r}"
    test_id = r["test_id"]
    time.sleep(1.0)  # 等待走到 waiting_user (显示页面)
    # 维护人员提交响应
    resp = post(f"/groundtest/{test_id}/ack")
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "completed" and item["result"] == "pass"
    results.append(assert_cond(ok, f"交互式测试完成: status={item and item['status']} "
                                   f"result={item and item['result']}"))
    member.stop()

    # T3 交互式抑制场景
    print("\n[T3] 交互式抑制场景 (测试过程中被抑制 → suppressed)")
    member = SimulateMember(MEMBER, scenario="interactive_suppressed").start()
    r = start_test("interactive")
    test_id = r["test_id"]
    time.sleep(1.0)
    post(f"/groundtest/{test_id}/ack")  # 维护人员响应
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "suppressed" and item["result"] == "abort"
    results.append(assert_cond(ok, f"测试被抑制: status={item and item['status']} "
                                   f"reason={item and item.get('suppression_reason')}"))
    member.stop()

    # T4 非交互式场景 (带故障 → fail)
    print("\n[T4] 非交互式场景 (带故障报告 → fail)")
    member = SimulateMember(MEMBER, scenario="non_interactive", with_fault=True).start()
    r = start_test("non_interactive")
    test_id = r["test_id"]
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "completed" and item["result"] == "fail"
    results.append(assert_cond(ok, f"非交互式(故障)完成: result={item and item['result']}"))
    member.stop()

    # T5 非交互式场景 (无故障 → pass)
    print("\n[T5] 非交互式场景 (无故障 → pass)")
    member = SimulateMember(MEMBER, scenario="non_interactive", with_fault=False).start()
    r = start_test("non_interactive")
    test_id = r["test_id"]
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "completed" and item["result"] == "pass"
    results.append(assert_cond(ok, f"非交互式(无故障)完成: result={item and item['result']}"))
    member.stop()

    # T6 初始抑制 → 清除 → 运行
    print("\n[T6] 初始抑制 → 清除 → 运行")
    member = SimulateMember(MEMBER, scenario="non_interactive").start()
    inject_member_status(inhibited=True)  # 初始抑制
    r = start_test("non_interactive")
    test_id = r["test_id"]
    time.sleep(1.0)  # 状态机等待清除
    inject_member_status(inhibited=False)  # 清除抑制
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "completed"
    results.append(assert_cond(ok, f"抑制清除后测试完成: status={item and item['status']}"))
    member.stop()

    # T7 终止指令
    print("\n[T7] 终止指令 (终止进行中的测试 → terminated)")
    member = SimulateMember(MEMBER, scenario="interactive").start()
    r = start_test("interactive")
    test_id = r["test_id"]
    time.sleep(1.0)
    post(f"/groundtest/{test_id}/terminate")
    item = poll_test(test_id)
    ok = item is not None and item["status"] == "terminated"
    results.append(assert_cond(ok, f"测试已终止: status={item and item['status']}"))
    member.stop()

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)

    # 恢复系统模式 (避免污染后续测试)
    try:
        force_mode("normal")
    except Exception:
        pass

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
