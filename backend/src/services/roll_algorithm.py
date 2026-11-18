"""整卷试封的单侧精确置换检验。

试封按整卷分派：同一卷内的试件不能拆开当作独立证据。检验在班次内固定
B 组卷数，枚举整卷在 A/B 之间的重分，统计 B 组合格件数至少达到实测值的
分配占比（并列计入）。计数使用整数动态规划，结果精确。
"""
import math
import time

import numpy as np

MAX_ROLLS = 40
MAX_PIECES = 100000
TIME_LIMIT_SECONDS = 10.0
# 单个候选方向的工作数组上限；超出则换方向，全部超出则拒绝判优。
MEMORY_LIMIT_BYTES = 640 * 1024 * 1024


class Refused(Exception):
    """规模或耗时超出判优边界；调用方应记录为拒绝判优，而不是计算失败。"""


def validate_parameters(params):
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError('分析参数必须是对象')
    unknown = set(params) - {'alpha'}
    if unknown:
        raise ValueError('存在未知分析参数：' + ', '.join(sorted(unknown)))
    alpha = params.get('alpha', 0.05)
    if isinstance(alpha, bool):
        raise ValueError('alpha必须是数值')
    try:
        alpha = float(alpha)
    except (TypeError, ValueError):
        raise ValueError('alpha必须是数值')
    if not math.isfinite(alpha) or not 0.001 <= alpha <= 0.2:
        raise ValueError('alpha超出范围')
    return {'alpha': alpha}


def _as_int(value, label):
    if isinstance(value, bool):
        raise ValueError(label + '必须是整数')
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(label + '必须是整数')
    if number != value:
        raise ValueError(label + '必须是整数')
    return number


def validate_snapshot(rows):
    """校验并规范化冻结快照；任何结构问题都明确拒绝。"""
    if not rows:
        raise ValueError('空批次无法分析')
    if len(rows) > MAX_ROLLS:
        raise ValueError('单次最多40卷，当前 %d 卷' % len(rows))
    cleaned = []
    seen = set()
    pieces = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError('卷记录格式不正确')
        shift = str(row.get('shift', '')).strip()
        roll_no = str(row.get('roll_no', '')).strip()
        process = str(row.get('process', '')).strip().upper()
        if not shift:
            raise ValueError('第 %d 卷缺少班次' % (index + 1))
        if not roll_no:
            raise ValueError('第 %d 卷缺少卷号' % (index + 1))
        if process not in ('A', 'B'):
            raise ValueError('卷 %s 的工艺必须为A或B' % roll_no)
        if roll_no in seen:
            raise ValueError('卷号重复：%s' % roll_no)
        seen.add(roll_no)
        qualified = _as_int(row.get('qualified'), '卷 %s 的合格件数' % roll_no)
        trial_pieces = _as_int(row.get('pieces'), '卷 %s 的试封件数' % roll_no)
        if not 1 <= trial_pieces <= MAX_PIECES:
            raise ValueError('每卷试封件数必须在1到%d之间' % MAX_PIECES)
        if not 0 <= qualified <= trial_pieces:
            raise ValueError('卷 %s 的合格件数必须在0到试封件数之间' % roll_no)
        if pieces is None:
            pieces = trial_pieces
        elif trial_pieces != pieces:
            raise ValueError('各卷试封件数必须相同：卷 %s 为 %d 件，应为 %d 件'
                             % (roll_no, trial_pieces, pieces))
        cleaned.append({'shift': shift, 'roll_no': roll_no, 'process': process,
                        'qualified': qualified, 'pieces': trial_pieces})
    groups = {}
    for row in cleaned:
        groups.setdefault(row['shift'], set()).add(row['process'])
    for shift, processes in groups.items():
        if processes != {'A', 'B'}:
            raise ValueError('班次 %s 缺少A组或B组整卷，无法在班次内重分，已拒绝' % shift)
    return cleaned


def _group_by_shift(rows):
    shifts = {}
    for row in rows:
        shifts.setdefault(row['shift'], []).append(row)
    return shifts


def _counts(shifts, process):
    out = {}
    for shift, rows in shifts.items():
        out[shift] = sum(1 for row in rows if row['process'] == process)
    return out


def _estimate(shifts, counts, cap):
    updates = 0
    for shift, rows in shifts.items():
        k = counts[shift]
        updates += sum(min(k, i) for i in range(1, len(rows) + 1))
    return updates * (cap + 1)


def _fits(shifts, counts, cap):
    longest = max(counts.values())
    return (longest + 2) * (cap + 1) * 8 <= MEMORY_LIMIT_BYTES


