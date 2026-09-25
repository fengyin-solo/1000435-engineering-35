"""统一状态机：全平台只有这一份「状态—动作—流转」实现。

前端不再各自维护动作与状态清单，统一通过接口（如 GET /api/audit/meta）
读取本模块产出的状态、动作与可执行动作，保证同一编号在两边的判断永远一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# 守卫返回 None 表示放行；返回说明文字表示拦下该动作。
Guard = Callable[[dict[str, Any]], str | None]


@dataclass(frozen=True)
class Transition:
    """一条流转规则：在 source 状态下执行 action，进入 target 状态。"""

    action: str
    source: str
    target: str
    guard: Guard | None = None
    # 守卫未通过时给出的补充说明；None 时使用守卫自己的返回值。
    blocked_hint: str | None = None


@dataclass
class StateMachine:
    """按 (当前状态, 动作) 判定能否流转的状态机。

    - 初始状态取 initial；终态为不允许任何动作的状态。
    - 同一动作允许配置多条规则（来源状态不同），未命中时给出可读原因。
    - 守卫（guard）用于承载与记录内容相关的业务约束，例如超标记录不能直接通过。
    """

    initial: str
    transitions: Iterable[Transition]
    _by_action: dict[str, list[Transition]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        table: dict[str, list[Transition]] = {}
        for rule in self.transitions:
            table.setdefault(rule.action, []).append(rule)
        self._by_action = table

    @property
    def statuses(self) -> list[str]:
        """全部状态，按首次在规则中出现的顺序排列，初始状态排最前。"""
        ordered = [self.initial]
        for rule in self._ordered_rules():
            for status in (rule.source, rule.target):
                if status not in ordered:
                    ordered.append(status)
        return ordered

    @property
    def actions(self) -> list[str]:
        """全部动作，按注册顺序去重。"""
        return list(self._by_action)

    def _ordered_rules(self) -> list[Transition]:
        return [rule for rules in self._by_action.values() for rule in rules]

    def terminal_statuses(self) -> list[str]:
        """没有任何可执行动作的状态即终态。"""
        sources = {rule.source for rule in self._ordered_rules()}
        return [status for status in self.statuses if status not in sources]

    def allowed_actions(self, entry: dict[str, Any]) -> list[str]:
        """该记录当前状态下、且通过业务守卫的动作，按注册顺序排列。"""
        status = str(entry.get("status") or "")
        allowed: list[str] = []
        for action in self.actions:
            rule = self._match(action, status)
            if rule is None:
                continue
            if rule.guard is not None and rule.guard(entry) is not None:
                continue
            allowed.append(action)
        return allowed

    def apply(self, entry: dict[str, Any], action: str) -> tuple[str | None, str | None]:
        """对记录执行动作。

        成功返回 (目标状态, None)；失败返回 (None, 原因说明)。
        重复下发等「当前状态已经是目标态」的情况会在这里被拦下并解释。
        """
        status = str(entry.get("status") or "")
        rules = self._by_action.get(action)
        if not rules:
            return None, f"动作「{action}」不属于可执行范围"
        rule = self._match(action, status)
        if rule is None:
            reachable = {r.source for r in rules}
            if status in {r.target for r in rules}:
                return None, f"当前状态为「{status}」，请勿重复执行「{action}」"
            return None, (
                f"「{status}」状态下不能执行「{action}」，"
                f"该动作只适用于{'、'.join(sorted(reachable))}状态"
            )
        if rule.guard is not None:
            reason = rule.guard(entry)
            if reason is not None:
                return None, rule.blocked_hint or reason
        return rule.target, None

    def _match(self, action: str, status: str) -> Transition | None:
        for rule in self._by_action.get(action, []):
            if rule.source == status:
                return rule
        return None
