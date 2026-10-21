"""
打印管理功能 (4.3.2) 端到端测试程序 (模拟打印机)

模拟 GIPC 打印机: 监听 UDP 34801 接收 AHMU 经 AFDX 发送的 ARINC744A 打印数据
(Printer_GIPC_Data, URQ/DATA 分块), 逐块回 ACK, 最后回作业状态 STATUS, 并保存
接收到的打印数据为 ps 文件, 验证打印链路闭环。

用法 (后端已启动):
    cd backend
    ./venv/Scripts/python.exe tests/test_print.py

覆盖场景:
    1. URQ 接收解析 (文件名 + 总块数)
    2. DATA 分块接收 + 逐块 ACK
    3. 作业状态反馈 (JobStatus=成功)
    4. 后端打印任务状态流转至 completed 并生成 ps 文件
    5. 打印内容一致性校验 (接收数据 == 提交内容)
    6. 前端提交打印请求 + 界面反馈 (WebSocket 广播 print_frame/print_completed)
"""
import asyncio
import os
import sys
import json
import socket
import time
import threading
import urllib.request
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.icd_print import (  # noqa: E402
    decode_print_message, encode_ack, encode_status_report, OPT_URQ, OPT_DATA,
)

try:
    import websockets  # noqa: E402
except ImportError:
    websockets = None

BASE = "http://127.0.0.1:8443/api/v1"
WS_URL = "ws://127.0.0.1:8443/ws/ahmu"
RECV_PORT = 34801   # 打印机监听端口 (接收 AHMU 打印数据)
SEND_PORT = 34802   # 打印机发送端口 (回复 ACK/状态)


def call(method: str, path: str, body: dict = None) -> dict:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode("utf-8"))


def submit_print_via_http(content: str, job_type: str = "file_transfer") -> dict:
    """模拟前端「提交打印」按钮: 与前端 Print.vue 的 submitPrint 完全一致的请求格式"""
    qs = urllib.parse.urlencode({"content": content, "job_type": job_type})
    return call("POST", f"/print/submit?{qs}")


class MockPrinter:
    """模拟打印机: 接收 AHMU 打印数据, 回 ACK/状态, 保存文件"""

    def __init__(self):
        self.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.recv_sock.bind(("127.0.0.1", RECV_PORT))
        self.recv_sock.settimeout(15)
        self.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.filename = None
        self.total_blocks = None
        self.blocks = []
        self.error = None
        self.done = False

    def run(self):
        try:
            # 1. URQ
            data, addr = self.recv_sock.recvfrom(4096)
            msg = decode_print_message(data)
            if not msg or msg["opt_code"] != OPT_URQ:
                self.error = f"首条应为 URQ, 实际: {msg}"
                return
            self.filename = msg["payload"].rstrip(b"\x00").decode("utf-8")
            self.total_blocks = msg["block_num"]
            self.send_sock.sendto(encode_ack(0), ("127.0.0.1", SEND_PORT))
            print(f"[打印机] 收到 URQ: 文件={self.filename} 总块数={self.total_blocks}")

            # 2. DATA 分块
            while True:
                data, addr = self.recv_sock.recvfrom(4096)
                msg = decode_print_message(data)
                if not msg or msg["opt_code"] != OPT_DATA:
                    self.error = f"应为 DATA, 实际: {msg}"
                    return
                self.blocks.append(msg["payload"])
                self.send_sock.sendto(encode_ack(msg["block_num"]), ("127.0.0.1", SEND_PORT))
                print(f"[打印机] 收到 DATA 块 {msg['block_num']}/{self.total_blocks} "
                      f"({len(msg['payload'])} 字节), 已回 ACK")
                if msg["block_num"] >= self.total_blocks:
                    break

            # 3. 作业状态反馈
            self.send_sock.sendto(encode_status_report(), ("127.0.0.1", SEND_PORT))
            print("[打印机] 已回作业状态 STATUS (成功)")
            self.done = True
        except socket.timeout:
            self.error = "接收打印数据超时"
        except Exception as e:
            self.error = str(e)
        finally:
            self.close()

    def close(self):
        for sock in (self.recv_sock, self.send_sock):
            try:
                sock.close()
            except OSError:
                pass

    def save_and_verify(self, content: str) -> bool:
        """保存接收数据并与提交内容比对"""
        received = b"".join(self.blocks).rstrip(b"\x00")
        expected = content.encode("utf-8")
        return received[:len(expected)] == expected


