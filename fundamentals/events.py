"""Announcement title -> event tag, following the keyword lists in strategy doc section 5 (channel A1).

Rules are checked in order and the first match wins, so put specific patterns
before general ones. Titles are matched after the ``name:`` prefix is removed.
Tags are plain strings; the channels and the odds model refer to them by name.
"""
from __future__ import annotations

import re

import pandas as pd

# (tag, title regex, optional category regex). Order matters.
RULES: list[tuple[str, str, str | None]] = [
    # --- one-vote-veto material (doc 2.2): suspected fraud / bad audit opinion
    ("fraud_risk", r"立案(调查|告知)|行政处罚事先告知|财务造假|证券虚假陈述|涉嫌.{0,6}(信息披露|财务|违法)", None),
    ("audit_bad", r"保留意见|无法表示意见|否定意见|非标准(无保留)?(审计)?意见|带强调事项段", None),
    # --- failure / termination first: "终止" must beat the generic restructure patterns
    ("restructure_terminate",
     r"终止.{0,12}(重大资产重组|重组|筹划|发行股份.{0,6}购买资产|收购|并购|吸收合并|换股)|不再.{0,4}(推进|筹划).{0,10}(重组|收购)", None),
    ("ctrl_change_terminate", r"终止.{0,10}(股份转让|协议转让|控制权|要约)|解除.{0,6}(股份转让|表决权)", None),
    # --- delisting / risk warning
    ("delist_notice", r"终止上市(?!风险提示)|进入退市整理|摘牌", None),
    ("delist_risk_prompt", r"终止上市风险提示|退市风险提示", None),
    ("st_removed", r"^(?!.*申请).*(撤销|摘).{0,16}(风险警示|特别处理)|股票.{0,6}撤销.{0,10}风险警示", None),
    ("st_apply_remove", r"申请撤销.{0,16}(风险警示|特别处理)", None),
    ("st_imposed", r"(被|将被|拟被|实施|继续).{0,6}(实施|被实施)?.{0,8}(退市风险警示|其他风险警示|风险警示)(?!.*撤销)", None),
    # --- bankruptcy / reorganisation
    ("bankruptcy_investor", r"重整投资人|战略投资者.{0,6}重整|重整.{0,8}(招募|投资协议)", None),
    ("bankruptcy_plan", r"重整计划|法院.{0,6}(裁定|批准).{0,6}重整|裁定.{0,8}重整", None),
    ("bankruptcy_pre", r"预重整|申请.{0,8}重整|受理.{0,10}重整|重整", None),
    # --- control change
    # "拟发生变更"是还没发生,不能算完成;所以"控制权"后面必须紧跟 已/发生,中间不能夹"拟"
    ("ctrl_change_done", r"控制权(?:已经?发生|已经?|发生)变更", None),
    ("ctrl_change", r"控制权.{0,6}变更|实际控制人.{0,8}(变更|变动)|表决权(委托|放弃)|要约收购|协议转让.{0,20}控制权|引入.{0,10}(战略投资|控股股东)", None),
    ("ctrl_change", r"实际控制人", r"实际控制人变更"),
    # --- restructure pipeline (most advanced node first)
    ("restructure_done", r"(实施情况报告书|标的资产.{0,6}过户|资产过户.{0,6}完成|交割完成|新增股份.{0,6}上市)", None),
    ("restructure_approved", r"(并购重组委|重组委).{0,8}(审核|审议).{0,6}通过|获得.{0,10}(注册批复|同意.{0,6}注册)|审核通过|同意.{0,10}注册", None),
    ("restructure_accepted", r"(申请文件|申请).{0,10}(获得|获).{0,8}受理|受理.{0,10}申请", None),
    ("restructure_inquiry", r"(重组|草案|预案|交易).{0,16}(问询函|审核问询)|(问询函).{0,10}(回复|重组)", None),
    ("restructure_draft", r"(重组|交易)(报告书|草案)|报告书\(草案\)|报告书（草案）|股东大会.{0,10}(审议通过).{0,10}(重组|发行股份)", None),
    ("restructure_plan", r"(重大资产重组|重组|发行股份.{0,8}购买资产|吸收合并|换股合并).{0,16}预案|筹划.{0,12}(重大资产重组|重组|发行股份|购买资产|吸收合并)", None),
    ("restructure_progress", r"(重大资产重组|发行股份.{0,8}购买资产|吸收合并|重组).{0,10}进展", None),
    ("restructure_misc", r"重大资产重组|发行股份.{0,8}购买资产|重大资产(购买|出售|置换)", None),
    ("asset_sale", r"出售.{0,6}(子公司|资产|股权)|转让.{0,6}(子公司|控股子公司).{0,4}股权", r"收购出售资产/股权"),
    ("debt_forgive", r"债务豁免|豁免.{0,6}债务|债务重组|免除.{0,6}债务", None),
    ("spin_off", r"分拆.{0,10}上市|拟分拆", None),
    # --- holders' behaviour
    ("holder_inc", r"增持", r"增持|持股变动"),
    ("holder_dec", r"减持", r"减持"),
    ("placement", r"向特定对象发行|定向增发|非公开发行", None),
]

_COMPILED = [(tag, re.compile(p), re.compile(c) if c else None) for tag, p, c in RULES]
_PREFIX = re.compile(r"^[^:：]{1,12}[:：]")


def strip_name(title: str) -> str:
    return _PREFIX.sub("", title, count=1)


def classify(title: str, category: str = "") -> str:
    t = strip_name(title)
    for tag, rx, crx in _COMPILED:
        if rx.search(t) and (crx is None or crx.search(category or "") or rx.pattern == crx.pattern):
            return tag
    return ""


def classify_series(titles: pd.Series, categories: pd.Series | None = None) -> pd.Series:
    cats = categories if categories is not None else pd.Series("", index=titles.index)
    cache: dict[tuple[str, str], str] = {}
    out = []
    for t, c in zip(titles.astype(str), cats.astype(str)):
        k = (t, c)
        if k not in cache:
            cache[k] = classify(t, c)
        out.append(cache[k])
    return pd.Series(out, index=titles.index)
