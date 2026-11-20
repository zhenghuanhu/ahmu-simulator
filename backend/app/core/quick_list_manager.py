"""
用户参数快捷访问列表 - 哈希索引稀疏列表管理算法 (5.4.3 批量创建用户参数快捷访问列表)

性能指标:
  支持创建不少于 100 张用户参数快捷访问列表, 基于哈希索引的稀疏列表管理算法。

数据结构设计:
  1. 全局列表注册表: 链地址法哈希表
     - 键为列表唯一 ID (系统自动生成), 值为列表对象指针
     - 初始容量 128, 负载因子 0.75, 支持动态扩容 (容量翻倍)
  2. 单张参数列表: 有序数组 + 哈希索引的混合结构
     - 参数条目按添加顺序存储在数组中, 支持下标访问 (快捷访问的第 N 个参数)
     - 维护参数名 → 数组下标的哈希映射, O(1) 按名查找
     - 每张列表预分配 64 个参数槽位 (> 50 要求, 留有余量)

算法复杂度:
  - 创建列表: O(1)
  - 添加参数: O(1) 平均, O(n) 最坏 (哈希冲突)
  - 按名访问: O(1) (哈希索引定位下标)
  - 按序访问: O(1) (数组下标)

存储容量评估:
  100 张 × 50 参数 × 64 字节 (名称+值+元数据) ≈ 320KB,
  加上哈希表与索引开销, 总内存 < 1MB, 完全在仿真服务器内存容量内。
"""
from __future__ import annotations
from typing import Optional

# ==================== 容量与性能常量 ====================
MAX_LISTS = 100              # 全局列表数量上限 (不少于 100 张)
MAX_PARAMS_PER_LIST = 50     # 单张列表参数数量上限 (要求 50)
PREALLOC_SLOTS = 64          # 每张列表预分配参数槽位数 (> 50 留有余量)
INITIAL_CAPACITY = 128       # 注册表哈希表初始容量
LOAD_FACTOR = 0.75           # 负载因子, 超过则动态扩容
PARAM_ENTRY_BYTES = 64       # 每参数条目内存估算 (名称 + 值 + 元数据)


class QuickParamList:
    """单张参数快捷访问列表: 有序数组 + 哈希索引混合结构

    有序数组: 参数条目按添加顺序存储, 支持 O(1) 下标访问 (第 N 个参数)
    哈希索引: 参数名 → 数组下标映射, 支持 O(1) 按名查找
    """

    def __init__(self, list_id: int, name: str):
        self.list_id = list_id
        self.name = name
        # 预分配 64 个参数槽位 (> 50 要求)
        self._entries: list[Optional[dict]] = [None] * PREALLOC_SLOTS
        self._index: dict[str, int] = {}   # 参数名 -> 数组下标 (哈希映射)
        self._count = 0

    # ---------- 属性 ----------
    @property
    def size(self) -> int:
        return self._count

    @property
    def capacity(self) -> int:
        return PREALLOC_SLOTS

    # ---------- 参数添加 (O(1) 平均) ----------
    def add_param(self, name: str, value=None) -> bool:
        """追加参数名-值对到数组末尾, 并在哈希索引记录 参数名 → 下标

        校验数量是否达 50 上限 (或预分配 64), 未满才添加。
        返回是否添加成功 (False = 已达上限)。
        """
        if self._count >= MAX_PARAMS_PER_LIST:
            return False
        if name in self._index:
            # 已存在: 更新值 (不重复添加)
            self._entries[self._index[name]]["value"] = value
            return True
        idx = self._count
        self._entries[idx] = {"name": name, "value": value}
        self._index[name] = idx
        self._count += 1
        return True

    # ---------- 快速访问 ----------
    def get_by_name(self, name: str) -> Optional[dict]:
        """按名称访问: 哈希索引直接定位数组下标, 一次查找完成 O(1)"""
        idx = self._index.get(name)
        if idx is None:
            return None
        return self._entries[idx]

    def get_by_index(self, idx: int) -> Optional[dict]:
        """按序号访问: 直接数组下标访问 O(1)"""
        if idx < 0 or idx >= self._count:
            return None
        return self._entries[idx]

    def contains(self, name: str) -> bool:
        return name in self._index

    def remove_param(self, name: str) -> bool:
        """删除参数 (前移后续条目, 保持添加顺序; O(n), 非性能关键路径)"""
        idx = self._index.get(name)
        if idx is None:
            return False
        for i in range(idx, self._count - 1):
            self._entries[i] = self._entries[i + 1]
            self._index[self._entries[i]["name"]] = i
        self._entries[self._count - 1] = None
        del self._index[name]
        self._count -= 1
        return True

    def param_names(self) -> list:
        return [self._entries[i]["name"] for i in range(self._count)]


