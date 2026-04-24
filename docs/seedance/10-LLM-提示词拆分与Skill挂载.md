# LLM 提示词拆分与 Skill 挂载

本文给出三段可组合配置：

1. 视频描述（用户层）
2. 执行规范 Skill（流程层）
3. 系统提示词（角色层）

## 1) 视频描述（用户层）

文件：
- `/Users/lucas/Documents/openwebui-seedance/templates/prompts/seedance_video_description_prompt.txt`

用途：
- 仅描述镜头语言与广告意图。
- 不包含工具调用步骤。

## 2) 执行规范 Skill（流程层）

文件：
- `/Users/lucas/Documents/openwebui-seedance/templates/skills/seedance-execution-skill/SKILL.md`

用途：
- 约束工具调用顺序与失败处理。
- 强制 `resolve -> generate`。

## 3) 系统提示词（角色层）

文件：
- `/Users/lucas/Documents/openwebui-seedance/templates/prompts/seedance_system_prompt.txt`

用途：
- 定义模型身份与职责边界。
- 防止模型猜测错误原因。

## 推荐挂载顺序（OpenWebUI）

1. 把系统提示词设置为模型 System Prompt。
2. 把执行 Skill 绑定到同一模型/助手。
3. 用户对话里只提交“视频描述正文 + asset_package_id”。

## 最小实测输入（用户消息）

```text
使用素材包 pkg_627740245f2c401f 生成一条 8 秒 16:9 暖色调早餐广告。
开场参考 @17356c7c-904f-4bef-b376-fc9cc1fa76de.jpg，
结尾定格参考 @cf27a95f-764a-4ef3-b6bc-ee286efd4d89.jpg。
节奏偏快，突出新鲜与活力，结尾强化品牌记忆点。
```
