"""
生命周期管理功能 (4.3.11) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_lifecycle.py

覆盖场景:
    1. 成员系统列表查询
    2. 正常模式下获取成员系统生命周期被拒绝
    3. 维护模式下获取成功 (上电运行时间 + 循环上电循环计数)
    4. 生命周期获取日志: 记录操作时间/操作用户/被操作的成员系统/操作状态
    5. 主动存储: 将生命周期数据保存到指定路径
"""
import json
import time
import urllib.request
import urllib.error
from typing import Optional

BASE = "http://127.0.0.1:8443/api/v1"


def call(method: str, path: str, body: Optional[dict] = None) -> dict:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def assert_cond(cond: bool, msg: str) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    return cond


def main():
    results = []
    print("=" * 60)
    print("生命周期管理功能 (4.3.11) 端到端测试")
    print("=" * 60)

    # 1. 成员系统列表
    print("\n[1] 成员系统列表查询")
    d = call("GET", "/lifecycle/members")
    members = d.get("memberList", [])
    ok = d.get("total", 0) > 0 and any(m["memberSystem"] == "HF_HSCU" for m in members)
    results.append(assert_cond(ok, f"成员系统 {d.get('total')} 个, 含 HF_HSCU"))

    # 2. 正常模式获取被拒绝
    print("\n[2] 正常模式下获取成员系统生命周期被拒绝")
    d = call("POST", "/lifecycle/member-retrieve/HF_HSCU")
    ok = d.get("status") == "error" and "非维护模式" in d.get("message", "")
    results.append(assert_cond(ok, f"正常模式拒绝: {d.get('message')}"))

    # 3. 强制维护模式
    print("\n[3] 强制维护模式")
    d = call("POST", "/system/force-mode?mode=maintenance")
    results.append(assert_cond(d.get("status") == "ok", "进入维护模式"))

    # 4. 维护模式下获取成员系统生命周期
    print("\n[4] 维护模式下获取成员系统生命周期数据")
    d = call("POST", "/lifecycle/member-retrieve/HF_HSCU?operator=TEST")
    ok = d.get("status") == "ok" and d.get("power_on_time", 0) > 0
    results.append(assert_cond(
        ok, f"获取成功: {d.get('member_name')} 运行={d.get('status_string')} "
            f"循环上电循环计数={d.get('power_cycle_count')}次"))

    # 5. 生命周期获取日志
    print("\n[5] 生命周期获取日志")
    d = call("GET", "/lifecycle/logs?page=1&size=10")
    logs = d.get("items", [])
    ok = len(logs) > 0 and logs[0]["status"] == "success" and logs[0]["operator"] == "TEST"
    if logs:
        top = logs[0]
        results.append(assert_cond(
            ok, f"日志: {top['operated_at'][:19]} 成员系统={top['equip_id']} 用户={top['operator']} 状态={top['status']}"))
    else:
        results.append(assert_cond(False, "未查询到获取日志"))

    # 6. 主动存储 (指定成员系统)
    print("\n[6] 主动存储 (指定成员系统 HF_HSCU)")
    d = call("POST", "/lifecycle/save", {"member_system": "HF_HSCU"})
    ok = d.get("status") == "ok" and d.get("count") == 1 and bool(d.get("file_path"))
    results.append(assert_cond(ok, f"已存储 {d.get('count')} 条 → {d.get('file_path')}"))

    # 7. 恢复正常模式
    print("\n[7] 恢复正常模式")
    call("POST", "/system/signals",
         {"Switch_Normal": True, "All_Gear_WOW": False, "Voted_Calibrated_Airspeed": 90})
    time.sleep(1)
    d = call("GET", "/system/mode")
    results.append(assert_cond(d.get("mode") == "normal", "已恢复正常模式"))

    # 汇总
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
