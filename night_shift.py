"""
大夜班排班邏輯。
"""
from datetime import datetime
from typing import Dict, List, Set, Tuple

from models import NurseInfo
from shift_utils import (
    should_skip_for_special_status,
    should_skip_for_p_shift,
    should_skip_for_leave,
    should_skip_for_consecutive_days,
)
from date_utils import (
    format_dates_to_dot_string,
    get_night_shift_groups_for_month,
    is_saturday,
    is_holiday,
)


def should_skip_night_shift(
    nurse: NurseInfo,
    target_dates: List,
    target_month: int,
    target_year: int,
    prev_month: int = None,
    next_month: int = None,
) -> Tuple[bool, str]:
    """
    判斷護理師是否需要跳過本次大夜排班。

    Returns:
        (是否跳過, 原因)

    大夜實際上是前一天晚上 11 點上班，因此請假檢查會同時看排班日
    及前一天，避免休假日晚上被排到大夜。
    """
    # 1. 檢查特殊身分，例如 55 歲以上、哺乳。
    skip, reason = should_skip_for_special_status(nurse)
    if skip:
        return True, reason

    # 2. 檢查 P1/P2/換心。
    skip, reason = should_skip_for_p_shift(nurse, target_month, target_dates)
    if skip:
        return True, reason

    # 3. 檢查公休/婚假；大夜需同時檢查前一天。
    skip, reason = should_skip_for_leave(nurse, target_dates, check_day_before=True)
    if skip:
        return True, reason

    manual_makeup_days = {
        block.get('date').date()
        for block in getattr(nurse, 'manual_makeup_shift_blocks', [])
        if block.get('date') and block.get('shift_type') != '大夜'
    }
    if any(d.date() in manual_makeup_days for d in target_dates):
        return True, '補班欄重疊'

    extra_makeup_days = {
        d.date()
        for d in getattr(nurse, 'extra_night_makeup_dates', []) + getattr(nurse, 'extra_small_night_makeup_dates', [])
    }
    if any(d.date() in extra_makeup_days for d in target_dates):
        return True, '額外補班重疊'

    # 4. 檢查是否超過連續工作天數限制。
    skip, reason = should_skip_for_consecutive_days(
        nurse, target_dates, target_month, target_year, prev_month, next_month
    )
    if skip:
        return True, reason

    return False, ''


def _apply_manual_night_shift_prefills(
    nurses: List[NurseInfo],
    night_groups: List[Tuple[List, str]],
    next_month: int,
) -> Tuple[List[Dict], List[Tuple[List, str]]]:
    """Create fixed results from dates already filled in the 大夜 column."""
    manual_results = []
    remaining_groups = []

    for group_dates, group_type in night_groups:
        taken_days = set()
        is_next_month_group = all(d.month == next_month for d in group_dates)

        for nurse in nurses:
            manual_days = {
                d.date()
                for d in getattr(nurse, 'manual_night_shift_dates', [])
                if any(d.date() == group_date.date() for group_date in group_dates)
            }
            selected_dates = [
                d for d in group_dates
                if d.date() in manual_days and d.date() not in taken_days
            ]
            if not selected_dates:
                continue

            taken_days.update(d.date() for d in selected_dates)
            holiday_dates = [d for d in selected_dates if is_holiday(d)]
            manual_results.append({
                'nurse': nurse,
                'dates': selected_dates,
                'group_type': group_type,
                'has_saturday': any(is_saturday(d) for d in selected_dates),
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': False,
                'is_next_month': is_next_month_group,
                'is_manual': True,
            })
            nurse.night_shift_dates.extend(selected_dates)
            print(f"  手動自填大夜: {nurse.name} {format_dates_to_dot_string(selected_dates)}")

        remaining_dates = [d for d in group_dates if d.date() not in taken_days]
        if remaining_dates:
            remaining_groups.append((remaining_dates, group_type))

    return manual_results, remaining_groups


