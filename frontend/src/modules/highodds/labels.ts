/** Column labels and formats for the x_* tables exported by the high_odds_v3 strategy. */

export type Fmt = 'pct' | 'pct1' | 'num' | 'num1' | 'int' | 'big' | 'date' | 'bool' | 'text'

export interface Col {
  key: string
  label: string
  fmt?: Fmt
  width?: number
}

export const TABLES: Record<string, { title: string; help: string; cols: Col[]; sort?: string }> = {
  x_journal: {
    title: '决策日志',
    help: '每个决策和原因：进观察池、深研未通过、进弹药池、建仓、加仓、证伪减仓、止盈、熔断……',
    sort: 'date',
    cols: [
      { key: 'date', label: '日期', fmt: 'date', width: 104 },
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'name', label: '名称', width: 96 },
      { key: 'action', label: '动作', width: 150 },
      { key: 'reason', label: '原因', width: 420 },
      { key: 'theme', label: '母题', width: 110 },
      { key: 'channels', label: '通道', width: 64 },
      { key: 'score', label: '评分', fmt: 'int', width: 64 },
      { key: 'odds', label: '赔率', fmt: 'num1', width: 70 },
      { key: 'weight', label: '仓位', fmt: 'pct1', width: 70 },
    ],
  },
  x_trips: {
    title: '逐笔往返',
    help: '一次建仓到清仓算一笔。退出原因取这笔交易期间最后几个决策。',
    sort: 'entry_date',
    cols: [
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'name', label: '名称', width: 96 },
      { key: 'entry_date', label: '建仓', fmt: 'date', width: 104 },
      { key: 'exit_date', label: '清仓', fmt: 'date', width: 104 },
      { key: 'return', label: '收益', fmt: 'pct', width: 80 },
      { key: 'pnl', label: '盈亏', fmt: 'big', width: 90 },
      { key: 'days', label: '天数', fmt: 'int', width: 64 },
      { key: 'theme', label: '母题', width: 110 },
      { key: 'channels', label: '通道', width: 64 },
      { key: 'score', label: '评分', fmt: 'int', width: 64 },
      { key: 'odds_at_entry', label: '建仓赔率', fmt: 'num1', width: 80 },
      { key: 'adds', label: '加仓', fmt: 'int', width: 56 },
      { key: 'exit_reason', label: '退出原因', width: 320 },
    ],
  },
  x_cards: {
    title: '赔率卡',
    help: '每次深研写下的完整赔率卡（价格均为后复权）。买入价 = min(现价, 买入线)，门槛按买入价判断。',
    sort: 'date',
    cols: [
      { key: 'date', label: '日期', fmt: 'date', width: 104 },
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'name', label: '名称', width: 96 },
      { key: 'theme_name', label: '母题', width: 110 },
      { key: 'channels', label: '通道', width: 60 },
      { key: 'price_h', label: '现价', fmt: 'num', width: 80 },
      { key: 'target_h', label: '基准价值 T', fmt: 'num', width: 90 },
      { key: 'falsify_h', label: '证伪价 D', fmt: 'num', width: 84 },
      { key: 'anchor', label: '下行锚', width: 100 },
      { key: 'buy_line_h', label: '买入线', fmt: 'num', width: 80 },
      { key: 'odds', label: '现价赔率', fmt: 'num1', width: 76 },
      { key: 'odds_entry', label: '买入价赔率', fmt: 'num1', width: 84 },
      { key: 'expected_entry', label: '期望', fmt: 'pct', width: 76 },
      { key: 'downside_entry', label: '下行', fmt: 'pct', width: 70 },
      { key: 'p_up', label: '成功概率', fmt: 'pct', width: 76 },
      { key: 'sens_passes', label: '敏感性', fmt: 'int', width: 64 },
      { key: 'score', label: '评分', fmt: 'int', width: 60 },
      { key: 'n_evidence', label: '证据', fmt: 'int', width: 56 },
      { key: 'veto', label: '否决', width: 60 },
      { key: 'qualified', label: '达标', fmt: 'bool', width: 60 },
      { key: 'checklist_fail_items', label: '检查清单“否”', width: 260 },
      { key: 'method', label: '估值方法', width: 220 },
      { key: 'p_why', label: '概率依据', width: 260 },
      { key: 'notes', label: '备注', width: 260 },
      { key: 'detail', label: '触发信号', width: 360 },
    ],
  },
  x_audits: {
    title: '月度审计',
    help: '附录 F：每月末每只持仓的当前赔率、风险贡献和动作。',
    sort: 'date',
    cols: [
      { key: 'date', label: '日期', fmt: 'date', width: 104 },
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'name', label: '名称', width: 96 },
      { key: 'tier', label: '层级', width: 80 },
      { key: 'weight', label: '仓位', fmt: 'pct1', width: 70 },
      { key: 'price_h', label: '现价', fmt: 'num', width: 80 },
      { key: 'target_h', label: '目标价', fmt: 'num', width: 80 },
      { key: 'falsify_h', label: '证伪价', fmt: 'num', width: 80 },
      { key: 'current_odds', label: '当前赔率', fmt: 'num1', width: 80 },
      { key: 'risk', label: '风险贡献', fmt: 'pct', width: 80 },
      { key: 'catalyst_deadline', label: '催化剂期限', fmt: 'date', width: 104 },
      { key: 'action', label: '动作', width: 100 },
    ],
  },
  x_ammo: {
    title: '期末弹药池',
    help: '回测结束时已算好赔率、只等价格的标的。',
    sort: 'expected_entry',
    cols: [
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'name', label: '名称', width: 96 },
      { key: 'theme_name', label: '母题', width: 110 },
      { key: 'channels', label: '通道', width: 60 },
      { key: 'date', label: '赔率卡日期', fmt: 'date', width: 104 },
      { key: 'price_h', label: '现价', fmt: 'num', width: 80 },
      { key: 'buy_line_h', label: '买入线', fmt: 'num', width: 80 },
      { key: 'target_h', label: '基准价值', fmt: 'num', width: 84 },
      { key: 'falsify_h', label: '证伪价', fmt: 'num', width: 80 },
      { key: 'expected_entry', label: '期望', fmt: 'pct', width: 76 },
      { key: 'score', label: '评分', fmt: 'int', width: 60 },
      { key: 'detail', label: '触发信号', width: 360 },
    ],
  },
  x_signals: {
    title: '通道信号',
    help: '每周扫描看到的所有通道信号（雷达池的来源）。',
    sort: 'date',
    cols: [
      { key: 'date', label: '扫描日', fmt: 'date', width: 104 },
      { key: 'symbol', label: '代码', width: 104 },
      { key: 'channel', label: '通道', width: 60 },
      { key: 'code', label: '信号', width: 140 },
      { key: 'grade', label: '证据级', width: 64 },
      { key: 'detail', label: '说明', width: 480 },
    ],
  },
}

