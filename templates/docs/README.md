# templates/docs 说明

本目录用于存放 **Skill 开发文档** 与 **Agent 行为规范文档**。

文档分层约定：

1. Skill/Agent 能力设计、路由规则、模板规范：放 `templates/docs/`。
2. WebUI 框架开发、接口与部署文档：放 `docs/seedance/`。
3. `docs/seedance/` 中涉及 Skill 逻辑时，引用 `templates/docs/` 对应文档，避免重复维护。

版本与发布门禁：

- 版本注册：`templates/versions/registry.json`
- 路由注册：`config/seedance_routing_registry.yaml`
- 发布门禁：`templates/docs/工具版本与发布门禁.md`
- 单文件导入打包：`scripts/seedance/build_standalone_tools.py`
