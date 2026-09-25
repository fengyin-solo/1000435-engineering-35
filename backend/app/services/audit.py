"""达标审核业务规则：状态流转、字段校验与筛选口径都收在这里。

状态机只有 app.statemachine 一份实现，前端通过 GET /api/audit/meta 获取
状态、动作与守卫说明，列表/详情再按当前状态下发「可执行动作」，两边天然同步。
"""
from __future__ import annotations

from typing import Any

from app.statemachine import StateMachine, Transition
from app.store import store

MODULE = "audit"
REQUIRED_FIELDS = ["审核编号", "审核周期", "审核范围"]
DISPLAY_FIELDS = ["审核编号", "审核周期", "审核范围", "超标次数", "整改项数", "审核结论", "审核人员"]
NUMBER_FIELDS = {"超标次数", "整改项数"}

STATUS_PENDING = "待审核"
STATUS_RUNNING = "审核中"
STATUS_PASSED = "已通过"
STATUS_RECTIFY = "需整改"

# 守卫说明同时提供给前端 meta，用于解释「为什么这个动作点不了」。
GUARD_HINT_PASS = "存在超标（超标次数大于 0）时不能确认通过，请先下发整改并闭环超标项"


def _exceed_count(entry: dict[str, Any]) -> int:
    try:
        return max(int(entry.get("超标次数") or 0), 0)
    except (TypeError, ValueError):
        return 0


def _as_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _no_exceedance(entry: dict[str, Any]) -> str | None:
    """确认通过的业务守卫：超标次数大于 0 一律转去下发整改。"""
    count = _exceed_count(entry)
    if count > 0:
        return f"本周期超标 {count} 次，不能确认通过，请先下发整改并闭环超标项"
    return None


# 达标审核唯一的状态—动作—流转定义，后端据此校验、前端据此渲染。
STATE_MACHINE = StateMachine(
    initial=STATUS_PENDING,
    transitions=[
        Transition("开始审核", STATUS_PENDING, STATUS_RUNNING),
        Transition(
            "确认通过",
            STATUS_RUNNING,
            STATUS_PASSED,
            guard=_no_exceedance,
        ),
        Transition("下发整改", STATUS_RUNNING, STATUS_RECTIFY),
    ],
)

# 终态不允许再执行任何动作，重复操作的拦截说明统一在这里维护。
TERMINAL_HINTS = {
    STATUS_PASSED: "审核已通过，无需重复操作",
    STATUS_RECTIFY: "整改已下发，请勿重复下发；如仍有问题请走新一轮审核登记",
}


class AuditService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("审核编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = [self._serialize(row) for row in rows[start:start + size]]
        return page_rows, total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._serialize(entry) if entry is not None else None

    def meta(self) -> dict[str, Any]:
        """供前端渲染动作/状态的唯一配置来源。"""
        return {
            "module": MODULE,
            "initial": STATE_MACHINE.initial,
            "statuses": STATE_MACHINE.statuses,
            "actions": STATE_MACHINE.actions,
            "terminal_statuses": STATE_MACHINE.terminal_statuses(),
            "transitions": [
                {"action": rule.action, "source": rule.source, "target": rule.target}
                for rule in STATE_MACHINE._ordered_rules()
            ],
            "guard_hints": {"确认通过": GUARD_HINT_PASS},
            "terminal_hints": TERMINAL_HINTS,
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        code = str(values["审核编号"]).strip()
        if any(str(row.get("审核编号") or "").strip() == code for row in store.rows(MODULE)):
            return None, f"审核编号 {code} 已存在，同一审核编号不能重复登记"
        numbers, message = self._parse_numbers(values)
        if message is not None:
            return None, message
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: str(values[field]).strip() for field in DISPLAY_FIELDS if field in values and field not in NUMBER_FIELDS})
        entry.update(numbers)
        entry["status"] = STATE_MACHINE.initial
        self._refresh_flags(entry)
        rows.append(entry)
        return self._serialize(entry), None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str, bool]:
        """执行动作，返回 (记录, 说明, 是否成功)。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"审核记录 {entry_id} 不存在或已归档", False
        target, reason = STATE_MACHINE.apply(entry, action)
        if target is None:
            return None, reason, False
        entry["status"] = target
        if target == STATUS_RECTIFY:
            # 下发整改时按超标次数生成整改项，保证「超标—整改」口径对得上。
            entry["整改项数"] = _exceed_count(entry)
        self._refresh_flags(entry)
        return self._serialize(entry), f"审核记录已{action}，当前状态：{target}", True

    def _parse_numbers(self, values: dict[str, Any]) -> tuple[dict[str, int], str | None]:
        parsed: dict[str, int] = {}
        for field in NUMBER_FIELDS:
            raw = values.get(field)
            if raw in (None, ""):
                parsed[field] = 0
                continue
            try:
                number = int(raw)
            except (TypeError, ValueError):
                return {}, f"{field}必须是非负整数，收到的是「{raw}」"
            if number < 0:
                return {}, f"{field}不能为负数，空数据请填 0"
            parsed[field] = number
        return parsed, None

    def _refresh_flags(self, entry: dict[str, Any]) -> None:
        entry["pending"] = entry["status"] in (STATUS_PENDING, STATUS_RUNNING)
        entry["abnormal"] = _exceed_count(entry) > 0

    def _serialize(self, entry: dict[str, Any]) -> dict[str, Any]:
        """列表与详情共用同一份序列化，保证两边字段与可执行动作完全一致。"""
        data: dict[str, Any] = {"id": int(entry.get("id", 0))}
        for field in DISPLAY_FIELDS:
            if field == "超标次数":
                data[field] = _exceed_count(entry)
            elif field == "整改项数":
                data[field] = _as_int(entry.get(field))
            else:
                data[field] = entry.get(field)
        data["审核状态"] = entry["status"]
        data["可执行动作"] = STATE_MACHINE.allowed_actions(entry)
        return data
