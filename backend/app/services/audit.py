"""达标审核业务规则：筛选、登记与统计；状态流转统一走 audit_machine。

状态、动作与流转条件只有 audit_machine.py 一份定义，本服务不再自己维护，
路由层也不做业务判断。
"""
from __future__ import annotations

from typing import Any

from app.services import audit_machine as machine
from app.store import store

MODULE = "audit"
REQUIRED_FIELDS = machine.REQUIRED_FIELDS
# 登记时允许一并写入的业务字段（缺省字段按空值入库，不静默丢弃）
OPTIONAL_FIELDS = ["超标次数", "整改项数", "审核结论", "审核人员"]


class AuditService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._filter(keyword=keyword, status=status)
        total = len(rows)
        start = max(page - 1, 0) * size
        return [machine.serialize_entry(row) for row in rows[start:start + size]], total

    def list_all(self, *, keyword: str | None = None, status: str | None = None) -> tuple[list[dict[str, Any]], int]:
        """导出等场景使用：返回过滤后的全量数据，口径与分页列表一致。"""
        rows = self._filter(keyword=keyword, status=status)
        return [machine.serialize_entry(row) for row in rows], len(rows)

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return machine.serialize_entry(entry) if entry is not None else None

    def stats(self, *, keyword: str | None = None, status: str | None = None) -> dict[str, int]:
        """列表页统计卡片：待审核记录数、超标总次数、需整改项数。"""
        rows = self._filter(keyword=keyword, status=status)
        return {
            "pending_count": sum(1 for row in rows if row.get("status") == machine.INITIAL_STATUS),
            "over_limit_total": sum(machine.parse_count(row.get("超标次数")) for row in rows),
            "rectify_total": sum(
                machine.parse_count(row.get("整改项数"))
                for row in rows
                if row.get("status") == "需整改"
            ),
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
            entry[field] = values.get(field)
        entry["status"] = machine.INITIAL_STATUS
        rows.append(entry)
        return machine.serialize_entry(entry), []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"审核记录 {entry_id} 不存在或已归档"
        reason = machine.validate_action(entry, str(action or "").strip())
        if reason is not None:
            return None, reason
        message = machine.apply_action(entry, str(action).strip())
        return machine.serialize_entry(entry), message

    def _filter(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("审核编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows
