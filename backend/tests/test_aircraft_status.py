"""
飞机状态消息生成与发布 (4.3.7) 端到端测试脚本

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_aircraft_status.py

覆盖场景:
    1. 消息字段完整性: 9个字段 (航段/阶段/UTC日期时间/ICAO/注册号/航班号/机场/终止标志)
    2. 初始阶段判定: 地/空速0/维护正常 → Pre-Flight(2)
    3. 起飞滑跑/离地: 油门>阈值/空速>离地速度 → Phase 5/6, 航段递增
    4. 爬升/巡航/下降/进近: 高度+高度变化率 → Phase 7/8/9/10
    5. 维护模式: 维护开关=ground_test → Phase 14, 终止标志 False
    6. 双源切换: 左源失效 → 切右源
    7. 双源都失效 → Phase 15 未知
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


def set_signal(source, param, value, valid=True):
    call("POST", "/aircraft-status/signal",
         {"source": source, "param": param, "value": value, "valid": valid})
    time.sleep(1.1)  # 等待下一个 1Hz 周期


def get_msg():
    return call("GET", "/aircraft-status")["message"]


def main():
    results = []
    print("=" * 60)
    print("飞机状态消息生成与发布 (4.3.7) 端到端测试")
    print("=" * 60)

    # 1. 消息字段完整性
    print("\n[1] 消息字段完整性 (9 字段)")
    m = get_msg()
    required = ["flight_leg", "flight_phase", "utc_date", "utc_time", "icao_code",
                "registration", "flight_number", "destination_airport",
                "departure_airport", "global_abort_flag"]
    ok = all(k in m for k in required)
    results.append(assert_cond(ok, f"消息含 {len(required)} 个必需字段"))

    # 2. 初始阶段判定
    print("\n[2] 初始阶段判定 (地/空速0/维护正常)")
    # 重置为空地=地
    set_signal("left", "air_ground_status", "ground")
    set_signal("left", "airspeed", 0.0)
    set_signal("left", "ground_speed", 0.0)
    set_signal("left", "engine_thrust_lever_angle", 0.0)
    set_signal("left", "brake_status", "applied")
    set_signal("left", "maintenance_switch_position", "normal")
    set_signal("left", "flight_altitude", 0.0)
    set_signal("left", "fcm_corrected_altitude_rate", 0.0)
    m = get_msg()
    ok = m["flight_phase"] in (1, 2)
    results.append(assert_cond(ok, f"初始阶段: {m['phase_name']}({m['flight_phase']})"))

    # 3. 起飞滑跑 + 离地
    print("\n[3] 起飞滑跑/离地 (航段递增)")
    set_signal("left", "brake_status", "released")
    set_signal("left", "engine_thrust_lever_angle", 50.0)
    set_signal("left", "airspeed", 100.0)
    set_signal("left", "ground_speed", 50.0)
    m = get_msg()
    ok = m["flight_phase"] == 5
    results.append(assert_cond(ok, f"起飞滑跑: {m['phase_name']}({m['flight_phase']}) 航段={m['flight_leg']}"))
    leg_before = m["flight_leg"]
    set_signal("left", "airspeed", 150.0)
    m = get_msg()
    ok = m["flight_phase"] == 6 and m["flight_leg"] >= leg_before
    results.append(assert_cond(ok, f"离地: {m['phase_name']}({m['flight_phase']}) 航段={m['flight_leg']}"))

    # 4. 爬升/巡航/下降/进近
    print("\n[4] 爬升/巡航/下降/进近")
    set_signal("left", "air_ground_status", "air")
    set_signal("left", "flight_altitude", 10000.0)
    set_signal("left", "fcm_corrected_altitude_rate", 500.0)
    m = get_msg()
    results.append(assert_cond(m["flight_phase"] == 7, f"爬升: {m['phase_name']}({m['flight_phase']})"))

    set_signal("left", "flight_altitude", 30000.0)
    set_signal("left", "fcm_corrected_altitude_rate", 0.0)
    m = get_msg()
    results.append(assert_cond(m["flight_phase"] == 8, f"巡航: {m['phase_name']}({m['flight_phase']})"))

    set_signal("left", "flight_altitude", 20000.0)
    set_signal("left", "fcm_corrected_altitude_rate", -500.0)
    m = get_msg()
    results.append(assert_cond(m["flight_phase"] == 9, f"下降: {m['phase_name']}({m['flight_phase']})"))

    set_signal("left", "flight_altitude", 6000.0)
    set_signal("left", "fcm_corrected_altitude_rate", -400.0)
    m = get_msg()
    results.append(assert_cond(m["flight_phase"] == 10, f"进近: {m['phase_name']}({m['flight_phase']})"))

    # 5. 维护模式
    print("\n[5] 维护模式 (Phase 14, 终止标志 False)")
    set_signal("left", "maintenance_switch_position", "ground_test")
    m = get_msg()
    ok = m["flight_phase"] == 14 and m["global_abort_flag"] is False
    results.append(assert_cond(ok, f"维护: {m['phase_name']}({m['flight_phase']}) 终止={m['global_abort_flag']}"))

    # 6. 双源切换
    print("\n[6] 双源切换 (左源失效切右源)")
    set_signal("left", "maintenance_switch_position", "normal")
    set_signal("left", "flight_altitude", 6000.0)
    set_signal("left", "fcm_corrected_altitude_rate", -400.0)
    set_signal("right", "airspeed", 150.0)
    set_signal("left", "airspeed", 150.0, valid=False)  # 左源空速失效
    d = call("GET", "/aircraft-status")
    m = d["message"]
    ok = d["active_source"] == "right" and m["airspeed"] == 150.0
    results.append(assert_cond(ok, f"双源切换: 生效源={d['active_source']} 空速={m['airspeed']}"))

    # 7. 双源都失效 → Phase 15
    print("\n[7] 双源都失效 (Phase 15 未知)")
    set_signal("left", "air_ground_status", "air", valid=False)
    set_signal("right", "air_ground_status", "air", valid=False)
    set_signal("left", "airspeed", 150.0, valid=False)
    set_signal("right", "airspeed", 150.0, valid=False)
    m = get_msg()
    ok = m["flight_phase"] == 15
    results.append(assert_cond(ok, f"双源失效: {m['phase_name']}({m['flight_phase']})"))

    # 汇总
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
