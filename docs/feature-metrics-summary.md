# AHMU 仿真器功能指标汇总

> 本文档汇总 AHMU 仿真器当前**已确认完成**的功能涉及的功能指标，按技术指标章节（5.4 性能指标系列）组织。
>
> **范围说明**：本文档聚焦用户此前提供的技术指标章节 5.4.1 ~ 5.4.4（故障数据接收 / 设备标识状态数据接收 / 快捷访问列表管理 / 列表参数设计），均已完成实现并通过验证。
>
> **标注约定**：
> - ✅ 已实现并通过验证；⚠️ 部分实现或需确认。
> - 凡口径或代码对应关系不明确、或为理论值/估算值而非实测值的，均以「待确认」标注，不臆断。

---

## 一、5.4.1 不同频率故障数据接收

### 1.1 指标总表

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 实现状态 |
| --- | --- | --- | --- | --- |
| 接收频率档位 | 支持 0.5Hz / 1Hz / 2Hz / 4Hz 四档，对应周期 2000ms / 1000ms / 500ms / 250ms | `config.py` `FAULT_DIAGNOSIS_CONFIG["report_frequencies"]` | 成员系统按不同频率上报故障数据 | ✅ |
| 接收通道划分 | 每个「成员系统-频率」组合建立独立通道对象，含 `channel_id` / `period` / `t_last_recv` / `timeout_threshold` / `drop_count` / `alerted` | 运行时由成员名录 + 频率构建 | 故障接收健康监控的基础数据结构 | ✅ |
| 接收超时阈值 | `timeout_threshold = 1.5 × period`（允许抖动） | 常量 `TIMEOUT_FACTOR = 1.5` | 判定「该收未收」丢帧的阈值 | ✅ |
| 丢帧判定与告警 | 1Hz 巡检，`elapsed > 1.5×period` 判丢帧；连续丢帧 ≥ 3 次上报「成员系统通信中断」告警 | 常量 `COMM_DROP_THRESHOLD = 3` | 成员系统通信异常检测与告警 | ✅ |
| 统一接收轮询粒度 | 设计为 50Hz（20ms）轮询；实际以 asyncio 事件驱动等价实现（数据到达即唤醒，延迟接近 0，优于轮询） | `_receive_loop` 调度粒度 `TICK = 0.05s` | 统一调度不同频率的接收通道 | ✅（等价实现，见「待确认」） |

### 1.2 实现代码

| 指标 | 文件路径 | 关键函数 / 类 / 常量 | 调用位置说明 |
| --- | --- | --- | --- |
| 接收频率档位 | `backend/app/config.py:88` | `report_frequencies` | 配置源，服务启动时读取 |
| 接收频率档位 | `backend/app/services/fault_diagnosis.py:39` | `REPORT_FREQUENCIES` | fallback 默认值，与 config 对齐 |
| 接收频率档位 | `backend/app/services/fault_diagnosis.py:162` | `random.choice(REPORT_FREQUENCIES)` | `_build_member_catalog` 为每成员分配频率 |
| 接收通道划分 | `backend/app/services/fault_diagnosis.py:48` | `class FaultReceiveChannel` | 通道对象 dataclass，`__post_init__` 计算 period/timeout |
| 接收通道划分 | `backend/app/services/fault_diagnosis.py:165` | `def _build_rx_channels` | `start()` 中调用，构建 `_rx_channels` |
| 接收超时阈值 | `backend/app/services/fault_diagnosis.py:44` | `TIMEOUT_FACTOR = 1.5` | 常量 |
| 接收超时阈值 | `backend/app/services/fault_diagnosis.py:63` | `timeout_threshold = period * TIMEOUT_FACTOR` | 通道 `__post_init__` 内计算 |
| 丢帧判定与告警 | `backend/app/services/fault_diagnosis.py:404` | `async def _health_monitor_loop` | 1Hz 巡检协程，`start()` 中 `create_task` |
| 丢帧判定与告警 | `backend/app/services/fault_diagnosis.py:424` | `drop_count >= COMM_DROP_THRESHOLD` | 告警触发条件 |
| 丢帧判定与告警 | `backend/app/services/fault_diagnosis.py:427` | `ws_manager.broadcast("fault_comm_interrupt", ...)` | 经 WebSocket 上报告警 |
| 收到数据更新 | `backend/app/services/fault_diagnosis.py:72` | `def mark_received` | 刷新 `t_last_recv`、清零 `drop_count` |
| 收到数据更新 | `backend/app/services/fault_diagnosis.py:370` | `_handle_fault_message` | 解析出成员后调用 `mark_received()` |
| 状态查询 | `backend/app/services/fault_diagnosis.py:440` | `def get_rx_channel_status` | 可观测性接口 |
| 统一接收轮询粒度 | `backend/app/services/fault_diagnosis.py:305` | `async def _receive_loop`，`TICK = 0.05` | 自动上报调度循环 |

