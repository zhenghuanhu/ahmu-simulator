"""
启动测试服务
- ARINC624状态机引擎
- 交互式/非交互式测试流程
- ACK确认机制
- 抑制条件监控
"""
import asyncio
from datetime import datetime
from typing import Optional
from enum import Enum
from loguru import logger
from sqlalchemy.orm import Session

from app.database import SessionLocal, StartupTest
from app.core.websocket_manager import ws_manager


class TestState(str, Enum):
    IDLE = "idle"
    COMMAND_RECEIVED = "command_received"
    IN_PROGRESS = "in_progress"
    WAITING_ACK = "waiting_ack"
    DISPLAY_PAGE = "display_page"
    COMPLETED = "completed"
    SUPPRESSED = "suppressed"
    ABORTED = "aborted"


class StartupTestService:
    """启动测试引擎 - ARINC624状态机"""

    def __init__(self):
        self._running = False
        self._active_tests: dict[str, dict] = {}  # test_id -> state info

    async def start(self):
        self._running = True
        logger.info("启动测试服务已启动")
        asyncio.create_task(self._suppression_monitor_loop())

    async def stop(self):
        self._running = False
        logger.info("启动测试服务已停止")

    async def start_test(self, member_system: str, test_type: str = "interactive") -> dict:
        """发起启动测试 - 状态流转: idle -> command_received -> in_progress"""
        db = SessionLocal()
        try:
            test = StartupTest(
                member_system=member_system,
                test_type=test_type,
                status=TestState.COMMAND_RECEIVED.value,
                result="pending",
            )
            db.add(test)
            db.commit()
            db.refresh(test)
            test_id = test.id
        except Exception as e:
            db.rollback()
            logger.error(f"创建启动测试失败: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            db.close()

        self._active_tests[test_id] = {
            "member_system": member_system,
            "state": TestState.COMMAND_RECEIVED,
            "steps": [],
        }

        # WebSocket推送状态变化
        await ws_manager.broadcast("test_state_change", {
            "test_id": test_id,
            "member_system": member_system,
            "state": TestState.COMMAND_RECEIVED.value,
            "timestamp": datetime.utcnow().isoformat(),
        })

        # 异步执行测试流程
        asyncio.create_task(self._execute_test(test_id, member_system, test_type))

        return {"status": "ok", "test_id": test_id, "state": TestState.COMMAND_RECEIVED.value}

    async def _execute_test(self, test_id: str, member_system: str, test_type: str):
        """执行测试流程 - 状态机流转"""
        state_info = self._active_tests.get(test_id)
        if not state_info:
            return

        try:
            # command_received -> in_progress
            await self._update_state(test_id, TestState.IN_PROGRESS, member_system)
            await asyncio.sleep(1.0)

            # 执行测试步骤 (Mock)
            steps = [
                {"name": "电源自检", "result": "pass", "detail": "供电正常"},
                {"name": "通信自检", "result": "pass", "detail": "A664/A429链路正常"},
                {"name": "内存自检", "result": "pass", "detail": "RAM/Flash检查通过"},
                {"name": "处理器自检", "result": "pass", "detail": "CPU运行正常"},
                {"name": "IO自检", "result": "pass", "detail": "数字/模拟IO正常"},
            ]
            state_info["steps"] = steps

            for step in steps:
                step["timestamp"] = datetime.utcnow().isoformat()
                await ws_manager.broadcast("test_step", {
                    "test_id": test_id,
                    "member_system": member_system,
                    "step": step,
                })
                await asyncio.sleep(0.5)

            # in_progress -> waiting_ack (交互式测试需要用户确认)
            if test_type == "interactive":
                await self._update_state(test_id, TestState.WAITING_ACK, member_system)
                await ws_manager.broadcast("test_interaction", {
                    "test_id": test_id,
                    "member_system": member_system,
                    "message": "测试完成, 请确认ACK",
                    "timestamp": datetime.utcnow().isoformat(),
                })
                # 等待ACK (最多30秒超时)
                state_info["waiting_ack"] = True
                try:
                    await asyncio.wait_for(
                        self._wait_for_ack(test_id), timeout=30.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"测试 {test_id} 等待ACK超时")
                    await self._update_state(test_id, TestState.ABORTED, member_system)
                    self._update_result(test_id, "abort")
                    return

            # waiting_ack/display_page -> completed
            await self._update_state(test_id, TestState.COMPLETED, member_system)
            self._update_result(test_id, "pass")
            await ws_manager.broadcast("test_completed", {
                "test_id": test_id,
                "member_system": member_system,
                "result": "pass",
                "steps": steps,
                "timestamp": datetime.utcnow().isoformat(),
            })

        except Exception as e:
            logger.error(f"测试执行异常: {e}")
            await self._update_state(test_id, TestState.ABORTED, member_system)
            self._update_result(test_id, "fail")

        finally:
            self._active_tests.pop(test_id, None)

    async def _wait_for_ack(self, test_id: str):
        """等待ACK确认"""
        future = asyncio.get_event_loop().create_future()
        state_info = self._active_tests.get(test_id)
        if state_info:
            state_info["ack_future"] = future
        await future

    async def send_ack(self, test_id: str) -> dict:
        """发送ACK确认"""
        state_info = self._active_tests.get(test_id)
        if not state_info:
            return {"status": "error", "message": "测试不存在或已完成"}

        future = state_info.get("ack_future")
        if future and not future.done():
            future.set_result(True)
            logger.info(f"ACK已确认: {test_id}")
            return {"status": "ok", "test_id": test_id}
        return {"status": "error", "message": "无需ACK或已超时"}

    async def _update_state(self, test_id: str, state: TestState, member_system: str):
        """更新测试状态"""
        db = SessionLocal()
        try:
            test = db.query(StartupTest).filter(StartupTest.id == test_id).first()
            if test:
                test.status = state.value
                if state in (TestState.COMPLETED, TestState.ABORTED):
                    test.end_time = datetime.utcnow()
                db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"更新测试状态失败: {e}")
        finally:
            db.close()

        state_info = self._active_tests.get(test_id)
        if state_info:
            state_info["state"] = state

        await ws_manager.broadcast("test_state_change", {
            "test_id": test_id,
            "member_system": member_system,
            "state": state.value,
            "timestamp": datetime.utcnow().isoformat(),
        })

    def _update_result(self, test_id: str, result: str):
        """更新测试结果"""
        db = SessionLocal()
        try:
            test = db.query(StartupTest).filter(StartupTest.id == test_id).first()
            if test:
                test.result = result
                db.commit()
        except Exception as e:
            db.rollback()
        finally:
            db.close()

    async def _suppression_monitor_loop(self):
        """抑制条件监控循环"""
        while self._running:
            try:
                # Mock: 监控成员系统抑制条件
                for test_id, state_info in list(self._active_tests.items()):
                    # 检查抑制条件 (Mock: 1%概率被抑制)
                    if random.random() < 0.01:
                        await self._update_state(test_id, TestState.SUPPRESSED,
                                                  state_info["member_system"])
                        self._update_result(test_id, "abort")
                        await ws_manager.broadcast("test_suppressed", {
                            "test_id": test_id,
                            "member_system": state_info["member_system"],
                            "reason": "抑制条件满足",
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                        self._active_tests.pop(test_id, None)
            except Exception as e:
                logger.error(f"抑制监控异常: {e}")
            await asyncio.sleep(1.0)

    def get_test_list(self, db: Session, member: Optional[str] = None,
                      page: int = 1, size: int = 20) -> dict:
        """获取测试记录列表"""
        query = db.query(StartupTest)
        if member:
            query = query.filter(StartupTest.member_system == member)
        query = query.order_by(StartupTest.start_time.desc())
        total = query.count()
        items = query.offset((page - 1) * size).limit(size).all()
        return {
            "total": total,
            "page": page,
            "size": size,
            "items": [{
                "id": t.id,
                "member_system": t.member_system,
                "test_type": t.test_type,
                "status": t.status,
                "result": t.result,
                "start_time": t.start_time.isoformat() if t.start_time else None,
                "end_time": t.end_time.isoformat() if t.end_time else None,
            } for t in items],
        }


# 全局单例
startup_test_service = StartupTestService()