export const STAT_LABELS: Record<string, string> = {
  name: '分组', trades: '笔数', win_rate: '胜率', avg_win: '平均盈利', avg_loss: '平均亏损', payoff: '盈亏比',
  expectancy: '每笔期望', pnl: '盈亏', right_tail: '右尾贡献', days_win: '盈利单持有天数', days_loss: '亏损单持有天数',
}

export function fmt(v: unknown, f: Fmt = 'text'): string {
  if (v === null || v === undefined || v === '') return '–'
  if (f === 'bool') return v ? '是' : '否'
  if (f === 'date') return String(v).slice(0, 10)
  if (f === 'text') return String(v)
  const n = Number(v)
  if (!Number.isFinite(n)) return '–'
  switch (f) {
    case 'pct': return `${(n * 100).toFixed(1)}%`
    case 'pct1': return `${(n * 100).toFixed(1)}%`
    case 'num': return n.toFixed(2)
    case 'num1': return n.toFixed(1)
    case 'int': return Math.round(n).toString()
    case 'big': return Math.abs(n) >= 1e4 ? `${(n / 1e4).toFixed(1)}万` : n.toFixed(0)
  }
  return String(v)
}

export function signOf(v: unknown, f?: Fmt): string {
  if (f !== 'pct' && f !== 'big') return ''
  const n = Number(v)
  return !Number.isFinite(n) || n === 0 ? '' : n > 0 ? 'up' : 'down'
}
