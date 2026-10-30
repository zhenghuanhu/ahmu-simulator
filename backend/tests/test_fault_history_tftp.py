"""
故障历史检索 TFTP 上传测试 (改动4)

对齐 OHMS 故障历史报告获取664.py 的 TFTPServer:
  检索任务完成后, AHMU 作为 TFTP 客户端向测试端 TFTPServer(端口 59) 上传故障历史文件。

覆盖:
  T1 TFTP 客户端直接上传 (WRQ -> ACK(0) -> DATA 分块 -> ACK, 模拟服务器收文件)
  T2 检索完成自动触发 TFTP 上传 (FHRetrievalRequest -> 服务器收到故障历史文件)

前置: 后端已启动 (uvicorn app.main:app --port 8443)
运行: cd backend && venv/Scripts/python.exe tests/test_fault_history_tftp.py
"""
import json
import os
import sys
import threading
import time
import urllib.request

# 确保可 import app 包 (backend 目录加入 sys.path)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulate_tftp_server import TftpServer
from app.services.fault_history_tftp import tftp_client

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
    print("故障历史检索 TFTP 上传测试 (改动4)")
    print("=" * 64)

    # T1 TFTP 客户端直接上传 (本地模拟服务器)
    print("\n[T1] TFTP 客户端直接上传")
    server = TftpServer(port=5960)  # 用独立端口避免与后端自动上传冲突
    result = {}
    def _serve():
        nonlocal result
        result = server.receive_once(timeout=6.0)
    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    time.sleep(0.3)
    payload = json.dumps({"member_system": "EEC_1", "items": [{"a": 1}]}).encode()
    r = tftp_client.upload("EEC_1.LUI", payload, host="127.0.0.1", port=5960)
    t.join(timeout=7.0)
    filename, data = result
    ok = (r["status"] == "ok" and filename == "EEC_1.LUI"
          and data == payload and r["bytes"] == len(payload))
    results.append(assert_cond(ok, f"上传 status={r['status']} 文件={filename} "
                                   f"字节={r.get('bytes')} 内容一致={data == payload}"))

    # T2 检索完成自动触发 TFTP 上传 (后端 -> 端口 59)
    print("\n[T2] 检索完成自动触发 TFTP 上传 (端口 59)")
    server2 = TftpServer(port=59)
    result2 = {}
    def _serve2():
        nonlocal result2
        result2 = server2.receive_once(timeout=8.0)
    t2 = threading.Thread(target=_serve2, daemon=True)
    t2.start()
    time.sleep(0.3)
    # 触发检索任务 (数字 equipID 3038 = EEC_1)
    post("/FHRetrievalRequest", {"equipID": 3038})
    t2.join(timeout=10.0)
    filename2, data2 = result2
    ok = filename2 is not None and filename2.endswith(".LUI") and data2
    results.append(assert_cond(ok, f"收到文件={filename2} 字节={len(data2) if data2 else 0}"))

    print("\n" + "=" * 64)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 64)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
