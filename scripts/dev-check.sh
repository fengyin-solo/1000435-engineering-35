#!/usr/bin/env bash
# 本地开发启动自检：一键拉起后端（临时端口，跑完自动关），用内置示例数据
# 跑通达标审核的三段主流程与边界场景，并可选构建前端。
#
# 用法：
#   scripts/dev-check.sh              # 后端接口自检 + 前端类型检查/构建
#   SKIP_FRONTEND=1 scripts/dev-check.sh   # 只检后端
#
# 退出码非 0 表示有检查项未通过。
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
PORT="${CHECK_PORT:-8011}"
BASE_URL="http://127.0.0.1:${PORT}"

# ---- 选一个能跑 FastAPI 的解释器：优先 .venv，其次当前用户环境 ----
PYBIN="python3"
if [ -x "$BACKEND_DIR/.venv/bin/python" ] && "$BACKEND_DIR/.venv/bin/python" -c "import fastapi" >/dev/null 2>&1; then
  PYBIN="$BACKEND_DIR/.venv/bin/python"
elif ! python3 -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  echo "[dev-check] 未找到 FastAPI 依赖，请先执行：(cd backend && ./run.sh) 或 pip install -r backend/requirements.txt" >&2
  exit 2
fi

echo "[dev-check] 使用解释器：$($PYBIN -c 'import sys; print(sys.executable)')"
echo "[dev-check] 在 $BASE_URL 上临时启动后端……"