def _count_assignments(shifts, counts, value_of, cap, upper, deadline):
    """整数 DP：按班次逐步合并，记录选取卷的合计值分布。

    upper=True  统计合计值 >= cap 的分配数（超出 cap 的值并入 cap 桶）。
    upper=False 统计合计值 <= cap 的分配数。
    每个班次独立选取 counts[shift] 卷，班次之间相乘。
    """
    length = cap + 1
    dp = np.zeros(length, dtype=np.int64)
    dp[0] = 1
    for shift, rows in shifts.items():
        k = counts[shift]
        values = [value_of(row) for row in rows]
        local = np.zeros((k + 1, length), dtype=np.int64)
        local[0] = dp
        used = 0
        for value in values:
            for picked in range(min(k - 1, used), -1, -1):
                source = local[picked]
                target = local[picked + 1]
                if upper:
                    if value >= cap:
                        target[cap] += int(source.sum())
                    else:
                        target[value:] += source[:length - value]
                        target[cap] += int(source[length - value:].sum())
                elif value <= cap:
                    target[value:] += source[:length - value]
            used += 1
            if time.monotonic() > deadline:
                raise Refused('超过%g秒判优时限，已拒绝判优；请减少卷数或缩小件数范围'
                              % TIME_LIMIT_SECONDS)
        dp = local[k]
    if upper:
        return int(dp[cap])
    return int(dp.sum())


def permutation_test(rows, alpha=0.05, time_limit=TIME_LIMIT_SECONDS):
    """返回单侧精确置换检验结果。"""
    time_limit = max(0.0, float(time_limit))
    started = time.monotonic()
    rolls = validate_snapshot(rows)
    if not 0.001 <= alpha <= 0.2:
        raise ValueError('alpha超出范围')
    pieces = rolls[0]['pieces']
    shifts = _group_by_shift(rolls)
    b_counts = _counts(shifts, 'B')
    a_counts = {shift: len(group) - b_counts[shift] for shift, group in shifts.items()}
    b_total = sum(row['qualified'] for row in rolls if row['process'] == 'B')
    a_total = sum(row['qualified'] for row in rolls if row['process'] == 'A')
    b_rolls = sum(b_counts.values())
    a_rolls = sum(a_counts.values())
    total_assignments = 1
    for group in shifts.values():
        k = b_counts[group[0]['shift']]
        total_assignments *= math.comb(len(group), k)

    candidates = [
        # B 组合格合计 >= 实测（上限截断在实测值）
        ('B组向上', b_counts, lambda r: r['qualified'], b_total, True),
        # B 组不合格合计 <= B组卷数*每卷件数 - 实测（等价事件）
        ('B组向下', b_counts, lambda r: r['pieces'] - r['qualified'],
         b_rolls * pieces - b_total, False),
        # A 组合格合计 <= 实测（整卷互补，等价事件）
        ('A组向下', a_counts, lambda r: r['qualified'], a_total, False),
        # A 组不合格合计 >= A组卷数*每卷件数 - 实测（等价事件）
        ('A组向上', a_counts, lambda r: r['pieces'] - r['qualified'],
         a_rolls * pieces - a_total, True),
    ]
    feasible = []
    for name, counts, value_of, cap, upper in candidates:
        if cap < 0:
            continue
        if not _fits(shifts, counts, cap):
            continue
        feasible.append((_estimate(shifts, counts, cap), name, counts, value_of, cap, upper))
    if not feasible:
        raise Refused('计算规模超出判优内存上限，已拒绝判优；请减少卷数或缩小件数范围')
    feasible.sort(key=lambda item: item[0])
    _, orientation, counts, value_of, cap, upper = feasible[0]
    deadline = started + time_limit
    matched = _count_assignments(shifts, counts, value_of, cap, upper, deadline)
    elapsed = time.monotonic() - started
    p_value = matched / total_assignments

    detail_rows = []
    for shift, group in shifts.items():
        detail_rows.append([
            shift,
            len(group),
            b_counts[shift],
            sum(row['qualified'] for row in group if row['process'] == 'A'),
            sum(row['qualified'] for row in group if row['process'] == 'B'),
            pieces,
        ])
    decision = ('拒绝“B工艺不优于A工艺”的原假设' if p_value < alpha
                else '未拒绝“B工艺不优于A工艺”的原假设')
    return {
        'metrics': {
            '单侧p值': p_value,
            '显著性阈值': alpha,
            'B组合格合计': b_total,
            'A组合格合计': a_total,
            '纳入卷数': len(rolls),
            '班次数': len(shifts),
            '每卷试封件数': pieces,
            '重分方案总数': total_assignments,
            '达标分配数': matched,
        },
        'interpretation': (
            '固定各班次B组卷数后，在班次内重分整卷共 %d 种分配；'
            'B组合格件数至少达到实测 %d 件的分配有 %d 种，单侧占比 p=%.6g。'
            '在预设α=%g下%s。同卷试件按整卷计入，未拆开。'
            % (total_assignments, b_total, matched, p_value, alpha, decision)
        ),
        'caveat': ('本检验只在班次内重分整卷，班次结构、卷数与每卷试封件数须预先固定；'
                   '事后改卷或按件拆分会夸大证据。未拒绝不代表等效，结论须结合业务成本与复试风险。'),
        'included_rolls': len(rolls),
        'columns': ['班次', '卷数', 'B组卷数', 'A组合格件数', 'B组合格件数', '每卷件数'],
        'rows': detail_rows,
        'table_title': '班次内整卷重分概况',
        'method': {'orientation': orientation, 'elapsed_seconds': round(elapsed, 3)},
    }
