# AHMU 仿真器 — CentOS 7.9 下位机适配说明

> 目标环境：**CentOS 7.9 实时操作系统，内核 3.10.0-1160**（下位机 / PXIe 工控机）

---

## 一、兼容性评估结论

| 项目 | 下位机实际值 | 是否兼容 | 结论与处理 |
| --- | --- | --- | --- |
| 内核 | 3.10.0-1160（CentOS 7.9 标准内核；实时版为 `kernel-rt`，`3.10.0-1160.x.rt*`） | ✅ | 满足；实时性依赖 `kernel-rt`（PREEMPT_RT），需验证 |
| glibc | 2.17 | ✅ | 依赖均为纯 Python 或 `manylinux2014` 预编译 wheel，兼容 glibc 2.17 |
| Python | 默认 `python3` = **3.6.8** | ❌ | 项目依赖最低要求 **3.8**，需安装 SCL `rh-python38`（Python 3.8.x） |
| systemd | 219 | ✅ | 支持 `Restart=`、`MemoryMax=`、`LimitNOFILE=` 等指令 |
| 前端 | 已构建静态 `dist` | ✅ | 无需 Node.js/npm |
| 数据库 | SQLite（Python 内置 sqlite3） | ✅ | WAL 模式，无需独立数据库服务 |
| 板卡驱动 | A664/A429/A825 C SDK（.so） | ✅ | 经 ctypes 加载；当前无实卡用 Mock 桩 |

**核心结论**：代码层已用 `pathlib` 跨平台路径，无硬编码 Windows 路径，可直接运行；唯一硬性缺口是 **Python 3.8**，需额外安装。

---

## 二、环境准备

### 2.1 确认系统与内核

```bash
cat /etc/redhat-release          # CentOS Linux release 7.9.2009 (Core)
uname -r                         # 3.10.0-1160.el7.x86_64 或 3.10.0-1160.x.rt*
cat /sys/kernel/realtime         # 1 = PREEMPT_RT 实时内核; 文件不存在 = 标准内核
```

### 2.2 安装 Python 3.8（SCL rh-python38）

CentOS 7.9 默认 `python3` 为 3.6.8，不满足依赖。推荐用官方 Software Collection：

```bash
# 1. 安装 SCL 仓库与 rh-python38
yum install -y centos-release-scl
yum install -y rh-python38 rh-python38-python-devel

# 2. 验证 (Python 3.8.x)
/opt/rh/rh-python38/root/usr/bin/python3.8 --version

# 3. 可选: 建立软链接方便调用
ln -sf /opt/rh/rh-python38/root/usr/bin/python3.8 /usr/local/bin/python3.8
```

> 备选：若工控机无法访问 yum 源，可离线源码编译 Python 3.8（需 `gcc make zlib-devel openssl-devel libffi-devel` 等），编译时 `./configure --enable-optimizations --prefix=/usr/local/python38`。

---

## 三、部署步骤

### 3.1 放置代码与前端产物

```bash
mkdir -p /opt/ahmu
# 将后端源码、start.sh、requirements*.txt、config、frontend/dist 解压到 /opt/ahmu
# 结构:
#   /opt/ahmu/backend/          (后端源码 + requirements-py38.txt)
#   /opt/ahmu/frontend/dist/    (前端构建产物)
#   /opt/ahmu/start.sh
```

### 3.2 创建运行用户并初始化（二选一）

**方式 A：交互式脚本（首次部署/调试）**

```bash
chmod +x /opt/ahmu/start.sh
/opt/ahmu/start.sh             # 自动检测 Python 3.8、建 venv、装依赖、启动
```

**方式 B：systemd 服务（生产部署，推荐）**

```bash
# 1. 创建运行用户
useradd -r -m -s /sbin/nologin ahmu

# 2. 用 Python 3.8 建 venv 并装依赖
/opt/rh/rh-python38/root/usr/bin/python3.8 -m venv /opt/ahmu/backend/venv
/opt/ahmu/backend/venv/bin/pip install -r /opt/ahmu/backend/requirements-py38.txt \
    -i https://mirrors.aliyun.com/pypi/simple/ --timeout 60 --retries 3

# 3. 安装服务单元并启动
cp /opt/ahmu/ahmu-simulator.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now ahmu-simulator
systemctl status ahmu-simulator
```