def run_sync_tests():
    """测试项 1~5: ARINC744A 协议闭环 (同步)"""
    results = []
    print("=" * 60)
    print("打印管理功能 (4.3.2) 端到端测试 (模拟打印机)")
    print("=" * 60)

    # 启动模拟打印机 (后台线程, 先监听再提交)
    printer = MockPrinter()
    t = threading.Thread(target=printer.run, daemon=True)
    t.start()
    time.sleep(0.5)

    # 提交打印任务
    content = "AHMU 打印测试报告\n这是一段用于验证 ARINC744A 打印协议的内容\n打印完成"
    print("\n[1] 提交打印任务")
    d = submit_print_via_http(content, "file_transfer")
    ok = d.get("status") == "ok" and bool(d.get("job_id"))
    job_id = d.get("job_id", "")
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] 打印任务已提交: {job_id}")

    t.join(timeout=20)

    print("\n[2] 打印机接收 URQ/DATA/状态")
    results.append(printer.done)
    print(f"  [{'PASS' if printer.done else 'FAIL'}] 打印机完成接收: "
          f"文件={printer.filename} 块数={printer.total_blocks} 数据块={len(printer.blocks)}")

    print("\n[3] 打印内容一致性")
    ok = printer.save_and_verify(content)
    results.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] 接收数据与提交内容一致")

    time.sleep(2)
    print("\n[4] 后端打印任务状态")
    d = call("GET", "/print/jobs?page=1&size=1")
    items = d.get("items", [])
    ok = bool(items) and items[0]["id"] == job_id and items[0]["status"] == "completed" and bool(items[0]["file_path"])
    if items:
        results.append(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] id={items[0]['id'][:8]} 状态={items[0]['status']} "
              f"文件={items[0]['file_path'] if items[0]['file_path'] else '--'}")
    else:
        results.append(False)
        print("  [FAIL] 未查询到打印任务")

    return results