class _HashMap:
    """链地址法哈希表 (全局列表注册表底层结构)

    初始容量 128, 负载因子 0.75, 超过则容量翻倍并重新哈希 (动态扩容)。
    容量保持 2 的幂, 用位运算取模 (key & (capacity-1)) 加速定位。
    """

    def __init__(self, capacity: int = INITIAL_CAPACITY, load_factor: float = LOAD_FACTOR):
        self._capacity = capacity
        self._load_factor = load_factor
        self._buckets: list[list] = [[] for _ in range(capacity)]
        self._size = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def size(self) -> int:
        return self._size

    @property
    def load_factor(self) -> float:
        return self._size / self._capacity

    def _slot(self, key) -> int:
        """哈希定位: 容量为 2 的幂, 位运算取模"""
        return hash(key) & (self._capacity - 1)

    def _resize(self):
        """动态扩容: 容量翻倍, 重新哈希所有条目"""
        new_capacity = self._capacity * 2
        new_buckets = [[] for _ in range(new_capacity)]
        for bucket in self._buckets:
            for k, v in bucket:
                new_buckets[hash(k) & (new_capacity - 1)].append((k, v))
        self._capacity = new_capacity
        self._buckets = new_buckets

    def put(self, key, value):
        """插入/更新 (O(1) 平均)"""
        if self.load_factor >= self._load_factor:
            self._resize()
        slot = self._slot(key)
        for i, (k, _v) in enumerate(self._buckets[slot]):
            if k == key:
                self._buckets[slot][i] = (key, value)
                return
        self._buckets[slot].append((key, value))
        self._size += 1

    def get(self, key, default=None):
        slot = self._slot(key)
        for k, v in self._buckets[slot]:
            if k == key:
                return v
        return default

    def remove(self, key):
        slot = self._slot(key)
        for i, (k, v) in enumerate(self._buckets[slot]):
            if k == key:
                del self._buckets[slot][i]
                self._size -= 1
                return v
        return None

    def items(self) -> list:
        result = []
        for bucket in self._buckets:
            for k, v in bucket:
                result.append((k, v))
        return result


class QuickListRegistry:
    """全局列表注册表: 哈希表 (键=列表ID, 值=列表对象指针)

    提供列表创建 (O(1)) / 参数添加 (O(1) 平均) / 快速访问 (O(1)) 的统一入口。
    """

    def __init__(self):
        self._table = _HashMap(INITIAL_CAPACITY, LOAD_FACTOR)
        self._seq = 0   # 列表 ID 自增序列 (系统自动生成)

    # ---------- 属性 ----------
    @property
    def count(self) -> int:
        return self._table.size

    @property
    def capacity(self) -> int:
        return self._table.capacity

    @property
    def load_factor(self) -> float:
        return self._table.load_factor

    # ---------- 列表创建 (O(1)) ----------
    def create_list(self, name: str, params: Optional[list] = None) -> Optional[int]:
        """接收创建请求: 校验全局列表数量是否达 100 上限, 未满则插入并返回列表 ID

        返回 None 表示已达上限。
        """
        if self.count >= MAX_LISTS:
            return None
        self._seq += 1
        list_id = self._seq
        lst = QuickParamList(list_id, name)
        for p in (params or []):
            lst.add_param(p)
        self._table.put(list_id, lst)
        return list_id

    def load_existing(self, list_id: int, name: str, params: Optional[list] = None):
        """从持久化存储重建索引 (启动时加载已有列表, 不经上限校验)"""
        lst = QuickParamList(list_id, name)
        for p in (params or []):
            lst.add_param(p)
        self._table.put(list_id, lst)
        self._seq = max(self._seq, list_id)

    # ---------- 列表查询/删除 ----------
    def get_list(self, list_id: int) -> Optional[QuickParamList]:
        return self._table.get(list_id)

    def delete_list(self, list_id: int) -> bool:
        return self._table.remove(list_id) is not None

    def all_lists(self) -> list:
        return [lst for _lid, lst in self._table.items()]

    # ---------- 参数添加 / 访问 (O(1)) ----------
    def add_param(self, list_id: int, name: str, value=None) -> bool:
        lst = self._table.get(list_id)
        if lst is None:
            return False
        return lst.add_param(name, value)

    def get_param_by_name(self, list_id: int, name: str) -> Optional[dict]:
        """按名称快速访问 O(1)"""
        lst = self._table.get(list_id)
        return lst.get_by_name(name) if lst else None

    def get_param_by_index(self, list_id: int, idx: int) -> Optional[dict]:
        """按序号快速访问 O(1)"""
        lst = self._table.get(list_id)
        return lst.get_by_index(idx) if lst else None

    def remove_param(self, list_id: int, name: str) -> bool:
        lst = self._table.get(list_id)
        return lst.remove_param(name) if lst else False

    # ---------- 容量评估 ----------
    def memory_usage_bytes(self) -> int:
        """估算内存占用: 参数条目 + 哈希表桶指针开销"""
        total = 0
        for _lid, lst in self._table.items():
            total += lst.size * PARAM_ENTRY_BYTES
        total += self._table.capacity * 8   # 桶指针 (8 字节/桶)
        return total

    def stats(self) -> dict:
        """算法统计 (可观测性)"""
        return {
            "list_count": self.count,
            "max_lists": MAX_LISTS,
            "registry_capacity": self.capacity,
            "load_factor": round(self.load_factor, 4),
            "memory_usage_bytes": self.memory_usage_bytes(),
            "memory_usage_kb": round(self.memory_usage_bytes() / 1024, 2),
            "prealloc_slots_per_list": PREALLOC_SLOTS,
            "max_params_per_list": MAX_PARAMS_PER_LIST,
        }


# 全局单例
quick_list_manager = QuickListRegistry()