def _remove_manual_makeup_night_blocks(
    nurses: List[NurseInfo],
    night_groups: List[Tuple[List, str]],
) -> List[Tuple[List, str]]:
    blocked_days = {
        block.get('date').date()
        for nurse in nurses
        for block in getattr(nurse, 'manual_makeup_shift_blocks', [])
        if block.get('shift_type') == '大夜' and block.get('date')
    }
    if not blocked_days:
        return night_groups

    remaining_groups = []
    for group_dates, group_type in night_groups:
        remaining_dates = [d for d in group_dates if d.date() not in blocked_days]
        blocked_in_group = [d for d in group_dates if d.date() in blocked_days]
        for d in blocked_in_group:
            print(f"  補班欄手動指定大夜，略過自動排班日期: {d.month}/{d.day}")
        if remaining_dates:
            remaining_groups.append((remaining_dates, group_type))
    return remaining_groups


def _remove_extra_makeup_night_blocks(
    nurses: List[NurseInfo],
    night_groups: List[Tuple[List, str]],
) -> List[Tuple[List, str]]:
    blocked_days = {
        d.date()
        for nurse in nurses
        for d in getattr(nurse, 'extra_night_makeup_dates', [])
    }
    if not blocked_days:
        return night_groups

    remaining_groups = []
    for group_dates, group_type in night_groups:
        remaining_dates = [d for d in group_dates if d.date() not in blocked_days]
        blocked_in_group = [d for d in group_dates if d.date() in blocked_days]
        for d in blocked_in_group:
            print(f"  額外大夜補班已占用，略過正常大夜日期: {d.month}/{d.day}")
        if remaining_dates:
            remaining_groups.append((remaining_dates, group_type))
    return remaining_groups


def _has_current_month_night_assignment(
    nurse: NurseInfo,
    results: List[Dict],
    target_year: int,
    target_month: int,
) -> bool:
    for result in results:
        if result.get('nurse') is not nurse:
            continue
        if any(d.year == target_year and d.month == target_month for d in result.get('dates', [])):
            return True
    return False


def _should_defer_regular_skip(reason: str) -> bool:
    """Return True when a regular-rotation skip should be retried later."""
    if not reason:
        return True
    if reason == '額外補班重疊':
        return False
    if reason in ('55歲以上', '哺乳'):
        return False
    if ('P1' in reason or 'P2' in reason or '換心' in reason) and '衝班' not in reason:
        return False
    return True


