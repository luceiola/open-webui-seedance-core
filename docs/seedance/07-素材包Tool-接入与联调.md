# 素材包 Tool 接入与联调

本文档目标：把“ZIP 素材包 + prompt 引用 + Seedance 生成”在 OpenWebUI 中跑通为可复用流程。

## 1. 前置条件

- OpenWebUI 已运行（建议使用当前目录 `.venv` 安装版本）。
- 已完成素材包后端路由改造（`/api/v1/material-packages/*`）。
- 已配置 `ARK_API_KEY`（服务端环境变量）。
- 对话模型具备 Tool Calling 能力。

## 2. 关键检查（先做）

先确认服务是否加载了素材包路由：

```bash
curl -sS http://127.0.0.1:8080/openapi.json \
  | jq -r '.paths | keys[]' \
  | rg "/api/v1/material-packages"
```

如果没有任何输出，说明当前进程还未加载新路由，先重启：

```bash
pkill -f "open-webui serve"
source .venv/bin/activate
open-webui serve --host 127.0.0.1 --port 8080
```

## 3. 导入 Tool

在 OpenWebUI 后台导入以下文件：

- [seedance_material_package_tool_v2.import.json](/Users/lucas/Documents/openwebui-seedance/templates/seedance_material_package_tool_v2.import.json)

注意：
- 仅维护一个导入文件：`seedance_material_package_tool_v2.import.json`。
- `Workspace -> Tools -> Import` 按钮只接受 `.json`（UI 限制），不会识别 `.py`。
- 如果旧版工具出现 `list_material_packages` 502，使用 v2 版本（本地后端优先实现）。

该 Tool 暴露 10 个能力：

- `create_material_package_from_chat_upload`
- `list_material_packages`
- `get_material_package`
- `get_material_package_assets`
- `resolve_material_references`
- `generate_video_with_material_package`
- `list_generation_tasks`
- `get_generation_task_status`
- `wait_generation_task`
- `generate_and_wait_with_material_package`

## 4. Tool 参数建议

`Valves` 推荐配置：

- `OPENWEBUI_BASE_URL`: `http://127.0.0.1:8080`
- `DEFAULT_SEEDANCE_MODEL`: 你的默认 Seedance 模型 ID
- `OPENWEBUI_API_KEY`: 可选；当请求上下文取不到用户认证时再配置

说明：
- Tool 会优先尝试透传当前请求的 `Authorization` 或 `token` Cookie。
- 因此同站点调用通常不需要额外填 `OPENWEBUI_API_KEY`。

## 5. 模型与工具绑定

1. 选择一个支持 Tool Calling 的基础模型作为对话模型。
2. 将本 Tool 绑定到该模型（或会话）。
3. 系统提示词建议加一条规则：

```text
当用户给出 asset_package_id 且需求为视频生成时，优先调用 generate_video_with_material_package。
在调用前，可先调用 resolve_material_references 校验 @引用是否存在。
```

## 6. 最小联调流程

1. 上传一个素材 ZIP（目前可先通过后端 API 上传）。
2. 记录返回 `asset_package_id`。
3. 在聊天中输入示例：

```text
使用素材包 pkg_xxx 生成 8 秒短片：开头使用 @intro.mp4，旁白用 @voice.wav，封面风格参考 @cover.jpg。
```

4. 预期链路：
- 模型调用 `resolve_material_references`（可选）
- 模型调用 `generate_video_with_material_package`
- 返回 `response_id/status/output_text`
- 模型调用 `get_generation_task_status` 或 `wait_generation_task`
- 返回最终 `status/video_url`

推荐（一步到位）：
- 模型直接调用 `generate_and_wait_with_material_package`，提交后等待终态并返回 `video_url`。

## 7. 常见问题

- 401/403：Tool 未带上用户认证；补 `OPENWEBUI_API_KEY` 或检查登录状态。
- 404（package）：`asset_package_id` 不存在，或用户不匹配。
- 400（missing_references）：prompt 中 `@文件名` 未命中素材包引用名。
- 400（ARK_API_KEY is not configured）：服务端环境变量未配置。
- 400（TOS is required for material package upload）：当前是 TOS-only 模式，需配置 `MATERIAL_PACK_TOS_ENABLED=true` 和 `TOS_*`。
- 400（Unable to resolve TOS URL for some references）：请检查 TOS 配置、桶权限及对象是否可签名下载。
