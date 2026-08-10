# 45-KB与Skill路由注册表规范

## 目标

为持续增长的知识库条目建立可审计、可回滚的路由治理机制，避免“口头约定”导致策略漂移。

## 一、元数据标准（KB 条目）

每条知识必须带以下字段：

- `kb_id`：知识库 ID（如 `KB-01-规则库`、`KB-02-模板库`）
- `doc_id`：文档唯一 ID
- `version`：语义版本（如 `v1.0.0`）
- `status`：`draft | active | deprecated`
- `intent_tags`：意图标签（如 `prompt_optimize`、`storyboard_template`）
- `media_scope`：`image | video | audio | text | mixed`
- `owner`：负责人
- `updated_at`：最后更新时间（ISO 8601）

模板类条目额外字段：

- `template_id`：模板标识（如 `storyboard_list_v1`）
- `output_schema`：输出字段定义
- `required_fields`：必填字段列表
- `missing_policy`：缺失字段策略（当前固定为 `[待补充]`）

## 二、路由注册表（Skill Routing Registry）

建议文件：`config/seedance_routing_registry.yaml`

推荐结构（示例）：

```yaml
version: v1
updated_at: "2026-08-10T00:00:00+08:00"
rules:
  - rule_id: R-DSP-001
    status: active
    priority: 100
    intent: media_describe_single_image
    trigger:
      mode: explicit_or_detected
      conditions:
        - single_media_reference_only
        - media_type=image
    route:
      skill: volcengine-media-description-execution-skill
      tool: describe_image
      kb_scope: []
      template_id: ""
      params:
        description_method: detailed_visual
        custom_instruction: ""
        output_language: zh-CN
```

## 三、优先级与冲突处理

固定优先级：

1. 显式口令
2. 会话上下文
3. 默认路由

冲突时按 `priority` 数值高者生效；相同优先级按 `rule_id` 字典序稳定决策。

## 四、变更门禁

新增或修改知识条目时，必须同时提交：

1. KB 条目内容与元数据
2. 路由注册表规则变更
3. 至少 1 条回归用例（输入 -> 命中 `rule_id` -> 预期输出）

禁止将仅当前轮有效的临时格式要求写入 KB 条目或路由注册表。

## 五、观测指标

按 `rule_id` 记录：

- 命中次数
- 描述成功率
- 用户追问率
- 人工纠正率
- 模板检索失败率

建议每月做一次低命中/高纠错规则治理，执行降级或废弃（`deprecated`）。