def schedule_night_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
    regular_start_index: int = None,
) -> Tuple[List[Dict], str, Set[str]]:
    """
    安排大夜班。

    Args:
        nurses: 護理師資料清單，依輪序排列。
        target_year: 目標年份。
        target_month: 目標月份。
        last_assigned_index: 上一次正常輪序排到的護理師索引。
        include_next_month_first: 是否納入下個月第一組跨月大夜。
        regular_start_index: 指定正常輪序起始索引；未指定時接續 last_assigned_index。

    Returns:
        (排班結果清單, 本月最後正常輪序者姓名, 因身分或特殊條件跳過者集合)
    """
    # 清掉本月預排的大夜日期，避免重新排班時累積舊資料。
    # 跨月排班由後續 scheduled_days 再統一處理。
    for nurse in nurses:
        nurse.night_shift_dates = [
            d for d in nurse.night_shift_dates
            if d.month != target_month or d.year != target_year
        ]

    # 取得本月大夜班組別。
    night_groups = get_night_shift_groups_for_month(target_year, target_month)

    # 計算下個月，用於跨月大夜與下個月第一組大夜。
    next_month = target_month + 1 if target_month < 12 else 1
    next_year = target_year if target_month < 12 else target_year + 1

    # 需要時補入下個月第一組未與本月最後一組重疊的大夜。
    if include_next_month_first:
        next_month_groups = get_night_shift_groups_for_month(next_year, next_month)

        if next_month_groups:
            # 用日期集合避免重複加入同一組跨月班。
            last_group_dates = night_groups[-1][0] if night_groups else []
            last_group_date_set = {d.date() for d in last_group_dates}

            # 找到下個月第一組沒有重疊的班組。
            for group_dates, group_type in next_month_groups:
                group_date_set = {d.date() for d in group_dates}
                if not (group_date_set & last_group_date_set):
                    night_groups.append((group_dates, group_type))
                    break

    scheduled_days = {d.date() for group_dates, _ in night_groups for d in group_dates}
    for nurse in nurses:
        nurse.night_shift_dates = [
            d for d in nurse.night_shift_dates
            if d.date() not in scheduled_days
        ]

    manual_results, night_groups = _apply_manual_night_shift_prefills(
        nurses, night_groups, next_month
    )
    night_groups = _remove_manual_makeup_night_blocks(nurses, night_groups)
    night_groups = _remove_extra_makeup_night_blocks(nurses, night_groups)

    # 計算上個月。
    prev_month = target_month - 1 if target_month > 1 else 12

    results = manual_results

    # 沒有護理師就直接回傳空結果。
    if not nurses:
        return results, '', set()

    current_index = (
        regular_start_index % len(nurses)
        if regular_start_index is not None and regular_start_index >= 0
        else (last_assigned_index + 1) % len(nurses)
    )

    # 記錄本月最後一位正常輪序排到的人。
    last_normal_assigned_name = ''

    # 待補班佇列，格式: [(nurse_index, original_dates), ...]
    makeup_queue = []

    # 記錄因身分或特殊條件被跳過的人，供 Excel 備註使用。
    identity_skipped_nurses = set()
    current_month_group_count = sum(
        1
        for group_dates, _ in night_groups
        if any(d.year == target_year and d.month == target_month for d in group_dates)
    )
    limit_one_assignment_per_month = len(nurses) >= current_month_group_count

    # 先處理從備註/補班欄讀到的待補班。
    for idx, nurse in enumerate(nurses):
        if nurse.pending_night_makeup:
            for makeup_info in nurse.pending_night_makeup:
                makeup_queue.append((idx, makeup_info['dates']))
                print(
                    f"  讀取到大夜待補班: {nurse.name} "
                    f"(原 {format_dates_to_dot_string(makeup_info['dates'])})"
                )

    user_makeup_queue = makeup_queue
    user_makeup_unassigned = []
    makeup_queue = []
    occupied_group_indexes = set()

    for makeup_index, original_dates in user_makeup_queue:
        nurse = nurses[makeup_index]
        assigned_makeup = False
        for group_idx, (group_dates, group_type) in enumerate(night_groups):
            if group_idx in occupied_group_indexes:
                continue

            should_skip, reason = should_skip_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )
            if should_skip:
                print(f"  補班跳過 {nurse.name}: {reason}")
                continue

            is_next_month_group = all(d.month == next_month for d in group_dates)
            holiday_dates = [d for d in group_dates if is_holiday(d)]
            results.append({
                'nurse': nurse,
                'dates': group_dates,
                'group_type': group_type,
                'has_saturday': any(is_saturday(d) for d in group_dates),
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': True,
                'is_next_month': is_next_month_group,
                'original_dates': original_dates,
            })
            nurse.night_shift_dates.extend(group_dates)
            occupied_group_indexes.add(group_idx)
            print(f"  補班: {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            assigned_makeup = True
            break

        if not assigned_makeup:
            user_makeup_unassigned.append((makeup_index, original_dates))

    night_groups = [
        item for group_idx, item in enumerate(night_groups)
        if group_idx not in occupied_group_indexes
    ]

    for group_dates, group_type in night_groups:
        assigned = False

        # 判斷這組是否完全屬於下個月。
        is_next_month_group = all(d.month == next_month for d in group_dates)

        for queued_idx, (makeup_index, original_dates) in enumerate(list(makeup_queue)):
            nurse = nurses[makeup_index]
            if (
                limit_one_assignment_per_month
                and _has_current_month_night_assignment(nurse, results, target_year, target_month)
            ):
                continue

            should_skip, reason = should_skip_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )
            if should_skip:
                continue

            holiday_dates = [d for d in group_dates if is_holiday(d)]
            results.append({
                'nurse': nurse,
                'dates': group_dates,
                'group_type': group_type,
                'has_saturday': any(is_saturday(d) for d in group_dates),
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': True,
                'is_next_month': is_next_month_group,
                'original_dates': original_dates,
            })
            nurse.night_shift_dates.extend(group_dates)
            makeup_queue.pop(queued_idx)
            print(f"  輪序回補: {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            assigned = True
            break

        if assigned:
            continue

        # 2. 正常輪序。
        attempts = 0
        while attempts < len(nurses):
            nurse = nurses[current_index]

            if (
                limit_one_assignment_per_month
                and _has_current_month_night_assignment(nurse, results, target_year, target_month)
            ):
                print(f"  跳過 {nurse.name}: 本月已有大夜")
                current_index = (current_index + 1) % len(nurses)
                attempts += 1
                continue

            should_skip, reason = should_skip_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )

            if not should_skip:
                has_saturday = any(is_saturday(d) for d in group_dates)
                holiday_dates = [d for d in group_dates if is_holiday(d)]
                has_holiday = len(holiday_dates) > 0

                results.append({
                    'nurse': nurse,
                    'dates': group_dates,
                    'group_type': group_type,
                    'has_saturday': has_saturday,
                    'has_holiday': has_holiday,
                    'holiday_dates': holiday_dates,
                    'is_makeup': False,
                    'is_next_month': is_next_month_group,
                })

                nurse.night_shift_dates.extend(group_dates)

                # 只用本月班組更新正常輪序定位點。
                if not is_next_month_group:
                    last_normal_assigned_name = nurse.name

                current_index = (current_index + 1) % len(nurses)
                assigned = True
                break

            print(f"  跳過 {nurse.name}: {reason}")

            # 身分或特殊條件跳過者需要寫回備註；公休/婚假不列入。
            if reason not in ('公休前一日', '婚假', '額外補班重疊'):
                identity_skipped_nurses.add(nurse.name)

            if _should_defer_regular_skip(reason):
                makeup_queue.append((current_index, group_dates))

            current_index = (current_index + 1) % len(nurses)
            attempts += 1

        if not assigned:
            print(f"警告: {format_dates_to_dot_string(group_dates)} 找不到可排班護理師")

    makeup_queue = user_makeup_unassigned + makeup_queue

    # 若仍有無法完成的補班，寫回 new_pending_makeup 供下次排班使用。
    if makeup_queue:
        print("\n提示: 以下護理師本月無法完成大夜補班:")
        for makeup_index, original_dates in makeup_queue:
            nurse = nurses[makeup_index]
            print(f"  - {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            # 寫回 nurse 的 new_pending_makeup，讓輸出端更新備註。
            nurse.new_pending_makeup.append({
                'dates': original_dates,
                'shift_type': '大夜',
            })

    results.sort(key=lambda result: min(result['dates']) if result.get('dates') else datetime.max)

    # 標記最後一筆本月正常輪序結果，輸出時加上 #。
    last_normal_result = None
    for result in results:
        if (
            not result.get('is_makeup')
            and not result.get('is_next_month')
            and not result.get('is_manual')
        ):
            last_normal_result = result

    if last_normal_result:
        last_normal_result['is_last_normal'] = True

    # 記錄本月大夜遇到平日國定假日的補償日期。
    for result in results:
        nurse = result['nurse']
        dates = result['dates']
        # 下個月的預排班不消耗本月補償。
        if not result.get('is_next_month'):
            for d in dates:
                # 只有本月平日國定假日才登記補償。
                if (
                    d.month == target_month
                    and d.year == target_year
                    and is_holiday(d)
                    and d.weekday() < 5
                ):
                    # 避免同一天補償重複記錄。
                    if d not in nurse.holiday_compensations_used:
                        nurse.holiday_compensations_used.append(d)
                        weekday_name = ['週一', '週二', '週三', '週四', '週五'][d.weekday()]
                        print(
                            f"  記錄 {nurse.name} 本月大夜平日國假補償: "
                            f"{d.month}/{d.day}({weekday_name})"
                        )

    print(f"  大夜正常輪序定位點: {last_normal_assigned_name}")
    return results, last_normal_assigned_name, identity_skipped_nurses


def format_night_shift_result(result: Dict) -> str:
    """
    格式化大夜排班結果，供 Excel 欄位輸出。

    一般格式: 3/9.3/10.3/11
    最後正常輪序者: 3/30.3/31.4/1#
    平日國假補償: 3/9.3/10.3/11 (3/10)
    """
    text = format_dates_to_dot_string(result['dates'])

    if result.get('is_last_normal'):
        text += '#'

    comp_dates = []
    for d in result.get('dates', []):
        if is_holiday(d) and d.weekday() < 5:
            comp_dates.append(d)
    if comp_dates:
        comp_unique = sorted({d.date(): d for d in comp_dates}.values())
        comp_text = ','.join(f"{d.month}/{d.day}" for d in comp_unique)
        text += f" ({comp_text})"

    return text
