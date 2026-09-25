"""达标审核接口：维护审核记录，覆盖开始审核、确认通过、下发整改等动作。

注意路由顺序：/export、/state-machine、/stats 等字面量路径必须声明在
/{entry_id} 之前，否则会被当成审核编号去解析整数，直接返回 422。
状态流转规则统一来自 services.audit_machine，本层不做业务判断。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services import audit_machine as machine
from app.services.audit import AuditService

router = APIRouter(prefix="/api/audit", tags=["达标审核"])

service = AuditService()

LIST_FIELDS = machine.DISPLAY_FIELDS


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按审核编号检索"),
    status: str | None = Query(default=None, description="待审核、审核中、已通过、需整改"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按审核编号与状态过滤达标审核列表；没有数据时返回空页，不报错。"""
    if page < 1:
        raise HTTPException(status_code=400, detail="页码从 1 开始")
    if size < 1 or size > 200:
        raise HTTPException(status_code=400, detail="每页条数需在 1 到 200 之间，请缩小分页范围")
    if status and status not in machine.STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"状态「{status}」不合法，允许：{'、'.join(machine.STATUSES)}",
        )
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


# ---- 字面量路径统一放在 /{entry_id} 之前，避免被路径参数吞掉导致 422 ----


@router.get("/state-machine")
def get_state_machine() -> dict[str, Any]:
    """返回达标审核状态机的唯一定义，前端按它渲染状态与动作，不再各写一份。"""
    return machine.machine_description()


@router.get("/stats")
def get_stats(
    keyword: str | None = Query(default=None, description="按审核编号检索"),
    status: str | None = Query(default=None, description="按状态过滤"),
) -> dict[str, int]:
    """列表页统计卡片数据。"""
    return service.stats(keyword=keyword, status=status)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按审核编号检索"),
    status: str | None = Query(default=None, description="按状态过滤"),
) -> dict[str, Any]:
    """导出达标审核清单：返回当前过滤条件下的全量数据；空结果给出说明而不是报错。"""
    items, total = service.list_all(keyword=keyword, status=status)
    message = "当前过滤条件下没有可导出的达标审核记录" if not items else None
    return {"module": "audit", "total": total, "message": message, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审核记录明细（与列表项同口径）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审核记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审核记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="审核记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条审核记录执行开始审核、确认通过、下发整改；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
