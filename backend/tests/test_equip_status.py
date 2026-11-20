"""
设备标识状态数据接收服务 (5.4.2) 单元测试
验证异步延迟处理算法四要点:
  1) 消息接收与时间戳标记 (t_arrival + 成员独立队列)
  2) 处理调度策略 (批量处理 + 最新优先)
  3) 延迟约束 (100ms 处理预算, 超时告警)
  4) 频率无关性 (队列大部分为空)
独立脚本, 无需后端启动 (直接实例化服务)。
"""
from __future__ import annotations
import asyncio
import json
import sys
import time
from pathlib import Path

# 将 backend 目录加入 sys.path, 使 `app` 包可导入 (独立脚本运行)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_database
from app.services.equip_status import EquipStatusService


def make_frame(member: str, seq: int) -> bytes:
    return json.dumps({
        "member_system": member,
        "equip_id": 3001,
        "status": {"seq": seq, "operational": True, "mode": "normal"},
    }).encode("utf-8")


def test_enqueue_latest_first(svc: EquipStatusService):
    """测试 1: 消息接收 + 时间戳标记 + 最新优先"""
    for i in range(3):
        svc._enqueue_frame(make_frame("MEM001", i))
    q = svc._queues.get("MEM001")
    assert q is not None, "应创建成员独立队列"
    assert len(q) == 1, f"队列应仅保留最新 1 帧, 实际 {len(q)}"
    assert svc._total_dropped == 2, f"应丢弃 2 帧过时数据, 实际 {svc._total_dropped}"
    assert svc._total_received == 3, f"应接收 3 帧, 实际 {svc._total_received}"
    # 取出的帧应带 t_arrival, 且为最新帧 (seq=2)
    frame = q.popleft()
    assert "t_arrival" in frame, "帧应附加 t_arrival 时间戳"
    assert frame["status"]["seq"] == 2, "最新优先: 应保留 seq=2 的最新帧"
    print(f"  [1] 最新优先 OK: 接收3帧 -> 队列1帧(seq=2), 丢弃2帧, t_arrival={frame['t_arrival']:.3f}")


def test_process_updates_status(svc: EquipStatusService):
    """测试 2: 处理调度 + 设备状态记录更新 + t_last_process"""
    svc._enqueue_frame(make_frame("MEM002", 100))
    # 同步驱动处理 (直接调用 _process_one, 无需等待 10Hz 循环)
    asyncio.run(_run_process_one(svc))
    assert "MEM002" in svc._device_status, "处理后应更新设备状态记录"
    assert svc._device_status["MEM002"]["status"]["seq"] == 100
    assert svc._t_last_process.get("MEM002", 0) > 0, "应更新 t_last_process"
    assert svc._total_processed == 1, f"应处理 1 帧, 实际 {svc._total_processed}"
    print("  [2] 处理调度 OK: 设备状态已更新, t_last_process 已记录")


async def _run_process_one(svc: EquipStatusService):
    await svc._process_batch()


def test_timeout_budget(svc: EquipStatusService):
    """测试 3: 100ms 处理预算 + 超时告警"""
    # 注入 200ms 模拟处理耗时 (> 100ms 预算)
    svc._simulated_process_delay = 0.2
    svc._enqueue_frame(make_frame("MEM003", 200))
    asyncio.run(_run_process_one(svc))
    assert svc._timeout_count == 1, f"应触发 1 次处理超时告警, 实际 {svc._timeout_count}"
    assert "MEM003" not in svc._device_status, "超时后不应更新设备状态记录"
    # 恢复正常耗时, 再处理应成功
    svc._simulated_process_delay = 0.0
    svc._enqueue_frame(make_frame("MEM003", 201))
    asyncio.run(_run_process_one(svc))
    assert "MEM003" in svc._device_status, "恢复正常后应成功处理"
    assert svc._device_status["MEM003"]["status"]["seq"] == 201
    print(f"  [3] 超时预算 OK: 200ms 处理触发超时告警(timeout_count={svc._timeout_count}), 恢复后正常处理")


def test_frequency_independence(svc: EquipStatusService):
    """测试 4: 频率无关性 (低频成员队列大部分为空)"""
    # 未收到任何帧的成员不占用队列
    empty = sum(1 for q in svc._queues.values() if not q)
    assert empty == len(svc._queues), "所有队列处理完后应为空 (低频成员零占用)"
    stats = svc.get_processing_stats()
    assert stats["total_received"] == svc._total_received
    assert stats["total_processed"] == svc._total_processed
    assert stats["total_dropped"] == svc._total_dropped
    print(f"  [4] 频率无关性 OK: 队列全部清空, 统计={json.dumps(stats, ensure_ascii=False)}")


def main():
    print("=" * 60)
    print("设备标识状态数据接收服务 (5.4.2) 单元测试")
    print("=" * 60)
    # 初始化数据库 (创建 equip_status_records 表)
    init_database()

    svc = EquipStatusService()
    # 手动构建小规模成员名录 (避免依赖 full 400 名录的随机性)
    svc._member_catalog = [{"code": f"MEM{i:03d}", "name": f"M{i:03d}", "ata": "45"}
                           for i in range(1, 6)]
    svc._member_freq = {m["code"]: 1.0 for m in svc._member_catalog}

    test_enqueue_latest_first(svc)
    test_process_updates_status(svc)
    test_timeout_budget(svc)
    test_frequency_independence(svc)

    print("=" * 60)
    print("全部测试通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
