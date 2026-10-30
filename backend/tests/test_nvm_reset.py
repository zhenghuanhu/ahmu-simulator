"""
数据重置管理功能 (4.3.10) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_nvm_reset.py

覆盖场景:
    1. 配置读取: 启用 NVM 重置服务的真实成员系统列表
    2. 模式校验: 正常模式拒绝 / 维护模式可重置
    3. 成员系统校验: 未启用成员拒绝 / 非法重置类型拒绝
    4. 重置执行: 状态流转 success, 结果存储
    5. 重置日志/结果: 记录操作时间/用户/设备/状态/结果码
    6. 批量重置: 部分接受 + 部分拒绝
    7. 故障历史重置: 清除故障历史数据
    8. 超时/失败分支: 注入 timeout/fail 响应, 验证 TIMEOUT/FAILED 状态
    9. 打印: 重置结果发送至信息系统打印机
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
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def assert_cond(cond: bool, msg: str) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    return cond


def wait_log_status(member: str, size: int = 5) -> dict:
    """轮询查询某成员最近一条重置日志, 返回日志 dict 或 {}"""
    d = call("GET", f"/nvm-reset/logs?member_system={member}&page=1&size={size}")
    items = d.get("items", [])
    return items[0] if items else {}


def main():
    results = []
    print("=" * 60)
    print("数据重置管理功能 (4.3.10) 端到端测试 (对齐 ICD 真实成员名)")
    print("=" * 60)

    # 1. 配置读取
    print("\n[1] 读取配置文件, 确定启用成员系统列表")
    d = call("GET", "/nvm-reset/members")
    members = d.get("members", [])
    ok = d.get("count", 0) == 50 and "EEC_1" in members and "MEM001" not in members
    results.append(assert_cond(ok, f"启用成员系统 {d.get('count')} 个 (真实成员名, 含 EEC_1)"))

    # 2. 正常模式拒绝
    print("\n[2] 正常模式下重置被拒绝")
    d = call("POST", "/nvm-reset/reset",
             {"member_system": "EEC_1", "reset_type": "full", "operator": "TEST"})
    ok = d.get("status") == "rejected" and "非维护模式" in d.get("message", "")
    results.append(assert_cond(ok, f"正常模式拒绝: {d.get('message')}"))

    # 2.5 正常模式下生成故障 (供故障历史重置验证)
    print("\n[2.5] 正常模式下生成故障数据 (供故障历史重置)")
    call("POST", "/fault/simulate?member=FQIC&code=FQIC-001&severity=major")
    before_faults = call("GET", "/fault/reports?member=FQIC&page=1&size=10").get("total", 0)
    results.append(assert_cond(before_faults > 0, f"正常模式生成故障 {before_faults} 条"))

    # 3. 强制维护模式
    print("\n[3] 强制维护模式")
    d = call("POST", "/system/force-mode?mode=maintenance")
    results.append(assert_cond(d.get("status") == "ok", "进入维护模式"))

    # 4. 成员系统/类型校验
    print("\n[4] 成员系统与重置类型校验")
    d = call("POST", "/nvm-reset/reset",
             {"member_system": "AHMU", "reset_type": "full"})
    ok = d.get("status") == "rejected" and "未启用" in d.get("message", "")
    results.append(assert_cond(ok, f"未启用成员系统拒绝 (AHMU): {d.get('message')}"))

    d = call("POST", "/nvm-reset/reset",
             {"member_system": "EEC_2", "reset_type": "bad_type"})
    ok = d.get("status") == "rejected" and "无效的重置类型" in d.get("message", "")
    results.append(assert_cond(ok, f"非法重置类型拒绝: {d.get('message')}"))

    # 5. 重置执行
    print("\n[5] 维护模式下重置执行")
    d = call("POST", "/nvm-reset/reset",
             {"member_system": "EEC_1", "reset_type": "full", "operator": "TEST"})
    ok = d.get("status") == "ok" and d.get("reset_id", "").startswith("NVM-")
    reset_id = d.get("reset_id", "")
    results.append(assert_cond(ok, f"重置指令已下发: {reset_id}"))

    time.sleep(4)  # 等待重置完成

    # 6. 重置日志
    print("\n[6] 重置日志记录")
    top = wait_log_status("EEC_1")
    ok = bool(top) and top["status"] in ("success", "failed")
    if top:
        results.append(assert_cond(
            ok, f"日志: {top['reset_id']} 状态={top['status']} 码={top['result_code']} 用户={top['operator']}"))
    else:
        results.append(assert_cond(False, "未查询到重置日志"))

    # 7. 重置结果
    print("\n[7] 重置结果存储")
    d = call("GET", f"/nvm-reset/results?reset_id={reset_id}&page=1&size=5")
    res_items = d.get("items", [])
    ok = len(res_items) > 0 and res_items[0]["result_code"] == "0"
    if res_items:
        r = res_items[0]
        results.append(assert_cond(
            ok, f"结果: {r['member_system']} 码={r['result_code']} 校验和={r['nvm_checksum']} 耗时={r['reset_duration_ms']}ms"))
    else:
        results.append(assert_cond(False, "未查询到重置结果"))

    # 8. 批量重置
    print("\n[8] 批量重置")
    d = call("POST", "/nvm-reset/batch-reset",
             {"member_systems": ["EEC_2", "EVMU", "MEM999"],
              "reset_type": "partial", "operator": "TEST"})
    ok = d.get("accepted_count") == 2 and d.get("rejected_count") == 1
    results.append(assert_cond(
        ok, f"接受 {d.get('accepted_count')} / 拒绝 {d.get('rejected_count')}"))

    # 8.5 故障历史重置 (Fault History Reset, 清除正常模式阶段生成的故障)
    print("\n[8.5] 故障历史重置 (清除故障历史数据)")
    d = call("POST", "/nvm-reset/reset",
             {"member_system": "FQIC", "reset_type": "fault_history", "operator": "TEST"})
    fh_reset_id = d.get("reset_id", "")
    ok = d.get("status") == "ok"
    results.append(assert_cond(ok, f"故障历史重置下发: {fh_reset_id}"))
    time.sleep(4)

    # 9. 注入失败响应 (FAILED 分支)
    print("\n[9] 注入失败响应 (验证 FAILED 状态)")
    call("POST", "/nvm-reset/inject-response", {"mode": "fail"})
    call("POST", "/nvm-reset/reset",
         {"member_system": "GCU_1", "reset_type": "full", "operator": "TEST"})
    time.sleep(4)
    top = wait_log_status("GCU_1")
    ok = bool(top) and top["status"] == "failed" and top["result_code"] != "0"
    results.append(assert_cond(ok, f"失败分支: 状态={top.get('status')} 码={top.get('result_code')}"))

    # 10. 注入超时响应 (TIMEOUT 分支)
    print("\n[10] 注入超时响应 (验证 TIMEOUT 状态, 等待超时)")
    call("POST", "/nvm-reset/inject-response", {"mode": "timeout"})
    call("POST", "/nvm-reset/reset",
         {"member_system": "FDU_1", "reset_type": "full", "operator": "TEST"})
    print("  等待 11 秒 (reset_timeout=10s)...")
    time.sleep(11)
    top = wait_log_status("FDU_1")
    ok = bool(top) and top["status"] == "timeout"
    results.append(assert_cond(ok, f"超时分支: 状态={top.get('status')} (应 timeout)"))

    # 11. 恢复 auto (注入 success 验证可恢复)
    print("\n[11] 恢复 auto 响应模式")
    d = call("POST", "/nvm-reset/inject-response", {"mode": "auto"})
    results.append(assert_cond(d.get("status") == "ok", f"恢复 auto: {d.get('mode')}"))

    # 12. 打印
    print("\n[12] 打印重置结果")
    d = call("POST", f"/nvm-reset/print/{reset_id}")
    ok = d.get("status") == "ok" and bool(d.get("print_job_id"))
    results.append(assert_cond(ok, f"打印任务: {d.get('print_job_id')} (电子盘 {d.get('print_dir')})"))

    d = call("POST", "/nvm-reset/print/NVM-99999999-9999")
    ok = d.get("status") == "error"
    results.append(assert_cond(ok, f"不存在的重置号报错: {d.get('message')}"))

    # 13. 恢复正常模式 + 验证故障历史清除
    print("\n[13] 恢复正常模式 + 验证故障历史清除")
    call("POST", "/system/force-mode?mode=normal")
    time.sleep(0.5)
    d = call("GET", "/system/mode")
    results.append(assert_cond(d.get("mode") == "normal", "已恢复正常模式"))
    after = call("GET", "/fault/reports?member=FQIC&page=1&size=10").get("total", 0)
    results.append(assert_cond(after == 0, f"故障历史已清除: {before_faults} -> {after} 条"))

    # 汇总
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
