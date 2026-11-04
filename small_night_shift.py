"""
小夜週排班邏輯。
"""
from datetime import datetime, timedelta
from typing import Dict, List, Set, Tuple

from models import NurseInfo
from shift_utils import (
    should_skip_for_p_shift,
    should_skip_for_leave,
    should_skip_for_consecutive_days,
)
from date_utils import (
    format_dates_to_dot_string,
    get_month_dates,
    is_holiday,
)


SMALL_NIGHT_SHIFT_TYPES = {'小夜', '小夜週'}


def _is_small_night_shift_type(shift_type: str) -> bool:
    return shift_type in SMALL_NIGHT_SHIFT_TYPES


def get_small_night_shift_groups_for_month(year: int, month: int) -> List[Tuple[List, str]]:
    """
    取得指定月份的小夜週班組。

    小夜週以完整週一到週五為一組。若月底週未滿五個平日，會往下個月補到
    週五；月初若不是週一，則從第一個完整週一開始。
    """
    groups = []
    dates = get_month_dates(year, month)

    current_group = []

    # 找到第一個完整週的週一。
    first_complete_idx = 0
    if dates:
        first_weekday = dates[0].weekday()
        if first_weekday != 0:
            for idx, date in enumerate(dates):
                if date.weekday() == 0:  # 週一
                    first_complete_idx = idx
                    break

    for date in dates[first_complete_idx:]:
        weekday = date.weekday()

        # 週一到週五為一組。
        if weekday in [0, 1, 2, 3, 4]:
            current_group.append(date)
            if weekday == 4:  # 週五，結束本組。
                groups.append((current_group, 'mon-fri'))
                current_group = []

    # 月底未滿週五時，往下個月補足到週五。
    if current_group:
        last_date = current_group[-1]
        last_weekday = last_date.weekday()

        days_to_add = 4 - last_weekday  # 補到週五(4)
        for i in range(1, days_to_add + 1):
            next_date = last_date + timedelta(days=i)
            while next_date.weekday() in [5, 6]:
                next_date += timedelta(days=1)
            if next_date.weekday() <= 4:
                current_group.append(next_date)

        groups.append((current_group, 'mon-fri'))

    return groups


def should_skip_small_night_shift(
    nurse: NurseInfo,
    target_dates: List,
    target_month: int,
    target_year: int,
    prev_month: int = None,
    next_month: int = None,
) -> Tuple[bool, str]:
    """
    判斷護理師是否需要跳過本次小夜週排班。

    Returns:
        (是否跳過, 原因)
    """
    # 1. 哺乳同仁不排小夜週。
    if nurse.is_breastfeeding:
        return True, '哺乳'

    # 2. 檢查 P1/P2/換心/待命。
    skip, reason = should_skip_for_p_shift(nurse, target_month, target_dates)
    if skip:
        return True, reason

    # 3. 檢查公休/婚假；小夜週不需要檢查前一天。
    skip, reason = should_skip_for_leave(nurse, target_dates, check_day_before=False)
    if skip:
        return True, reason

    # 4. 小夜週不能和已排的大夜重疊。
    if nurse.night_shift_dates:
        night_dates = {d.date() for d in nurse.night_shift_dates}
        if any(d.date() in night_dates for d in target_dates):
            return True, '大夜重疊'

    manual_makeup_days = {
        block.get('date').date()
        for block in getattr(nurse, 'manual_makeup_shift_blocks', [])
        if block.get('date') and not _is_small_night_shift_type(block.get('shift_type'))
    }
    if any(d.date() in manual_makeup_days for d in target_dates):
        return True, '補班欄重疊'

    # 5. 檢查是否超過連續工作天數限制。
    skip, reason = should_skip_for_consecutive_days(
        nurse, target_dates, target_month, target_year, prev_month, next_month
    )
    if skip:
        return True, reason

    return False, ''


