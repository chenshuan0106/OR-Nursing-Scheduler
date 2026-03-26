"""
大夜班排班核心邏輯
"""
from typing import List, Dict, Tuple

from models import NurseInfo
from shift_utils import (
    should_skip_for_special_status,
    should_skip_for_p_shift,
    should_skip_for_leave,
    should_skip_for_consecutive_days,
    parse_nurse_info_from_row,
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
    判斷護理人員是否應該跳過這次大夜班
    
    返回: (是否跳過, 原因)
    
    注意：大夜班是前一天晚上 11 點上班
    例如排 3/16 的大夜，實際 3/15 晚上 11 點就要去
    所以公休到 3/15 的人不能排 3/16 開始的 cost
    """
    # 1. 檢查特殊狀態（55歲、哺乳）
    skip, reason = should_skip_for_special_status(nurse)
    if skip:
        return True, reason
    
    # 2. 檢查 P1/P2/換心
    skip, reason = should_skip_for_p_shift(nurse, target_month, target_dates)
    if skip:
        return True, reason
    
    # 3. 檢查公休/婚假（大夜班需要檢查前一天）
    skip, reason = should_skip_for_leave(nurse, target_dates, check_day_before=True)
    if skip:
        return True, reason
    
    # 4. 檢查連續7天上班
    skip, reason = should_skip_for_consecutive_days(
        nurse, target_dates, target_month, target_year, prev_month, next_month
    )
    if skip:
        return True, reason
    
    return False, ''


def schedule_night_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
) -> Tuple[List[Dict], str]:
    """
    排大夜班

    Args:
        nurses: 護理人員列表（按照名單順序）
        target_year: 目標年份
        target_month: 目標月份
        last_assigned_index: 上個月最後輪到的人的索引
        include_next_month_first: 是否包含下個月第一個 cost

    Returns:
        (排班結果列表, 正常輪序最後一個人的名字)

        注意：正常輪序最後一個人 ≠ 最後排班的人
        補班的人不算在正常輪序中，下個月應該從正常輪序最後一個人繼續往下排
    """
    # 在清空本月資料前，先記錄上月跨月排班中包含本月「平日」國定假日的日期
    # 只有週一到週五的國定假日才能補償（跳過一次假日值班，不用補班）
    for nurse in nurses:
        nurse.night_shift_dates = [
            d for d in nurse.night_shift_dates 
            if d.month != target_month or d.year != target_year
        ]
    
    # 取得本月的大夜班組別
    night_groups = get_night_shift_groups_for_month(target_year, target_month)

    # 注意：不過濾上個月排的本月cost
    # 原因：上個月排的本月第一個cost只是「預告」
    # 本月排班時會從第一個cost開始重新排，覆蓋預告
    
    # 計算下個月
    next_month = target_month + 1 if target_month < 12 else 1
    next_year = target_year if target_month < 12 else target_year + 1
    
    # 如果需要包含下個月第一個 cost
    if include_next_month_first:
        next_month_groups = get_night_shift_groups_for_month(next_year, next_month)
        
        if next_month_groups:
            # 取得本月最後一組的所有日期(使用完整日期物件)
            last_group_dates = night_groups[-1][0] if night_groups else []
            last_group_date_set = set(d.date() for d in last_group_dates)
            
            # 找到第一個與本月最後一組沒有日期重疊的組別
            for group_dates, group_type in next_month_groups:
                group_date_set = set(d.date() for d in group_dates)
                # 如果沒有重疊的日期,就加入這個組別
                if not (group_date_set & last_group_date_set):
                    night_groups.append((group_dates, group_type))
                    break
    
    # 計算前後月
    prev_month = target_month - 1 if target_month > 1 else 12

    results = []

    # 如果沒有護理人員，直接返回空列表
    if not nurses:
        return results, ''

    current_index = (last_assigned_index + 1) % len(nurses)

    # 追蹤正常輪序最後一個人（不含補班）
    last_normal_assigned_name = ''

    # 待補班名單 (因公休跳過的人)
    # 格式: [(nurse_index, original_dates), ...]
    makeup_queue = []

    # 因身分別被跳過的護理人員（用於 Excel 顯示）
    identity_skipped_nurses = set()

    # 從備註欄讀取的待補班（上個月遺留的）加入 makeup_queue
    for idx, nurse in enumerate(nurses):
        if nurse.pending_night_makeup:
            for makeup_info in nurse.pending_night_makeup:
                makeup_queue.append((idx, makeup_info['dates']))
                print(f"  從備註讀取待補班: {nurse.name} (原本 {format_dates_to_dot_string(makeup_info['dates'])})")
    
    for group_dates, group_type in night_groups:
        assigned = False
        
        # 判斷這組是否為下個月的班
        is_next_month_group = all(d.month == next_month for d in group_dates)
        
        # 1. 先檢查待補班名單
        for i, (makeup_index, original_dates) in enumerate(makeup_queue):
            nurse = nurses[makeup_index]
            
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
                    'is_makeup': True,
                    'is_next_month': is_next_month_group,
                    'original_dates': original_dates,  # 補班原始日期
                })
                
                nurse.night_shift_dates.extend(group_dates)
                makeup_queue.pop(i)
                print(f"  補班: {nurse.name} (原本 {format_dates_to_dot_string(original_dates)} 公休)")
                assigned = True
                break
        
        if assigned:
            continue
        
        # 2. 正常輪值
        attempts = 0
        while attempts < len(nurses):
            nurse = nurses[current_index]
            
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

                # 記錄正常輪序最後一個人（只記錄本月的，不記錄下月的）
                if not is_next_month_group:
                    last_normal_assigned_name = nurse.name

                current_index = (current_index + 1) % len(nurses)
                assigned = True
                break
            else:
                print(f"  跳過 {nurse.name}: {reason}")

                # 記錄因身分別（55歲/哺乳/P/換心）被跳過的護理人員
                if reason not in ('公休中', '婚假'):
                    identity_skipped_nurses.add(nurse.name)

                if reason == '公休中':
                    makeup_queue.append((current_index, group_dates))
                    print(f"    → 加入待補班名單")

                current_index = (current_index + 1) % len(nurses)
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
                'shift_type': '大夜',
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
                        print(f"  記錄 {nurse.name} 本月大夜包含平日國定假日: {d.month}/{d.day}({weekday_name})")

    print(f"  正常輪序定位點: {last_normal_assigned_name}")
    return results, last_normal_assigned_name, identity_skipped_nurses


def format_night_shift_result(result: Dict) -> str:
    """
    格式化大夜班結果為 Excel 顯示格式
    
    正常班：3/9.3/10.3/11
    正常輪序最後一個：3/30.3/31.4/1#
    補班：3/29.3/30.3/31
          (原3/22.3/23.3/24)  ← 括號換行
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
