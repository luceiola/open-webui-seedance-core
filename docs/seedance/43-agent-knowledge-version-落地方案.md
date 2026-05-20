# 43-agent-knowledge-version-落地方案

文档日期：2026-05-13  
文档目的：固化当前共识的三个事项，作为后续实施基线。  
当前状态：仅文档落地，不包含代码开发与系统改动。

---

## 1. 共识摘要

本轮确定的三个事项如下：

1. 新 Agent 规划：新增一个用于优化 Prompt 的 Agent，目标模型固定为 Seedance 2.0。
2. 知识库规划：建立可检索、可维护的 Agent 知识库，先启动 `KB-01-规则库`。
3. 版本规划：WebUI 二开与 Agent 能力包分离版本治理，并将版本事实同步到 WebUI Knowledge。

---

## 2. 事项一：新 Agent 规划（Seedance Prompt Optimizer）

### 2.1 目标

为用户输入的原始 Prompt 提供结构化优化建议，输出可直接用于 Seedance 2.0 的结果。

### 2.2 定位与边界

1. 目标模型固定为 `Seedance 2.0`，不允许被运行时参数覆盖。
2. Agent 负责 Prompt 优化，不负责代替用户完成素材资产管理。
3. Agent 输出应兼顾机器可读和人可读。

### 2.3 已确认的设计决策

1. 集成方式：`Skill + Prompt + 专用 Tool`。
2. 输出风格：`结构化改写 + 解释说明`。
3. 输入范围：包含素材引用信息与分镜脚本上下文。
4. 输出契约：`JSON + 可读摘要`。
5. 优先级：稳定性/可控性优先。
6. 语言策略：跟随用户输入语言。

### 2.4 输出契约（v1 草案）

```json
{
  "model": "seedance-2.0",
  "optimized_prompt": "...",
  "negative_prompt": "...",
  "reasoning": [
    "..."
  ],
  "risk_checks": [
    "..."
  ],
  "kb_trace": {
    "kb_version": "...",
    "source_refs": [
      "..."
    ]
  }
}
```

备注：`kb_trace` 用于后续接入知识来源可追溯能力。

---

## 3. 事项二：知识库规划（先落 KB-01）

### 3.1 目标

构建可持续维护的 Seedance 规则知识来源，支撑 Agent 稳定输出和低幻觉率。

### 3.2 知识库组织建议

1. `KB-01-规则库`：模型能力边界、约束规则、禁用写法（首批启动）。
2. `KB-02-模板库`：镜头/风格/场景模板与可复用片段。
3. `KB-03-案例库`：优劣 Prompt 对照与失败复盘。
4. `KB-04-FAQ库`：常见报错、定位路径、修复策略。

### 3.3 KB-01 初始化范围（首批）

1. 首批规模：5-10 篇规则文档（先跑通链路）。
2. 文档来源：当前预期为需登录网站。
3. 对无法直接访问的文档，采用替代输入：
   - 导出为 PDF/Markdown 后入库；
   - 粘贴关键正文；
   - 提供截图再做结构化整理。

### 3.4 入库字段规范

每条知识建议包含以下元数据字段：

1. `model`: `seedance-2.0`
2. `source`: 文档来源标识
3. `source_url`: 原始链接（可空）
4. `version`: 规则版本号
5. `updated_at`: 更新时间（ISO8601）
6. `lang`: `zh` / `en` / `multi`
7. `reliability`: `official` / `internal-reviewed` / `draft`
8. `tags`: 主题标签数组

### 3.5 与当前系统能力的对应关系

当前仓库已具备 Knowledge 与 Retrieval 路由及文件入库/重建索引能力，可直接承接 KB 资料管理：

1. `POST /api/v1/knowledge/create`
2. `POST /api/v1/knowledge/{id}/file/add`
3. `POST /api/v1/knowledge/reindex`
4. `POST /api/v1/knowledge/metadata/reindex`

本阶段仅记录方案，不执行实际入库操作。

---

## 4. 事项三：版本治理规划（WebUI 与 Agent 解耦）

### 4.1 核心原则

1. 采用双版本制：WebUI 与 Agent 各自独立版本线。
2. Agent 以能力包为发布单位（非全量打包）。
3. 通过兼容矩阵建立两者关系，而非强绑定同号发布。

### 4.2 版本模型

1. WebUI 版本：沿用现有项目版本节奏（如 `v1.1.x`）。
2. Agent 版本：能力包独立 SemVer（如 `seedance-prompt-optimizer@1.0.0`）。
3. 兼容表达：例如 `webui >=1.1.2 <1.2.0`。

### 4.3 版本知识入 WebUI Knowledge（已确认）

版本事实也作为知识库管理，采用：

1. `版本卡片`：每个版本一条结构化文档。
2. `兼容矩阵`：WebUI 与 Agent 版本映射总表。
3. 更新策略：发布前强制更新（门禁项）。

建议最小字段：

1. `product`: `webui` / `agent`
2. `component_id`
3. `version`
4. `status`
5. `compatible_with`
6. `release_date`
7. `breaking_changes`
8. `rollback_to`
9. `source_release_id`

---

## 5. 执行顺序（后续实施阶段）

后续进入实施时，建议顺序如下：

1. M1：落地 `seedance-prompt-optimizer` 能力包骨架与输出契约。
2. M2：初始化 `KB-01-规则库`（5-10 篇），验证检索可用性。
3. M3：补齐双版本治理清单与版本知识库（卡片+矩阵）门禁。

---

## 6. 本文档不包含的内容

1. 不包含任何后端/前端代码变更。
2. 不包含数据库迁移与发布脚本改造。
3. 不包含实际知识入库执行记录。

---

## 7. 后续协作输入清单

为进入实施阶段，需要提供以下输入：

1. `KB-01` 首批文档地址与访问方式（账号/导出件）。
2. 首个 Agent 能力包命名（建议：`seedance-prompt-optimizer`）。
3. 版本卡片模板中的发布字段来源（发布流水号规则）。

