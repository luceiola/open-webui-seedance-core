# 46-双Agent踩坑报告（RunningHub-Seedance2 / BTN-Image2）

## 1. 目的与范围

本文用于沉淀 `runninghub_seedance2_tool` 与 `btn_image2_tool` 两个 agent 在接入、联调、上线过程中的高频问题与修复经验，供后续开发与运维复用。

覆盖范围：

1. Tool 编排层（templates 下工具、prompt、skill）
2. 任务中心落库与任务面板展示
3. WebUI `%...` 媒体引用转接到 `au vendor` 命令
4. 按业务线分组的 key routing
5. dev/prod 环境发布与运行时一致性

---

## 2. 核心结论（先看）

1. **模板文件更新不等于运行时生效**：Open-WebUI 运行时用的是数据库 `tool.content`，不是 `templates/*.py` 文件本身。  
2. **`%引用` 不能直接传给 `au vendor`**：必须先做 WebUI 资产解析与转接。  
3. **任务面板可见性依赖统一落库字段**：`prompt_text`、`generation_params`、`prompt_resources`、`artifact_kind` 等缺一会影响展示。  
4. **提交阶段不要回传误导字段**：RunningHub 提交成功时 `video_url` 必须固定 `暂无`，仅查询成功终态才展示真实视频地址。  
5. **生产环境路径必须以 `DATA_DIR` 为准**：否则会写到 `.data-dev`，导致“任务已提交但面板看不到/资源错目录”。  
6. **必须禁止杜撰 task_id**：`task_id` 只能来自工具真实返回。

---

## 3. 踩坑记录

## 3.1 `KEY_ROUTING_NO_GROUP`

现象：

1. 任务提交失败，报 `KEY_ROUTING_NO_GROUP`。  
2. 日志提示 `No key routing group matched for provider=runninghub`。

根因：

1. `config/key_routing.json` 的 `bindings.group_id` 与 `webui.db` 中真实 group UUID 不一致。  
2. 常见错误是使用了逻辑名（例如 `grp_seedance_k1`）而非数据库真实 group id。

修复：

1. 用 `webui.db` 的 `group` 与 `group_member` 表确认真实 group id。  
2. 将 `seedance/runninghub/btn_image2` 三个 provider 的 alias 绑定到真实 group UUID。  
3. 重新验证每个用户是否能命中唯一 alias。

防回归：

1. 发布前做一次“binding 命中率”检查。  
2. 新增业务线分组后，同步更新 key routing，不要只改 env。

---

## 3.2 `%引用` 直接传命令导致本地路径不存在

现象：

1. `au vendor ...` 报 `local image not found: %image_001.png`。  
2. 工具执行失败，但用户在对话中明明写了 `%...`。

根因：

1. `%...` 是 WebUI 媒体引用 token，不是 CLI 可直接读取的本地路径。  
2. 工具层缺少 token -> 可访问 URL/本地输入 的桥接。

修复：

1. 在 `templates/shared/toolkit.py` 建立统一桥接逻辑（`AUMediaReferenceBridge`）。  
2. 先解析 `%...` 对应媒体资产，再生成命令参数（`--image/--video/--audio`）。

防回归：

1. 所有新 `au vendor` 工具复用同一桥接层，避免重复造轮子。  
2. 测试覆盖：`prompt` 含 `%...`、`image_refs` 含 `%...`、多媒体混合引用。

---

## 3.3 任务面板中引用信息丢失

现象：

1. 任务弹窗里提示词有 `%...`，但参数区引用丢失，或反过来。  
2. 同类任务展示不一致。

根因：

1. `prompt_text`、`prompt_resources`、`generation_params` 没有同时写入或写法不一致。  
2. 引用类型未区分（图片/视频/音频传错槽位）。

修复：

1. 提交时统一写入：  
   - `prompt_text`（保留 `%...` 原文）  
   - `prompt_resources`（可展示资源）  
   - `generation_params`（含输入引用，且按 image/video/audio 分类）  
2. 禁止在工具层“静默改写提示词”。

防回归：

1. 增加断言：同一次提交中，`prompt` 与参数引用要一致可追溯。  
2. UI 验收时同时检查“提示词区”和“参数区”。

---

## 3.4 RunningHub 提交阶段误回 `video_url`

现象：

1. 任务状态是 `QUEUED`，但返回了一个可访问 URL。  
2. 该 URL 实际是参考素材/TOS 资源，不是生成结果。

根因：

