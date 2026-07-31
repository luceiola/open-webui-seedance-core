# Agents Overview (templates)

本文档用于快速说明 `templates/` 下各 Agent 的定位与文件映射。

统一版本与路由：

- 版本注册表：`templates/versions/registry.json`
- 路由注册表：`config/seedance_routing_registry.yaml`
- 发布门禁：`templates/docs/工具版本与发布门禁.md`
- 工具导入方式：不再维护 `*.import.json`，统一直接粘贴 `templates/*_tool.py` 内容。

## 1) Seedance 视频生成 Agent

- 用途：基于 media-assets（`%素材路径`）进行视频任务提交、查询、等待、任务入库。
- System Prompt：`prompts/seedance_system_prompt.txt`
- Skill：`skills/seedance-execution-skill/SKILL.md`
- Tool：`seedance_material_package_tool.py`
- 关键入口函数：
  - `generate_video_with_media_assets`
  - `list_generation_tasks`
  - `get_generation_task_status`
  - `wait_generation_task`

## 2) HappyHorse 视频生成 Agent

- 用途：HappyHorse 参考生视频链路，支持 `%素材路径` 引用与任务查询。
- System Prompt：`prompts/happyhorse_system_prompt.txt`
- Skill：`skills/happyhorse-execution-skill/SKILL.md`
- Tool：`happyhorse_media_tool.py`
- 关键入口函数：
  - `generate_video_with_happyhorse`
  - `get_happyhorse_task_status`
  - `wait_happyhorse_task`

## 3) GPT-Image-2 图片生成 Agent

- 用途：基于 media-assets（`%素材路径`）进行图片生成，包含结果归档与任务查询。
- System Prompt：`prompts/gpt_image2_system_prompt.txt`
- Skill：`skills/gpt-image2-execution-skill/SKILL.md`
- Tool：`gpt_image2_media_tool.py`
- 关键入口函数：
  - `generate_image_with_media_assets`
  - `list_generation_tasks`
  - `get_generation_task_status`
  - `wait_generation_task`

## 4) Doubao Seed Prompt Base Agent（共建 + 基础素材描述）

- 用途：支持“视频提示词共建/改稿”“基础素材描述复用”。
- 说明：不处理专业分镜模板，不提交生成任务。
- System Prompt：`prompts/doubao_seed_prompt_system_prompt.txt`
- Skill：`skills/doubao-seed-prompt-execution-skill/SKILL.md`
- Tool：`doubao_seed_prompt_tool.py`
- 关键入口函数：
  - `co_create_video_prompt_with_seed_pro`
  - `describe_media_assets_for_prompt`
  - `list_media_assets`
  - `get_media_asset`
  - `get_media_asset_url`
  - `resolve_media_asset_references`

## 5) Doubao Seed Storyboard Template Agent（分镜模板专用）

- 用途：仅处理 `storyboard_list_v1` 专业分镜模板输出。
- 说明：只做模板产出，不做共建改稿与优化，不提交生成任务。
- System Prompt：`prompts/doubao_seed_storyboard_template_system_prompt.txt`
- Skill：`skills/doubao-seed-storyboard-template-execution-skill/SKILL.md`
- Tool：`doubao_seed_prompt_tool.py`
- 关键入口函数：
  - `describe_media_assets_for_prompt`（固定 `template_id=storyboard_list_v1`）
  - `resolve_media_asset_references`
  - `list_media_assets`
  - `get_media_asset`
  - `get_media_asset_url`

## 6) RunningHub Seedance2 视频 Agent（au vendor）

- 用途：调用 `au vendor rh-seedance2-video / rh-seedance2-mini-video / rh-seedance2-fast-video` 进行多模态视频生成，支持统一任务中心可见与状态刷新。
- System Prompt：`prompts/runninghub_seedance2_system_prompt.txt`
- Skill：`skills/runninghub-seedance2-execution-skill/SKILL.md`
- Tool：`runninghub_seedance2_tool.py`
- 关键入口函数：
  - `generate_video_with_runninghub_seedance2`
  - `list_generation_tasks`
  - `get_generation_task_status`
  - `wait_generation_task`

## 7) RunningHub Hailuo H3 视频 Agent（au vendor）

- 用途：固定调用 `au vendor rh-hailuo-h3-video`，支持文生视频、显式首尾帧图生视频和多模态参考生视频。
- System Prompt：`prompts/runninghub_hailuo_h3_system_prompt.txt`
- Skill：`skills/runninghub-hailuo-h3-execution-skill/SKILL.md`
- Tool：`runninghub_hailuo_h3_tool.py`
- 关键入口函数：
  - `generate_video_with_runninghub_hailuo_h3`
  - `list_generation_tasks`
  - `get_generation_task_status`
  - `wait_generation_task`

## 8) BTN Image2 图片 Agent（au vendor）

- 用途：调用 `au vendor btn-image2-gen / btn-image2-edit`，默认 9:16 竖屏尺寸与 auto 质量，支持多图参考并写入统一任务中心。
- 说明：该 Agent 为直接等待完成型，不提供查询方法。
- System Prompt：`prompts/btn_image2_system_prompt.txt`
- Skill：`skills/btn-image2-execution-skill/SKILL.md`
- Tool：`btn_image2_tool.py`
- 关键入口函数：
  - `generate_image_with_btn_image2_gen`
  - `edit_image_with_btn_image2`

## 9) 其他文件说明

- `skills/seedance-user-guide-skill/SKILL.md`：固定版用户手册输出技能（说明类，不负责生成任务）。
- `prompts/seedance_video_description_prompt.txt`：视频描述模板提示词（模板用途，非主编排 Agent）。
- `seedance_video_tool.py`：早期模板工具，当前主线已由 `seedance_material_package_tool.py` 替代。
