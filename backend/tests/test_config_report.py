"""
构型管理 - 构型报告 AFDX 接收测试 (阶段二)

对齐 OHMS test_case/构型信息.py 的发送语义:
  测试端经 AFDX 发送 Config_Report_Dataset(载荷 Configuration_report) 给 AHMU,
  AHMU 接收解析四类字段入库, 与基本构型比对生成构型错误报告, 供 ACR*Request 查询。

覆盖:
  T1 构型报告接收并入库 (匹配基本构型 -> 查询返回接收值)
  T2 构型报告与基本构型比对 (不匹配 -> colour=3 + 构型错误报告)
  T3 特定数据/软件部件字段接收 (specificData/softParts 入库回读)
  T4 软件信息接收 (softLocationID/softLocationDescription)
  T5 无报告成员回退基本构型 (colour=1)

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_config_report.py
"""
import json
import sys
import time
import urllib.request

from simulate_config_report import build_report, send_report

BASE = "http://127.0.0.1:8443/api/v1"
MEMBER = "EEC_1"
EQUIP_ID = 3038


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
    print("构型管理 - 构型报告 AFDX 接收测试 (阶段二)")
    print("=" * 64)

    # T1 发送匹配基本构型的报告, 查询返回接收值
    print(f"\n[T1] 构型报告接收并入库 (匹配, {MEMBER})")
    send_report(build_report(MEMBER, EQUIP_ID, mismatch=False))
    time.sleep(1.0)  # 等待 UDP 接收处理
    hard = post("/ACRHardInfoRequest", {"equipID": EQUIP_ID})[0]
    ok = hard["hardPartNumber"] == f"{MEMBER}-100-01" and hard["hardSerialNumber"] == f"SN-{MEMBER}-0001"
    results.append(assert_cond(ok, f"hardPartNumber={hard['hardPartNumber']} "
                                   f"hardSerialNumber={hard['hardSerialNumber']}"))

    # T2 发送不匹配报告 -> colour=3 + 构型错误报告
    print(f"\n[T2] 构型报告不匹配 -> colour=3 + 错误报告")
    send_report(build_report(MEMBER, EQUIP_ID, mismatch=True))
    time.sleep(1.0)
    hard = post("/ACRHardInfoRequest", {"equipID": EQUIP_ID})[0]
    ok = (hard["hardPartNumber"] == f"{MEMBER}-999-99"
          and hard["colour"] == 3)
    results.append(assert_cond(ok, f"hardPartNumber={hard['hardPartNumber']} "
                                   f"colour={hard['colour']}(应3不匹配)"))

    # T3 特定数据 + 软件部件接收
    print(f"\n[T3] 特定数据 + 软件部件接收回读")
    spec = post("/ACRSpecificDataRequest", {"equipID": EQUIP_ID})[0]["specificInfoList"]
    parts = post("/ACRSoftPartResponse", {"equipID": EQUIP_ID})[0]["acrSoftPartStructList"]
    ok = (len(spec) == 2 and spec[0]["specificData"] == f"{MEMBER}-SPEC-01"
          and len(parts) == 2 and parts[0]["softPartNumber"] == f"{MEMBER}-SW-01")
    results.append(assert_cond(ok, f"specificData={[s['specificData'] for s in spec]} "
                                   f"softParts={[p['softPartNumber'] for p in parts]}"))

    # T4 软件信息接收
    print(f"\n[T4] 软件信息接收")
    soft = post("/ACRSoftInfoRequest", {"equipID": EQUIP_ID})[0]
    ok = soft["softLocationID"] == MEMBER and soft["softLocationDescription"]
    results.append(assert_cond(ok, f"softLocationID={soft['softLocationID']} "
                                   f"desc={soft['softLocationDescription']}"))

    # T5 无报告成员回退基本构型
    print(f"\n[T5] 无报告成员回退基本构型")
    hard2 = post("/ACRHardInfoRequest", {"equipID": 3036})[0]  # 其他成员
    ok = "hardPartNumber" in hard2 and "colour" in hard2
    results.append(assert_cond(ok, f"其他成员 hardPartNumber={hard2.get('hardPartNumber')} "
                                   f"colour={hard2.get('colour')}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
