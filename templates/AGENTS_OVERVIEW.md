# Agents Overview (templates)

本文档用于快速说明 `templates/` 下各 Agent 的定位与文件映射。

## 1) Seedance 视频生成 Agent

- 用途：基于 media-assets（`%素材路径`）进行视频任务提交、查询、等待、任务入库。
- System Prompt：`prompts/seedance_system_prompt.txt`
- Skill：`skills/seedance-execution-skill/SKILL.md`
- Tool：`seedance_material_package_tool.py`
- Import 包：`seedance_material_package_tool_v2.import.json`（`id=seedance_material_package_tool_v2`）
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
- Import 包：`happyhorse_media_tool_v1.import.json`（`id=happyhorse_media_tool_v1`）
- 关键入口函数：
  - `generate_video_with_happyhorse`
  - `get_happyhorse_task_status`
  - `wait_happyhorse_task`

## 3) GPT-Image-2 图片生成 Agent

- 用途：基于 media-assets（`%素材路径`）进行图片生成，包含结果归档与任务查询。
- System Prompt：`prompts/gpt_image2_system_prompt.txt`
- Skill：`skills/gpt-image2-execution-skill/SKILL.md`
- Tool：`gpt_image2_media_tool.py`
- Import 包：`gpt_image2_media_tool_v1.import.json`（`id=gpt_image2_media_tool_v1`）
- 关键入口函数：
  - `generate_image_with_media_assets`
  - `list_generation_tasks`
  - `get_generation_task_status`
  - `wait_generation_task`

## 4) Doubao Seed Prompt Merged Agent（共创 + 优化 + 素材描述）

- 用途：同一 Agent 内支持“视频提示词共创/改稿”“KB 强依赖优化”“媒体素材描述复用”。
- 说明：仍不提交生成任务，仅做 prompt 侧能力。
- System Prompt：`prompts/doubao_seed_prompt_system_prompt.txt`
- Skill：`skills/doubao-seed-prompt-execution-skill/SKILL.md`
- Tool：`doubao_seed_prompt_tool.py`
- Import 包：`doubao_seed_prompt_tool_v1.import.json`（`id=doubao_seed_prompt_tool_v1`）
- 关键入口函数：
  - `co_create_video_prompt_with_seed_pro`
  - `optimize_video_prompt_with_kb_for_seedance2`
  - `describe_media_assets_for_prompt`
  - `list_media_assets`
  - `get_media_asset`
  - `get_media_asset_url`
  - `resolve_media_asset_references`
  - `get_seed_pro_multimodal_input_limits`

## 5) 其他文件说明

- `skills/seedance-user-guide-skill/SKILL.md`：固定版用户手册输出技能（说明类，不负责生成任务）。
- `prompts/seedance_video_description_prompt.txt`：视频描述模板提示词（模板用途，非主编排 Agent）。
- `seedance_video_tool.py`：早期模板工具，当前主线已由 `seedance_material_package_tool.py` 替代。