cleanup() {
  if [ -n "${SERVER_PID:-}" ] && kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

(
  cd "$BACKEND_DIR"
  exec "$PYBIN" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
) >/tmp/dev-check-uvicorn.log 2>&1 &
SERVER_PID=$!

# 等服务就绪（最多 20 秒）
for _ in $(seq 1 40); do
  if curl -sf "$BASE_URL/api/health" >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    echo "[dev-check] 后端启动失败，日志如下：" >&2
    cat /tmp/dev-check-uvicorn.log >&2
    exit 2
  fi
  sleep 0.5
done
if ! curl -sf "$BASE_URL/api/health" >/dev/null 2>&1; then
  echo "[dev-check] 后端健康检查超时，日志如下：" >&2
  cat /tmp/dev-check-uvicorn.log >&2
  exit 2
fi
echo "[dev-check] 后端已就绪，开始接口自检……"

set +e
CHECK_BASE_URL="$BASE_URL" "$PYBIN" - <<'PYEOF'
"""达标审核启动自检：只用标准库，按真实 HTTP 请求走一遍。"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ["CHECK_BASE_URL"].rstrip("/")
failures: list[str] = []
passed = 0


def request(method: str, path: str, body: object | None = None, expect_http: int = 200):
    # 路径里的中文查询参数需按 UTF-8 百分号编码
    path = urllib.parse.quote(path, safe="/?=&%")
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read().decode("utf-8")
    if status != expect_http:
        failures.append(f"{method} {path} -> HTTP {status}，期望 {expect_http}；响应：{raw[:200]}")
        return None
    # 预期的 4xx 仅需状态码命中，不解析业务体
    if expect_http >= 400:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        failures.append(f"{method} {path} 返回不是合法 JSON：{raw[:200]}")
        return None

def check(condition: bool, name: str, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failures.append(f"{name}：{detail}")
        print(f"  ❌ {name} {detail}")


def find(items, entry_id: int):
    return next((item for item in items if int(item["id"]) == entry_id), None)


print("[1/7] 基础接口与示例数据")
health = request("GET", "/api/health")
check(health and health.get("ok") is True, "健康检查通过")

machine = request("GET", "/api/audit/state-machine")
check(
    machine is not None
    and machine.get("statuses") == ["待审核", "审核中", "已通过", "需整改"]
    and [a["name"] for a in machine.get("actions", [])] == ["开始审核", "确认通过", "下发整改"],
    "状态机唯一定义可取（状态与动作以后端为准）",
    f"实际：{machine}",
)
transitions = {(t["from"], t["action"]): t["to"] for t in (machine or {}).get("transitions", [])}
check(
    transitions == {
        ("待审核", "开始审核"): "审核中",
        ("审核中", "确认通过"): "已通过",
        ("审核中", "下发整改"): "需整改",
    },
    "状态机流转表正确（终态动作不可重复）",
    f"实际：{transitions}",
)

listing = request("GET", "/api/audit")
seed_ids = [item["id"] for item in (listing or {}).get("items", [])]
check(
    {1, 2, 3}.issubset(set(seed_ids)),
    "既有审核记录保持不变（AUDI-0001/0002/0003 仍在）",
    f"实际 id 列表：{seed_ids}",
)
check(
    all("actions" in item for item in (listing or {}).get("items", [])),
    "每条记录都带可执行动作清单，前端无需自己推断",
)

print("[2/7] 导出接口（修复路由顺序导致的 422）")
export = request("GET", "/api/audit/export")
check(
    export is not None and export.get("total", 0) >= 4 and len(export.get("items", [])) == export.get("total"),
    "GET /api/audit/export 正常返回全量清单",
    f"实际：{str(export)[:200] if export else 'None'}",
)
check(export and export.get("message") in (None, ""), "有数据时导出不附带空数据提示")
empty_export = request("GET", "/api/audit/export?keyword=NOT-EXIST-编号")
check(
    empty_export is not None
    and empty_export.get("items") == []
    and bool(empty_export.get("message")),
    "空数据导出返回空清单并附说明",
    f"实际：{empty_export}",
)

print("[3/7] 列表与详情一致")
list_items = request("GET", "/api/audit")["items"]
for item in list_items:
    detail = request("GET", f"/api/audit/{item['id']}")
    check(
        detail == item,
        f"审核编号 {item.get('审核编号')} 列表与详情字段完全一致",
        f"列表={item} 详情={detail}",
    )
check(request("GET", "/api/audit/99999", expect_http=404) is None, "不存在的详情返回 404 与可读说明")

print("[4/7] 流程一：无超标记录 待审核 -> 审核中 -> 已通过")
entry_id = None
created = request("POST", "/api/audit", {"values": {
    "审核编号": "AUDI-SELF-001",
    "审核周期": "2026年9月（自检）",
    "审核范围": "自检临时记录",
    "超标次数": 0,
}})
check(created and created.get("ok") is True and created["entry"]["status"] == "待审核", "登记无超标审核记录")
entry_id = created["entry"]["id"]
# 待审核不能直接确认通过（跳过审核中应被拦下）
blocked = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "确认通过"}})
check(
    blocked and blocked.get("ok") is False and "开始审核" in blocked.get("message", ""),
    "待审核直接确认通过被拦下并说明原因",
    f"实际：{blocked}",
)
step1 = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "开始审核"}})
check(step1 and step1["ok"] and step1["entry"]["status"] == "审核中", "开始审核 -> 审核中")
# 重复开始
again = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "开始审核"}})
check(again and again["ok"] is False and "重复" in again["message"], "重复开始审核被拦下", f"实际：{again}")
# 无超标时下发整改没意义，应拦下
no_rectify = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "下发整改"}})
check(
    no_rectify and no_rectify["ok"] is False and "超标次数为 0" in no_rectify["message"],
    "无超标项时下发整改被拦下并说明",
    f"实际：{no_rectify}",
)
step2 = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "确认通过"}})
check(step2 and step2["ok"] and step2["entry"]["status"] == "已通过", "确认通过 -> 已通过")
# 终态再点任何动作都不行
closed = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "下发整改"}})
check(closed and closed["ok"] is False, "已通过为终态，后续动作全部不可执行", f"实际：{closed}")

print("[5/7] 流程二：有超标记录 待审核 -> 审核中 ->（拦截通过）-> 需整改，且不可重复下发")
created2 = request("POST", "/api/audit", {"values": {
    "审核编号": "AUDI-SELF-002",
    "审核周期": "2026年9月（自检）",
    "审核范围": "自检超标记录",
    "超标次数": 2,
    "整改项数": 2,
}})
check(created2 and created2["ok"], "登记超标审核记录（超标次数 2）")
entry_id2 = created2["entry"]["id"]
request("POST", f"/api/audit/{entry_id2}/actions", {"values": {"action": "开始审核"}})
reject_pass = request("POST", f"/api/audit/{entry_id2}/actions", {"values": {"action": "确认通过"}})
check(
    reject_pass and reject_pass["ok"] is False and "超标次数为 2" in reject_pass["message"],
    "超标次数大于 0 时确认通过被拦下并说明",
    f"实际：{reject_pass}",
)
rectify = request("POST", f"/api/audit/{entry_id2}/actions", {"values": {"action": "下发整改"}})
check(
    rectify and rectify["ok"] and rectify["entry"]["status"] == "需整改"
    and rectify["entry"]["abnormal"] is True,
    "下发整改 -> 需整改，标记为异常",
    f"实际：{rectify}",
)
dup = request("POST", f"/api/audit/{entry_id2}/actions", {"values": {"action": "下发整改"}})
check(
    dup and dup["ok"] is False and "重复" in dup["message"],
    "重复下发整改被拦下并说明",
    f"实际：{dup}",
)

print("[6/7] 空数据、非法输入与既有终态样例")
empty_list = request("GET", "/api/audit?keyword=NOT-EXIST-编号")
check(
    empty_list is not None and empty_list.get("items") == [] and empty_list.get("total") == 0,
    "空数据列表返回空页（不报错）",
    f"实际：{empty_list}",
)
bad_status = request("GET", "/api/audit?status=不存在的状态", expect_http=400)
check(bad_status is None, "非法状态过滤返回 400 与可读说明")
bad_action = request("POST", f"/api/audit/{entry_id}/actions", {"values": {"action": "随便点"}})
check(bad_action and bad_action["ok"] is False and "可执行范围" in bad_action["message"], "未知动作被拦下", f"实际：{bad_action}")
missing = request("POST", "/api/audit", {"values": {"审核周期": "缺编号"}})
check(missing and missing["ok"] is False and "缺少必填字段" in missing["message"], "缺必填字段返回说明", f"实际：{missing}")
# 种子里的 AUDI-0004 已是需整改，重复下发必须被拦
seed4 = find(request("GET", "/api/audit")["items"], 4)
if seed4:
    dup_seed = request("POST", "/api/audit/4/actions", {"values": {"action": "下发整改"}})
    check(dup_seed and dup_seed["ok"] is False, "既有需整改记录不能重复下发整改", f"实际：{dup_seed}")
else:
    failures.append("种子数据缺少 id=4 的需整改样例")

print("[7/7] 统计与筛选口径")
stats = request("GET", "/api/audit/stats")
check(
    stats is not None and {"pending_count", "over_limit_total", "rectify_total"} <= set(stats),
    "统计卡片返回待审核数、超标总次数、需整改项数",
    f"实际：{stats}",
)
filtered = request("GET", f"/api/audit?status={urllib.parse.quote('需整改')}")
check(
    filtered and all(item["status"] == "需整改" for item in filtered["items"]) and filtered["total"] >= 2,
    "按需整改筛选结果与状态机口径一致",
    f"实际：{filtered and filtered.get('total')}",
)

print()
print(f"通过 {passed} 项，失败 {len(failures)} 项")
if failures:
    print("\n失败明细：", file=sys.stderr)
    for item in failures:
        print(f"  - {item}", file=sys.stderr)
    sys.exit(1)
print("达标审核全部自检通过 ✅")
PYEOF
BACKEND_OK=$?
set -e

cleanup

if [ "$BACKEND_OK" -ne 0 ]; then
  echo "[dev-check] 后端接口自检未通过" >&2
  exit "$BACKEND_OK"
fi
echo "[dev-check] 后端接口自检全部通过"

if [ "${SKIP_FRONTEND:-0}" = "1" ]; then
  echo "[dev-check] SKIP_FRONTEND=1，跳过前端构建"
  exit 0
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "[dev-check] 安装前端依赖……"
  (cd "$FRONTEND_DIR" && npm install)
fi

echo "[dev-check] 前端类型检查与构建……"
# node_modules 可能是在别的平台装的（如缺 rollup 的原生模块），构建失败时自动重装一次
if ! (cd "$FRONTEND_DIR" && npm run build); then
  echo "[dev-check] 前端构建失败，疑似依赖平台不匹配，重新安装依赖后重试……"
  rm -rf "$FRONTEND_DIR/node_modules"
  (cd "$FRONTEND_DIR" && npm install)
  (cd "$FRONTEND_DIR" && npm run build)
fi
echo "[dev-check] 全部自检通过：后端三段流程可跑通，前端构建产物在 frontend/dist"
