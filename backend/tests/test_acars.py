"""
ACARS 管理功能端到端测试 (模拟下游链路 CPM+RDCU+CMU)

覆盖:
  T1 配置读取 (优先级/下传链路/触发逻辑)
  T2 配置校验 (非法优先级/下传链路/触发逻辑被拒绝)
  T3 链路状态查询 (idle/busy/lost)
  T4 端到端: 上行 EOR 请求 -> AHMU 封 A664 维护数据 -> 下游链路处理 -> 回 A664 ACK
  T5 消息记录 (上行/下行消息入库)

前置: 后端已启动 (AHMU 仿真器, 监听 8443 / UDP 34810)
运行: cd backend && venv/Scripts/python.exe tests/test_acars.py
"""
import json
import socket
import sys
import os
import threading
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from tests.simulate_acars_chain import ACARSDownstream  # noqa: E402

BASE = "http://127.0.0.1:8443/api/v1"


def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"status": "error", "message": f"HTTP {e.code}"}


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    results = []

    print("=" * 64)
    print("ACARS 管理功能端到端测试 (模拟下游链路 CPM+RDCU+CMU)")
    print("=" * 64)

    # T1 配置读取
    print("\n[1] ACARS 配置读取")
    d = call("GET", "/acars/config")
    ok = d.get("priority") is not None and "downlink_channel" in d and "trigger_mode" in d
    results.append(assert_cond(ok, f"配置齐全: priority={d.get('priority')}, "
                                   f"channel={d.get('downlink_channel')}, trigger={d.get('trigger_mode')}"))

    # T2 配置校验
    print("\n[2] 配置校验")
    d = call("POST", "/acars/config", {"priority": 2, "downlink_channel": "a429", "trigger_mode": "periodic"})
    results.append(assert_cond(d.get("status") == "ok", "合法配置保存成功 (priority=2/a429/periodic)"))
    d = call("POST", "/acars/config", {"priority": 9})
    results.append(assert_cond(d.get("status") == "error", "非法优先级 9 被拒绝"))
    d = call("POST", "/acars/config", {"downlink_channel": "bad"})
    results.append(assert_cond(d.get("status") == "error", "非法下传链路 bad 被拒绝"))
    d = call("POST", "/acars/config", {"trigger_mode": "bad"})
    results.append(assert_cond(d.get("status") == "error", "非法触发逻辑 bad 被拒绝"))
    # 恢复默认
    call("POST", "/acars/config", {"priority": 1, "downlink_channel": "a664", "trigger_mode": "on_request"})

    # T3 链路状态
    print("\n[3] 链路状态查询")
    d = call("GET", "/acars/status")
    ok = d.get("status") in ("idle", "busy", "lost")
    results.append(assert_cond(ok, f"链路状态={d.get('status')}"))

    # T4 端到端: 上行 EOR 请求 -> 下发报告 (A664 链路)
    print("\n[4] 端到端 ACARS 交互 (上行请求 -> 下发报告)")
    result = {}

    def run_downstream():
        chain = ACARSDownstream(recv_port=34811, send_port=34810, timeout=8.0)
        try:
            chain.send_uplink_eor("DNL_ACMF101/0/EOR")
            result["downstream"] = chain.handle_downlink("normal")
        except socket.timeout:
            result["downstream"] = {"ok": False, "error": "超时 (未收到 AHMU 下行)"}
        finally:
            chain.close()

    t = threading.Thread(target=run_downstream, daemon=True)
    t.start()
    time.sleep(0.5)  # 等下游发 EOR 请求
    t.join(timeout=15)
    ds = result.get("downstream", {})
    ok = bool(ds.get("ok")) and bool(ds.get("crc_ok"))
    results.append(assert_cond(ok, f"下游链路完成: crc_ok={ds.get('crc_ok')}, "
                                   f"payload_size={ds.get('payload_size')}"))

    # T5 消息记录
    print("\n[5] 消息记录")
    time.sleep(0.5)
    d = call("GET", "/acars/messages?page=1&size=20")
    total = d.get("total", 0)
    uplinks = [m for m in d.get("items", []) if m["direction"] == "uplink"]
    downlinks = [m for m in d.get("items", []) if m["direction"] == "downlink"]
    results.append(assert_cond(total > 0, f"消息已入库: total={total} (上行 {len(uplinks)} / 下行 {len(downlinks)})"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
