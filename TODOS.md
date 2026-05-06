# TODOS

## Deferred from 2026-05-05 CEO Review (Health Check feature)

### TODO-1: AI 体检解读（LLM commentary）

**What:** 在体检报告顶部添加 LLM 生成的 300-500 字自然语言解读，结合穿透、分红、配置、集中度数据生成个性化评语。

**Why:** 数据可视化是理性的，自然语言解读是感性的。两者结合才是"体检报告"而不是"数据仪表板"。与 E大自己正在推的"AI+组合"方向呼应。

**Pros:**
- 让冷冰冰的数据变成有叙事的报告
- 显著提高分享时的吸引力（雪球用户倾向分享"结论"而不是"表格"）
- 利用 Claude/DeepSeek/Qwen API，技术门槛低

**Cons:**
- 法律风险：LLM 说错话可能被当成 E大名义的投资建议
- 需要严谨的提示词设计 + 免责声明
- 需要确定 LLM 服务商和成本（每日调用 × 成本）

**Context:** 已在 SCOPE EXPANSION 对话中评估。当前先完成数据层 + 基础可视化，V2 再加此功能。实施前需：(1) 法律免责文案审查；(2) 系统提示词设计评审；(3) 输出示例人工审核至少 10 条。

**Effort estimate:** M (human ~2 days / CC ~30 min)
**Priority:** P2
**Depends on:** health_check.json 数据层稳定运行至少 2 周

---

### TODO-2: 体检报告分享卡（Canvas 图片生成）

**What:** 一键生成 1080×1920 分辨率的体检结果分享卡图片，包含持仓概览、胜率、收益、E大配置符合度等关键指标。带水印和项目链接。

**Why:** design doc 明确的验证阶段核心任务是"把 GitHub Pages 分享给 10-20 个真实用户"。图片比链接在微信群/雪球传播效率高 10 倍。

**Pros:**
- 直接支持 design doc 的需求验证任务
- 图片天然具备病毒传播潜力（截图文化）
- 隐私模式（隐藏金额只显示比例）降低分享阻力

**Cons:**
- Canvas 中文排版需要细心调试（字体、换行、对齐）
- 1080×1920 分辨率在不同设备测试成本
- 需要决定分享卡的"品牌美学"

**Context:** 验证阶段的头号交互功能。先完成体检数据层和可视化，等用户开始主动要求分享时再加，避免过早优化。

**Effort estimate:** M (human ~2 days / CC ~30 min)
**Priority:** P2
**Depends on:** 体检报告核心功能上线 + 至少 1 个用户主动问"能分享吗"

---

### TODO-3: 数据源统一到 tushare（用户决定，已同步到主 plan）

**What:** fetch_fund_portfolio.py 和 fetch_stock_basic.py 全部使用 tushare，不引入 AKShare。

**Why:** 用户在 D4 对话中明确要求用 tushare 而不是 AKShare，理由未展开（可能是已有 token 投入 + pipeline 统一性 + 数据质量）。

**Context:** tushare 的 fund_portfolio 接口需要 5000+ 积分才能每分钟 200 次请求。如用户积分不足，则降级为单次调用限流 2req/min，对 61 只基金需约 30 分钟。可接受（周频任务）。如将来积分不足成为瓶颈，再回头考虑 AKShare fallback。

**Priority:** P1（主 plan 执行时必须遵守）

---

## Longer-term (from project_longwin.md)

- 前端读 funds.json：在策略预测 tab 显示 data_quality 警示标签 / category badge / trips 明细
- 解决 unit_only 基金的复权问题（19 只持仓影响）
- 优化策略规则（红利上限、可转债、二级债基特殊处理）
- 当前预测的"下次触发条件"与历史模式回归验证
- 文章 Tab 搜索/筛选（关键词/poCode/时间段）
- 净值图表 modal 改用 adjNav（目前是 unitNav）
- 集成到 MoneyWatch (Next.js)
