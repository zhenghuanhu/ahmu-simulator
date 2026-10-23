"""
启动测试服务 (ARINC624 启动测试交互通信)

职责:
  1. 按成员系统规范模拟与成员系统的启动测试交互通信 (Mock UDP 承载 AFDX)
  2. 支持维护人员经交互式页面触发成员系统启动执行测试
  3. 消息: 运行测试指令/终止指令/继续测试指令 (OHMS->成员),
           测试进行中响应/抑制测试响应/显示页面响应/终止响应/成员系统状态 (成员->OHMS),
           ACK 确认 (OHMS->成员)
  4. 前置条件: 飞机处于地面 + 维护开关处于启动测试/加载位置 (复用维护模式信号)
  5. 抑制条件监视: 指令前 + 执行期间持续监视成员抑制条件
  6. 两个场景:
     交互式: 运行->进行中->ACK->显示页面->ACK->展示页面->维护人员响应->继续指令->进行中->ACK->抑制->抑制响应->ACK->通知
     非交互式: 初始抑制->检查->清除->报告无抑制->运行->进行中->ACK->执行完成->测试完成标志+故障报告->判定通过/失败

数据流:
  成员系统 --(成员响应, UDP recv_port)--> AHMU 启动测试服务
  AHMU 启动测试服务 --(OHMS 指令, UDP send_port)--> 成员系统
"""
import asyncio
import socket
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, StartupTest
from app.core.websocket_manager import ws_manager
from app.core.icd_startup_test import (
    encode_message, decode_message, message_name,
    MSG_RUN_TEST, MSG_TERMINATE_TEST, MSG_CONTINUE_TEST, MSG_ACK,
    MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED, MSG_DISPLAY_PAGE,
    MSG_TEST_TERMINATED, MSG_MEMBER_STATUS,
)
from app.config import STARTUP_TEST_CONFIG


class TestState(str, Enum):
    """启动测试状态机"""
    IDLE = "idle"
    COMMAND_RECEIVED = "command_received"   # 已发运行指令, 待成员进行中响应
    IN_PROGRESS = "in_progress"             # 测试进行中
    DISPLAY_PAGE = "display_page"           # 收到显示页面响应, 待展示
    WAITING_USER = "waiting_user"           # 交互式页面等待维护人员响应
    COMPLETED = "completed"                 # 测试完成
    SUPPRESSED = "suppressed"               # 被抑制 (提前终止)
    TERMINATED = "terminated"               # 已终止
    ABORTED = "aborted"                     # 中止 (超时/异常)


STATE_NAMES = {
    TestState.IDLE.value: "空闲",
    TestState.COMMAND_RECEIVED.value: "指令已发送",
    TestState.IN_PROGRESS.value: "测试进行中",
    TestState.DISPLAY_PAGE.value: "交互页面展示",
    TestState.WAITING_USER.value: "等待维护人员响应",
    TestState.COMPLETED.value: "已完成",
    TestState.SUPPRESSED.value: "被抑制",
    TestState.TERMINATED.value: "已终止",
    TestState.ABORTED.value: "已中止",
}


