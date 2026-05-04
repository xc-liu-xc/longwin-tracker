"""E大长赢策略框架 — 从历史文章中提取的决策规则引擎.

This module encodes ETF拯救世界 (E大)'s LongWin investment strategy as a
declarative rule set extracted from 350+ historical 且慢/微信 articles.

Used for:
  1. Tagging historical trades with which rule(s) triggered them.
  2. Predicting next likely moves given current portfolio state.

Source articles (key references):
  - 74371 (2026-04): A股指数基金的基本逻辑 — 宽基本质是周期性
  - 73435 (2026-04): 红利估值与持续买入逻辑
  - 73567 (2026-04): 永远不要买贵了 — 创业板10年不涨案例
  - 74233 (2026-04): 长赢小组投资笔记 — 当前操作思路
  - 62497 (2026-01): 坚持纪律 — "野生AI" 机械执行
  - 61927 (2026-01): 波段提款 — 不再受外界声音影响
  - 58201 (2025-12): 红利100加仓位置 = 现价下跌3%
  - 59479 (2025-12): 中证500大幅减仓位置
  - 21493 (2026-01-07): 中证500 60% 收益率减仓 + 轮动到消费
  - 21547 (2026-01-09): 压力位减仓节奏
  - 19297 (2025-08-13): 策略机械触发，不看多空
  - 2904  (2023-02-01): 大盘连涨3月 → 再平衡减仓
  - WX-2021-09-27: 左侧买卖完整阐述
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal, Tuple

# ============================================================
# 资产类别定义 (Asset Categories)
# ============================================================

CATEGORIES = {
    "broad_index": {
        "label": "宽基指数",
        "examples": ["沪深300", "中证500", "上证50", "中证800", "中证1000", "创业板", "A500"],
        "approach": "低买高卖波段，不长期持有",
        "rationale": (
            "A股新股大量上市，宽基本质是'周期性的癫狂和绝望'。"
            "护城河巨头占比少，靠周期波动赚钱。低买高卖一定不会错。"
        ),
        "fund_name_keywords": ["300", "500", "50ETF", "180ETF", "全指", "指数增强", "A500", "1000", "创业板"],
    },
    "dividend": {
        "label": "红利/低波",
        "examples": ["中证红利", "红利低波100", "300红利"],
        "approach": "长期持有为主，下跌加仓，少量轮换",
        "rationale": (
            "A股市场特殊，红利能稳定分红，跌了买更多反而拿到更多分红。"
            "2017-2018第一波建仓，目前开启第二轮。S计划改为每月持续买入。"
        ),
        "fund_name_keywords": ["红利"],
    },
    "sector": {
        "label": "行业指数",
        "examples": ["医药", "消费", "传媒", "环保", "证券", "金融地产", "军工", "新能源"],
        "approach": "波段为主，估值低位建仓",
        "rationale": (
            "行业指数有交易价值无投资价值，靠周期性波动。"
            "大幅下跌后开始波段，做低成本，等估值修复或下一轮风口。"
        ),
        "fund_name_keywords": ["医药", "医疗", "健康", "消费", "传媒", "环保", "证券", "金融", "地产", "军工", "新能源"],
    },
    "bond": {
        "label": "债券/固收",
        "examples": ["国开债", "信用债", "美元债", "纯债"],
        "approach": "防守仓位，再平衡工具",
        "rationale": "提款计划留3年现金类资产应对熊市；用于股债再平衡。",
        "fund_name_keywords": ["国开债", "信用债", "纯债", "美元债", "短债", "1-3年", "7-10年"],
    },
    "overseas": {
        "label": "海外/QDII",
        "examples": ["恒生", "标普500", "纳指", "中概互联", "海外医疗", "全球油气"],
        "approach": "分散配置，部分波段",
        "rationale": "海外配置降低单一市场风险，叠加汇率维度。",
        "fund_name_keywords": ["恒生", "标普", "海外", "美元", "全球", "QDII", "中概", "纳斯达克", "原油", "油气", "黄金"],
    },
    "convertible": {
        "label": "可转债/混合",
        "examples": ["可转债"],
        "approach": "弱相关防御",
        "rationale": "可转债有股性也有债性，是相对独立的资产。",
        "fund_name_keywords": ["可转债"],
    },
}


def categorize_fund(fund_name: str) -> str:
    """根据基金名匹配资产类别.

    优先级：可转债 > 红利 > 债券 > 海外宽基(标普/纳指) > 海外其他 > 行业 > 宽基.
    注意：标普500/纳指类按"海外宽基"处理，享受宽基的60%减仓规则.
    """
    if not fund_name:
        return "unknown"
    if "可转债" in fund_name:
        return "convertible"
    for kw in CATEGORIES["dividend"]["fund_name_keywords"]:
        if kw in fund_name:
            return "dividend"
    for kw in CATEGORIES["bond"]["fund_name_keywords"]:
        if kw in fund_name:
            return "bond"
    # 海外宽基 → 当作 broad_index 处理 (标普500/纳指等)
    if any(kw in fund_name for kw in ["标普500", "纳指", "纳斯达克"]):
        return "broad_index"
    for kw in CATEGORIES["overseas"]["fund_name_keywords"]:
        if kw in fund_name:
            return "overseas"
    for kw in CATEGORIES["sector"]["fund_name_keywords"]:
        if kw in fund_name:
            return "sector"
    for kw in CATEGORIES["broad_index"]["fund_name_keywords"]:
        if kw in fund_name:
            return "broad_index"
    return "unknown"


# ============================================================
# 决策规则 (Decision Rules)
# ============================================================

@dataclass
class Rule:
    """一条决策规则."""
    id: str
    name: str
    action: Literal["buy", "sell", "hold"]
    category: str  # 适用类别 ("any" or one of CATEGORIES)
    description: str
    source_article: str
    # Programmatic predicate: returns True if rule fires.
    # Inputs: a Context dataclass below.


@dataclass
class TradeContext:
    """评估一笔交易/状态时的上下文."""
    fund_name: str
    category: str
    return_pct: Optional[float] = None       # 当前收益率（vs avg buy cost）
    hold_days: Optional[int] = None
    pos_units: int = 0                       # 当前持仓份数
    days_since_last_buy: Optional[int] = None
    days_since_last_sell: Optional[int] = None
    market_recent_rally_months: Optional[int] = None  # 大盘连涨月数
    drawdown_from_peak_pct: Optional[float] = None    # 距期间高点的回撤
    # Article context (for tagging past trades):
    article_text: str = ""
    sell_streak: int = 0  # 同品种连续卖出次数（含本次）


# 卖出规则 (SELL rules) - 优先级从高到低
SELL_RULES = [
    Rule(
        id="SELL_RETURN_60",
        name="收益率≥60% 加大减仓",
        action="sell",
        category="broad_index",
        description="宽基平均收益率达到 60%+，进入持续减仓阶段，每周/每月稳定卖出。",
        source_article="21493 (2026-01-07): 把平均收益率60%以上的中证500分一些给别人",
    ),
    Rule(
        id="SELL_RETURN_30",
        name="收益率≥30% 启动减仓",
        action="sell",
        category="broad_index",
        description="宽基收益率超过 30% 开始考虑首次减仓 1 份，标志性减仓节点。",
        source_article="历史交易模式 (2021-09 中证500首卖 +37.4%, 2025-08 +30.4%)",
    ),
    Rule(
        id="SELL_RESISTANCE",
        name="技术压力位减仓",
        action="sell",
        category="any",
        description="碰到小压力位继续缓慢减持，到大压力位进行更大笔减持。",
        source_article="21547 (2026-01-09): 500碰到小压力位，继续缓慢减持",
    ),
    Rule(
        id="SELL_REBALANCE_RALLY",
        name="大盘连涨再平衡",
        action="sell",
        category="any",
        description="大盘连涨 3 个月以上，持仓仓位超出合理区间，动态再平衡减仓。",
        source_article="2904 (2023-02-01): 大盘连涨3个月，仓位超合理区间",
    ),
    Rule(
        id="SELL_VALUATION_PEAK",
        name="估值历史高位",
        action="sell",
        category="any",
        description="估值进入历史高位区间（如军工100倍向2007/2015高点挺进），减仓倒计时。",
        source_article="60643 (2026-01-08): 中证军工100倍，减仓倒计时",
    ),
    Rule(
        id="SELL_GRID_PROFIT",
        name="网格波段获利",
        action="sell",
        category="sector",
        description="波动大的不死品种，跌X%买，涨X%卖，每格留利润。",
        source_article="66685 (2026-02-07): 网格波段适合波动巨大的不死品种",
    ),
    Rule(
        id="SELL_MECHANICAL",
        name="机械执行（兜底标签）",
        action="sell",
        category="any",
        description="正常调仓，策略触发卖出机制，不代表看多看空，机械执行。",
        source_article="19297 (2025-08-13): 交易策略触发卖出机制，机械执行",
    ),
]

# 买入规则 (BUY rules)
BUY_RULES = [
    Rule(
        id="BUY_DIVIDEND_MONTHLY",
        name="红利每月持续买入",
        action="buy",
        category="dividend",
        description="S计划红利每月至少买1份，承受短期下跌、坚持纪律持续投入。",
        source_article="73435 (2026-04-15): 估值不低也要继续买入红利",
    ),
    Rule(
        id="BUY_DIVIDEND_DROP_3",
        name="红利下跌3% 加仓",
        action="buy",
        category="dividend",
        description="预设加仓位置在当前价位下跌 3% 左右。",
        source_article="58201 (2025-12-17): 红利100下一次加仓位置约下跌3%",
    ),
    Rule(
        id="BUY_LEFT_SIDE_GRID",
        name="左侧分批买入（空间维度）",
        action="buy",
        category="any",
        description="下跌 5%/7%/10% 加仓，避免筹码聚集在下跌高位。空间策略。",
        source_article="WX-2021-09-27: 我的左侧买入节奏",
    ),
    Rule(
        id="BUY_LEFT_SIDE_TIME",
        name="左侧分批买入（时间维度）",
        action="buy",
        category="any",
        description="按时间节奏定期买入，不一定是定投，但保持时间分散。",
        source_article="WX-2021-09-27: 时间空间至少占其一",
    ),
    Rule(
        id="BUY_VALUATION_LOW",
        name="估值历史低位建仓",
        action="buy",
        category="sector",
        description="行业指数跌至估值历史低位（如医药 PB 2008-10 以来最低），开始建仓。",
        source_article="71383 (2026-03-23): 全指医药估值历史第二低",
    ),
    Rule(
        id="BUY_ROTATION",
        name="轮动 — 卖热买冷",
        action="buy",
        category="any",
        description="把高收益的卖一些，把跌幅大的辣鸡品种买一些回来，做'好人'。",
        source_article="21493 (2026-01-07): 把500分一些给别人，把辣鸡消费买回来",
    ),
    Rule(
        id="BUY_AFTER_BIG_DROP",
        name="腰斩后波段建仓",
        action="buy",
        category="sector",
        description="基金跌幅 50%+ 后开始波段建仓，分批降低成本。",
        source_article="74233 (2026-04-25): 医疗基金跌60%多，腰斩开始买，做波段",
    ),
]

# 持有规则
HOLD_RULES = [
    Rule(
        id="HOLD_DIVIDEND_LONG",
        name="红利长期持有",
        action="hold",
        category="dividend",
        description="第一轮红利15年只卖2份，剩余可能一直持有。",
        source_article="74371 (2026-04-22): 第一波红利建仓，长期持有",
    ),
    Rule(
        id="HOLD_BOND_DEFENSE",
        name="债券防守仓位",
        action="hold",
        category="bond",
        description="始终保留 3 年提款额的现金类资产，确保熊市不被动卖股。",
        source_article="61459 (2026-01-10): 提款计算器Q&A",
    ),
]


# ============================================================
# 规则匹配引擎 (Rule Engine)
# ============================================================

def evaluate_sell(ctx: TradeContext) -> List[Tuple[Rule, float]]:
    """
    给定上下文，返回触发的卖出规则列表 [(rule, confidence)].
    Confidence 0-1, 越高表示规则匹配度越高.

    设计原则：
      - 收益率阈值规则只对 broad_index 生效（红利长期持有不卖，债券防守不卖）
      - 兜底 SELL_MECHANICAL 只对历史已发生卖出做标签时使用
    """
    matches = []
    cat = ctx.category
    r = ctx.return_pct or 0

    # SELL_RETURN_60 (强信号) - 宽基60%以上加大减仓
    if cat == "broad_index" and r >= 55:
        conf = 0.95 if r >= 60 else 0.75
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_RETURN_60"), conf))

    # SELL_RETURN_30 (中信号)
    if cat == "broad_index" and 25 <= r < 55:
        conf = 0.8 if r >= 30 else 0.55
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_RETURN_30"), conf))

    # SELL_REBALANCE_RALLY - 大盘连涨 3+ 月
    if ctx.market_recent_rally_months and ctx.market_recent_rally_months >= 3 and r > 5 and cat == "broad_index":
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_REBALANCE_RALLY"), 0.7))

    # SELL_RESISTANCE - 接近期间高点 + 正收益
    if r > 15 and ctx.drawdown_from_peak_pct is not None and ctx.drawdown_from_peak_pct > -3:
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_RESISTANCE"), 0.6))

    # SELL_GRID_PROFIT (sector + 适中收益)
    if cat == "sector" and 8 <= r <= 25:
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_GRID_PROFIT"), 0.55))

    return matches


def evaluate_sell_for_tagging(ctx: TradeContext) -> List[Tuple[Rule, float]]:
    """对历史已发生的卖出做标签时使用，包含兜底标签."""
    matches = evaluate_sell(ctx)
    if not matches:
        # 历史已发生卖出但无明确触发规则 → 机械执行
        matches.append((next(x for x in SELL_RULES if x.id == "SELL_MECHANICAL"), 0.3))
    return matches


def evaluate_buy(ctx: TradeContext) -> List[Tuple[Rule, float]]:
    """评估买入信号."""
    matches = []
    cat = ctx.category
    r = ctx.return_pct or 0
    pos = ctx.pos_units

    # BUY_DIVIDEND_MONTHLY (红利每月)
    if cat == "dividend":
        if ctx.days_since_last_buy is None or ctx.days_since_last_buy >= 25:
            matches.append((next(x for x in BUY_RULES if x.id == "BUY_DIVIDEND_MONTHLY"), 0.7))
        # 加仓信号 - 下跌
        if r < -3:
            matches.append((next(x for x in BUY_RULES if x.id == "BUY_DIVIDEND_DROP_3"), 0.85))

    # BUY_AFTER_BIG_DROP (sector 大跌)
    if cat == "sector" and r < -30:
        matches.append((next(x for x in BUY_RULES if x.id == "BUY_AFTER_BIG_DROP"), 0.8))

    # BUY_LEFT_SIDE_GRID (任意下跌)
    if r < -5:
        matches.append((next(x for x in BUY_RULES if x.id == "BUY_LEFT_SIDE_GRID"), 0.5))

    # BUY_VALUATION_LOW (sector + 大跌)
    if cat == "sector" and r < -20 and pos < 5:
        matches.append((next(x for x in BUY_RULES if x.id == "BUY_VALUATION_LOW"), 0.7))

    return matches


def predict_next_action(ctx: TradeContext) -> Dict:
    """对当前持仓状态做下一步预测，并给出下次行动的触发条件."""
    sells = evaluate_sell(ctx)
    buys = evaluate_buy(ctx)

    all_signals = []
    for rule, conf in sells:
        all_signals.append({
            "action": "sell",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "confidence": conf,
            "description": rule.description,
            "source": rule.source_article,
        })
    for rule, conf in buys:
        all_signals.append({
            "action": "buy",
            "rule_id": rule.id,
            "rule_name": rule.name,
            "confidence": conf,
            "description": rule.description,
            "source": rule.source_article,
        })

    all_signals.sort(key=lambda s: -s["confidence"])

    if not all_signals:
        primary = {"action": "hold", "rule_id": "HOLD",
                   "rule_name": "继续持有/观望", "confidence": 0.5,
                   "description": "无明显触发信号，按节奏等待。",
                   "source": ""}
    else:
        primary = all_signals[0]

    # 计算下次触发的具体条件（净值阈值/时间）
    threshold = compute_next_threshold(ctx)

    return {
        "primary_signal": primary,
        "all_signals":    all_signals[:5],
        "next_threshold": threshold,
    }


def compute_next_threshold(ctx: TradeContext) -> Dict:
    """估算下次行动的触发条件 — 给出具体的净值/时间数值，便于追踪."""
    cat = ctx.category
    r = ctx.return_pct or 0

    if cat == "broad_index":
        # 距下一个减仓阈值还差多少
        if r < 25:
            need = 30 - r
            return {"type": "return_threshold", "next_action": "sell",
                    "description": f"需再涨约 {need:.1f}% (净值升幅) → 触发首次减仓 30% 阈值"}
        if r < 55:
            need = 60 - r
            return {"type": "return_threshold", "next_action": "sell",
                    "description": f"需再涨约 {need:.1f}% → 进入 60% 加大减仓阶段"}
        return {"type": "rolling_sell", "next_action": "sell",
                "description": "已在 60% 减仓阶段，按压力位/月度节奏持续卖出"}

    if cat == "dividend":
        if r < -3:
            return {"type": "drop_buy", "next_action": "buy",
                    "description": "已触发下跌 3% 加仓位置，下一次买入信号成立"}
        if r < 0:
            need = abs(r) + 3
            return {"type": "drop_buy", "next_action": "buy",
                    "description": f"再跌约 {need:.1f}% 触发加仓；月度节奏到也会买"}
        days = ctx.days_since_last_buy
        if days is not None:
            wait = max(0, 30 - days)
            return {"type": "monthly_buy", "next_action": "buy",
                    "description": f"距下次月度买入约 {wait} 天（红利每月至少买 1 份）"}
        return {"type": "monthly_buy", "next_action": "buy",
                "description": "红利每月持续买入"}

    if cat == "sector":
        if r < -30:
            return {"type": "grid_buy", "next_action": "buy",
                    "description": "已腰斩，分批波段建仓中。再跌 5-10% 加大力度"}
        if r < -10:
            return {"type": "grid_buy", "next_action": "buy",
                    "description": "处于建仓波段，每 5-7% 跌幅加一份"}
        if r > 15:
            return {"type": "grid_sell", "next_action": "sell",
                    "description": "波段获利出局，留利润给下一格"}
        return {"type": "wait", "next_action": "hold",
                "description": "等待估值进一步偏离均值"}

    if cat == "bond":
        return {"type": "defensive", "next_action": "hold",
                "description": "防守仓位，维持 3 年提款额"}

    if cat == "overseas":
        return {"type": "watch", "next_action": "hold",
                "description": "海外配置以分散为主，按计划微调"}

    return {"type": "unknown", "next_action": "hold", "description": ""}


# ============================================================
# 框架元数据 (用于前端展示)
# ============================================================

PHILOSOPHY = [
    {"id": "low_buy_high_sell", "title": "低买高卖",
     "desc": "宽基的本质是周期性的癫狂和绝望，靠波动赚钱。"},
    {"id": "left_side", "title": "左侧交易",
     "desc": "越涨越卖、越跌越买。不猜顶底，分批进出。"},
    {"id": "time_space", "title": "时间+空间维度",
     "desc": "下跌幅度触发（空间）或月度节奏触发（时间），至少占其一。"},
    {"id": "discipline", "title": "机械执行",
     "desc": "做'野生AI'，纪律高于判断，无视外界声音。"},
    {"id": "diversify", "title": "风格分散",
     "desc": "价值/成长/红利都要有，无论哪个风格涨都赚钱。"},
    {"id": "rotation", "title": "做'好人'轮动",
     "desc": "卖热门给抢的人，买冷门为他们分担。卖热买冷。"},
    {"id": "rebalance", "title": "动态再平衡",
     "desc": "仓位超合理区间则减，市场连涨3月+则减。"},
    {"id": "grid_profit", "title": "网格留利润",
     "desc": "波动大的不死品种适合网格，每格留利润给下一轮。"},
]


def framework_summary() -> Dict:
    """导出完整框架元数据，供前端展示."""
    return {
        "philosophy": PHILOSOPHY,
        "categories": {k: {**v, "fund_name_keywords": v.get("fund_name_keywords", [])}
                      for k, v in CATEGORIES.items()},
        "sell_rules": [{"id": r.id, "name": r.name, "category": r.category,
                       "description": r.description, "source": r.source_article}
                      for r in SELL_RULES],
        "buy_rules": [{"id": r.id, "name": r.name, "category": r.category,
                      "description": r.description, "source": r.source_article}
                     for r in BUY_RULES],
        "hold_rules": [{"id": r.id, "name": r.name, "category": r.category,
                       "description": r.description, "source": r.source_article}
                      for r in HOLD_RULES],
    }
