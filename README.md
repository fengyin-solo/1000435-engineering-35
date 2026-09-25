# 污水处理厂工艺管控平台

面向污水处理厂进出水监测、工艺单元、加药曝气、污泥处置与达标排放的一体化工艺管控后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 启动自检（推荐先跑一遍）

```bash
scripts/dev-check.sh        # 或 make check
SKIP_FRONTEND=1 scripts/dev-check.sh   # 只检后端接口
```

脚本会在临时端口（默认 8011，可用 `CHECK_PORT` 覆盖）拉起后端，用内置示例数据
把达标审核的三段流程与边界场景跑一遍（共 30+ 项断言），再做前端类型检查与构建，
结束后自动关闭临时服务：

1. 无超标记录：登记 → 开始审核 → 确认通过；
2. 有超标记录：登记 → 开始审核 → 确认通过被拦（说明先整改）→ 下发整改 → 重复下发被拦；
3. 导出清单：`GET /api/audit/export` 正常返回，空过滤条件返回空清单并附说明。

边界说明（空数据、超标次数、重复下发）：

- **空数据**：列表接口始终返回 `{items: [], total: 0}` 空页，不报错；
  导出接口在空结果时额外带 `message` 说明当前过滤条件下没有记录。
- **超标次数**：为空、缺失或非数字时按 0 处理（列表、详情、统计同口径）；
  超标次数大于 0 时不能「确认通过」，必须先「下发整改」；为 0 时不能下发整改。
- **重复下发**：「需整改」是终态，再次「下发整改」会被状态机拦下并说明，
  「已通过」同理；「开始审核」也不能重复执行。

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 厂区单元 | `plant` | 工艺单元 | 单元编码、单元名称、处理工艺 |
| 进水监测 | `inflow` | 进水记录 | 监测编号、采样时间、进水流量 |
| 出水监测 | `effluent` | 出水记录 | 监测编号、采样时间、出水流量 |
| 曝气控制 | `aeration` | 曝气记录 | 记录编号、曝气池编号、溶解氧值 |
| 加药管理 | `dosing` | 加药单 | 加药单号、药剂名称、投加浓度 |
| 污泥处置 | `sludge` | 污泥处置单 | 处置单号、污泥来源、含水率 |
| 脱水运行 | `dewater` | 脱水记录 | 记录编号、脱水机编号、进泥量 |
| 泵站运行 | `pump` | 泵站 | 泵站编号、泵组台数、运行泵号 |
| 鼓风机组 | `blower` | 鼓风机组 | 机组编号、机组型号、额定风量 |
| 膜组件 | `membrane` | 膜组 | 膜组编号、膜型号、膜面积 |
| 在线仪表 | `online` | 在线仪表 | 仪表编号、仪表类型、测量范围 |
| 取样检测 | `sample` | 检测单 | 检测单号、取样点位、检测项目 |
| 药剂出入 | `chemical` | 药剂单据 | 单据编号、药剂名称、规格型号 |
| 能耗管理 | `energy` | 能耗记录 | 记录编号、统计日期、用电量 |
| 报警中心 | `alarm` | 报警事件 | 报警编号、报警类型、报警等级 |
| 设备检修 | `maint` | 检修单 | 检修单号、关联设备、检修类型 |
| 受限空间作业 | `permit` | 作业许可单 | 许可编号、作业类型、作业地点 |
| 达标审核 | `audit` | 审核记录 | 审核编号、审核周期、审核范围 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。
- 达标审核的状态、动作与流转条件只有一份定义：
  `backend/app/services/audit_machine.py`。前端不自己维护动作与状态清单，
  统一从 `GET /api/audit/state-machine` 取定义、按每条记录返回的 `actions[]`
  （含 `enabled` 与不可点 `reason`）渲染按钮，保证同一审核编号两边永远同步。
- 列表与详情共用同一序列化出口（`serialize_entry`），两条路径返回字段完全一致。
- `/export`、`/state-machine`、`/stats` 等字面量路由必须声明在 `/{entry_id}`
  之前，否则路径参数会把它们当成审核编号解析而返回 422。
