"""
发动机配平功能 (4.3.9) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_engine_trim.py

覆盖场景 (对齐 ICD N1_Trim 信号):
    1. 数据校验: 发动机编号越界 / 配平值越界(±2.5% N1) / 类型非法(n1) / 通道非法(left/right) / 来源非法
    2. 三来源指令下发: ground / cockpit / pmat (含左右配平通道)
    3. 并发限制: 同一发动机重复指令被拒绝
    4. 配平执行: 指令状态流转至 completed, 配平数据到位 (N1_Trim_Applied)
    5. 数据查询: N1_Trim 配平数据 / 指令列表(含 engine_side) / 整体状态
"""
import json
import time
import urllib.request
import urllib.error
from typing import Optional

BASE = "http://127.0.0.1:8443/api/v1"


def call(method: str, path: str, body: Optional[dict] = None) -> dict:
    """调用 REST API, 返回解析后的 JSON"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def assert_cond(cond: bool, msg: str):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    return cond


def main():
    results = []
    print("=" * 60)
    print("发动机配平功能端到端测试 (对齐 ICD N1_Trim)")
    print("=" * 60)

    # 1. 数据校验
    print("\n[1] 数据校验")
    r = call("POST", "/engine-trim/command",
             {"engine_id": 9, "trim_type": "n1", "target_trim": 1.0, "source": "ground"})
    results.append(assert_cond(r["status"] == "error", f"发动机编号越界拒绝: {r.get('message')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "n1", "target_trim": 3.0, "source": "ground"})
    results.append(assert_cond(r["status"] == "error", f"配平值越界拒绝(±2.5% N1): {r.get('message')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "thrust", "target_trim": 1.0, "source": "ground"})
    results.append(assert_cond(r["status"] == "error", f"配平类型非法拒绝(仅 n1): {r.get('message')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "n1", "engine_side": "middle",
              "target_trim": 1.0, "source": "ground"})
    results.append(assert_cond(r["status"] == "error", f"配平通道非法拒绝(left/right): {r.get('message')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "n1", "target_trim": 1.0, "source": "xxx"})
    results.append(assert_cond(r["status"] == "error", f"来源非法拒绝: {r.get('message')}"))

    # 2. 三来源指令下发 (含左右通道)
    print("\n[2] 三来源指令下发 (ground/cockpit/pmat, 含左右通道)")
    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "n1", "engine_side": "left",
              "target_trim": 1.5, "source": "ground"})
    results.append(assert_cond(r["status"] == "ok", f"ground(left) 指令下发: {r.get('command_id')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 2, "trim_type": "n1", "engine_side": "right",
              "target_trim": 2.0, "source": "cockpit"})
    results.append(assert_cond(r["status"] == "ok", f"cockpit(right) 指令下发: {r.get('command_id')}"))

    r = call("POST", "/engine-trim/command",
             {"engine_id": 3, "trim_type": "n1", "target_trim": -1.0, "source": "pmat"})
    results.append(assert_cond(r["status"] == "ok", f"pmat(left默认) 指令下发: {r.get('command_id')}"))

    # 3. 并发限制
    print("\n[3] 并发限制")
    r = call("POST", "/engine-trim/command",
             {"engine_id": 1, "trim_type": "n1", "target_trim": 0.5, "source": "cockpit"})
    results.append(assert_cond(r["status"] == "error", f"同发动机并发拒绝: {r.get('message')}"))

    # 4. 等待配平执行完成
    print("\n[4] 配平执行 (等待 EMU 配平到位, 周期 200ms)")
    print("  等待 8 秒...")
    time.sleep(8)

    cmds = call("GET", "/engine-trim/commands")
    completed = {c["engine_id"]: c for c in cmds["items"] if c["status"] == "completed"}
    for eid in (1, 2, 3):
        c = completed.get(eid)
        ok = c is not None and c["applied_trim"] == c["target_trim"]
        results.append(assert_cond(ok, f"发动机{eid} 配平完成 (applied={c['applied_trim'] if c else None})"))

    # 5. 数据查询 (N1_Trim 字段对齐)
    print("\n[5] 数据查询")
    for eid, target in ((1, 1.5), (2, 2.0), (3, -1.0)):
        d = call("GET", f"/engine-trim/data?engine_id={eid}")["data"]
        ok = (d is not None
              and abs(d["trim_value"] - target) < 0.01
              and d["trim_status"] == "applied"
              and "n1_trim_applied" in d and abs(d["n1_trim_applied"] - target) < 0.01)
        results.append(assert_cond(
            ok, f"发动机{eid} N1 配平数据: n1_trim_applied={d.get('n1_trim_applied') if d else None}, "
                f"status={d.get('trim_status') if d else None}"))

    # 指令列表含 engine_side (items 按时间倒序, 取每台发动机最新一条)
    side_map = {}
    for c in cmds["items"]:
        if c["engine_id"] not in side_map:
            side_map[c["engine_id"]] = c.get("engine_side")
    results.append(assert_cond(
        side_map.get(2) == "right", f"指令列表 engine_side: 发动机2={side_map.get(2)} (应 right)"))

    st = call("GET", "/engine-trim/status")
    results.append(assert_cond(st["running"] and st["engine_count"] == 4,
                               f"整体状态: running={st['running']}, engine_count={st['engine_count']}"))

    # 汇总
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    exit(main())
