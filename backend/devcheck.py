#!/usr/bin/env python3
"""本地开发启动自检：起后端真实进程，把达标审核三段流程与边界场景跑一遍。

用法：
    ./devcheck.py                 # 自动在空闲端口拉起 uvicorn 并自检，结束后自动停服
    ./devcheck.py --url http://127.0.0.1:8000   # 对已经启动的服务做自检

覆盖：
    1. 健康检查与状态机元数据（前后端共用的同一份状态/动作配置）
    2. 列表/详情/导出一致性，空筛选数据返回空页而非报错
    3. 状态流转：待审核→审核中→已通过（无超标）
    4. 超标次数 > 0 时确认通过被拦、必须先下发整改，且不能重复下发
    5. 路由顺序：/export、/meta 不被 /{entry_id} 截获（不再 422）
    6. 登记：缺字段、负数/非数字超标次数、重复审核编号都要有可读说明

退出码 0 表示全部通过，非 0 表示有失败项，可直接接到构建/启动脚本后面。
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def call(self, method: str, path: str, body: object | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())


class CheckRunner:
    def __init__(self, api: ApiClient) -> None:
        self.api = api
        self.failures: list[str] = []
        self.passed = 0

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  ✓ {name}")
        else:
            self.failures.append(f"{name} {detail}".strip())
            print(f"  ✗ {name} {detail}")

    def expect_ok(self, name: str, method: str, path: str, body: object | None = None) -> dict:
        status, payload = self.api.call(method, path, body)
        self.check(name, status == 200, f"HTTP {status} {payload}")
        return payload if isinstance(payload, dict) else {}

    # ---- 各段流程 ----

    def health_and_meta(self) -> None:
        print("[1/6] 健康检查与状态机元数据")
        health = self.expect_ok("健康检查 200", "GET", "/api/health")
        self.check("示例数据已加载", int(health.get("modules", 0)) > 0, str(health))

        # export / meta 必须排在 /{entry_id} 之前，否则被当 int 解析报 422
        status, export_payload = self.api.call("GET", "/api/audit/export")
        self.check("导出接口不返回 422（路由顺序正确）", status == 200, f"HTTP {status}")
        meta = self.expect_ok("状态机元数据可读", "GET", "/api/audit/meta")
        self.check(
            "元数据声明了唯一的状态与动作",
            meta.get("statuses") == ["待审核", "审核中", "已通过", "需整改"]
            and meta.get("actions") == ["开始审核", "确认通过", "下发整改"],
            str(meta),
        )
        self.check("终态包含已通过/需整改", set(meta.get("terminal_statuses", [])) == {"已通过", "需整改"})
        self.export_payload = export_payload if isinstance(export_payload, dict) else {}

    def list_and_detail(self) -> None:
        print("[2/6] 列表、详情与导出一致性")
        listing = self.expect_ok("列表可读", "GET", "/api/audit")
        items = listing.get("items", [])
        self.check("列表至少含 4 条示例审核记录", len(items) >= 4, f"实际 {len(items)} 条")
        self.check("列表总数与条目数一致", listing.get("total") == len(items))
        row = items[0]
        detail = self.expect_ok("详情可读", "GET", f"/api/audit/{row['id']}")
        self.check("列表与详情字段一致（含审核状态/可执行动作）", row == detail, f"\n列表={row}\n详情={detail}")
        self.check("导出内容与列表一致", self.export_payload.get("items") == items)
        self.check("详情的状态机字段齐全", detail.get("审核状态") and isinstance(detail.get("可执行动作"), list))

    def empty_data(self) -> None:
        print("[3/6] 空数据：筛选无结果返回空页，导出返回空列表")
        miss = "AUDI-NOT-EXIST-" + uuid.uuid4().hex[:8]
        empty = self.expect_ok("无匹配关键词返回 200 空页", "GET", f"/api/audit?keyword={urllib.parse.quote(miss)}")
        self.check("空页 items 为空、total 为 0", empty.get("items") == [] and empty.get("total") == 0)
        export_empty = self.expect_ok("空结果导出 200 且 items 为空", "GET", f"/api/audit/export?keyword={urllib.parse.quote(miss)}")
        self.check("空导出 total 为 0", export_empty.get("total") == 0)
        status, payload = self.api.call("GET", "/api/audit/999999")
        self.check("不存在的详情返回 404 且带说明", status == 404 and "不存在" in str(payload))

    def clean_pass_flow(self) -> str:
        print("[4/6] 流程一：无超标记录 待审核→审核中→确认通过")
        code = "AUDI-CHECK-OK-" + uuid.uuid4().hex[:6]
        created = self.expect_ok(
            "登记无超标审核记录",
            "POST",
            "/api/audit",
            {"values": {"审核编号": code, "审核周期": "2026年9月", "审核范围": "自检-总排口", "超标次数": 0}},
        )
        entry = created.get("entry") or {}
        entry_id = entry.get("id")
        self.check("新记录为待审核、仅可开始审核", entry.get("审核状态") == "待审核" and entry.get("可执行动作") == ["开始审核"], str(entry))

        # 待审核状态直接确认通过 / 下发整改都不允许（动作不允许跨状态）
        status, blocked = self.api.call("POST", f"/api/audit/{entry_id}/actions", {"action": "确认通过"})
        self.check("待审核不能直接确认通过", status == 200 and isinstance(blocked, dict) and not blocked.get("ok"))
        started = self.expect_ok("开始审核", "POST", f"/api/audit/{entry_id}/actions", {"action": "开始审核"})
        self.check("开始后进入审核中、可确认通过", (started.get("entry") or {}).get("审核状态") == "审核中")
        # 重复开始审核
        _, dup_start = self.api.call("POST", f"/api/audit/{entry_id}/actions", {"action": "开始审核"})
        self.check("重复开始审核被拦并说明", isinstance(dup_start, dict) and "重复" in str(dup_start.get("message")))
        passed = self.expect_ok("确认通过", "POST", f"/api/audit/{entry_id}/actions", {"action": "确认通过"})
        self.check("通过后为已通过、无可用动作", (passed.get("entry") or {}).get("审核状态") == "已通过"
                   and (passed.get("entry") or {}).get("可执行动作") == [])
        return str(entry_id)

    def exceed_flow(self) -> None:
        print("[5/6] 流程二：超标记录必须先整改；重复下发要有说明")
        code = "AUDI-CHECK-EX-" + uuid.uuid4().hex[:6]
        created = self.expect_ok(
            "登记超标审核记录",
            "POST",
            "/api/audit",
            {"values": {"审核编号": code, "审核周期": "2026年9月", "审核范围": "自检-生化段", "超标次数": 2}},
        )
        entry_id = (created.get("entry") or {}).get("id")
        self.expect_ok("开始审核（超标记录）", "POST", f"/api/audit/{entry_id}/actions", {"action": "开始审核"})
        _, blocked_pass = self.api.call("POST", f"/api/audit/{entry_id}/actions", {"action": "确认通过"})
        msg = (blocked_pass or {}).get("message", "") if isinstance(blocked_pass, dict) else ""
        self.check("超标时确认通过被拦并解释超标次数", "超标" in msg and "2" in msg, msg)
        rectified = self.expect_ok("下发整改", "POST", f"/api/audit/{entry_id}/actions", {"action": "下发整改"})
        rect_entry = rectified.get("entry") or {}
        self.check("进入需整改、整改项数按超标次数落为 2", rect_entry.get("审核状态") == "需整改" and rect_entry.get("整改项数") == 2, str(rect_entry))
        _, dup = self.api.call("POST", f"/api/audit/{entry_id}/actions", {"action": "下发整改"})
        self.check("重复下发整改被拦并说明", isinstance(dup, dict) and "重复" in str(dup.get("message")), str(dup))
        # 终态任何动作都不再可用
        _, again = self.api.call("POST", f"/api/audit/{entry_id}/actions", {"action": "确认通过"})
        self.check("需整改后确认通过也被拦", isinstance(again, dict) and not again.get("ok"))

    def validation_messages(self) -> None:
        print("[6/6] 登记校验：缺字段、非法超标次数、重复编号都有说明")
        _, missing = self.api.call("POST", "/api/audit", {"values": {"审核周期": "2026年9月"}})
        self.check("缺必填字段被拦并指出字段名", isinstance(missing, dict) and "审核编号" in str(missing.get("message")))
        _, negative = self.api.call(
            "POST", "/api/audit",
            {"values": {"审核编号": "AUDI-NEG-1", "审核周期": "x", "审核范围": "y", "超标次数": -1}},
        )
        self.check("负数超标次数被拦并说明", isinstance(negative, dict) and "不能为负数" in str(negative.get("message")))
        _, bad_text = self.api.call(
            "POST", "/api/audit",
            {"values": {"审核编号": "AUDI-TXT-1", "审核周期": "x", "审核范围": "y", "超标次数": "两次"}},
        )
        self.check("非数字超标次数被拦并说明", isinstance(bad_text, dict) and "非负整数" in str(bad_text.get("message")))
        existing = self.api.call("GET", "/api/audit")[1]["items"][0]["审核编号"]
        _, duplicate = self.api.call(
            "POST", "/api/audit",
            {"values": {"审核编号": existing, "审核周期": "2026年9月", "审核范围": "重复编号"}},
        )
        self.check("重复审核编号被拦并说明", isinstance(duplicate, dict) and "已存在" in str(duplicate.get("message")))

    def run(self) -> bool:
        self.health_and_meta()
        self.list_and_detail()
        self.empty_data()
        self.clean_pass_flow()
        self.exceed_flow()
        self.validation_messages()
        print()
        if self.failures:
            print(f"自检未通过：{len(self.failures)} 项失败，{self.passed} 项通过")
            for failure in self.failures:
                print(f"  - {failure}")
            return False
        print(f"自检全部通过：{self.passed} 项检查 OK，达标审核三段流程与边界场景均可跑通")
        return True


def wait_until_ready(api: ApiClient, attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            status, _ = api.call("GET", "/api/health")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="达标审核本地开发启动自检")
    parser.add_argument("--url", help="对已运行的后端做自检，缺省时自动拉起 uvicorn")
    args = parser.parse_args()

    proc: subprocess.Popen[bytes] | None = None
    if args.url:
        base_url = args.url
    else:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        print(f"启动本地后端：{base_url} …")
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
            cwd=HERE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    try:
        if not wait_until_ready(ApiClient(base_url)):
            print("后端在限定时间内未就绪，请先用 ./run.sh 查看启动日志", file=sys.stderr)
            return 2
        return 0 if CheckRunner(ApiClient(base_url)).run() else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
