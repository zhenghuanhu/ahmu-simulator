"""
生命周期管理功能 (4.3.11) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_lifecycle.py

覆盖场景:
    1. ATA分类列表 / 设备列表 / 状态查询
    2. 正常模式下获取被拒绝
    3. 维护模式下获取成功 (上电运行时间 + 上电循环计数)
    4. 生命周期获取日志: 记录操作时间/操作用户/被操作的设备/操作状态
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
        with urllib.request.urlopen(req, timeout=20) as resp:
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

    # 1. ATA分类列表
    print("\n[1] ATA分类列表查询")
    d = call("GET", "/lifecycle/atas?page=1&size=200")
    atas = d.get("tcEquipStructList", [])
    ok = d.get("total", 0) > 0 and len(atas) > 0
    results.append(assert_cond(ok, f"ATA章节 {d.get('total')} 个"))

    # 2. 设备列表
    print("\n[2] 设备列表查询")
    d = call("GET", "/lifecycle/equips?page=1&size=16")
    equips = d.get("tcEquipStructList", [])
    ok = len(equips) > 0 and "equipID" in equips[0]
    first_equip = equips[0]["equipID"] if equips else None
    results.append(assert_cond(ok, f"设备 {d.get('total')} 个, 首个 {first_equip}"))

    # 3. 正常模式拒绝
    print("\n[3] 正常模式下获取被拒绝")
    d = call("POST", f"/lifecycle/retrieve/{first_equip}")
    ok = d.get("status") == "error" and "非维护模式" in d.get("message", "")
    results.append(assert_cond(ok, f"正常模式拒绝: {d.get('message')}"))

    # 4. 强制维护模式
    print("\n[4] 强制维护模式")
    d = call("POST", "/system/force-mode?mode=maintenance")
    results.append(assert_cond(d.get("status") == "ok", "进入维护模式"))

    # 5. 维护模式下获取
    print("\n[5] 维护模式下获取生命周期数据")
    d = call("POST", f"/lifecycle/retrieve/{first_equip}?operator=TEST")
    ok = d.get("status") == "ok" and d.get("power_on_time", 0) > 0
    results.append(assert_cond(
        ok, f"获取成功: {d.get('equip_name')} 运行={d.get('status_string')} 循环={d.get('power_cycle_count')}次"))

    # 6. 生命周期获取日志
    print("\n[6] 生命周期获取日志")
    d = call("GET", "/lifecycle/logs?page=1&size=10")
    logs = d.get("items", [])
    ok = len(logs) > 0 and logs[0]["status"] == "success" and logs[0]["operator"] == "TEST"
    if logs:
        top = logs[0]
        results.append(assert_cond(
            ok, f"日志: {top['operated_at'][:19]} 设备={top['equip_id']} 用户={top['operator']} 状态={top['status']}"))
    else:
        results.append(assert_cond(False, "未查询到获取日志"))

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
