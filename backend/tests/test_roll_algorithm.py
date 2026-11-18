import itertools
import math
import random
import time

import pytest

from src.services import roll_algorithm
from src.services.roll_algorithm import Refused, permutation_test, validate_snapshot


def roll(shift, process, qualified, pieces=10, roll_no=None):
    return {'shift': shift, 'roll_no': roll_no or f'{shift}-{process}-{qualified}-{random.random()}',
            'process': process, 'qualified': qualified, 'pieces': pieces}


def brute_force(rows):
    """直接枚举班次内全部整卷重分，作为独立 oracle。"""
    shifts = {}
    for row in rows:
        shifts.setdefault(row['shift'], []).append(row)
    b_total = sum(r['qualified'] for r in rows if r['process'] == 'B')
    options = []
    for group in shifts.values():
        k = sum(1 for r in group if r['process'] == 'B')
        options.append([sum(group[i]['qualified'] for i in combo)
                        for combo in itertools.combinations(range(len(group)), k)])
    matched = total = 0
    for combination in itertools.product(*options):
        total += 1
        matched += sum(combination) >= b_total
    return matched, total


def test_ties_are_counted_in():
    # 班次 S1 的 B 卷 7 件、A 卷 3 件；S2 两卷各 4 件（并列）。
    # 4 种重分：7+4、7+4、3+4、3+4；实测 B=11，两处并列达标都计入。
    rows = [roll('S1', 'B', 7, roll_no='R1'), roll('S1', 'A', 3, roll_no='R2'),
            roll('S2', 'B', 4, roll_no='R3'), roll('S2', 'A', 4, roll_no='R4')]
    result = permutation_test(rows)
    assert result['metrics']['重分方案总数'] == 4
    assert result['metrics']['达标分配数'] == 2
    assert result['metrics']['单侧p值'] == 2 / 4


def test_matches_brute_force_on_random_batches():
    rng = random.Random(7)
    checked = 0
    for _ in range(120):
        shifts = []
        pieces = rng.randint(2, 12)
        for s in range(rng.randint(1, 3)):
            group = [roll(f'S{s}', rng.choice('AB'), rng.randint(0, pieces), pieces)
                     for _ in range(rng.randint(2, 6))]
            if {r['process'] for r in group} != {'A', 'B'}:
                continue
            shifts.extend(group)
        if not shifts:
            continue
        matched, total = brute_force(shifts)
        result = permutation_test(shifts)
        assert (result['metrics']['达标分配数'], result['metrics']['重分方案总数']) == (matched, total)
        checked += 1
    assert checked > 80


def test_rolls_are_never_split_across_arms():
    """若同卷试件可拆开，2 卷 20 件会产生远多于此的重分方案数。"""
    rows = [roll('S1', 'B', 9, 10, 'R1'), roll('S1', 'A', 6, 10, 'R2'),
            roll('S2', 'B', 8, 10, 'R3'), roll('S2', 'A', 7, 10, 'R4')]
    result = permutation_test(rows)
    assert result['metrics']['重分方案总数'] == 2 * 2


def test_shift_imbalance_rejected():
    rows = [roll('S1', 'B', 9), roll('S1', 'A', 6), roll('S2', 'B', 8)]
    with pytest.raises(ValueError, match='班次 S2 缺少'):
        validate_snapshot(rows)


def test_missing_shift_rejected():
    with pytest.raises(ValueError, match='缺少班次'):
        validate_snapshot([{'shift': ' ', 'roll_no': 'R1', 'process': 'A',
                            'qualified': 1, 'pieces': 10}])


def test_duplicate_roll_no_rejected():
    rows = [roll('S1', 'B', 9, roll_no='R1'), roll('S1', 'A', 6, roll_no='R1')]
    with pytest.raises(ValueError, match='卷号重复'):
        validate_snapshot(rows)


def test_unequal_pieces_rejected():
    rows = [roll('S1', 'B', 9, 10, 'R1'), roll('S1', 'A', 6, 11, 'R2')]
    with pytest.raises(ValueError, match='试封件数必须相同'):
        validate_snapshot(rows)


def test_bad_counts_rejected():
    with pytest.raises(ValueError, match='合格件数必须'):
        validate_snapshot([roll('S1', 'B', 11, 10, 'R1'), roll('S1', 'A', 6, 10, 'R2')])
    with pytest.raises(ValueError, match='失*|合格件数必须是整数'):
        validate_snapshot([roll('S1', 'B', 5.5, 10, 'R1'), roll('S1', 'A', 6, 10, 'R2')])
    with pytest.raises(ValueError, match='工艺必须'):
        validate_snapshot([{'shift': 'S1', 'roll_no': 'R1', 'process': 'C',
                            'qualified': 1, 'pieces': 10}])


def test_empty_and_too_many_rejected():
    with pytest.raises(ValueError, match='空批次'):
        validate_snapshot([])
    rows = [roll('S1', 'B' if i < 21 else 'A', 5, roll_no=f'R{i}') for i in range(41)]
    with pytest.raises(ValueError, match='最多40卷'):
        validate_snapshot(rows)


def test_parameters_whitelisted():
    assert roll_algorithm.validate_parameters({}) == {'alpha': 0.05}
    assert roll_algorithm.validate_parameters({'alpha': 0.1}) == {'alpha': 0.1}
    for bad in ({'alpha': 0.5}, {'alpha': True}, {'alpha': 'x'}, {'beta': 1}, {'alpha': 0}):
        with pytest.raises(ValueError):
            roll_algorithm.validate_parameters(bad)


def test_40_rolls_max_pieces_within_ten_seconds():
    """40卷、每卷10万件、班次内20/20：最坏规模下须10秒内给出精确分数。"""
    rng = random.Random(11)
    rows = [roll('S1', 'B' if i < 20 else 'A', rng.randint(45000, 55000), 100000,
                 roll_no=f'R{i:02d}') for i in range(40)]
    started = time.monotonic()
    result = permutation_test(rows, time_limit=10)
    elapsed = time.monotonic() - started
    assert elapsed < 10
    matched = result['metrics']['达标分配数']
    total = result['metrics']['重分方案总数']
    assert total == math.comb(40, 20)
    assert 0 <= matched <= total
    assert result['metrics']['单侧p值'] == matched / total


def test_timeout_is_refused(monkeypatch):
    monkeypatch.setattr(roll_algorithm, 'TIME_LIMIT_SECONDS', 10.0)
    rows = [roll('S1', 'B' if i < 20 else 'A', 50000, 100000, roll_no=f'R{i}') for i in range(40)]
    with pytest.raises(Refused):
        permutation_test(rows, time_limit=-1)
