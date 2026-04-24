# Git 工作流

## 1. 初始化仓库

```bash
git init
git add .
git commit -m "chore: bootstrap openwebui-seedance workspace"
```

## 2. 分支策略（轻量）

- `main`: 可运行版本
- `feat/*`: 新功能（如 `feat/seedance-tool-mvp`）
- `fix/*`: 问题修复

## 3. 提交信息建议

- `chore:` 环境/脚手架
- `docs:` 文档
- `feat:` 新功能
- `fix:` 修复
- `refactor:` 重构
- `test:` 测试

示例：

```text
feat(seedance): add async task submit and polling
fix(tool): handle 429 retry with backoff
docs: add tool integration guide
```

## 4. 合并前检查

- 本地可启动 OpenWebUI
- Tool 至少有 1 条成功调用记录
- 关键路径日志可定位问题
