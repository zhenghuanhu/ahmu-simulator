"""
启动测试消息编解码 (ARINC624 启动测试交互)

对齐《OHMS 与成员系统启动测试消息》表 9 定义, Mock 环境用 JSON over UDP 承载 AFDX。

消息类型 (表 9 + ACK 补充):

  方向 OHMS -> 成员系统:
    RUN_TEST       启动测试运行测试指令     请求成员系统开始执行启动测试
    TERMINATE_TEST 启动测试终止指令         请求成员系统终止正在进行的启动测试
    CONTINUE_TEST  启动测试继续测试指令     通知成员系统维护人员已响应, 请求继续执行
    ACK            确认消息 (ARINC624)      明确确认收到成员系统的响应

  方向 成员系统 -> OHMS:
    TEST_IN_PROGRESS 启动测试测试进行中响应  确认已启动或恢复执行启动测试
    TEST_SUPPRESSED  启动测试抑制测试响应    通知存在抑制条件, 无法开始/继续
    DISPLAY_PAGE     启动测试显示页面响应    请求 OHMS 显示预定义交互式测试页面
    TEST_TERMINATED  启动测试终止响应        确认已/将发生进行中测试的提前终止
    MEMBER_STATUS    成员系统状态报告        抑制条件存在/清除 + 测试完成标志 + 故障报告

JSON 载荷结构 (通用):
    {
        "type": "<消息类型>",
        "member_system": "PAA_L3",
        "test_id": "<uuid>",
        "page_id": "<交互页面标识, DISPLAY_PAGE/CONTINUE_TEST 用>",
        "response": { ... 维护人员响应 ... },
        "inhibited": true/false,          # MEMBER_STATUS 用: 是否存在抑制条件
        "inhibit_conditions": [ ... ],    # MEMBER_STATUS 用: 抑制条件列表
        "test_completed": true/false,     # MEMBER_STATUS 用: 测试完成标志
        "fault_reports": [ ... ],         # MEMBER_STATUS 用: 测试相关故障报告
        "reason": "...",                  # 抑制/终止原因
    }
"""
import json
from typing import Any, Optional

# 消息类型常量
MSG_RUN_TEST = "run_test"                # 启动测试运行测试指令 (OHMS -> 成员)
MSG_TERMINATE_TEST = "terminate_test"    # 启动测试终止指令 (OHMS -> 成员)
MSG_CONTINUE_TEST = "continue_test"      # 启动测试继续测试指令 (OHMS -> 成员)
MSG_ACK = "ack"                          # ACK 确认 (OHMS -> 成员)

MSG_TEST_IN_PROGRESS = "test_in_progress"  # 启动测试测试进行中响应 (成员 -> OHMS)
MSG_TEST_SUPPRESSED = "test_suppressed"    # 启动测试抑制测试响应 (成员 -> OHMS)
MSG_DISPLAY_PAGE = "display_page"          # 启动测试显示页面响应 (成员 -> OHMS)
MSG_TEST_TERMINATED = "test_terminated"    # 启动测试终止响应 (成员 -> OHMS)
MSG_MEMBER_STATUS = "member_status"        # 成员系统状态报告 (成员 -> OHMS)

# 消息中文名 (用于日志/展示)
MSG_NAMES = {
    MSG_RUN_TEST: "启动测试运行测试指令",
    MSG_TERMINATE_TEST: "启动测试终止指令",
    MSG_CONTINUE_TEST: "启动测试继续测试指令",
    MSG_ACK: "ACK确认",
    MSG_TEST_IN_PROGRESS: "启动测试测试进行中响应",
    MSG_TEST_SUPPRESSED: "启动测试抑制测试响应",
    MSG_DISPLAY_PAGE: "启动测试显示页面响应",
    MSG_TEST_TERMINATED: "启动测试终止响应",
    MSG_MEMBER_STATUS: "成员系统状态报告",
}

# 成员系统 -> OHMS 的消息 (需 ACK 确认)
MEMBER_TO_OHMS = {
    MSG_TEST_IN_PROGRESS, MSG_TEST_SUPPRESSED,
    MSG_DISPLAY_PAGE, MSG_TEST_TERMINATED,
}

# 预定义交互式测试页面标识 (DISPLAY_PAGE 用)
PREDEFINED_PAGES = {
    "PAGE_1": "地面测试交互页 1 (执行开关操作)",
    "PAGE_2": "地面测试交互页 2 (观察指示确认)",
    "PAGE_3": "地面测试交互页 3 (输入参数)",
}


def encode_message(msg_type: str, **fields) -> bytes:
    """将启动测试消息编码为 JSON 字节 (Mock 承载 AFDX)"""
    payload = {"type": msg_type, **fields}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def decode_message(data: bytes) -> Optional[dict]:
    """解析启动测试 JSON 消息, 返回 dict 或 None"""
    try:
        msg = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(msg, dict) or "type" not in msg:
        return None
    return msg


def message_name(msg_type: str) -> str:
    """消息类型 -> 中文名"""
    return MSG_NAMES.get(msg_type, msg_type)


def is_member_message(msg_type: str) -> bool:
    """是否成员系统 -> OHMS 的消息 (需 ACK)"""
    return msg_type in MEMBER_TO_OHMS
