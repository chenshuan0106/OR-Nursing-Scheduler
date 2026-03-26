"""
小夜班排班核心邏輯
"""
from typing import List, Dict, Tuple

from models import NurseInfo
from shift_utils import (
    should_skip_for_special_status,
    should_skip_for_p_shift,
    should_skip_for_leave,
    should_skip_for_consecutive_days,
)
from date_utils import (
    format_dates_to_dot_string,
    get_month_dates,
    is_holiday,
)


def get_small_night_shift_groups_for_month(year: int, month: int) -> List[Tuple[List, str]]:
    """
    取得指定月份的小夜班組別
    小夜班是星期一～五為一組 (一個 cost)

    返回: [(日期列表, 組別類型), ...]
    組別類型: 'mon-fri'

    注意：
    - 月底可能會跨月，例如 3/31(一)、4/1(二)、4/2(三)、4/3(四)、4/4(五)
    - 月初不完整的組別會被跳過（例如4/1-4/3不會成為組別）
    """
    from datetime import timedelta

    groups = []
    dates = get_month_dates(year, month)

    # 計算下個月
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    current_group = []

    # 找到第一個完整組別的開始（週一）
    first_complete_idx = 0
    if dates:
        first_weekday = dates[0].weekday()
        # 如果第一天不是週一(0)，跳過直到找到週一
        if first_weekday != 0:
            for idx, date in enumerate(dates):
                if date.weekday() == 0:  # 週一
                    first_complete_idx = idx
                    break

    for date in dates[first_complete_idx:]:
        weekday = date.weekday()

        # 星期一～五
        if weekday in [0, 1, 2, 3, 4]:
            current_group.append(date)
            if weekday == 4:  # 週五，結束這一組
                groups.append((current_group, 'mon-fri'))
                current_group = []

    # 處理月底未結束的組（可能需要跨月補齊）
    if current_group:
        last_date = current_group[-1]
        last_weekday = last_date.weekday()

        # 需要補到週五
        days_to_add = 4 - last_weekday  # 補到週五(4)
        for i in range(1, days_to_add + 1):
            next_date = last_date + timedelta(days=i)
            # 跳過週六日
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
    判斷護理人員是否應該跳過這次小夜班

    返回: (是否跳過, 原因)
    """
    # 0. 【新增】全域免排班檢查：懷孕、交叉訓練等直接跳過，且不需補班
    if getattr(nurse, 'is_exempt', False):
        return True, getattr(nurse, 'exempt_reason', '免排班')

    # 1. 檢查特殊狀態（小夜班只檢查哺乳，不檢查55歲）
    if nurse.is_breastfeeding:
        return True, '哺乳'

    # 2. 檢查 P1/P2/換心/待命
    skip, reason = should_skip_for_p_shift(nurse, target_month, target_dates)
    if skip:
        return True, reason

    # 3. 檢查公休（小夜班不需要檢查前一天）
    skip, reason = should_skip_for_leave(nurse, target_dates, check_day_before=False)
    if skip:
        return True, reason

    # 5. 檢查是否與大夜重疊（同一天）
    if nurse.night_shift_dates:
        night_dates = {d.date() for d in nurse.night_shift_dates}
        if any(d.date() in night_dates for d in target_dates):
            return True, '大夜重疊'

    # 6. 檢查連續7天上班
    skip, reason = should_skip_for_consecutive_days(
        nurse, target_dates, target_month, target_year, prev_month, next_month
    )
    if skip:
        return True, reason

    return False, ''


def schedule_small_night_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
) -> Tuple[List[Dict], str, set]:
    """
    排小夜班

    Args:
        nurses: 護理人員列表（按照名單順序）
        target_year: 目標年份
        target_month: 目標月份
        last_assigned_index: 上個月最後輪到的人的索引
        include_next_month_first: 是否包含下個月第一個 cost

    Returns:
        (排班結果列表, 正常輪序最後一個人的名字)

        注意：正常輪序最後一個人 ≠ 最後排班的人
        補班的人不算在正常輪序中，下個月應該從正常輪序最後一個人繼續往上排

    注意：小夜班是「往上排」，從上個月最後輪到的人的上一位開始
    """
    # 在清空本月資料前，先記錄上月跨月排班中包含本月「平日」國定假日的日期
    # 只有週一到週五的國定假日才能補償（跳過一次假日值班，不用補班）
    for nurse in nurses:
        nurse.small_night_shift_dates = [
            d for d in nurse.small_night_shift_dates 
            if d.month != target_month or d.year != target_year
        ]
    
    # 取得本月的小夜班組別
    small_night_groups = get_small_night_shift_groups_for_month(target_year, target_month)

    # 注意：不過濾上個月排的本月cost
    # 原因：上個月排的本月第一個cost只是「預告」
    # 本月排班時會從第一個cost開始重新排，覆蓋預告

    # 計算下個月
    next_month = target_month + 1 if target_month < 12 else 1
    next_year = target_year if target_month < 12 else target_year + 1

    # 如果需要包含下個月第一個 cost
    if include_next_month_first:
        next_month_groups = get_small_night_shift_groups_for_month(next_year, next_month)

        if next_month_groups:
            # 取得本月最後一組的所有日期(使用完整日期物件)
            last_group_dates = small_night_groups[-1][0] if small_night_groups else []
            last_group_date_set = set(d.date() for d in last_group_dates)

            # 找到第一個與本月最後一組沒有日期重疊的組別
            for group_dates, group_type in next_month_groups:
                group_date_set = set(d.date() for d in group_dates)
                # 如果沒有重疊的日期,就加入這個組別
                if not (group_date_set & last_group_date_set):
                    small_night_groups.append((group_dates, group_type))
                    break

    # 計算前後月
    prev_month = target_month - 1 if target_month > 1 else 12

    results = []

    # 如果沒有護理人員，直接返回空列表
    if not nurses:
        return results, ''

    # 小夜班是「往上排」，從上個月最後輪到的人的上一位開始
    # last_assigned_index 是上個月最後輪到的人
    # 往上一位就是 last_assigned_index - 1
    current_index = (last_assigned_index - 1) % len(nurses)

    # 追蹤正常輪序最後一個人（不含補班）
    last_normal_assigned_name = ''

    # 待補班名單 (因公休跳過的人)
    # 格式: [(nurse_index, original_dates), ...]
    makeup_queue = []

    # 從備註欄讀取的待補班（上個月遺留的）加入 makeup_queue
    for idx, nurse in enumerate(nurses):
        if nurse.pending_small_night_makeup:
            for makeup_info in nurse.pending_small_night_makeup:
                makeup_queue.append((idx, makeup_info['dates']))
                print(f"  從備註讀取待補班: {nurse.name} (原本 {format_dates_to_dot_string(makeup_info['dates'])})")

    # 追蹤每人跳過次數（用於「每人只能跳過一次」規則）
    skip_count = {nurse.name: 0 for nurse in nurses}

    # 因身分別被跳過的護理人員（用於 Excel 顯示）
    identity_skipped_nurses = set()
    
    for group_dates, group_type in small_night_groups:
        assigned = False

        # 判斷這組是否為下個月的班
        is_next_month_group = all(d.month == next_month for d in group_dates)

        # 1. 先檢查待補班名單
        for i, (makeup_index, original_dates) in enumerate(makeup_queue):
            nurse = nurses[makeup_index]

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
                    'is_makeup': True,
                    'is_next_month': is_next_month_group,
                    'original_dates': original_dates,  # 補班原始日期
                })

                nurse.small_night_shift_dates.extend(group_dates)
                makeup_queue.pop(i)
                print(f"  補班: {nurse.name} (原本 {format_dates_to_dot_string(original_dates)} 公休)")
                assigned = True
                break

        if assigned:
            continue

        # 2. 正常輪值（往上排）
        attempts = 0
        while attempts < len(nurses):
            nurse = nurses[current_index]

            should_skip, reason = should_skip_small_night_shift(
                nurse, group_dates, target_month, target_year, prev_month, next_month
            )
            
            # 每人只能跳過一次（非補班原因）：如果已經跳過一次且原因不是「公休中」或「大夜重疊」，則不跳過
            original_reason = reason
            if should_skip and reason not in ['公休中', '大夜重疊']:
                if skip_count.get(nurse.name, 0) >= 1:
                    should_skip = False
                    reason = ''
                    print(f"  強制排班 {nurse.name} (已跳過一次，不再跳過)")

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
                
                # 輸出排班記錄
                date_str = format_dates_to_dot_string(group_dates)
                print(f"  正常輪序: {nurse.name} {date_str}")

                # 記錄正常輪序最後一個人（只記錄本月的，不記錄下月的）
                if not is_next_month_group:
                    last_normal_assigned_name = nurse.name

                # 往上排，所以是 -1
                current_index = (current_index - 1) % len(nurses)
                assigned = True
                break
            else:
                print(f"  跳過 {nurse.name}: {reason}")

                # 記錄因身分別（哺乳/P/換心）被跳過的護理人員
                if reason not in ('公休中', '婚假', '大夜重疊'):
                    identity_skipped_nurses.add(nurse.name)

                if reason in ['公休中', '大夜重疊']:
                    makeup_queue.append((current_index, group_dates))
                    print(f"    → 加入待補班名單")

                # 往上排，所以是 -1
                current_index = (current_index - 1) % len(nurses)
                attempts += 1

        if not assigned:
            print(f"警告: 無法為 {format_dates_to_dot_string(group_dates)} 找到可排班的人員")

    # 如果還有人在待補班名單但本月排不完，記錄到 new_pending_makeup
    if makeup_queue:
        print(f"\n注意: 以下人員本月尚未補班完成:")
        for makeup_index, original_dates in makeup_queue:
            nurse = nurses[makeup_index]
            print(f"  - {nurse.name} (原本 {format_dates_to_dot_string(original_dates)})")
            # 記錄到 nurse 的 new_pending_makeup，輸出時會寫入備註
            nurse.new_pending_makeup.append({
                'dates': original_dates,
                'shift_type': '小夜',
            })

    # 標記正常輪序最後一個班次（加 # 標註）
    last_normal_result = None
    for result in results:
        if not result.get('is_makeup') and not result.get('is_next_month'):
            last_normal_result = result
    
    if last_normal_result:
        last_normal_result['is_last_normal'] = True

    # 記錄本月排班中包含的平日國定假日（供假日班補償使用）
    for result in results:
        nurse = result['nurse']
        dates = result['dates']
        # 只檢查本月的排班（不含下月預告）
        if not result.get('is_next_month'):
            for d in dates:
                # 檢查：本月 + 國定假日 + 平日（週一到週五）
                if (d.month == target_month and d.year == target_year 
                    and is_holiday(d) 
                    and d.weekday() < 5):  # 0=週一, 4=週五
                    # 記錄這個平日國定假日日期，供假日班排班使用
                    if d not in nurse.holiday_compensations_used:
                        nurse.holiday_compensations_used.append(d)
                        weekday_name = ['週一', '週二', '週三', '週四', '週五'][d.weekday()]
                        print(f"  記錄 {nurse.name} 本月小夜週包含平日國定假日: {d.month}/{d.day}({weekday_name})")

    print(f"  正常輪序定位點: {last_normal_assigned_name}")
    return results, last_normal_assigned_name, identity_skipped_nurses


def format_small_night_shift_result(result: Dict) -> str:
    """
    格式化小夜班結果為 Excel 顯示格式
    
    正常班：3/9-3/13
    正常輪序最後一個：3/30-4/3#
    補班：3/16-3/20
          (原3/9-3/13)  ← 括號換行
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
