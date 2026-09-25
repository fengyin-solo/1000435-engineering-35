.PHONY: install backend frontend check

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
	cd frontend && npm install

backend:
	cd backend && ./run.sh

frontend:
	cd frontend && npm run dev

# 本地开发启动自检：临时起后端跑通达标审核三段流程，并构建前端
check:
	scripts/dev-check.sh
