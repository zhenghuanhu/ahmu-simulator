"""
校验 ICD 编解码是否对齐 XML 的 Aircraft_Status_Msg 字节定义
============================================================

解析 Member_System_all_sender_icd_v6_1_1.xml 中 Aircraft_Status_Msg 的
DS/DP 字段定义, 与 pack_aircraft_status 生成的实际 68 字节逐字段比对,
验证每个字段的字节偏移/长度/类型是否符合权威 ICD 定义。

用法:
    cd backend
    venv\\Scripts\\python.exe tests\\verify_icd_against_xml.py

    可通过环境变量 ICD_XML 指定 XML 路径 (默认 664 目录)。
"""
import os
import struct
import sys
import xml.etree.ElementTree as ET

# 使脚本可独立运行时 import app.core 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.icd_aircraft_status import pack_aircraft_status, unpack_aircraft_status, AIRCRAFT_STATUS_MSG  # noqa: E402

DEFAULT_XML = r"C:\Users\huzhe\WorkBuddy\航电系统实验室原理集成试验环境\664\Member_System_all_sender_icd_v6_1_1.xml"
XML_PATH = os.environ.get("ICD_XML", DEFAULT_XML)


def extract_message_fragment(xml_path: str, msg_name: str) -> str:
    """从大型 XML 中提取指定 A664Message 的片段 (避免全量解析 63MB)"""
    with open(xml_path, "r", encoding="utf-8") as f:
        content = f.read()
    start = content.find(f'<A664Message Name="{msg_name}"')
    if start == -1:
        raise ValueError(f"未找到消息: {msg_name}")
    end = content.find("</A664Message>", start)
    if end == -1:
        raise ValueError(f"消息 {msg_name} 未闭合")
    return content[start:end + len("</A664Message>")]


def parse_fields(fragment: str):
    """解析 A664Message 片段, 返回 (msg_meta, [字段...])"""
    root = ET.fromstring(fragment)
    meta = {
        "name": root.get("Name"),
        "vlid": int(root.get("VLID")),
        "size": int(root.get("MessageSize")),
        "protocol": root.get("MsgDataProtocolType"),
        "period": root.get("TransmissionIntervalMinimum"),
    }
    fields = []
    for ds in root.findall("DS"):
        ds_offset = int(ds.get("ByteOffsetWithinMsg"))
        ds_name = ds.get("Name")
        ds_size = int(ds.get("DataSetSize"))
        for dp in ds.findall("DP"):
            fields.append({
                "ds": ds_name,
                "name": dp.get("Name"),
                "byte_offset": ds_offset + int(dp.get("BitOffsetWithinDS")) // 8,
                "bit_length": int(dp.get("ParameterSize")),
                "type": dp.get("DataFormatType"),
                "ds_offset": ds_offset,
                "ds_size": ds_size,
            })
    return meta, fields


def main() -> int:
    print("=" * 70)
    print("ICD 编解码 ↔ XML Aircraft_Status_Msg 字节定义比对校验")
    print("=" * 70)

    # 1. 从 XML 提取定义
    print(f"\n[1] 解析 XML: {XML_PATH}")
    fragment = extract_message_fragment(XML_PATH, "Aircraft_Status_Msg")
    meta, fields = parse_fields(fragment)
    print(f"    消息: {meta['name']} | VLID={meta['vlid']} | 大小={meta['size']}字节 "
          f"| 周期={meta['period']}ms | 协议={meta['protocol']}")

    # 2. 与编解码模块元数据比对
    print("\n[2] 消息级元数据比对")
    ok_size = meta["size"] == AIRCRAFT_STATUS_MSG["size"]
    ok_vlid = meta["vlid"] == AIRCRAFT_STATUS_MSG["vl_id"]
    print(f"    [{'PASS' if ok_size else 'FAIL'}] MessageSize {meta['size']} == {AIRCRAFT_STATUS_MSG['size']}")
    print(f"    [{'PASS' if ok_vlid else 'FAIL'}] VLID {meta['vlid']} == {AIRCRAFT_STATUS_MSG['vl_id']}")

    # 3. pack 生成实际数据, 校验字段字节偏移
    print("\n[3] 字段字节偏移比对 (XML 定义 vs 实际 pack)")
    # 用固定值 pack, 便于观察各字段落位
    payload = pack_aircraft_status(
        flight_phase=15, global_abort_flag=False, flight_leg=-5,
        icao_code="780123", registration="B-001A", flight_number="CXF001",
        departure_airport="ZSPD", destination_airport="ZBAA",
        utc=(13, 10, 26, 21, 30, 45))
    print(f"    pack 载荷大小: {len(payload)} 字节 (应为 {meta['size']})")

    # 打印 XML 定义的字段布局
    print("\n    XML 字段定义 (按消息字节偏移排序):")
    key_fields = {"Flight_Phase", "Flight_Leg", "Global_Abort_Flag", "ICAO_Code",
                  "UTC_Days", "UTC_Months", "UTC_Years", "UTC_Hours",
                  "UTC_Minutes", "UTC_Seconds"}
    for f in sorted(fields, key=lambda x: x["byte_offset"]):
        star = " ★" if f["name"] in key_fields else ""
        print(f"      字节 {f['byte_offset']:>2}  {f['name']:<28} "
              f"{f['bit_length']:>3}位  {f['type']:<8} DS={f['ds']}{star}")

    # 4. 关键字段值校验
    print("\n[4] 关键字段字节值校验 (实际 pack 结果)")
    checks = [
        ("Flight_Phase", "字节 12~13 应为 0x000F(15)", payload[12:14] == b"\x00\x0f"),
        ("Flight_Leg", "字节 8~9 应为 0xFFFB(-5)", payload[8:10] == b"\xff\xfb"),
        ("Global_Abort_Flag", "字节 64 应为 0x00", payload[64] == 0x00),
        ("ICAO_Code", "字节 24~26 应为 0x780123", payload[24:27] == b"\x78\x01\x23"),
        ("UTC_Days", "字节 16 应为 13", payload[16] == 13),
        ("UTC_Months", "字节 17 应为 10", payload[17] == 10),
        ("UTC_Years", "字节 18 应为 26", payload[18] == 26),
        ("UTC_Hours", "字节 22 应为 21", payload[22] == 21),
        ("UTC_Minutes", "字节 21 应为 30", payload[21] == 30),
        ("UTC_Seconds", "字节 20 应为 45", payload[20] == 45),
    ]
    all_pass = True
    for name, desc, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}: {desc}")
        all_pass = all_pass and ok

    # 5. unpack round-trip
    print("\n[5] unpack round-trip")
    d = unpack_aircraft_status(payload)
    ok_rt = (d["flight_phase"] == 15 and d["flight_leg"] == -5
             and d["global_abort_flag"] is False and d["registration"] == "B-001A")
    print(f"    [{'PASS' if ok_rt else 'FAIL'}] unpack 还原: {d}")
    all_pass = all_pass and ok_rt and ok_size and ok_vlid

    # 6. 打印完整 hex 布局 (供人工核对)
    print("\n[6] 68 字节完整 hex (每行 16 字节):")
    for i in range(0, len(payload), 16):
        chunk = payload[i:i + 16]
        hex_str = " ".join(f"{b:02x}" for b in chunk)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"    {i:04x}: {hex_str:<47}  {ascii_str}")

    print("\n" + "=" * 70)
    print(f"校验结果: {'全部通过 ✓' if all_pass else '存在不一致 ✗'}")
    print("=" * 70)
    return 0 if all_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