class StartupTestService:
    """启动测试引擎 - ARINC624 交互状态机"""

    def __init__(self):
        cfg = STARTUP_TEST_CONFIG
        self._running = False
        self._recv_host = cfg["recv_host"]
        self._recv_port = cfg["recv_port"]
        self._send_port = cfg["send_port"]
        self._ack_timeout = cfg["ack_timeout_sec"]
        self._page_timeout = cfg["interactive_page_timeout_sec"]

        self._rx_sock: Optional[socket.socket] = None
        self._send_sock: Optional[socket.socket] = None

        # test_id -> 运行时状态
        self._active_tests: dict[str, dict] = {}
        # test_id -> 等待消息的 asyncio.Queue
        self._msg_queues: dict[str, asyncio.Queue] = {}
        # test_id -> 维护人员响应的 future (交互式页面)
        self._user_futures: dict[str, asyncio.Future] = {}

        # member_system -> 成员系统状态 (抑制条件/测试完成/故障报告)
        self._member_states: dict[str, dict] = {}

    # ==================== 生命周期 ====================

    async def start(self):
        self._running = True
        self._setup_udp_sockets()
        asyncio.create_task(self._rx_loop())
        logger.info("启动测试服务已启动 (UDP 成员交互 + 抑制监视)")
        return self

    async def stop(self):
        self._running = False
        self._close_udp_sockets()
        logger.info("启动测试服务已停止")

    # ==================== UDP 收发 (Mock AFDX) ====================

    def _setup_udp_sockets(self):
        try:
            self._rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._rx_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._rx_sock.bind((self._recv_host, self._recv_port))
            self._rx_sock.setblocking(False)
            self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            logger.info(f"[StartupTest] UDP 监听 {self._recv_host}:{self._recv_port}, "
                        f"发送到 127.0.0.1:{self._send_port}")
        except OSError as e:
            logger.error(f"[StartupTest] UDP socket 初始化失败: {e}")
            self._close_udp_sockets()

    def _close_udp_sockets(self):
        for sock in (self._rx_sock, self._send_sock):
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
        self._rx_sock = None
        self._send_sock = None

    def _send_udp(self, msg_type: str, **fields):
        """发送消息给成员系统"""
        if not self._send_sock:
            return
        try:
            data = encode_message(msg_type, **fields)
            self._send_sock.sendto(data, ("127.0.0.1", self._send_port))
        except OSError as e:
            logger.error(f"[StartupTest] 发送 {message_name(msg_type)} 失败: {e}")

    async def _rx_loop(self):
        """接收成员系统消息并路由"""
        loop = asyncio.get_event_loop()
        while self._running and self._rx_sock:
            try:
                data, addr = await loop.sock_recvfrom(self._rx_sock, 65535)
                msg = decode_message(data)
                if not msg:
                    logger.warning(f"[StartupTest] 无法解析成员消息: {data[:80]!r}")
                    continue
                await self._handle_member_message(msg)
            except asyncio.CancelledError:
                break
            except OSError:
                await asyncio.sleep(0.5)

    async def _handle_member_message(self, msg: dict):
        """处理成员系统 -> OHMS 消息"""
        msg_type = msg.get("type")
        member = msg.get("member_system", "")
        test_id = msg.get("test_id")

        # 成员系统状态报告: 更新状态 (抑制条件/测试完成/故障报告)
        if msg_type == MSG_MEMBER_STATUS:
            self._member_states[member] = {
                "inhibited": bool(msg.get("inhibited", False)),
                "inhibit_conditions": msg.get("inhibit_conditions", []),
                "test_completed": bool(msg.get("test_completed", False)),
                "fault_reports": msg.get("fault_reports", []),
                "updated_at": datetime.utcnow(),
            }
            logger.info(f"[StartupTest] 成员 {member} 状态: 抑制={self._member_states[member]['inhibited']}, "
                        f"测试完成={self._member_states[member]['test_completed']}")
            # 广播状态供前端/测试观察
            await ws_manager.broadcast("member_status", {
                "member_system": member,
                **self._member_states[member],
                "timestamp": datetime.utcnow().isoformat(),
            })
            # 若成员报告测试完成, 唤醒等待完成的状态机
            if test_id and test_id in self._msg_queues:
                await self._msg_queues[test_id].put(msg)
            return

        # 需 ACK 确认的成员消息: 先回 ACK, 再路由给等待者
        if msg_type in (MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED,
                        MSG_DISPLAY_PAGE, MSG_TEST_TERMINATED):
            self._send_udp(MSG_ACK, member_system=member, test_id=test_id,
                           ack_for=msg_type)
            logger.info(f"[StartupTest] 收到成员 {member} 的 {message_name(msg_type)}, 已回 ACK")

        # 路由到对应测试的等待队列
        if test_id and test_id in self._msg_queues:
            await self._msg_queues[test_id].put(msg)
        else:
            logger.debug(f"[StartupTest] 收到无匹配测试的消息: {message_name(msg_type)}")

    async def _wait_msg(self, test_id: str, expect_types, timeout: float) -> Optional[dict]:
        """等待特定类型的成员消息 (非匹配丢弃), 超时返回 None"""
        queue = self._msg_queues.setdefault(test_id, asyncio.Queue())
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if msg.get("type") in expect_types:
                return msg

    # ==================== 发起启动测试 ====================

    def _check_preconditions(self) -> Optional[str]:
        """前置条件检查: 飞机地面 + 维护开关∈{地面测试,数据加载}

        复用维护模式信号 (all_gear_wow=True 为地面, maintenance_switch)。
        返回 None 表示满足, 否则返回不满足原因描述。
        """
        from app.services.maintenance_mode import maintenance_service
        s = maintenance_service.signals
        if not s.get("wow_valid") or s.get("all_gear_wow") is not True:
            return "前置条件不满足: 飞机未处于地面状态"
        if s.get("maintenance_switch") not in ("ground_test", "data_load"):
            return "前置条件不满足: 维护开关未处于启动测试/加载位置"
        return None

    def _member_inhibited(self, member_system: str) -> bool:
        """查询成员系统当前是否被抑制"""
        st = self._member_states.get(member_system)
        return bool(st and st.get("inhibited"))

    def _member_inhibit_conditions(self, member_system: str) -> list:
        st = self._member_states.get(member_system)
        return (st or {}).get("inhibit_conditions", [])

    async def start_test(self, member_system: str, test_type: str = "interactive") -> dict:
        """发起启动测试"""
        member_system = (member_system or "").strip()
        if not member_system:
            return {"status": "error", "message": "成员系统不能为空"}
        if test_type not in ("interactive", "non_interactive"):
            return {"status": "error", "message": f"无效测试类型: {test_type}"}

        # 前置条件检查
        pre_err = self._check_preconditions()
        if pre_err:
            return {"status": "error", "message": pre_err}

        # 重置成员测试状态 (清除上一次测试残留的 test_completed/fault_reports,
        # 抑制条件 inhibited 保留——那是成员当前真实状态)
        if member_system in self._member_states:
            self._member_states[member_system]["test_completed"] = False
            self._member_states[member_system]["fault_reports"] = []

        # 创建记录
        test_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            test = StartupTest(
                id=test_id, member_system=member_system,
                test_type=test_type, status=TestState.IDLE.value,
                result="pending",
            )
            db.add(test)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[StartupTest] 创建记录失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        self._active_tests[test_id] = {
            "member_system": member_system,
            "test_type": test_type,
            "state": TestState.IDLE,
        }

        asyncio.create_task(self._run_test_flow(test_id, member_system, test_type))
        return {"status": "ok", "test_id": test_id, "state": TestState.IDLE.value,
                "member_system": member_system, "test_type": test_type}

    # ==================== 测试流程状态机 ====================

    async def _run_test_flow(self, test_id: str, member_system: str, test_type: str):
        """执行启动测试流程 (交互式/非交互式)"""
        try:
            # 1. 指令前检查成员抑制条件
            if self._member_inhibited(member_system):
                await self._handle_initial_inhibited(test_id, member_system, test_type)

            # 2. 发运行测试指令
            await self._set_state(test_id, TestState.COMMAND_RECEIVED, member_system)
            self._send_udp(MSG_RUN_TEST, member_system=member_system, test_id=test_id)
            logger.info(f"[StartupTest] 已发运行测试指令 -> {member_system} ({test_type})")

            # 3. 等测试进行中响应
            msg = await self._wait_msg(
                test_id, {MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED}, self._ack_timeout)
            if not msg:
                await self._finish(test_id, TestState.ABORTED, "abort",
                                   "成员系统无测试进行中响应 (超时)")
                return
            if msg["type"] == MSG_TEST_SUPPRESSED:
                await self._finish(test_id, TestState.SUPPRESSED, "abort",
                                   "成员系统被抑制, 无法开始测试", msg)
                return
            await self._set_state(test_id, TestState.IN_PROGRESS, member_system)
            logger.info(f"[StartupTest] {member_system} 测试进行中")

            if test_type == "interactive":
                await self._interactive_flow(test_id, member_system)
            else:
                await self._non_interactive_flow(test_id, member_system)

        except Exception as e:
            logger.error(f"[StartupTest] 测试流程异常: {e}")
            await self._finish(test_id, TestState.ABORTED, "fail", f"异常: {e}")

    async def _interactive_flow(self, test_id: str, member_system: str):
        """交互式测试流程: 显示页面 -> 维护人员响应 -> 继续指令 -> 抑制/完成"""
        # 1. 等显示页面响应
        msg = await self._wait_msg(
            test_id, {MSG_DISPLAY_PAGE, MSG_TEST_SUPPRESSED}, self._ack_timeout)
        if not msg:
            await self._finish(test_id, TestState.ABORTED, "abort", "成员系统无显示页面响应 (超时)")
            return
        if msg["type"] == MSG_TEST_SUPPRESSED:
            await self._finish(test_id, TestState.SUPPRESSED, "abort",
                               "测试执行期间被抑制", msg)
            return

        page_id = msg.get("page_id", "PAGE_1")
        await self._set_state(test_id, TestState.DISPLAY_PAGE, member_system)
        await ws_manager.broadcast("test_display_page", {
            "test_id": test_id, "member_system": member_system,
            "page_id": page_id, "timestamp": datetime.utcnow().isoformat(),
        })

        # 2. 展示交互页面, 等待维护人员响应
        await self._set_state(test_id, TestState.WAITING_USER, member_system)
        resp = await self._wait_user_response(test_id, self._page_timeout)
        if resp is None:
            await self._finish(test_id, TestState.ABORTED, "abort", "等待维护人员响应超时")
            return

        # 3. 发继续测试指令 (含维护人员响应)
        self._send_udp(MSG_CONTINUE_TEST, member_system=member_system, test_id=test_id,
                       page_id=page_id, response=resp)
        logger.info(f"[StartupTest] 已发继续测试指令 -> {member_system}, 响应={resp}")

        # 4. 等测试进行中响应 (继续执行)
        msg = await self._wait_msg(
            test_id, {MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED}, self._ack_timeout)
        if not msg:
            await self._finish(test_id, TestState.ABORTED, "abort", "继续指令无进行中响应 (超时)")
            return
        if msg["type"] == MSG_TEST_SUPPRESSED:
            await self._finish(test_id, TestState.SUPPRESSED, "abort",
                               "继续执行时被抑制", msg)
            return
        await self._set_state(test_id, TestState.IN_PROGRESS, member_system)

        # 5. 继续执行期间监视抑制/终止/完成
        await self._monitor_until_done(test_id, member_system)

    async def _non_interactive_flow(self, test_id: str, member_system: str):
        """非交互式测试流程: 执行直到完成, 检查故障报告判定通过/失败"""
        await self._monitor_until_done(test_id, member_system)

    async def _monitor_until_done(self, test_id: str, member_system: str):
        """监视测试执行直至: 抑制/终止/完成 (成员状态测试完成标志)"""
        while True:
            # 测试已被外部终止/结束 → 退出监视循环
            if test_id not in self._active_tests:
                return
            # 检查成员抑制状态
            if self._member_inhibited(member_system):
                await self._finish(test_id, TestState.SUPPRESSED, "abort",
                                   "成员抑制条件激活, 测试提前终止",
                                   self._member_states[member_system])
                return
            # 检查测试完成标志
            st = self._member_states.get(member_system, {})
            if st.get("test_completed"):
                await self._judge_result(test_id, member_system, st.get("fault_reports", []))
                return
            # 等成员消息 (终止/抑制/状态更新)
            msg = await self._wait_msg(
                test_id,
                {MSG_TEST_SUPPRESSED, MSG_TEST_TERMINATED, MSG_MEMBER_STATUS},
                self._ack_timeout)
            if not msg:
                continue
            if msg["type"] == MSG_TEST_SUPPRESSED:
                await self._finish(test_id, TestState.SUPPRESSED, "abort",
                                   "成员报告测试被抑制", msg)
                return
            if msg["type"] == MSG_TEST_TERMINATED:
                await self._finish(test_id, TestState.TERMINATED, "abort",
                                   "成员确认测试终止", msg)
                return

    async def _judge_result(self, test_id: str, member_system: str, fault_reports: list):
        """根据成员故障报告判定测试通过/失败"""
        # 有激活的测试相关故障报告 -> 失败; 否则通过
        active_faults = [f for f in fault_reports if f.get("active", True)]
        result = "fail" if active_faults else "pass"
        detail = {
            "fault_reports": fault_reports,
            "active_fault_count": len(active_faults),
        }
        await self._finish(test_id, TestState.COMPLETED, result,
                           f"测试完成, 故障报告 {len(active_faults)} 条激活", detail)

    async def _handle_initial_inhibited(self, test_id: str, member_system: str, test_type: str):
        """初始被抑制: 显示抑制条件, 等待清除 (非交互式场景步骤 b~d)"""
        conditions = self._member_inhibit_conditions(member_system)
        logger.info(f"[StartupTest] {member_system} 初始被抑制, 条件={conditions}, 等待清除")
        await ws_manager.broadcast("test_inhibited", {
            "test_id": test_id, "member_system": member_system,
            "inhibit_conditions": conditions, "timestamp": datetime.utcnow().isoformat(),
        })
        # 等待成员报告无抑制 (清除抑制条件)
        timeout = self._page_timeout
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while self._member_inhibited(member_system):
            if loop.time() > deadline:
                await self._finish(test_id, TestState.SUPPRESSED, "abort",
                                   "抑制条件未清除 (超时)")
                return
            await asyncio.sleep(1.0)
        logger.info(f"[StartupTest] {member_system} 抑制条件已清除, 继续发起测试")

    # ==================== 维护人员交互 ====================

    async def _wait_user_response(self, test_id: str, timeout: float):
        """等待维护人员交互响应, 超时返回 None"""
        future = asyncio.get_event_loop().create_future()
        self._user_futures[test_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._user_futures.pop(test_id, None)

    async def send_user_response(self, test_id: str, response: dict) -> dict:
        """维护人员提交交互页面响应"""
        st = self._active_tests.get(test_id)
        if not st:
            return {"status": "error", "message": "测试不存在或已结束"}
        if st.get("state") != TestState.WAITING_USER:
            return {"status": "error", "message": f"当前状态 {st.get('state')} 不等待维护人员响应"}
        future = self._user_futures.get(test_id)
        if future and not future.done():
            future.set_result(response)
            return {"status": "ok", "test_id": test_id}
        return {"status": "error", "message": "无待处理的维护人员响应"}

    async def terminate_test(self, test_id: str) -> dict:
        """终止正在进行的启动测试 (OHMS -> 成员 终止指令)

        发终止指令后直接结束测试 (不再等待终止响应——避免与监视循环竞争消息队列)。
        成员的终止响应仍会收到并回 ACK, 只是不阻塞本接口。
        """
        st = self._active_tests.get(test_id)
        if not st:
            # 测试已结束: 查询数据库给出最终状态, 便于用户理解
            db = SessionLocal()
            try:
                test = db.query(StartupTest).filter(StartupTest.id == test_id).first()
                if test:
                    return {"status": "error",
                            "message": f"测试已结束 (状态={STATE_NAMES.get(test.status, test.status)}, "
                                       f"结果={test.result})"}
            finally:
                db.close()
            return {"status": "error", "message": "测试不存在或已结束"}
        member = st["member_system"]
        self._send_udp(MSG_TERMINATE_TEST, member_system=member, test_id=test_id)
        await self._finish(test_id, TestState.TERMINATED, "abort", "终止指令已发送")
        return {"status": "ok", "test_id": test_id, "state": TestState.TERMINATED.value}

    # ==================== 状态与结果更新 ====================

    async def _set_state(self, test_id: str, state: TestState, member_system: str):
        st = self._active_tests.get(test_id)
        if st:
            st["state"] = state
        self._update_status(test_id, state.value)
        await ws_manager.broadcast("test_state_change", {
            "test_id": test_id, "member_system": member_system,
            "state": state.value, "state_name": STATE_NAMES.get(state.value, state.value),
            "timestamp": datetime.utcnow().isoformat(),
        })

    async def _finish(self, test_id: str, state: TestState, result: str,
                      reason: str, extra: dict = None):
        """结束测试: 更新状态/结果, 广播, 清理"""
        member = (self._active_tests.get(test_id) or {}).get("member_system", "")
        # 先落 result 再落 status, 保证广播 test_state_change 时数据库已是最终态
        self._update_result(test_id, result, reason, extra)
        await self._set_state(test_id, state, member)
        # 广播结束事件
        await ws_manager.broadcast("test_completed", {
            "test_id": test_id, "member_system": member,
            "state": state.value, "result": result, "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        if state == TestState.SUPPRESSED:
            await ws_manager.broadcast("test_suppressed", {
                "test_id": test_id, "member_system": member,
                "reason": reason, "timestamp": datetime.utcnow().isoformat(),
            })
        # 清理
        self._active_tests.pop(test_id, None)
        self._msg_queues.pop(test_id, None)
        self._user_futures.pop(test_id, None)
        logger.info(f"[StartupTest] 测试 {test_id} 结束: state={state.value}, result={result}, reason={reason}")

    def _update_status(self, test_id: str, status: str):
        db = SessionLocal()
        try:
            test = db.query(StartupTest).filter(StartupTest.id == test_id).first()
            if test:
                test.status = status
                if status in (TestState.COMPLETED.value, TestState.SUPPRESSED.value,
                              TestState.TERMINATED.value, TestState.ABORTED.value):
                    test.end_time = datetime.utcnow()
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[StartupTest] 更新状态失败: {e}")
        finally:
            db.close()

    def _update_result(self, test_id: str, result: str, reason: str = None,
                       extra: dict = None):
        import json as _json
        db = SessionLocal()
        try:
            test = db.query(StartupTest).filter(StartupTest.id == test_id).first()
            if test:
                test.result = result
                test.end_time = datetime.utcnow()
                if reason:
                    test.suppression_reason = reason
                if extra:
                    test.test_detail = _json.dumps(extra, ensure_ascii=False, default=str)
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"[StartupTest] 更新结果失败: {e}")
        finally:
            db.close()

    # ==================== 成员系统状态注入 (模拟接口) ====================

    async def report_member_status(self, member_system: str, inhibited: bool = False,
                                   inhibit_conditions: list = None,
                                   test_completed: bool = False,
                                   fault_reports: list = None) -> dict:
        """注入成员系统状态 (供测试端/模拟成员系统调用)"""
        msg = {
            "type": MSG_MEMBER_STATUS,
            "member_system": member_system,
            "inhibited": inhibited,
            "inhibit_conditions": inhibit_conditions or [],
            "test_completed": test_completed,
            "fault_reports": fault_reports or [],
        }
        await self._handle_member_message(msg)
        return {"status": "ok", "member_system": member_system,
                "inhibited": inhibited, "test_completed": test_completed}

    def get_member_state(self, member_system: str) -> dict:
        st = self._member_states.get(member_system)
        if not st:
            return {"member_system": member_system, "known": False}
        return {"member_system": member_system, "known": True, **st}

    # ==================== 查询 ====================

    def get_test_list(self, db: Session, member: Optional[str] = None,
                      page: int = 1, size: int = 20) -> dict:
        query = db.query(StartupTest)
        if member:
            query = query.filter(StartupTest.member_system == member)
        query = query.order_by(StartupTest.start_time.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total, "page": page, "size": size,
            "items": [{
                "id": t.id,
                "member_system": t.member_system,
                "test_type": t.test_type,
                "status": t.status,
                "result": t.result,
                "start_time": t.start_time.isoformat() if t.start_time else None,
                "end_time": t.end_time.isoformat() if t.end_time else None,
                "suppression_reason": t.suppression_reason,
                "test_detail": t.test_detail,
            } for t in items],
        }

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "active_tests": [
                {"test_id": tid, "member_system": st["member_system"],
                 "test_type": st["test_type"], "state": st["state"].value}
                for tid, st in self._active_tests.items()
            ],
            "preconditions": self._check_preconditions() is None,
            "member_states": {
                m: {"inhibited": s["inhibited"], "test_completed": s["test_completed"],
                    "inhibit_conditions": s["inhibit_conditions"]}
                for m, s in self._member_states.items()
            },
        }


# 全局单例
startup_test_service = StartupTestService()