### 1.3 待确认

- **「理论最大响应延迟 20ms」**：设计文档的轮询理论值；代码采用事件驱动实现，实际延迟接近 0，但**未做显式延迟测量**，20ms 仅为设计口径参考，非实测值。

---

## 二、5.4.2 不同频率设备标识状态数据接收

### 2.1 指标总表

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 实现状态 |
| --- | --- | --- | --- | --- |
| 接收频率档位 | 支持 0.25Hz / 0.5Hz / 1Hz / 2Hz 四档（注意与 5.4.1 的 0.5/1/2/4Hz 不同） | `config.py` `EQUIP_STATUS_CONFIG["report_frequencies"]` | 成员系统按不同频率发送设备标识状态数据 | ✅ |
| 消息时间戳标记 | 收到帧后附加到达时间戳 `t_arrival`（`time.monotonic()`），不立即处理 | 运行时接收时生成 | 支撑异步延迟处理与统计 | ✅ |
| 成员独立队列 | 每成员独立处理队列，采用 `deque(maxlen=1)` 实现「最新优先」，积压时丢弃中间过时数据 | 运行时惰性创建 | 周期性状态数据的去重与最新保留 | ✅ |
| 处理调度频率 | 处理协程以 10Hz（100ms 间隔）批量取出各队列数据 | `EQUIP_STATUS_CONFIG["process_hz"] = 10` | 批量处理调度节奏 | ✅ |
| 单次处理时间预算 | 单次处理预算上限 100ms（远小于最小周期 500ms 的 20%），超时告警并立即返回 | `EQUIP_STATUS_CONFIG["timeout_budget_ms"] = 100` | 防止处理队列阻塞 | ✅ |
| 频率无关性 | 队列惰性创建，低频成员队列大部分为空，仅在收到消息时短暂唤醒，CPU 占用几乎为零 | 运行时行为 | 资源集中服务高频成员 | ✅（设计说明，见「待确认」） |

### 2.2 实现代码

| 指标 | 文件路径 | 关键函数 / 类 / 常量 | 调用位置说明 |
| --- | --- | --- | --- |
| 接收频率档位 | `backend/app/config.py:260` | `EQUIP_STATUS_CONFIG`（`report_frequencies`） | 配置源 |
| 接收频率档位 | `backend/app/services/equip_status.py:47` | `class EquipStatusService` | 服务单例，`__init__` 读取配置 |
| 消息时间戳标记 | `backend/app/services/equip_status.py:171` | `def _enqueue_frame`（`frame["t_arrival"]`） | 接收循环收到帧后调用 |
| 成员独立队列 | `backend/app/services/equip_status.py:163` | `def _get_queue`（`deque(maxlen=1)`） | 惰性创建每成员队列 |
| 成员独立队列 | `backend/app/services/equip_status.py:167` | `q = deque(maxlen=1)` | 最新优先核心实现 |
| 处理调度频率 | `backend/app/services/equip_status.py:195` | `async def _process_loop`，`interval = 1/process_hz` | 10Hz 处理循环 |
| 处理调度频率 | `backend/app/services/equip_status.py:205` | `async def _process_batch` | 批量遍历队列取最新帧 |
| 单次处理预算 | `backend/app/services/equip_status.py:213` | `async def _process_one`（`asyncio.wait_for`） | 100ms 预算包裹处理 |
| 单次处理预算 | `backend/app/services/equip_status.py:221` | `timeout=self._timeout_budget` | 超时预算参数 |
| 超时告警 | `backend/app/services/equip_status.py:227` | `logger.error(...)` + `_timeout_count` | 超时告警并立即返回 |
| 设备状态记录 | `backend/app/services/equip_status.py:232` | `async def _do_process` | 更新内存状态 + 持久化 + WebSocket 推送 |
| 数据持久化 | `backend/app/database.py:791` | `class EquipStatusRecord` | 每成员一条 upsert |
| 统计接口 | `backend/app/services/equip_status.py:276` | `def get_processing_stats` | 接收/处理/丢弃/超时统计 |
| 查询接口 | `backend/app/api/routes.py:1405` | `GET /api/v1/equip-status/stats`、`/api/v1/equip-status` | 对外查询路由 |
| 服务注册 | `backend/app/main.py:75,100,134` | `equip_status_service.start/stop` | 应用启动/关闭时调用 |

