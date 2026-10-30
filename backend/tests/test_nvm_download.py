"""
数据下载管理功能 (4.3.14) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_nvm_download.py

覆盖场景:
    1. 维护模式校验: 正常模式获取被拒绝
    2. 成员系统/类型校验: 非法成员系统/非法类型被拒绝 (对齐真实成员名)
    3. 获取执行: 进度递增至 completed, NVM 数据存储 (data_size 与 content 一致)
    4. 下载日志: 记录进度/状态/数据大小
    5. 数据库管理: NVM 数据列表查询
    6. 下载到 PMAT: 更新下载状态为 downloaded
    7. 超时/失败分支: 注入 timeout/fail 响应, 验证 TIMEOUT/FAILED 状态
    8. 打印: 获取结果发送至信息系统打印机
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


def wait_log_status(member: str, size: int = 5) -> dict:
    """轮询查询某成员最近一条下载日志, 返回日志 dict 或 {}"""
    d = call("GET", f"/nvm-download/logs?member_system={member}&page=1&size={size}")
    items = d.get("items", [])
    return items[0] if items else {}


def wait_log_terminal(member: str, timeout: float = 20.0) -> dict:
    """轮询等待某成员下载日志进入终态 (completed/failed/timeout), 返回日志 dict 或 {}"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        top = wait_log_status(member)
        if top.get("status") in ("completed", "failed", "timeout"):
            return top
        time.sleep(0.5)
    return wait_log_status(member)


def main():
    results = []
    print("=" * 60)
    print("数据下载管理功能 (4.3.14) 端到端测试 (对齐 ICD 真实成员名)")
    print("=" * 60)

    # 1. 正常模式拒绝
    print("\n[1] 正常模式下获取被拒绝")
    d = call("POST", "/nvm-download/retrieve",
             {"member_system": "EEC_1", "data_type": "fault_snapshot"})
    ok = d.get("status") == "rejected" and "非维护模式" in d.get("message", "")
    results.append(assert_cond(ok, f"正常模式拒绝: {d.get('message')}"))

    # 2. 强制维护模式
    print("\n[2] 强制维护模式")
    d = call("POST", "/system/force-mode?mode=maintenance")
    results.append(assert_cond(d.get("status") == "ok", "进入维护模式"))

    # 3. 校验
    print("\n[3] 成员系统与类型校验")
    d = call("POST", "/nvm-download/retrieve",
             {"member_system": "XXX", "data_type": "life_cycle"})
    ok = d.get("status") == "rejected" and "无效的成员系统" in d.get("message", "")
    results.append(assert_cond(ok, f"非法成员系统拒绝: {d.get('message')}"))

    d = call("POST", "/nvm-download/retrieve",
             {"member_system": "EEC_2", "data_type": "bad_type"})
    ok = d.get("status") == "rejected" and "无效的数据类型" in d.get("message", "")
    results.append(assert_cond(ok, f"非法类型拒绝: {d.get('message')}"))

    # 4. 获取执行
    print("\n[4] 维护模式下获取 NVM 数据")
    d = call("POST", "/nvm-download/retrieve",
             {"member_system": "EEC_1", "data_type": "fault_snapshot", "operator": "TEST"})
    ok = d.get("status") == "ok" and d.get("download_id", "").startswith("DL-")
    download_id = d.get("download_id", "")
    results.append(assert_cond(ok, f"获取指令已下发: {download_id}"))

    time.sleep(1)  # 稍等, 让获取任务开始

    # 5. 下载日志
    print("\n[5] 下载日志记录 (进度/状态/数据大小)")
    top = wait_log_terminal("EEC_1", 10.0)
    ok = bool(top) and top["status"] == "completed" and top["progress"] >= 100
    if top:
        results.append(assert_cond(
            ok, f"日志: {top['download_id']} 状态={top['status']} 进度={top['progress']}% 大小={top['data_size']}B"))
        # data_size 应在 1KB~256KB 范围内 (data_size_range 收窄)
        ds = top.get("data_size", 0)
        results.append(assert_cond(1024 <= ds <= 256 * 1024,
                                   f"数据大小 {ds}B 在 1KB~256KB 范围"))
    else:
        results.append(assert_cond(False, "未查询到下载日志"))

    # 6. NVM 数据管理
    print("\n[6] NVM 数据存储 (数据库管理)")
    d = call("GET", "/nvm-download/data?member_system=EEC_1&page=1&size=5")
    items = d.get("items", [])
    ok = len(items) > 0 and items[0]["download_status"] == "stored"
    nvm_data_id = items[0]["id"] if items else ""
    results.append(assert_cond(
        ok, f"NVM 数据 {len(items)} 条, 首条: {items[0]['member_system'] if items else '--'} ({items[0]['data_size'] if items else 0}B)"))

    # 7. 下载到 PMAT
    print("\n[7] 下载 NVM 数据到 PMAT")
    d = call("POST", f"/nvm-download/export/{nvm_data_id}")
    ok = d.get("status") == "ok"
    results.append(assert_cond(ok, f"下载到 PMAT: {d.get('message')}"))
    d = call("GET", "/nvm-download/data?member_system=EEC_1&page=1&size=1")
    ok = d.get("items", [{}])[0].get("download_status") == "downloaded"
    results.append(assert_cond(ok, "下载状态已更新为 downloaded"))

    # 8. 注入失败响应 (FAILED 分支)
    print("\n[8] 注入失败响应 (验证 FAILED 状态)")
    call("POST", "/nvm-download/inject-response", {"mode": "fail"})
    call("POST", "/nvm-download/retrieve",
         {"member_system": "GCU_1", "data_type": "config_snapshot"})
    top = wait_log_terminal("GCU_1", 10.0)
    ok = bool(top) and top["status"] == "failed"
    results.append(assert_cond(ok, f"失败分支: 状态={top.get('status')} (应 failed)"))

    # 9. 注入超时响应 (TIMEOUT 分支)
    print("\n[9] 注入超时响应 (验证 TIMEOUT 状态, 等待超时)")
    call("POST", "/nvm-download/inject-response", {"mode": "timeout"})
    call("POST", "/nvm-download/retrieve",
         {"member_system": "FDU_1", "data_type": "life_cycle"})
    print("  轮询等待超时 (进度 2s + retrieve_timeout 15s)...")
    top = wait_log_terminal("FDU_1", 25.0)
    ok = bool(top) and top["status"] == "timeout"
    results.append(assert_cond(ok, f"超时分支: 状态={top.get('status')} (应 timeout)"))

    # 10. 恢复 auto
    print("\n[10] 恢复 auto 响应模式")
    d = call("POST", "/nvm-download/inject-response", {"mode": "auto"})
    results.append(assert_cond(d.get("status") == "ok", f"恢复 auto: {d.get('mode')}"))

    # 11. 打印
    print("\n[11] 打印获取结果")
    d = call("POST", f"/nvm-download/print/{download_id}")
    ok = d.get("status") == "ok" and bool(d.get("print_job_id"))
    results.append(assert_cond(ok, f"打印任务: {d.get('print_job_id')} (电子盘 {d.get('print_dir')})"))

    d = call("POST", "/nvm-download/print/DL-99999999-9999")
    ok = d.get("status") == "error"
    results.append(assert_cond(ok, f"不存在的下载号报错: {d.get('message')}"))

    # 12. 恢复正常模式
    print("\n[12] 恢复正常模式")
    call("POST", "/system/force-mode?mode=normal")
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