1. 提交阶段误从响应里抽取任意 URL，当成 `video_url`。  
2. 结果地址与素材地址未做语义区分。

修复：

1. RunningHub 提交阶段固定：`video_url=暂无`。  
2. 仅在 `get_generation_task_status / wait_generation_task` 且成功终态时展示真实视频地址。  
3. 该约束放在 skill/prompt 层，不靠临时代码兜底。

防回归：

1. 增加规则：`QUEUED/RUNNING/PENDING` 禁止展示 `video_url`。  
2. 查询阶段再做视频 URL 合法性判断。

---

## 3.5 BTN `response_format` 参数不兼容上游

现象：

1. `btn-image2-gen` 报：`Unknown parameter: 'response_format'`。  

根因：

1. 工具默认透传了 `--response-format`，但上游 Tapque 不接受该参数。

修复：

1. `btn_image2_tool` 删除 `--response-format` 透传。  
2. 任务 `generation_params` 不再写 `response_format`。  
3. 同步更新 skill/prompt/测试断言。

防回归：

1. 对外部 vendor 参数保持最小集合，避免“想当然”透传。  
2. 每次 vendor 升级做一次参数白名单复核。

---

## 3.6 prod 写入 `.data-dev` 导致任务面板异常

现象：

1. prod 提交的任务产物写到 `.data-dev`。  
2. 任务面板读的是 `.data-prod`，出现“任务看不到/结果错位”。

根因（双重）：

1. 路径选择逻辑默认优先 `.data-dev`。  
2. 更关键：运行时实际执行的是 DB 中旧版工具内容，未使用新模板代码。

修复：

1. 工具路径解析改为优先 `DATA_DIR`，其次 `CACHE_DIR`，再 fallback。  
2. 同步修正 gpt-image2 本地任务默认目录逻辑（同样优先 `DATA_DIR`）。  
3. 将新工具内容更新到 `webui.db` 的 `tool.content`。  
4. 必要时重启服务并验证新任务实际落盘路径。

防回归：

1. 发布后必须验证“数据库 tool 内容”是否与模板一致。  
2. 不要只看 git 文件是否已改。

---

## 3.7 Agent 杜撰 task_id

现象：

1. 对话里出现了 `task_id`，但任务面板与落盘都查不到。  

根因：

1. 模型根据命名习惯拼接了“像真的”任务号，而非工具真实回包。

修复：

1. 在 BTN 与 RunningHub 的 prompt/skill 中新增硬约束：  
   - `task_id` 只能取 `tool.response.task_id/response_id`。  
   - 未返回则必须显示 `task_id=暂无`，并声明“未创建任务”。  
   - 禁止输出臆造任务号。

防回归：

1. 把“task_id 真实性”纳入验收用例。  
2. 线上抽检对话日志与任务落盘的一致性。

---

## 4. 两个 Agent 的统一工程实践

1. **统一桥接层**：媒体引用转接统一放 `templates/shared/toolkit.py`。  
2. **统一任务字段契约**：提交即写 `prompt_text/generation_params/prompt_resources`。  
3. **统一发布顺序**：`core` 改 -> 同步 `dev/prod` -> 更新 DB tool content -> 验证。  
4. **统一路径策略**：所有本地产物路径优先 `DATA_DIR`。  
5. **统一失败反馈**：错误字段回传要结构化且原样，避免“润色丢信息”。

---

## 5. 发布与回归清单（建议每次都跑）

1. 配置层：
   - `key_routing.json` 的 `bindings.group_id` 与 DB `group.id` 一致  
   - `ark.env` 中 provider 对应 env 已配置
2. 工具层：
   - `templates/*_tool.py` 与 `*_import.json` 同步  
   - `webui.db.tool.content` 与当前模板一致
3. 路径层：
   - 运行进程 `DATA_DIR` 指向正确目录（prod=`.data-prod`）  
   - 新任务 `generation_params` 不得出现 `.data-dev`（prod）
4. 任务层：
   - 提交后 `tasks/*.json` 可查  
   - 任务面板可见，弹窗包含 prompt 与参数引用
5. 对话层：
   - `task_id` 与落盘一致  
   - 提交阶段无误导性 `video_url`

---

## 6. 结语

这两个 agent 的主要风险并不在“命令是否可调用”，而在“上下游契约一致性”：  
`引用转接`、`任务落库字段`、`路径环境`、`DB 运行时版本`、`对话返回真实性`。  

后续新接入任何 `au vendor` 能力时，建议直接按本文清单走一次，能规避大部分重复问题。

