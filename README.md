---
title: Creierbot
emoji: 🐳
colorFrom: pink
colorTo: indigo
sdk: docker
app_port: 7860
---

# 🚀 Creierosu Bot

A powerful Telegram bot with a FastAPI Sales Dashboard.

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/new/import?repository=https://github.com/fixonz/creierosubotsales)

## ⚡ Deployment Instructions

### 1. Simple One-Click Deployment
1.  Click the **Deploy on Railway** button above.
2.  If you don't have an account, sign up for a trial.
3.  Add your **Environment Variables** (from `.env`) in the Railway dashboard.
4.  **Important:** In the Railway service settings, go to **Volumes** and add a volume mounted at `/app/data` to keep your database safe!

### 2. VPS Deployment (Docker)
If you want to use a VPS (Google, Alibaba, etc.), follow these guides:
- [GCP (Google Cloud) Guide](GCP_DEPLOY_GUIDE.md)
- [Alibaba Cloud Guide](ALIBABA_DEPLOY_GUIDE.md)
- [Free Hosting Guide](FREE_HOSTING_GUIDE.md)

## 🐳 Running locally with Docker
```bash
docker-compose up --build
```

---
*Created by Antigravity*
