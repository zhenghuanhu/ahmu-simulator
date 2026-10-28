"""
构型管理 ACR 查询接口测试 (阶段一: ACR 查询接口 + colour 状态)

对齐 OHMS test_case/构型信息.py + libs/config_libs/config_verify.py 的解析路径:
  - ACRATAsRequest       -> [ {acrAtaStructList: [{ata, ataName}]} ]
  - ACREquipsRequest     -> [ {totalCount, equipNameList: [{equipName}]} ]
  - ACRHardInfoRequest   -> [ {colour, state, hardPartNumber, hardPartDescription,
                                hardSerialNumber, hardModificationStatus} ]
  - ACRSpecificDataRequest -> [ {specificInfoList: [{colour, specificData}]} ]
  - ACRSoftInfoRequest   -> [ {colour, softLocationID, softLocationDescription} ]
  - ACRSoftPartResponse  -> [ {acrSoftPartStructList: [{colour, softPartNumber,
                                softPartDescription}]} ]

覆盖:
  T1 ATA 列表获取 (acrAtaStructList 非空 + ata/ataName 字段)
  T2 设备列表获取 (totalCount + equipNameList)
  T3 硬件信息 (colour/state/hardPartNumber/hardSerialNumber/hardModificationStatus)
  T4 特定数据 (specificInfoList + colour + specificData)
  T5 软件信息 (colour + softLocationID + softLocationDescription)
  T6 软件部件 (acrSoftPartStructList + colour + softPartNumber/softPartDescription)
  T7 colour 状态 (默认 1 正常)

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_config_acr.py
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8443/api/v1"


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
    print("构型管理 ACR 查询接口测试 (阶段一)")
    print("=" * 64)

    # T1 ATA 列表
    print("\n[T1] ATA 章节列表 (ACRATAsRequest)")
    resp = post("/ACRATAsRequest")
    ata_list = resp[0]["acrAtaStructList"]
    ok = len(ata_list) > 0 and all("ata" in a and "ataName" in a for a in ata_list)
    results.append(assert_cond(ok, f"acrAtaStructList 非空且含 ata/ataName: {len(ata_list)} 个 ATA"))
    print(f"      示例: {[(a['ata'], a['ataName']) for a in ata_list[:5]]}")

    # T2 设备列表 (取第一个 ATA)
    print("\n[T2] 设备列表 (ACREquipsRequest)")
    first_ata = ata_list[0]["ata"]
    resp = post("/ACREquipsRequest", {"ata": first_ata, "pageNumber": 1, "numberPerPage": 20})
    total = resp[0]["totalCount"]
    equips = resp[0]["equipNameList"]
    ok = total > 0 and len(equips) > 0 and "equipName" in equips[0]
    results.append(assert_cond(ok, f"ATA {first_ata} 设备 totalCount={total}, 本页 {len(equips)} 个"))

    # 取一个 equipID 用于后续查询
    equip_id = equips[0].get("equipID") if equips else None
    equip_name = equips[0].get("equipName") if equips else None

    # T3 硬件信息
    print("\n[T3] 硬件信息 (ACRHardInfoRequest)")
    resp = post("/ACRHardInfoRequest", {"equipID": equip_id})
    hard = resp[0]
    ok = (hard.get("colour") in (1, 2, 3) and hard.get("state")
          and hard.get("hardPartNumber") and hard.get("hardSerialNumber")
          and hard.get("hardModificationStatus"))
    results.append(assert_cond(
        ok, f"equipID {equip_id} ({equip_name}): colour={hard.get('colour')} "
            f"state={hard.get('state')} PNR={hard.get('hardPartNumber')}"))

    # T4 特定数据
    print("\n[T4] 特定数据 (ACRSpecificDataRequest)")
    resp = post("/ACRSpecificDataRequest", {"equipID": equip_id, "pageNumber": 1, "numberPerPage": 15})
    spec_list = resp[0]["specificInfoList"]
    ok = len(spec_list) > 0 and all("specificData" in s and "colour" in s for s in spec_list)
    results.append(assert_cond(ok, f"specificInfoList {len(spec_list)} 条, 首条={spec_list[0] if spec_list else None}"))

    # T5 软件信息
    print("\n[T5] 软件信息 (ACRSoftInfoRequest)")
    resp = post("/ACRSoftInfoRequest", {"equipID": equip_id})
    soft = resp[0]
    ok = soft.get("softLocationID") and soft.get("softLocationDescription") and soft.get("colour")
    results.append(assert_cond(
        ok, f"softLocationID={soft.get('softLocationID')} desc={soft.get('softLocationDescription')}"))

    # T6 软件部件
    print("\n[T6] 软件部件 (ACRSoftPartResponse)")
    resp = post("/ACRSoftPartResponse", {"equipID": equip_id, "pageNumber": 1, "numberPerPage": 10})
    part_list = resp[0]["acrSoftPartStructList"]
    ok = len(part_list) > 0 and all("softPartNumber" in p and "softPartDescription" in p
                                    and "colour" in p for p in part_list)
    results.append(assert_cond(ok, f"acrSoftPartStructList {len(part_list)} 条, 首条={part_list[0] if part_list else None}"))

    # T7 colour 状态
    print("\n[T7] colour 状态语义 (1正常/2变更/3不匹配)")
    colours = {hard.get("colour"), soft.get("colour")}
    for p in part_list:
        colours.add(p.get("colour"))
    ok = all(c in (1, 2, 3) for c in colours)
    results.append(assert_cond(ok, f"所有 colour 取值均在 {1, 2, 3} 内: {colours}"))

    # T8 别名接口
    print("\n[T8] 别名接口 (ACREquipmentNameRequest/ACRSpecificInfoRequest/ACRSoftPartRequest)")
    a1 = post("/ACREquipmentNameRequest", {"ata": first_ata})[0].get("totalCount")
    a2 = post("/ACRSpecificInfoRequest", {"equipID": equip_id})[0].get("specificInfoList")
    a3 = post("/ACRSoftPartRequest", {"equipID": equip_id})[0].get("acrSoftPartStructList")
    ok = a1 == total and a2 is not None and a3 is not None
    results.append(assert_cond(ok, f"3 个别名接口均正常: total={a1}, specific={len(a2) if a2 else 0}, part={len(a3) if a3 else 0}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total_n = len(results)
    print(f"测试结果: {passed}/{total_n} 通过")
    print("=" * 64)
    return 0 if passed == total_n else 1


if __name__ == "__main__":
    sys.exit(main())