### 2.3 待确认

- **「0.25Hz 低频有充足 4 秒窗口」**：设计说明性结论，代码通过惰性队列 + 低频空队列体现，**无显式的「4 秒窗口」实现或测量**，属设计口径描述。
- **单次处理预算 100ms 的实测效果**：`asyncio.wait_for` 仅在存在 await 点时生效；纯同步耗时（如写库）若超 100ms 不会被强制中断。当前处理路径为快速操作，超时仅作为防御性保护，**未做满载压测确认**。

---

## 三、5.4.3 批量创建用户参数快捷访问列表

### 3.1 指标总表

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 实现状态 |
| --- | --- | --- | --- | --- |
| 全局列表数量上限 | 不少于 100 张，`MAX_LISTS = 100`，超限拒绝创建 | 常量 `MAX_LISTS` | 批量创建快捷访问列表的容量边界 | ✅ |
| 注册表哈希表 | 链地址法哈希表，初始容量 128、负载因子 0.75、容量翻倍动态扩容，位运算取模 `key & (cap-1)` | 常量 `INITIAL_CAPACITY` / `LOAD_FACTOR` | 全局列表注册表的存储结构 | ✅ |
| 单张列表预分配槽位 | 64 个参数槽位（超过 50 要求，留有余量） | 常量 `PREALLOC_SLOTS = 64` | 单张列表的数组容量 | ✅ |
| 列表创建复杂度 | O(1)（校验上限 → 插入注册表 → 返回 ID） | 算法分析 | 高频创建场景 | ✅ |
| 参数添加复杂度 | O(1) 平均 / O(n) 最坏（哈希冲突），校验 50/64 上限后追加数组 + 记录哈希索引 | 算法分析 | 高频添加参数场景 | ✅ |
| 快速访问复杂度 | 按名 O(1)（哈希索引定位下标）、按序 O(1)（数组下标） | 算法分析 | 快捷访问第 N 个参数 / 按名查找 | ✅ |
| 内存占用 | 100×50×64≈320KB（最低要求）；满载 100×64×64≈400KB；总内存 <1MB | 常量 `PARAM_ENTRY_BYTES = 64` | 存储容量评估 | ✅（估算值，见「待确认」） |

### 3.2 实现代码

| 指标 | 文件路径 | 关键函数 / 类 / 常量 | 调用位置说明 |
| --- | --- | --- | --- |
| 全局列表上限 | `backend/app/core/quick_list_manager.py:32` | `MAX_LISTS = 100` | 常量 |
| 全局列表上限 | `backend/app/core/quick_list_manager.py:225` | `def create_list`（`:230` 上限校验） | 创建列表入口 |
| 注册表哈希表 | `backend/app/core/quick_list_manager.py:126` | `class _HashMap` | 链地址法哈希表 |
| 注册表哈希表 | `backend/app/core/quick_list_manager.py:133` | `__init__(capacity=128, load_factor=0.75)` | 初始容量与负载因子 |
| 注册表哈希表 | `backend/app/core/quick_list_manager.py:155` | `def _resize` | 容量翻倍动态扩容 |
| 注册表哈希表 | `backend/app/core/quick_list_manager.py:201` | `class QuickListRegistry`（`:208` 实例化 `_HashMap`） | 全局注册表 |
| 单张列表预分配 | `backend/app/core/quick_list_manager.py:35` | `PREALLOC_SLOTS = 64` | 常量 |
| 单张列表预分配 | `backend/app/core/quick_list_manager.py:41` | `class QuickParamList`（`:52` `_entries = [None]*64`） | 有序数组 + 哈希索引 |
| 参数添加 | `backend/app/core/quick_list_manager.py:66` | `def add_param`（`:76` 上限校验） | O(1) 添加 + 哈希索引记录 |
| 快速访问 | `backend/app/core/quick_list_manager.py:93` | `def get_by_name` | 按名 O(1) |
| 快速访问 | `backend/app/core/quick_list_manager.py:100` | `def get_by_index` | 按序 O(1) |
| 内存评估 | `backend/app/core/quick_list_manager.py:280` | `def memory_usage_bytes` | 内存占用估算 |
| 统计接口 | `backend/app/core/quick_list_manager.py:288` | `def stats` | 列表数/容量/负载因子/内存 |
| 服务集成 | `backend/app/services/param_monitor.py:698` | `def create_quick_list` | 走内存算法 + 落库 |
| 服务集成 | `backend/app/services/param_monitor.py:721` | `def get_quick_list_stats` | 算法统计 |
| 服务集成 | `backend/app/services/param_monitor.py:756` | `def add_param_to_list` | 50/64 上限校验 |
| 数据持久化 | `backend/app/database.py:328` | `class QuickAccessList` | 列表落库表 |
| 查询接口 | `backend/app/api/routes.py:524-567` | `/params/quicklists` 系列路由 | 增删改查接口 |