async def run_frontend_test():
    """测试项 6: 前端提交打印请求 + 界面反馈验证 (WebSocket)"""
    results = []
    print("\n[5] 前端提交打印请求 + 界面反馈验证")

    if websockets is None:
        print("  [SKIP] 未安装 websockets 库, 跳过界面反馈验证")
        return results

    content = "前端提交打印测试内容"
    frames_received = []
    completed = False

    # 启动模拟打印机 (后台线程)
    printer = MockPrinter()
    pt = threading.Thread(target=printer.run, daemon=True)
    pt.start()
    await asyncio.sleep(0.5)

    try:
        async with websockets.connect(WS_URL) as ws:
            await ws.recv()  # 跳过欢迎消息

            # 模拟前端「提交打印」按钮 (与前端 Print.vue submitPrint 相同请求)
            d = await asyncio.to_thread(submit_print_via_http, content, "file_transfer")
            ok_submit = d.get("status") == "ok" and bool(d.get("job_id"))
            results.append(ok_submit)
            print(f"  [{'PASS' if ok_submit else 'FAIL'}] 提交打印 (模拟前端按钮): job_id={d.get('job_id')}")

            # 监听 WebSocket 广播 (模拟前端界面反馈)
            try:
                while True:
                    msg = await asyncio.wait_for(ws.recv(), timeout=10)
                    m = json.loads(msg)
                    ev = m.get("event")
                    if ev == "print_frame":
                        frames_received.append(m["data"]["frame_type"])
                    elif ev == "print_state":
                        pass  # 状态流转 (前端据此更新状态标签)
                    elif ev == "print_completed":
                        completed = True
                        break
                    elif ev == "print_failed":
                        break
            except asyncio.TimeoutError:
                pass
    except OSError as e:
        print(f"  [FAIL] WebSocket 连接失败: {e}")
        results.append(False)
        results.append(False)
        results.append(False)
        return results

    pt.join(timeout=5)

    # 断言界面反馈: 协议帧广播 (URQ/DATA)
    ok_frames = "URQ" in frames_received and "DATA" in frames_received
    results.append(ok_frames)
    print(f"  [{'PASS' if ok_frames else 'FAIL'}] 协议帧广播 (界面展示帧序列): {frames_received}")

    # 断言界面反馈: 完成广播 print_completed (前端进度 100% + 刷新任务列表)
    results.append(completed)
    print(f"  [{'PASS' if completed else 'FAIL'}] 完成反馈 print_completed (界面进度100%+刷新)")

    # 校验后端任务 completed + ps 文件
    time.sleep(0.5)
    d = call("GET", "/print/jobs?page=1&size=1")
    items = d.get("items", [])
    ok_job = bool(items) and items[0]["status"] == "completed" and bool(items[0]["file_path"])
    results.append(ok_job)
    print(f"  [{'PASS' if ok_job else 'FAIL'}] 任务状态 completed + 生成 ps 文件")

    # 失败链路: 空内容被拒绝 (前端 ElMessage.warning 提示)
    d = submit_print_via_http("", "file_transfer")
    ok_empty = d.get("status") == "error"
    results.append(ok_empty)
    print(f"  [{'PASS' if ok_empty else 'FAIL'}] 空内容被拒绝 (界面提示): {d.get('message')}")

    return results


def run_block_transfer_test():
    """测试项 6: 块传输模式交互 (RTS/CTS/DATA/STATUS)"""
    results = []
    print("\n[6] 块传输模式交互")

    try:
        from simulate_printer import SimulatePrinter
    except ImportError:
        print("  [SKIP] 无法导入 simulate_printer 模块")
        return results

    content = "块传输模式测试内容"
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_print_received")
    os.makedirs(output_dir, exist_ok=True)

    # 6.1 正常收发 (RTS -> CTS -> DATA -> STATUS)
    result_box = {}
    printer = SimulatePrinter("127.0.0.1", 34801, 34802, output_dir)

    def _run():
        try:
            result_box["result"] = printer.handle_one_job()
        except Exception as e:
            result_box["result"] = {"ok": False, "error": str(e)}

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.5)

    d = submit_print_via_http(content, "block_transfer")
    ok_submit = d.get("status") == "ok"
    results.append(ok_submit)
    print(f"  [{'PASS' if ok_submit else 'FAIL'}] 提交块传输打印: job_id={d.get('job_id')}")

    t.join(timeout=15)
    r = result_box.get("result", {})
    ok_recv = r.get("ok") is True
    results.append(ok_recv)
    print(f"  [{'PASS' if ok_recv else 'FAIL'}] 模拟打印机块传输接收: {r}")

    # 6.2 内容一致性
    if ok_recv and r.get("save_path") and os.path.exists(r["save_path"]):
        with open(r["save_path"], "rb") as f:
            received = f.read()
        ok_content = received == content.encode("utf-8")
    else:
        ok_content = False
    results.append(ok_content)
    print(f"  [{'PASS' if ok_content else 'FAIL'}] 块传输内容一致性")

    # 6.3 后端任务 completed
    time.sleep(1)
    d = call("GET", "/print/jobs?page=1&size=1")
    items = d.get("items", [])
    ok_job = bool(items) and items[0]["status"] == "completed" and bool(items[0]["file_path"])
    results.append(ok_job)
    print(f"  [{'PASS' if ok_job else 'FAIL'}] 块传输任务 completed + ps 文件")

    printer.close()
    return results


def main():
    results = run_sync_tests()
    results += asyncio.run(run_frontend_test())
    results += run_block_transfer_test()

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"测试结果: {passed}/{total} 通过")
    print("=" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
