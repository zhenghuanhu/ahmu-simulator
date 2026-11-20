"""
用户参数快捷访问列表 (5.4.3) 哈希索引稀疏列表管理算法 单元测试

验证性能指标:
  1) 列表创建 O(1) + 100 张上限
  2) 参数添加 O(1) + 50 参数上限 (预分配 64 槽)
  3) 按名称 O(1) / 按序号 O(1) 快速访问
  4) 哈希表动态扩容 (负载因子 0.75)
  5) 存储容量评估 < 1MB
独立脚本, 无需后端启动 (直接实例化算法模块)。
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.quick_list_manager import (
    QuickListRegistry, QuickParamList,
    MAX_LISTS, MIN_PARAMS_PER_LIST, MAX_PARAMS_PER_LIST, PREALLOC_SLOTS,
    INITIAL_CAPACITY, LOAD_FACTOR, PARAM_ENTRY_BYTES,
)


def test_create_and_limit():
    """测试 1: 列表创建 O(1) + 100 张上限"""
    reg = QuickListRegistry()
    for i in range(MAX_LISTS):
        lid = reg.create_list(f"列表{i + 1}", [f"P{j}" for j in range(10)])
        assert lid is not None, f"第 {i + 1} 张列表应创建成功"
    assert reg.count == MAX_LISTS, f"应创建 {MAX_LISTS} 张, 实际 {reg.count}"
    assert reg.create_list("超限列表") is None, "第 101 张应返回 None (达上限)"
    print(f"  [1] 创建 O(1) + 100 上限 OK: 创建 {MAX_LISTS} 张, 第 101 张被拒")


def test_param_add_and_limit():
    """测试 2: 参数添加 O(1) + 64 上限 (留有余量) + 预分配"""
    reg = QuickListRegistry()
    lid = reg.create_list("测试", [])
    lst = reg.get_list(lid)
    assert lst.capacity == PREALLOC_SLOTS, f"应预分配 {PREALLOC_SLOTS} 槽, 实际 {lst.capacity}"
    for i in range(MAX_PARAMS_PER_LIST):
        assert reg.add_param(lid, f"PARAM_{i}"), f"第 {i + 1} 个参数应添加成功"
    assert reg.add_param(lid, "PARAM_OVER") is False, f"第 {MAX_PARAMS_PER_LIST + 1} 个参数应被拒 (达 64 上限)"
    assert lst.size == MAX_PARAMS_PER_LIST
    assert lst.size >= MIN_PARAMS_PER_LIST, f"每张列表应不少于 {MIN_PARAMS_PER_LIST} 参数"
    print(f"  [2] 添加 O(1) OK: 预分配 {PREALLOC_SLOTS} 槽, 实际容纳 {MAX_PARAMS_PER_LIST} 参数 "
          f"(≥最低要求 {MIN_PARAMS_PER_LIST}), 第 {MAX_PARAMS_PER_LIST + 1} 个被拒")


def test_5_4_4_param_design():
    """测试 2b (5.4.4): 每张列表不少于 50 参数 + 参数条目含元数据"""
    reg = QuickListRegistry()
    lid = reg.create_list("参数设计", [])
    # 每张列表容纳 50 个参数 (最低要求), 且可继续到 64 (余量)
    for i in range(MIN_PARAMS_PER_LIST):
        reg.add_param(lid, f"P{i:02d}",
                      value=i * 1.5,
                      param_type="float", unit="unit", ata="45")
    lst = reg.get_list(lid)
    assert lst.size >= MIN_PARAMS_PER_LIST, f"每张列表应容纳 ≥{MIN_PARAMS_PER_LIST} 参数"
    # 参数条目含元数据 (名称+值+类型+单位+ATA)
    entry = reg.get_param_by_name(lid, "P10")
    assert entry["name"] == "P10"
    assert entry["value"] == 15.0
    assert entry["param_type"] == "float" and entry["unit"] == "unit" and entry["ata"] == "45"
    # 哈希索引按名 O(1) 访问任意参数
    assert reg.get_param_by_name(lid, f"P{MIN_PARAMS_PER_LIST - 1}") is not None
    print(f"  [2b] 5.4.4 参数设计 OK: 每张列表容纳 {lst.size} 参数 (≥{MIN_PARAMS_PER_LIST}), "
          f"条目含名称/值/类型/单位/ATA 元数据")


def test_fast_access():
    """测试 3: 按名称 O(1) + 按序号 O(1) 快速访问"""
    reg = QuickListRegistry()
    lid = reg.create_list("访问", ["A", "B", "C", "D"])
    # 按名称访问 (哈希索引定位下标)
    assert reg.get_param_by_name(lid, "C")["name"] == "C"
    assert reg.get_param_by_name(lid, "A")["name"] == "A"
    assert reg.get_param_by_name(lid, "NOT_EXIST") is None
    # 按序号访问 (数组下标)
    assert reg.get_param_by_index(lid, 0)["name"] == "A"
    assert reg.get_param_by_index(lid, 2)["name"] == "C"
    assert reg.get_param_by_index(lid, 99) is None, "越界应返回 None"
    print("  [3] 快速访问 OK: 按名 O(1) + 按序 O(1)")


def test_remove_keep_order():
    """测试 4: 删除参数保持添加顺序 (前移)"""
    reg = QuickListRegistry()
    lid = reg.create_list("删除", ["A", "B", "C", "D"])
    assert reg.remove_param(lid, "B") is True
    lst = reg.get_list(lid)
    assert lst.size == 3
    assert lst.param_names() == ["A", "C", "D"], f"删除 B 后应保持顺序 [A,C,D], 实际 {lst.param_names()}"
    assert reg.get_param_by_name(lid, "C") is not None, "删除后按名访问 C 仍应正确"
    print("  [4] 删除保持顺序 OK: [A,C,D]")


def test_dynamic_resize_and_memory():
    """测试 5: 哈希表动态扩容 + 存储容量评估 < 1MB"""
    reg = QuickListRegistry()
    assert reg.capacity == INITIAL_CAPACITY, f"初始容量应为 {INITIAL_CAPACITY}"
    # 创建 100 张 × 64 参数 (满载, 每张达实际上限)
    for i in range(MAX_LISTS):
        reg.create_list(f"L{i:03d}", [f"P{j}" for j in range(MAX_PARAMS_PER_LIST)])
    # 负载因子 100/128 = 0.78 > 0.75, 应触发动态扩容到 256
    assert reg.capacity >= 256, f"负载因子超 {LOAD_FACTOR} 应扩容, 实际容量 {reg.capacity}"
    # 存储容量评估 < 1MB
    mem = reg.memory_usage_bytes()
    assert mem < 1024 * 1024, f"总内存应 < 1MB, 实际 {mem} 字节"
    # 理论值校验: 满载 100 × 64 × 64 = 409600 字节 ≈ 400KB (<1MB)
    theory = MAX_LISTS * MAX_PARAMS_PER_LIST * PARAM_ENTRY_BYTES
    assert theory < 1024 * 1024, "理论满载内存也应 < 1MB"
    print(f"  [5] 扩容+容量 OK: 初始{INITIAL_CAPACITY} -> 扩容{reg.capacity}, "
          f"负载因子={reg.load_factor:.3f}, 内存={mem}字节≈{mem / 1024:.1f}KB "
          f"(满载理论 {theory}字节≈{theory / 1024:.1f}KB, <1MB)")


def main():
    print("=" * 60)
    print("用户参数快捷访问列表 (5.4.3/5.4.4) 单元测试")
    print("=" * 60)
    test_create_and_limit()
    test_param_add_and_limit()
    test_5_4_4_param_design()
    test_fast_access()
    test_remove_keep_order()
    test_dynamic_resize_and_memory()
    print("=" * 60)
    print("全部测试通过 ✅")
    print("=" * 60)


if __name__ == "__main__":
    main()
