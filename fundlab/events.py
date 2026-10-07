"""Announcement title -> event type.

Rules are ordered; the first match wins. Each rule is (event, channel, regex
on the title, optional regex on eastmoney's 公告类型). ``channel`` is the v3
screening channel the event feeds (A 并购重组/控制权, B 困境反转, C 错杀,
D 资产重估, E 内部人, V 一票否决 / 风险). Titles of intermediary documents
(核查意见, 法律意见书, 保荐书...) are dropped first so one deal is not
counted five times.

The table is plain data on purpose: add keywords here when you see a title
that is misclassified (``python -m fundlab build`` re-applies the rules).
"""
from __future__ import annotations

import re

import pandas as pd

# Documents written *about* an event by an intermediary, or procedural paperwork.
NOISE = re.compile(
    r"核查意见|法律意见|保荐书|保荐总结|财务顾问报告|独立财务顾问|审核报告|鉴证报告|专项核查|摘要|"
    r"英文版|更正|补充更正|取消|网络投票|提示性公告的更正|说明会|问答|投资者关系|自查报告|"
    r"会计师事务所关于|律师事务所关于|证券股份有限公司关于|证券有限责任公司关于"
)

# (event, channel, title regex, type regex or None)
RULES: list[tuple[str, str, str, str | None]] = [
    # ---- veto / risk (V) ------------------------------------------------------
    ("delist_risk", "V", r"终止上市.*风险|退市风险警示|可能被终止上市|可能.*终止上市|实施退市风险|股票被实施.*\*ST|面值退市|市值退市|撤销.*退市", None),
    ("delist_final", "V", r"(?<!债券)(?<!公司债)终止上市(?:暨|的公告|决定)|股票.*摘牌|进入退市整理期|退市整理期交易", None),
    ("audit_cleared", "B", r"(?:非标|保留意见|否定意见|无法表示意见|强调事项).*影响(?:已)?消除", None),
    ("st_removed", "B", r"撤销.*风险警示|摘帽|撤销其他特别处理|撤销退市风险", None),
    # Only the first notice counts as "new ST"; follow-ups (相关事项的进展) are classified separately.
    ("st_progress", "V", r"(?:被)?实施.*风险警示.*进展", None),
    ("st_imposed", "V", r"(?:将被|被)?实施其他风险警示|股票简称变更为.*ST", None),
    ("investigation", "V", r"(?<!报案并收到)立案告知|立案调查|行政处罚事先告知|涉嫌.*违法|被采取.*强制措施|留置", None),
    # §2.2: 非标准意见且涉及持续经营 -> 保留 / 否定 / 无法表示意见 on the financial statements.
    # 带强调事项段的无保留意见 is a softer warning (audit_emphasis).
    ("audit_adverse", "V", r"无法表示意见|(?<!无)保留意见(?!.*内部控制)|否定意见(?!内控)(?!.*内部控制审计)", None),
    ("audit_emphasis", "V", r"强调事项段|非标准(?:审计)?意见|非标意见|解释性说明", None),
    ("audit_change", "V", r"变更会计师事务所|变更.*审计机构|改聘", None),
    # The annual "非经营性资金占用及其他关联资金往来" statement is routine: only real occupation / illegal guarantees.
    ("funds_occupied", "V", r"(?:被|违规|非经营性)占用.*(?:整改|归还|偿还|进展|风险)|违规担保|违规占用|资金占用.*(?:整改|归还|偿还|进展|风险提示)", None),
    ("frozen", "V", r"(?:控股股东|实际控制人|第一大股东).*(?:司法冻结|轮候冻结|被冻结|司法拍卖|被动减持|强制平仓|平仓)", None),
    ("debt_default", "V", r"债务逾期|逾期.*(?:借款|贷款|债务)|未能.*兑付|违约", None),
    # ---- restructuring / control (A) -----------------------------------------
    ("control_terminated", "C", r"终止.*(?:控制权|股份转让|协议转让|表决权委托|要约收购)|(?:控制权|股份转让|协议转让).*(?:终止|解除)", None),
    ("ma_terminated", "C", r"终止.*(?:重大资产重组|重组|发行股份购买|购买资产|收购|筹划)|重组.*终止", None),
    ("ma_approved", "A", r"(?:重组|购买资产|吸收合并).*(?:注册|核准)批复|同意.*注册|获得.*(?:中国证监会|证监会).*(?:同意|批复)|审核委员会.*审议通过|重组委.*通过", None),
    ("ma_closed", "A", r"(?:标的资产|资产).*过户完成|重组.*实施完毕|过户.*完成", None),
    ("ma_draft", "A", r"重组(?:报告书|草案)|发行股份.*购买资产.*(?:报告书|草案)|重大资产(?:购买|出售|置换).*(?:报告书|草案)", None),
    ("ma_plan", "A", r"筹划.*(?:重大资产重组|发行股份购买|重组)|重组预案|购买资产.*预案|吸收合并.*预案|停牌.*重组|重大资产重组.*停牌", None),
    ("ma_progress", "A", r"重组.*进展|重组.*问询|购买资产.*进展|吸收合并.*进展", None),
    ("control_change", "A", r"控制权(?:拟)?(?:发生)?变更|筹划.*控制权|实际控制人(?:拟)?(?:发生)?变更|控股股东(?:、实际控制人)?(?:拟)?(?:发生)?变更(?!名称|登记|工商)|(?<!豁免)要约收购(?:报告书|提示)|表决权委托|表决权放弃", None),
    ("stake_transfer", "A", r"协议转让|股份转让协议|权益变动.*(?:受让|协议)|引入.*战略投资者", None),
    ("asset_sale", "A", r"出售.*(?:子公司|资产|股权)|剥离", None),
    ("bankruptcy", "B", r"重整计划.*(?:批准|执行完毕)|重整投资人|预重整|破产重整|法院.*受理.*重整", None),
    ("debt_restructure", "B", r"债务重组|债务豁免|豁免.*债务|展期", None),
    # ---- financing / insider (E) ---------------------------------------------
    ("placement_major", "E", r"(?:向特定对象|非公开)发行.*(?:控股股东|实际控制人|大股东)(?:认购|全额)|控股股东.*认购", None),
    ("placement", "E", r"(?:向特定对象|非公开)发行(?!.*(?:债券|公司债|可转换|短期融资)).*(?:预案|方案)|定向发行说明书", None),
    ("buyback_cancel", "E", r"回购.*(?:用于注销|注销.*回购)|以集中竞价.*回购.*注销", None),
    ("buyback_plan", "E", r"回购.*(?:方案|预案|报告书)|拟回购", r"回购预案|回购报告书|回购方案修订"),
    ("incentive", "E", r"(?:限制性股票|股票期权|股权)激励计划(?:（草案）|\(草案\)|草案)|员工持股计划(?:（草案）|\(草案\)|草案)", None),
    ("insider_buy", "E", r"增持(?!.*(?:结果|完成|进展|期限届满))|增持计划", r"增持"),
    ("insider_sell", "V", r"减持.*预披露|拟减持|减持计划(?!.*(?:届满|实施完毕|结果|完成|进展))", None),
    # ---- spinoff / revaluation (D) -------------------------------------------
    ("spinoff", "D", r"分拆.*上市|子公司.*(?:首次公开发行|IPO|辅导备案)", None),
    # ---- results (B) ---------------------------------------------------------
    ("impairment", "B", r"计提.*(?:减值|商誉)|资产减值准备|商誉减值", None),
]
_COMPILED = [(e, c, re.compile(t), re.compile(k) if k else None) for e, c, t, k in RULES]
EVENT_CHANNEL = {e: c for e, c, _, _ in RULES}


def classify_title(title: str, kind: str = "") -> str | None:
    t = title or ""
    if NOISE.search(t):
        return None
    for event, _, rx, krx in _COMPILED:
        if rx.search(t) and (krx is None or krx.search(kind or "") or rx.pattern):
            return event
    return None


def classify(df: pd.DataFrame) -> pd.Series:
    """Vectorised over a frame with ``title`` and ``category`` columns."""
    titles = df["title"].fillna("").astype(str)
    out = pd.Series(None, index=df.index, dtype=object)
    noise = titles.str.contains(NOISE)
    left = ~noise
    for event, _, rx, _ in _COMPILED:
        if not left.any():
            break
        hit = left & titles.str.contains(rx)
        out[hit] = event
        left &= ~hit
    return out
