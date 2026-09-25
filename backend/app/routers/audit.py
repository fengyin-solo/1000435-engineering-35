"""达标审核接口：维护审核记录，覆盖开始审核、确认通过、下发整改等动作。

注意路由顺序：/export、/meta 这类固定路径必须声明在 /{entry_id} 之前，
否则会被详情路由按 int 解析，直接返回 422。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit import AuditService

router = APIRouter(prefix="/api/audit", tags=["达标审核"])

service = AuditService()

LIST_FIELDS = ["审核编号", "审核周期", "审核范围", "超标次数", "整改项数", "审核结论", "审核人员", "审核状态"]


class ActionPayload(BaseModel):
    """动作报文：兼容前端直接提交 {action}，也兼容登记用的 values 包裹。"""

    action: str | None = None
    values: dict[str, Any] = {}


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按审核编号检索"),
    status: str | None = Query(default=None, description="待审核、审核中、已通过、需整改"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按审核编号与状态过滤达标审核列表；没有匹配数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    keyword: str | None = Query(default=None, description="按审核编号检索"),
    status: str | None = Query(default=None, description="待审核、审核中、已通过、需整改"),
) -> dict[str, Any]:
    """导出达标审核清单：返回当前过滤条件下的全量数据（无数据时 items 为空列表）。"""
    items, total = service.list_entries(keyword=keyword, status=status, page=1, size=10000)
    return {"module": "audit", "total": total, "items": items}


@router.get("/meta")
def audit_meta() -> dict[str, Any]:
    """状态机元数据：前端按这份唯一配置渲染状态、动作与拦截说明。"""
    return service.meta()


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审核记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审核记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审核记录，缺字段时说明原因而不是静默丢弃。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message or "审核记录登记失败")
    return ActionResult(ok=True, message="审核记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条审核记录执行动作；不允许的动作（含重复下发）会被拦下并说明原因。"""
    action = str(payload.action or payload.values.get("action") or "").strip()
    if not action:
        return ActionResult(ok=False, message="缺少 action 字段，请明确要执行的动作")
    entry, message, ok = service.run_action(entry_id, action)
    if not ok:
        return ActionResult(ok=False, message=message, entry=entry)
    return ActionResult(ok=True, message=message, entry=entry)