def _apply_manual_small_night_shift_prefills(
    nurses: List[NurseInfo],
    small_night_groups: List[Tuple[List, str]],
    next_month: int,
) -> Tuple[List[Dict], List[Tuple[List, str]]]:
    """Create fixed results from dates already filled in the 小夜週 column."""
    manual_results = []
    remaining_groups = []

    for group_dates, group_type in small_night_groups:
        taken_days = set()
        is_next_month_group = all(d.month == next_month for d in group_dates)

        for nurse in nurses:
            manual_days = {
                d.date()
                for d in getattr(nurse, 'manual_small_night_shift_dates', [])
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
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': False,
                'is_next_month': is_next_month_group,
                'is_manual': True,
            })
            nurse.small_night_shift_dates.extend(selected_dates)
            print(f"  手動自填小夜週: {nurse.name} {format_dates_to_dot_string(selected_dates)}")

        remaining_dates = [d for d in group_dates if d.date() not in taken_days]
        if remaining_dates:
            remaining_groups.append((remaining_dates, group_type))

    return manual_results, remaining_groups


def _remove_manual_makeup_small_night_blocks(
    nurses: List[NurseInfo],
    small_night_groups: List[Tuple[List, str]],
) -> List[Tuple[List, str]]:
    blocked_days = {
        block.get('date').date()
        for nurse in nurses
        for block in getattr(nurse, 'manual_makeup_shift_blocks', [])
        if _is_small_night_shift_type(block.get('shift_type')) and block.get('date')
    }
    if not blocked_days:
        return small_night_groups

    remaining_groups = []
    for group_dates, group_type in small_night_groups:
        remaining_dates = [d for d in group_dates if d.date() not in blocked_days]
        blocked_in_group = [d for d in group_dates if d.date() in blocked_days]
        for d in blocked_in_group:
            print(f"  補班欄手動指定小夜，略過自動排班日期: {d.month}/{d.day}")
        if remaining_dates:
            remaining_groups.append((remaining_dates, group_type))
    return remaining_groups