### 3.3 依赖安装要点

- **清单**：下位机 Python 3.8 使用 `requirements-py38.txt`（各依赖已降级至 3.8 兼容版本：fastapi 0.124.4 / uvicorn 0.33.0 / pydantic 2.10.6 / sqlalchemy 2.0.57 等，并含 `eval_type_backport`）。
- **镜像**：工控机若无法访问国外 PyPI，务必使用国内镜像（阿里云/清华/中科大，脚本已内置容错）。
- **glibc 2.17**：`pydantic-core`、`msgpack` 等 C 扩展提供 `manylinux2014`（x86_64）预编译 wheel，可直接安装，无需 gcc 编译；若个别包无对应 wheel 需编译，请额外安装 `gcc rh-python38-python-devel`。

---

## 四、实时性配置（内核 3.10.0-1160）

| 配置项 | 措施 | 说明 |
| --- | --- | --- |
| 实时内核 | 确认安装 `kernel-rt`（`yum install kernel-rt`），`uname -r` 含 `.rt` 后缀 | PREEMPT_RT 全抢占内核，保障调度延迟 |
| CPU 隔离 | 内核参数 `isolcpus` 隔离核心给实时任务 | 减少 CPU 争抢 |
| 进程优先级 | systemd `CPUSchedulingPolicy=fifo` + `Nice=-10`（服务文件内可选） | 提升 AHMU 进程实时优先级 |
| 内存锁定 | `mlockall` 锁定内存避免换页（板卡高频收发路径） | 避免缺页中断 |
| 中断亲和 | 板卡中断绑定到隔离 CPU | 降低接收抖动 |

> 本项目实时性指标（5.4 章节）：故障数据接收 20ms 轮询粒度、设备状态处理 10Hz + 100ms 预算，均远低于 PREEMPT_RT 内核可保证的调度延迟（µs~ms 级），标准内核 + 事件驱动也可满足功能要求；实时内核主要用于硬件板卡收发路径的确定性保障。

---

## 五、验证要点

| # | 验证项 | 命令 / 方法 | 预期结果 |
| --- | --- | --- | --- |
| 1 | 系统版本 | `cat /etc/redhat-release` | CentOS Linux release 7.9.2009 |
| 2 | 内核版本 | `uname -r` | 3.10.0-1160.el7.x86_64（或 `.rt`） |
| 3 | 实时性 | `cat /sys/kernel/realtime` | 1（若文件存在） |
| 4 | Python 版本 | `/opt/rh/rh-python38/root/usr/bin/python3.8 --version` | Python 3.8.x |
| 5 | 依赖导入 | `venv/bin/python -c "import uvicorn,fastapi,sqlalchemy,pydantic,msgpack"` | 无报错 |
| 6 | 服务启动 | `systemctl status ahmu-simulator` | active (running) |
| 7 | API 响应 | `curl http://127.0.0.1:8443/api/v1/system/status` | 返回 JSON（member_count、icd_loaded 等） |
| 8 | 前端访问 | 浏览器打开 `http://<下位机IP>:8443`，登录 TEST/123456 | 页面正常加载 |
| 9 | 实时调度（可选） | `cyclictest -p 80 -t 1 -n -i 1000 -l 100000` | 最大延迟在预期范围（µs~ms 级） |
| 10 | 连续运行 | 压测/挂机 48h，观察 `ps`/`systemctl`/日志 | 无崩溃、内存平稳 |

---

## 六、常见问题

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `python3 --version` 为 3.6 | 系统默认 python3 是 3.6.8 | 用 SCL 的 `/opt/rh/rh-python38/root/usr/bin/python3.8` |
| pip 安装超时/失败 | 工控机无法访问 pypi.org | 用国内镜像（`-i https://mirrors.aliyun.com/pypi/simple/`） |
| `No module named uvicorn` | venv 依赖未装全 | 删除 `backend/venv` 重新执行 `start.sh` |
| WebSocket 收不到推送 | 误用多 worker 启动 | 必须单 worker（见 `ahmu-simulator.service` 注释） |
| 端口 8443 被占用 | 其他进程占用 | `ss -lntp \| grep 8443` 定位后处理 |
| 防火墙拦截 | 下位机防火墙未放行 8443 | `firewall-cmd --add-port=8443/tcp --permanent && firewall-cmd --reload` |
