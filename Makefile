.PHONY: install backend frontend check check-backend build-frontend

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 本地开发自检：后端自动起服跑达标审核三段流程与边界场景（空数据/超标/重复下发）
check-backend:
	cd backend && python3 devcheck.py

# 前端类型检查 + 构建
build-frontend:
	cd frontend && npm run build

# 构建后自检：先构建前端，再起后端把接口流程全部跑通
check: build-frontend check-backend
