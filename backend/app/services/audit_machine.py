"""达标审核状态机：全平台关于「状态、动作、流转条件」的唯一一份定义。

后端 services/routers 直接引用这里做校验；前端不再各写一份动作与状态，
而是通过 GET /api/audit/state-machine 拉取同一份定义来决定按钮显隐，
因此同一审核编号在两边永远同步，不会再出现「点不动的动作」。

状态流转：

    待审核 --开始审核--> 审核中 --确认通过(超标次数为0)--> 已通过
                              \\--下发整改(存在超标项)--> 需整改

- 已通过、需整改都是终态：动作不允许重复执行（含重复下发整改）。
- 超标次数缺失、为空或不是合法数字时按 0 处理；超标次数大于 0 时
  不能确认通过，需先下发整改。
"""
from __future__ import annotations

from typing import Any

# 初始状态与全部状态（顺序即业务上的状态序列）
INITIAL_STATUS = "待审核"
STATUSES = ["待审核", "审核中", "已通过", "需整改"]

# 动作 -> 目标状态，仅此三种动作
ACTION_TARGETS: dict[str, str] = {
    "开始审核": "审核中",
    "确认通过": "已通过",
    "下发整改": "需整改",
}
ACTIONS = list(ACTION_TARGETS)

# 每个动作允许从哪些状态发起；缺省或不在表内即该动作当前不可执行
ACTION_SOURCES: dict[str, list[str]] = {
    "开始审核": ["待审核"],
    "确认通过": ["审核中"],
    "下发整改": ["审核中"],
}

SUCCESS_MESSAGES: dict[str, str] = {
    "开始审核": "审核记录已开始审核",
    "确认通过": "审核记录已确认通过",
    "下发整改": "整改单已下发，请跟踪整改项闭环",
}

# 业务展示字段（列表列序与登记字段同源）
DISPLAY_FIELDS = ["审核编号", "审核周期", "审核范围", "超标次数", "整改项数", "审核结论", "审核人员", "审核状态"]
REQUIRED_FIELDS = ["审核编号", "审核周期", "审核范围"]
COUNT_FIELDS = ["超标次数", "整改项数"]


def parse_count(value: Any) -> int:
    """超标次数、整改项数统一按整数口径解释。

    空数据（None、空字符串、纯空白）以及无法解析的非数字都按 0 计，
    保证列表与详情、筛选与统计口径一致。
    """
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def validate_action(entry: dict[str, Any], action: str) -> str | None:
    """校验动作能否执行。返回 None 表示允许，否则返回给操作者看的拒绝说明。"""
    if action not in ACTION_TARGETS:
        return f"动作「{action or '空'}」不属于达标审核可执行范围（允许：{'、'.join(ACTIONS)}）"
    status = str(entry.get("status") or "")
    if status not in STATUSES:
        return f"当前状态「{status or '空'}」不是合法的审核状态，请刷新列表后重试"
    if status not in ACTION_SOURCES[action]:
        if action == "开始审核":
            return "审核已开始，不能重复开始审核"
        if action == "下发整改" and status == "需整改":
            return "整改单已下发，请勿重复下发；可在整改闭环后重新发起审核"
        if status in ("已通过", "需整改"):
            return f"当前状态为「{status}」（终态），审核流程已结束，不能再执行「{action}」"
        # 指出该动作的前置动作，避免操作者对着点不动的按钮无从下手
        sources = ACTION_SOURCES[action]
        if len(sources) == 1:
            prerequisite = next(
                (name for name, target in ACTION_TARGETS.items() if target == sources[0]),
                "",
            )
            if prerequisite:
                return f"当前状态为「{status}」，不能直接「{action}」，请先「{prerequisite}」"
        return f"当前状态为「{status}」，不能执行「{action}」（仅 {'、'.join(sources)} 状态可执行）"
    over_count = parse_count(entry.get("超标次数"))
    if action == "确认通过" and over_count > 0:
        return f"超标次数为 {over_count}，存在未闭环的超标项，不能确认通过；请先下发整改"
    if action == "下发整改" and over_count <= 0:
        return "超标次数为 0，没有可整改的超标项，无需下发整改；可直接确认通过"
    return None


def apply_action(entry: dict[str, Any], action: str) -> str:
    """执行动作并原地更新状态。调用前必须先通过 validate_action。"""
    target = ACTION_TARGETS[action]
    entry["status"] = target
    # 终态（已通过、需整改）不再待处理；需整改与存在超标项都视为异常
    entry["pending"] = target not in ("已通过", "需整改")
    entry["abnormal"] = target == "需整改" or parse_count(entry.get("超标次数")) > 0
    return SUCCESS_MESSAGES[action]


def available_actions(entry: dict[str, Any]) -> list[dict[str, str]]:
    """计算单条记录当前可点的动作（附带不可点原因，前端直接渲染按钮态）。"""
    result: list[dict[str, str]] = []
    for action in ACTIONS:
        reason = validate_action(entry, action)
        result.append({
            "action": action,
            "enabled": reason is None,
            "reason": reason or "",
        })
    return result


def serialize_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """列表与详情共用的唯一出口：两条路径拿到的字段与口径完全一致。"""
    payload = dict(entry)
    status = str(entry.get("status") or INITIAL_STATUS)
    # 展示列「审核状态」始终以机内 status 为准，消除历史脏数据里两份状态不一致
    payload["审核状态"] = status
    for field in COUNT_FIELDS:
        payload[field] = parse_count(entry.get(field))
    payload["status"] = status
    payload["pending"] = status not in ("已通过", "需整改")
    payload["abnormal"] = status == "需整改" or parse_count(entry.get("超标次数")) > 0
    payload["actions"] = available_actions(payload)
    return payload


def machine_description() -> dict[str, Any]:
    """对外暴露的状态机定义；前端按这份定义渲染，不再自己维护状态与动作。"""
    return {
        "module": "audit",
        "initial_status": INITIAL_STATUS,
        "statuses": list(STATUSES),
        "actions": [
            {
                "name": action,
                "target": ACTION_TARGETS[action],
                "from": list(ACTION_SOURCES[action]),
            }
            for action in ACTIONS
        ],
        "transitions": [
            {"from": source, "action": action, "to": ACTION_TARGETS[action]}
            for action in ACTIONS
            for source in ACTION_SOURCES[action]
        ],
        "rules": [
            "待审核的记录先「开始审核」进入审核中",
            "审核中且超标次数为 0 才能「确认通过」",
            "审核中且存在超标项（超标次数大于 0）才能「下发整改」",
            "已通过、需整改均为终态，动作不能重复执行，整改单不可重复下发",
            "超标次数为空或非数字时按 0 处理",
        ],
        "count_fields": list(COUNT_FIELDS),
        "display_fields": list(DISPLAY_FIELDS),
        "required_fields": list(REQUIRED_FIELDS),
    }