### 3.3 待确认

- **内存占用为估算值**：`PARAM_ENTRY_BYTES = 64` 为「名称+值+元数据」的估算字节数，`memory_usage_bytes()` 按 `size × 64 + 桶指针×8` 估算，**非实测内存**；实测占用可能因 Python 对象开销偏高。

---

## 四、5.4.4 用户参数快捷访问列表参数设计

### 4.1 指标总表

| 指标名称 | 定义与计算口径 | 数据来源 | 使用场景 | 实现状态 |
| --- | --- | --- | --- | --- |
| 每张列表参数数量 | 不少于 50 个（最低要求 `MIN_PARAMS_PER_LIST = 50`）；实际上限 64（`MAX_PARAMS_PER_LIST = 64`，= 预分配槽位，留有余量） | 常量 `MIN_PARAMS_PER_LIST` / `MAX_PARAMS_PER_LIST` | 单张列表的参数容量 | ✅ |
| 参数条目结构 | 名称 + 值 + 元数据（类型/单位/ATA 等可选），每条目约 64 字节 | `add_param(name, value, **meta)` | 参数快捷访问的条目组织 | ✅ |

### 4.2 实现代码

| 指标 | 文件路径 | 关键函数 / 类 / 常量 | 调用位置说明 |
| --- | --- | --- | --- |
| 每张列表参数数量 | `backend/app/core/quick_list_manager.py:33` | `MIN_PARAMS_PER_LIST = 50` | 最低要求常量 |
| 每张列表参数数量 | `backend/app/core/quick_list_manager.py:34` | `MAX_PARAMS_PER_LIST = 64` | 实际上限常量 |
| 每张列表参数数量 | `backend/app/core/quick_list_manager.py:76` | `add_param` 内 `_count >= MAX_PARAMS_PER_LIST` 校验 | 添加时上限校验 |
| 参数条目结构 | `backend/app/core/quick_list_manager.py:66` | `def add_param(name, value=None, **meta)` | 名称+值+元数据 |
| 参数条目结构 | `backend/app/core/quick_list_manager.py:259` | `QuickListRegistry.add_param(..., **meta)` | 透传元数据 |

---

## 五、指标关联的验证方式（对应测试）

| 技术指标章节 | 单元测试文件 | 覆盖的指标 |
| --- | --- | --- |
| 5.4.1 故障数据接收 | 无独立测试脚本（通过 `_health_monitor_loop` 逻辑 + `FaultReceiveChannel` 计算直接验证） | 频率档位 / 通道划分 / 超时阈值 / 丢帧告警 |
| 5.4.2 设备标识状态接收 | `backend/tests/test_equip_status.py` | 最新优先 / 处理调度 / 超时预算 / 频率无关性 |
| 5.4.3 / 5.4.4 快捷访问列表 | `backend/tests/test_quick_list.py` | 100 上限 / 50+64 参数 / O(1) 访问 / 扩容 / 内存<1MB |

> **说明**：5.4.1 未提供独立单元测试脚本，其健康监控逻辑在当前会话通过临时 Python 断言验证（`FaultReceiveChannel` 的 period/timeout 计算、`mark_received` 更新/清零、连续 3 次丢帧告警），**是否需补正式测试脚本为待确认项**。
