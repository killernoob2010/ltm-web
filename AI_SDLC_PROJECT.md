---
schema_version: 1
project_id: lightweight-trading-management-web
project_type: web_app
branches:
  testing: staging
  production: main
environments:
  testing: Render ltm-web-staging + Supabase LTM WEB STAGING
  production: Render ltm-web + Supabase LTM WEB
sources:
  setup: README.md
  workflow: 开发流程_备忘.md
  requirements: docs
  handoff: handoffs
  releases: 版本更新记录.md
production_confirmation: required
worktree_policy: project_defined
---

# AI SDLC 项目适配

本文件只负责把 AI SDLC 路由到项目现有事实来源。环境命令、业务规则、交接状态和发布历史分别以 frontmatter 中列出的原文件为准。
