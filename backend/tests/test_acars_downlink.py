"""
ACARS 下行消息下发功能测试 (send_downlink / 页面「下传」按钮)

覆盖场景:
  T1 normal       正常响应: 下游链路(CPM+RDCU+CMU)逐级 ACK, 下传成功
  T2 ack-timeout  下游丢失: 不回 A664 ACK, AHMU 重发后判「A664 ACK 超时」并标记链路丢失
  T3 bad-response 异常响应: 回错误帧(数据帧) 而非 ACK, AHMU 丢弃非匹配帧后判超时

前置: 后端已启动 (AHMU 仿真器, 监听 8443 / UDP 34810)
运行: cd backend && venv/Scripts/python.exe tests/test_acars_downlink.py
"""
import json
import sys
import os
import threading
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.simulate_acars_chain import ACARSDownstream  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"


def call(method, path):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"status": "error", "message": f"HTTP {e.code}"}


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def run_scenario(mode: str, label: str, expect_ok: bool, expect_msg: str):
    """启动下游链路模拟线程 + 调用 /acars/send, 断言结果"""
    print(f"\n[{label}] 场景: {mode}")
    result = {}

    def chain_worker():
        chain = ACARSDownstream(recv_port=34811, send_port=34810)
        try:
            result["chain"] = chain.handle_downlink(mode)
        except Exception as e:  # noqa: BLE001
            result["chain"] = {"ok": False, "error": str(e)}
        finally:
            chain.close()

    t = threading.Thread(target=chain_worker, daemon=True)
    t.start()
    time.sleep(0.3)  # 等下游链路就绪

    d = call("POST", "/acars/send?message_type=test&content=%E4%B8%8B%E8%A1%8C%E6%B5%8B%E8%AF%95&priority=1")
    t.join(timeout=25)

    chain_res = result.get("chain", {})
    ok = (d.get("status") == "ok") == expect_ok
    msg_match = expect_msg in (d.get("message") or "")
    # 成功场景还校验下游收到 CRC 正确的维护数据
    if expect_ok:
        ok = ok and bool(chain_res.get("ok")) and bool(chain_res.get("crc_ok"))
    print(f"  [{'PASS' if ok and msg_match else 'FAIL'}] 后端返回 status={d.get('status')} "
          f"message={d.get('message')} | 下游: {chain_res}")
    return ok and msg_match


def main():
    results = []
    print("=" * 64)
    print("ACARS 下行消息下发功能测试 (send_downlink / A664 链路)")
    print("=" * 64)

    # T1 正常响应
    results.append(run_scenario("normal", "T1 正常响应", True, "下行已发送"))

    # T2 下游丢失 (不回 A664 ACK)
    results.append(run_scenario("ack-timeout", "T2 下游丢失", False, "A664 ACK 超时"))

    # T3 异常响应 (回数据帧而非 ACK)
    results.append(run_scenario("bad-response", "T3 异常响应", False, "A664 ACK 超时"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
