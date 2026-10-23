"""
故障历史检索测试 (启动测试服务 / 199 成员启动测试)

对齐 OHMS test_case/200NVM.py 的测试流程:
  遍历 199 个成员系统, 对每个成员:
    1. FHATAsRequest   获取 ATA 编号列表
    2. FHEquipRequest  获取设备列表
    3. FHRetrievalRequest 开始故障历史检索任务
    4. FHStatusRequest 查询任务状态 == Complete
  验证 AHMU 能支持 >= 199 个成员提供启动测试服务 (pmat_inittest_ms >= 199)。

覆盖:
  T1 ATA 列表获取 (FHATAsRequest 返回非空)
  T2 设备列表获取 (FHEquipRequest 返回 >= 199 个成员)
  T3 199 成员故障历史检索 (FHRetrieval -> FHStatus == Complete)
  T4 检索任务状态持久化 (任务记录 + 状态查询)

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_fault_history.py
"""
import json
import sys
import os
import time
import urllib.request

BASE = "http://127.0.0.1:8443/api/v1"
TARGET_MEMBERS = 199  # 对齐 200NVM.py 的 pmat_inittest_ms >= 199


def post(path, data=None):
    url = BASE + path
    if data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
    else:
        req = urllib.request.Request(url, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def assert_cond(cond, msg):
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}")
    return bool(cond)


def main():
    results = []
    print("=" * 64)
    print("故障历史检索测试 (启动测试服务 / 199 成员)")
    print("=" * 64)

    # T1 ATA 列表
    print("\n[T1] ATA 编号列表获取 (FHATAsRequest)")
    atas = post("/FHATAsRequest")
    ata_list = atas.get("fhATAStructList", [])
    ok = len(ata_list) > 0
    results.append(assert_cond(ok, f"ATA 列表非空: {len(ata_list)} 个 ATA"))

    # T2 设备列表
    print("\n[T2] 成员系统设备列表获取 (FHEquipRequest)")
    equips = post("/FHEquipRequest", {})
    equip_list = equips.get("fhEquipStructList", [])
    ok = len(equip_list) >= TARGET_MEMBERS
    results.append(assert_cond(ok, f"设备列表 >= {TARGET_MEMBERS} 个成员: 实际 {len(equip_list)}"))

    # T3 199 成员故障历史检索 (批量发起 -> 统一查询, 避免串行 sleep 超时)
    print(f"\n[T3] {TARGET_MEMBERS} 个成员故障历史检索 (FHRetrieval -> FHStatus)")
    members = equip_list[:TARGET_MEMBERS]
    # 批量发起检索任务
    for m in members:
        try:
            post("/FHRetrievalRequest", {"equipID": m["equipID"]})
        except Exception as e:
            print(f"    发起失败: {m['equipID']} {e}")
    time.sleep(1.0)  # 等待全部任务完成 (Mock 延迟 0.5s)
    # 统一查询状态
    success = 0
    failed = []
    for m in members:
        try:
            status_resp = post("/FHStatusRequest", {"equipID": m["equipID"]})
            status = status_resp[0]["fhStatusStructList"][0]["status"]
            if status == "Complete":
                success += 1
            else:
                failed.append((m["equipID"], status))
        except Exception as e:
            failed.append((m["equipID"], str(e)))
    ok = success >= TARGET_MEMBERS and not failed
    results.append(assert_cond(
        ok, f"检索完成 {success}/{TARGET_MEMBERS} 个成员 (应全部 Complete)"))
    if failed:
        print(f"    失败成员: {failed[:5]}")

    # T4 状态查询一致性
    print("\n[T4] 检索任务状态持久化")
    try:
        first = members[0]["equipID"]
        status_resp = post("/FHStatusRequest", {"equipID": first})
        status = status_resp[0]["fhStatusStructList"][0]["status"]
        results.append(assert_cond(status == "Complete", f"成员 {first} 状态可重复查询: {status}"))
    except Exception as e:
        results.append(assert_cond(False, f"状态查询失败: {e}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
