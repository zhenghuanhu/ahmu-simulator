"""
发动机配平 N1_Trim 信号 AFDX 接收测试 (P2)

验证 AHMU 经 UDP(Mock AFDX) 接收 FCS/EEC 发送的 N1_Trim 信号,
解码 ICD 信号名并注入 EMU 配平数据 (对齐 ICD FCS_Output_Data_AF20 / A664_Msg_EEC_FCSFMS)。

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_engine_trim_rx.py
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
    from simulate_n1_trim import send_n1_trim, build_signals
    results = []
    print("=" * 60)
    print("发动机配平 N1_Trim 信号 AFDX 接收测试 (对齐 ICD)")
    print("=" * 60)

    # T1 接收 N1_Trim 信号并解码 (验证不受配平执行影响的字段: DIS/命令/上下限)
    print("\n[T1] 接收 N1_Trim 信号并解码")
    send_n1_trim(1, build_signals(available=0, command_left=1.5,
                                  lower=-2.0, upper=2.0))
    time.sleep(1)  # 等待 report loop 回调缓存
    d = call("GET", "/engine-trim/data?engine_id=1")["data"]
    ok = (d is not None
          and d.get("n1_trim_available") is False   # 0=Disable
          and d.get("n1_trim_command_left") == 1.5
          and d.get("n1_trim_lower_limit") == -2.0
          and d.get("n1_trim_upper_limit") == 2.0)
    results.append(assert_cond(ok, f"N1_Trim 解码: available={d.get('n1_trim_available') if d else None}, "
                                   f"command_left={d.get('n1_trim_command_left') if d else None}, "
                                   f"范围 [{d.get('n1_trim_lower_limit') if d else None}~{d.get('n1_trim_upper_limit') if d else None}]"))

    # T2 N1_Trim_Disable=1 前置条件拒绝
    print("\n[T2] N1_Trim_Disable=1 前置条件拒绝配平")
    send_n1_trim(2, build_signals(disable=1))
    time.sleep(0.5)
    r = call("POST", "/engine-trim/command",
             {"engine_id": 2, "trim_type": "n1", "target_trim": 1.0, "source": "ground"})
    time.sleep(1)  # 等待状态机执行
    cmds = call("GET", "/engine-trim/commands")
    eng2 = [c for c in cmds.get("items", []) if c["engine_id"] == 2]
    rejected = [c for c in eng2 if c["status"] == "rejected"]
    ok = bool(rejected) and "禁止" in (rejected[0].get("reject_reason") or "")
    results.append(assert_cond(ok, f"Disable 拒绝: {rejected[0].get('reject_reason') if rejected else '--'}"))

    # T3 Engine_Trim_Failure 接入故障诊断
    print("\n[T3] Engine_Trim_Failure=Active 接入故障诊断")
    send_n1_trim(3, build_signals(failure=1))
    time.sleep(1)  # 等待 report loop 检测并上报
    d = call("GET", "/engine-trim/data?engine_id=3")["data"]
    ok = d is not None and d.get("engine_trim_failure") is True
    results.append(assert_cond(ok, f"配平故障接收: engine_trim_failure={d.get('engine_trim_failure') if d else None}"))

    # T4 动态上下限越界拒绝 (收窄到 -1.0~+1.0)
    print("\n[T4] 动态上下限越界拒绝 (收窄到 -1.0~+1.0)")
    send_n1_trim(4, build_signals(lower=-1.0, upper=1.0))
    time.sleep(0.5)
    r = call("POST", "/engine-trim/command",
             {"engine_id": 4, "trim_type": "n1", "target_trim": 1.5, "source": "ground"})
    ok = r.get("status") == "error" and "越界" in r.get("message", "")
    results.append(assert_cond(ok, f"动态上下限越界拒绝: {r.get('message')}"))

    # 清理注入状态 (恢复默认, 避免污染其他测试)
    print("\n[清理] 恢复 N1_Trim 信号默认状态")
    for eid in (1, 2, 3, 4):
        send_n1_trim(eid, build_signals(applied=0.0, available=1, disable=0, failure=0,
                                        command_left=0.0, command_right=0.0,
                                        lower=-2.5, upper=2.5))
    time.sleep(1)

    # 汇总
    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