def _has_current_month_small_night_assignment(
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
    if reason == '哺乳':
        return False
    if ('P1' in reason or 'P2' in reason or '換心' in reason) and '衝班' not in reason:
        return False
    return True


def schedule_small_night_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
    regular_start_index: int = None,
    reserved_results: List[Dict] = None,
) -> Tuple[List[Dict], str, Set[str]]:
    """
    安排小夜週。

    Args:
        nurses: 護理師資料清單，依輪序排列。
        target_year: 目標年份。
        target_month: 目標月份。
        last_assigned_index: 上一次正常輪序排到的護理師索引。
        include_next_month_first: 是否納入下個月第一組跨月小夜週。
        regular_start_index: 指定正常輪序起始索引；未指定時接續 last_assigned_index。
        reserved_results: 已先排定的小夜結果（例如額外補班），其日期不再納入正常輪排。

    Returns:
        (排班結果清單, 本月最後正常輪序者姓名, 因身分或特殊條件跳過者集合)

    小夜週輪序方向與大夜相反，因此預設從 last_assigned_index - 1 開始。
    """
    reserved_days = {
        d.date()
        for result in reserved_results or []
        for d in result.get('dates', [])
    }

    # 清掉本月預排的小夜週日期，避免重新排班時累積舊資料。
    # 已先排定的額外補班必須保留。
    # 上月 Excel 已排、跨入本月的小夜週（例如 12/28-1/1）也要保留；
    # 落在本月班組內的日期會在下方 scheduled_days 過濾掉。
    for nurse in nurses:
        manual_days = {d.date() for d in getattr(nurse, 'manual_small_night_shift_dates', [])}
        nurse.small_night_shift_dates = [
            d for d in nurse.small_night_shift_dates
            if d.date() in reserved_days
            or d.date() in manual_days
            or d.month != target_month or d.year != target_year
        ]

    # 取得本月小夜週班組。
    small_night_groups = get_small_night_shift_groups_for_month(target_year, target_month)

    # 計算下個月，用於跨月小夜週與下個月第一組小夜週。
    next_month = target_month + 1 if target_month < 12 else 1
    next_year = target_year if target_month < 12 else target_year + 1

    # 需要時補入下個月第一組未與本月最後一組重疊的小夜週。
    if include_next_month_first:
        next_month_groups = get_small_night_shift_groups_for_month(next_year, next_month)

        if next_month_groups:
            last_group_dates = small_night_groups[-1][0] if small_night_groups else []
            last_group_date_set = {d.date() for d in last_group_dates}

            for group_dates, group_type in next_month_groups:
                group_date_set = {d.date() for d in group_dates}
                if not (group_date_set & last_group_date_set):
                    small_night_groups.append((group_dates, group_type))
                    break

    scheduled_days = {d.date() for group_dates, _ in small_night_groups for d in group_dates}
    for nurse in nurses:
        nurse.small_night_shift_dates = [
            d for d in nurse.small_night_shift_dates
            if d.date() in reserved_days or d.date() not in scheduled_days
        ]

    if reserved_days:
        remaining_groups = []
        for group_dates, group_type in small_night_groups:
            remaining_dates = [d for d in group_dates if d.date() not in reserved_days]
            if remaining_dates:
                remaining_groups.append((remaining_dates, group_type))
        small_night_groups = remaining_groups

    manual_results, small_night_groups = _apply_manual_small_night_shift_prefills(
        nurses, small_night_groups, next_month
    )
    small_night_groups = _remove_manual_makeup_small_night_blocks(nurses, small_night_groups)

    # 計算上個月，用於跨月連續天數檢查。
    prev_month = target_month - 1 if target_month > 1 else 12

    results = manual_results

    if not nurses:
        return results, '', set()

    current_index = (
        regular_start_index % len(nurses)
        if regular_start_index is not None and regular_start_index >= 0
        else (last_assigned_index - 1) % len(nurses)
    )

    # 記錄本月最後一位正常輪序排到的人。
    last_normal_assigned_name = ''

    # 待補班佇列，格式: [(nurse_index, original_dates), ...]
    makeup_queue = []

    # 先處理從備註/補班欄讀到的待補班。
    for idx, nurse in enumerate(nurses):
        if nurse.pending_small_night_makeup:
            for makeup_info in nurse.pending_small_night_makeup:
                makeup_queue.append((idx, makeup_info['dates']))
                print(
                    f"  讀取到小夜待補班: {nurse.name} "
                    f"(原 {format_dates_to_dot_string(makeup_info['dates'])})"
                )

    # 記錄因身分或特殊條件被跳過的人，供 Excel 備註使用。
    identity_skipped_nurses = set()
    current_month_group_count = sum(
        1
        for group_dates, _ in small_night_groups
        if any(d.year == target_year and d.month == target_month for d in group_dates)
    )
    limit_one_assignment_per_month = len(nurses) >= current_month_group_count

    user_makeup_queue = makeup_queue
    user_makeup_unassigned = []
    makeup_queue = []
    occupied_group_indexes = set()

    for makeup_index, original_dates in user_makeup_queue:
        nurse = nurses[makeup_index]
        assigned_makeup = False
        for group_idx, (group_dates, group_type) in enumerate(small_night_groups):
            if group_idx in occupied_group_indexes:
                continue

            should_skip, reason = should_skip_small_night_shift(
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
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': True,
                'is_next_month': is_next_month_group,
                'original_dates': original_dates,
            })
            nurse.small_night_shift_dates.extend(group_dates)
            occupied_group_indexes.add(group_idx)
            print(f"  補班: {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            assigned_makeup = True
            break

        if not assigned_makeup:
            user_makeup_unassigned.append((makeup_index, original_dates))

    small_night_groups = [
        item for group_idx, item in enumerate(small_night_groups)
        if group_idx not in occupied_group_indexes
    ]

    for group_dates, group_type in small_night_groups:
        assigned = False

        # 判斷這組是否完全屬於下個月。
        is_next_month_group = all(d.month == next_month for d in group_dates)

        for queued_idx, (makeup_index, original_dates) in enumerate(list(makeup_queue)):
            nurse = nurses[makeup_index]
            if (
                limit_one_assignment_per_month
                and _has_current_month_small_night_assignment(nurse, results, target_year, target_month)
            ):
                continue

            should_skip, reason = should_skip_small_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )
            if should_skip:
                continue

            holiday_dates = [d for d in group_dates if is_holiday(d)]
            results.append({
                'nurse': nurse,
                'dates': group_dates,
                'group_type': group_type,
                'has_holiday': len(holiday_dates) > 0,
                'holiday_dates': holiday_dates,
                'is_makeup': True,
                'is_next_month': is_next_month_group,
                'original_dates': original_dates,
            })
            nurse.small_night_shift_dates.extend(group_dates)
            makeup_queue.pop(queued_idx)
            print(f"  輪序回補: {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            assigned = True
            break

        if assigned:
            continue

        # 2. 正常輪序；小夜週由後往前輪。
        attempts = 0
        while attempts < len(nurses):
            nurse = nurses[current_index]

            if (
                limit_one_assignment_per_month
                and _has_current_month_small_night_assignment(nurse, results, target_year, target_month)
            ):
                print(f"  跳過 {nurse.name}: 本月已有小夜")
                current_index = (current_index - 1) % len(nurses)
                attempts += 1
                continue

            should_skip, reason = should_skip_small_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )

            if not should_skip:
                holiday_dates = [d for d in group_dates if is_holiday(d)]
                has_holiday = len(holiday_dates) > 0

                results.append({
                    'nurse': nurse,
                    'dates': group_dates,
                    'group_type': group_type,
                    'has_holiday': has_holiday,
                    'holiday_dates': holiday_dates,
                    'is_makeup': False,
                    'is_next_month': is_next_month_group,
                })

                nurse.small_night_shift_dates.extend(group_dates)

                date_str = format_dates_to_dot_string(group_dates)
                print(f"  正常輪序: {nurse.name} {date_str}")

                # 只用本月班組更新正常輪序定位點。
                if not is_next_month_group:
                    last_normal_assigned_name = nurse.name

                current_index = (current_index - 1) % len(nurses)
                assigned = True
                break

            print(f"  跳過 {nurse.name}: {reason}")

            # 身分或特殊條件跳過者需要寫回備註；公休/婚假/大夜重疊不列入。
            if reason not in ('公休前一日', '婚假', '大夜重疊'):
                identity_skipped_nurses.add(nurse.name)

            if _should_defer_regular_skip(reason):
                makeup_queue.append((current_index, group_dates))
                print("    加入待補班佇列")

            current_index = (current_index - 1) % len(nurses)
            attempts += 1

        if not assigned:
            print(f"警告: {format_dates_to_dot_string(group_dates)} 找不到可排班護理師")

    makeup_queue = user_makeup_unassigned + makeup_queue

    # 若仍有無法完成的補班，寫回 new_pending_makeup 供下次排班使用。
    if makeup_queue:
        print("\n提示: 以下護理師本月無法完成小夜補班:")
        for makeup_index, original_dates in makeup_queue:
            nurse = nurses[makeup_index]
            print(f"  - {nurse.name} (原 {format_dates_to_dot_string(original_dates)})")
            # 待補班備註格式使用「小夜」，與 shift_utils/excel_handler 保持一致。
            nurse.new_pending_makeup.append({
                'dates': original_dates,
                'shift_type': '小夜',
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

    # 記錄本月小夜週遇到平日國定假日的補償日期。
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
                            f"  記錄 {nurse.name} 本月小夜週平日國假補償: "
                            f"{d.month}/{d.day}({weekday_name})"
                        )

    print(f"  小夜正常輪序定位點: {last_normal_assigned_name}")
    return results, last_normal_assigned_name, identity_skipped_nurses


def format_small_night_shift_result(result: Dict) -> str:
    """
    格式化小夜週排班結果，供 Excel 欄位輸出。

    一般格式: 3/9-3/13
    最後正常輪序者: 3/30-4/3#
    平日國假補償: 3/9-3/13 (3/10)
    """
    dates = result['dates']
    if not dates:
        return ''

    first_date = dates[0]
    last_date = dates[-1]
    text = f"{first_date.month}/{first_date.day}-{last_date.month}/{last_date.day}"

    if result.get('is_last_normal'):
        text += '#'

    comp_dates = []
    for d in dates:
        if is_holiday(d) and d.weekday() < 5:
            comp_dates.append(d)
    if comp_dates:
        comp_unique = sorted({d.date(): d for d in comp_dates}.values())
        comp_text = ','.join(f"{d.month}/{d.day}" for d in comp_unique)
        text += f" ({comp_text})"

    return text
