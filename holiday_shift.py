"""
假日班排班核心邏輯

假日班欄位順序（輪值順序）：
1. 六白班 (週六白班)
2. 六小夜 (週六小夜)
3. 日大夜 (週日大夜)
4. 日白班 (週日白班)
5. 日小夜 (週日小夜)

排班規則：
- 5 個欄位共用同一個輪值索引，從上個月最後一位「往下排」
- 假日班別全部標紅色字
- 格式：日期+班別，例如 3/8白班、3/9大夜

跳過規則（不用補）：
- 移植班（換心）、公休：直接跳過不用補
- P1/P2 衝班或連7天上班：直接跳過不用補

調整規則（需要找下一個可以的班，本月內補）：
- 哺乳同仁遇假日大小夜 → 調整至白班輪次
- 55歲以上遇假日大夜 → 調整至白班或小夜輪次
- 小夜週衝突（前週日、後週六、後週日）→ 找下一個可以的班
- 平日大夜衝突 → 找下一個可以的班
- 同週末衝突 → 找下一個可以的班

特殊規則：
- 同一個週末不可以六日都值班
- 平日大小夜遇國定假日 → 可跳過假日值班一次（每次遇到都可以跳）

國定假日補償標示格式：
- 補X/X(原X/X小夜)（黑字）
"""
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from models import NurseInfo
from date_utils import (
    get_month_dates,
    is_holiday,
    is_saturday,
    get_holiday_name,
    format_date_to_string,
    get_p_shift_dates_for_month,
    would_cause_consecutive_7_days,
)
from shift_utils import should_skip_for_p_shift


# 假日班型定義（按輪值順序）
HOLIDAY_SHIFT_TYPES = [
    {'id': 'sat_day', 'name': '六白班', 'weekday': 5, 'shift': '白班', 'order': 1},
    {'id': 'sat_small_night', 'name': '六小夜', 'weekday': 5, 'shift': '小夜', 'order': 2},
    {'id': 'sun_night', 'name': '日大夜', 'weekday': 6, 'shift': '大夜', 'order': 3},
    {'id': 'sun_day', 'name': '日白班', 'weekday': 6, 'shift': '白班', 'order': 4},
    {'id': 'sun_small_night', 'name': '日小夜', 'weekday': 6, 'shift': '小夜', 'order': 5},
    {'id': 'weekday_holiday_day', 'name': '平日國假白班', 'weekday': None, 'shift': '白班', 'order': 6},
]


@dataclass
class HolidayShiftSlot:
    """假日班時段"""
    date: datetime
    shift_type_id: str  # 'sat_day', 'sat_small_night', 'sun_night', 'sun_day', 'sun_small_night', 'weekday_holiday_day'
    shift_name: str     # '白班', '小夜', '大夜'
    weekday: int        # 0-4=平日, 5=週六, 6=週日
    weekend_key: str    # 用來識別同一個週末，格式：'YYYY-WW' (年-週數)
    is_next_month: bool = False  # 是否為下個月的班
    
    @property
    def display_text(self) -> str:
        """顯示格式：日期+班別，例如 3/8白班"""
        return f"{self.date.month}/{self.date.day}{self.shift_name}"


@dataclass
class HolidayShiftResult:
    """假日班排班結果"""
    slot: HolidayShiftSlot
    nurse: NurseInfo
    column_index: int = 1  # output column index (1-based)
    is_makeup: bool = False          # 是否為補班（因衝突而延後排的）
    original_slot_text: str = ''     # 原本的班次（如果是補班的話）
    is_cross_month_makeup: bool = False  # 是否為跨月補班（!標記）
    is_holiday_compensation: bool = False  # 是否為國定假日補償跳過
    compensation_date: datetime = None     # 國定假日日期
    compensation_shift_type: str = ''      # 原本的班型（大夜/小夜）
    is_last_normal: bool = False     # 是否為正常輪序最後一個班次（加#標註）
    is_skipped: bool = False         # 是否為跳過記錄（不排班，只顯示原因）
    skip_reason: str = ''            # 跳過原因（公休、P/換心、國定假日補償等）


def get_weekend_key(date: datetime) -> str:
    """取得週末識別碼，用來判斷同一個週末"""
    iso_year, iso_week, _ = date.isocalendar()
    return f"{iso_year}-{iso_week:02d}"


def get_holiday_shift_slots_for_month(
    year: int, 
    month: int,
    include_next_month_first: bool = True,
) -> List[HolidayShiftSlot]:
    """
    取得指定月份的所有假日班時段
    
    返回按照輪值順序排列的假日班時段列表：
    每個週末依序為：六白班 → 六小夜 → 日大夜 → 日白班 → 日小夜
    平日國定假日：只有白班（按日期插入）
    """
    dates = get_month_dates(year, month)
    slots = []
    
    # 計算下個月
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    # 收集所有需要排班的日期
    # 格式: [(date, is_next_month), ...]
    all_dates_to_schedule = []
    
    # 收集本月所有週末和平日國定假日
    for date in dates:
        weekday = date.weekday()
        if weekday in [5, 6]:  # 週六或週日
            all_dates_to_schedule.append((date, False))
        elif is_holiday(date):  # 平日國定假日
            all_dates_to_schedule.append((date, False))
    
    # 如果需要包含下個月第一個週末和國定假日
    if include_next_month_first:
        next_month_dates = get_month_dates(next_year, next_month)
        added_next_weekend = False
        next_weekend_key = None
        
        for date in next_month_dates:
            weekday = date.weekday()
            
            if weekday in [5, 6]:
                current_weekend_key = get_weekend_key(date)
                
                if next_weekend_key is None:
                    next_weekend_key = current_weekend_key
                
                # 只加入第一個週末
                if current_weekend_key == next_weekend_key:
                    all_dates_to_schedule.append((date, True))
                    if weekday == 6:  # 週日結束
                        added_next_weekend = True
                elif added_next_weekend:
                    break
            
            elif is_holiday(date) and not added_next_weekend:
                # 下個月第一個週末之前的平日國定假日也要加入
                all_dates_to_schedule.append((date, True))
            
            elif added_next_weekend:
                # 週末結束後，檢查緊接著的平日國定假日（如清明連假）
                if is_holiday(date) and weekday in [0, 1, 2, 3, 4]:
                    all_dates_to_schedule.append((date, True))
                else:
                    break
    
    # 按日期排序
    all_dates_to_schedule.sort(key=lambda x: x[0])
    
    # 產生班次
    for date, is_next_month in all_dates_to_schedule:
        weekday = date.weekday()
        weekend_key = get_weekend_key(date)
        
        if weekday == 5:  # 週六
            # 六白班
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='sat_day',
                shift_name='白班',
                weekday=5,
                weekend_key=weekend_key,
                is_next_month=is_next_month,
            ))
            # 六小夜
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='sat_small_night',
                shift_name='小夜',
                weekday=5,
                weekend_key=weekend_key,
                is_next_month=is_next_month,
            ))
        
        elif weekday == 6:  # 週日
            # 日大夜
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='sun_night',
                shift_name='大夜',
                weekday=6,
                weekend_key=weekend_key,
                is_next_month=is_next_month,
            ))
            # 日白班
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='sun_day',
                shift_name='白班',
                weekday=6,
                weekend_key=weekend_key,
                is_next_month=is_next_month,
            ))
            # 日小夜
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='sun_small_night',
                shift_name='小夜',
                weekday=6,
                weekend_key=weekend_key,
                is_next_month=is_next_month,
            ))
        
        else:  # 平日國定假日
            # 只有白班
            slots.append(HolidayShiftSlot(
                date=date,
                shift_type_id='weekday_holiday_day',
                shift_name='白班',
                weekday=weekday,
                weekend_key=weekend_key,  # 用相同週的 key
                is_next_month=is_next_month,
            ))
    
    return slots


def _group_consecutive_dates(dates: List[datetime]) -> List[List[datetime]]:
    """將日期列表分組為連續的日期組"""
    if not dates:
        return []
    
    sorted_dates = sorted(dates)
    groups = []
    current_group = [sorted_dates[0]]
    
    for i in range(1, len(sorted_dates)):
        if (sorted_dates[i] - sorted_dates[i-1]).days == 1:
            current_group.append(sorted_dates[i])
        else:
            groups.append(current_group)
            current_group = [sorted_dates[i]]
    
    groups.append(current_group)
    return groups


def check_small_night_week_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
) -> Tuple[bool, str]:
    """
    檢查是否與小夜週衝突

    規則：小夜週的前面週日、後面週六、後面週日三天不可排假日值班
    """
    slot_date = slot.date.date() if hasattr(slot.date, 'date') else slot.date
    print(f"    [小夜週衝突檢查入口] {nurse.name}, slot={slot_date.month}/{slot_date.day}, "
          f"small_night_shift_dates={[f'{d.month}/{d.day}' for d in nurse.small_night_shift_dates] if nurse.small_night_shift_dates else '空'}")

    if not nurse.small_night_shift_dates:
        return False, ''

    # 取得小夜週的日期範圍（分組處理）
    for sn_dates_group in _group_consecutive_dates(nurse.small_night_shift_dates):
        if not sn_dates_group:
            continue

        first_day = min(sn_dates_group)
        last_day = max(sn_dates_group)

        # 前面週日：小夜週第一天之前最近的週日
        days_to_prev_sunday = (first_day.weekday() + 1) % 7
        if days_to_prev_sunday == 0:
            days_to_prev_sunday = 7
        prev_sunday = first_day - timedelta(days=days_to_prev_sunday)

        # 後面週六：小夜週最後一天之後最近的週六
        days_to_next_saturday = (5 - last_day.weekday()) % 7
        if days_to_next_saturday == 0:
            days_to_next_saturday = 7
        next_saturday = last_day + timedelta(days=days_to_next_saturday)

        # 後面週日：小夜週最後一天之後最近的週日
        days_to_next_sunday = (6 - last_day.weekday()) % 7
        if days_to_next_sunday == 0:
            days_to_next_sunday = 7
        next_sunday = last_day + timedelta(days=days_to_next_sunday)

        # 小夜週期間本身 + 前面週日 + 後面週六週日 都不能排
        blocked_dates = {prev_sunday.date(), next_saturday.date(), next_sunday.date()}

        # 調試日誌
        slot_date = slot.date.date() if hasattr(slot.date, 'date') else slot.date
        print(f"    [小夜週衝突檢查] {nurse.name}: 小夜週={first_day.month}/{first_day.day}-{last_day.month}/{last_day.day}, "
              f"blocked={[f'{d.month}/{d.day}' for d in sorted(blocked_dates)]}, "
              f"slot={slot_date.month}/{slot_date.day}, in_blocked={slot_date in blocked_dates}")

        if slot_date in blocked_dates:
            return True, f'小夜週({first_day.month}/{first_day.day}-{last_day.month}/{last_day.day})衝突'

    return False, ''


def check_same_weekend_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    assigned_results: List[HolidayShiftResult],
) -> Tuple[bool, str]:
    """
    檢查同一個週末是否已有值班
    規則：同一個週末不可以六日都值班
    
    注意：平日國定假日（weekday_holiday_day）不算在週末內，不會造成衝突
    """
    # 如果當前 slot 是平日國定假日，不檢查同週末衝突
    if slot.shift_type_id == 'weekday_holiday_day':
        return False, ''
    
    for result in assigned_results:
        # 跳過國定假日補償的結果（那個不算真的值班）
        if result.is_holiday_compensation:
            continue
        
        # 跳過平日國定假日（那個不算週末班）
        if result.slot.shift_type_id == 'weekday_holiday_day':
            continue
        
        if result.nurse.name == nurse.name and result.slot.weekend_key == slot.weekend_key:
            return True, f'同週末已有{result.slot.display_text}'
    
    return False, ''


def check_night_shift_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
) -> Tuple[bool, str]:
    """
    檢查是否與平日大夜班衝突

    三種情況：
    1. 大夜班是前一天晚上 11 點上班，所以檢查當天和前一天
    2. 大夜班包含週六時，同週末的週日假日班不能排（避免六日都上班）
    3. 假日班是小夜或大夜時，隔天有大夜班也衝突
       （因為隔天的大夜實際上是假日班當天晚上 11 點開始）
    """
    if not nurse.night_shift_dates:
        return False, ''

    night_date_set = {d.date() for d in nurse.night_shift_dates}

    slot_date = slot.date.date()
    day_before = (slot.date - timedelta(days=1)).date()
    day_after = (slot.date + timedelta(days=1)).date()

    # 檢查 1: 當天或前一天有大夜班
    if slot_date in night_date_set or day_before in night_date_set:
        return True, '平日大夜衝突'

    # 檢查 2: 如果假日班是週日，檢查同週末的週六是否有大夜班
    if slot.weekday == 6:  # 週日
        # 找到同週末的週六
        same_weekend_saturday = slot.date - timedelta(days=1)
        if same_weekend_saturday.date() in night_date_set:
            return True, '大夜含週六(避免六日都上班)'

    # 檢查 3: 假日小夜時，隔天有大夜班會衝突
    # 例如：3/22 假日小夜（下班約23:00）+ 3/23 大夜（實際上是 3/22 晚上 23:00 上班）→ 衝突
    # 注意：假日大夜不需要檢查，因為假日大夜是「前一天」晚上上班，和隔天大夜不衝突
    if slot.shift_name == '小夜':
        if day_after in night_date_set:
            day_after_date = slot.date + timedelta(days=1)
            return True, f'隔天({day_after_date.month}/{day_after_date.day})有大夜'

    return False, ''


def check_p_shift_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    target_year: int,
) -> Tuple[bool, str]:
    """
    Check current-month P1/P2/transplant. Skip if any applies.

    換心額外規則：前一天還在換心月，隔天也不能排班（連續性）
    """
    # 檢查當天的月份
    skip, reason = should_skip_for_p_shift(nurse, slot.date.month, [slot.date])
    if skip:
        return True, reason

    # 檢查前一天的月份（換心連續性）
    day_before = slot.date - timedelta(days=1)
    if day_before.month != slot.date.month:
        # 前一天是不同月份，檢查那個月是否換心
        month_key = f'{day_before.month}月'
        if nurse.is_transplant.get(month_key):
            return True, f'{month_key}換心(前日)'

    return False, ''


def check_leave_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
) -> Tuple[bool, str]:
    """檢查是否公休或婚假中"""
    # 檢查婚假
    if nurse.wedding_leave_dates:
        wedding_date_set = {d.date() for d in nurse.wedding_leave_dates}
        
        # 檢查當天是否婚假
        if slot.date.date() in wedding_date_set:
            return True, '婚假'
        
        # 大夜班：檢查前一天是否婚假（因為大夜是前一天晚上 11 點上班）
        if slot.shift_name == '大夜':
            day_before = (slot.date - timedelta(days=1)).date()
            if day_before in wedding_date_set:
                return True, '婚假'
    
    # 檢查公休
    if nurse.leave_dates:
        leave_date_set = {d.date() for d in nurse.leave_dates}
        
        # 檢查當天是否公休
        if slot.date.date() in leave_date_set:
            return True, '公休'
        
        # 大夜班：檢查前一天是否公休（因為大夜是前一天晚上 11 點上班）
        if slot.shift_name == '大夜':
            day_before = (slot.date - timedelta(days=1)).date()
            if day_before in leave_date_set:
                return True, '公休'
    
    return False, ''


def check_age_breastfeeding_restriction(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
) -> Tuple[bool, str, Optional[str]]:
    """
    檢查哺乳/55歲以上的班型限制
    
    Returns:
        (是否需要調整, 原因, 可調整的班型)
        可調整班型: 'day_only' (只能白班), 'day_or_small_night' (白班或小夜), None
    """
    shift_name = slot.shift_name
    
    # 哺乳：大夜和小夜都不行，只能白班
    if nurse.is_breastfeeding:
        if shift_name in ['大夜', '小夜']:
            return True, '哺乳', 'day_only'
    
    # 55歲以上：大夜不行，可以白班或小夜
    if nurse.is_age_55_plus:
        if shift_name == '大夜':
            return True, '55歲以上', 'day_or_small_night'
    
    return False, '', None


def get_holiday_compensation_for_slot(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    used_compensations: Dict[str, List[datetime]],  # {nurse_name: [已使用的國定假日]}
) -> Optional[Dict]:
    """
    檢查護理人員是否有可用的國定假日補償
    
    只檢查上個月跨月排班中包含本月國定假日的記錄
    （這些記錄在排班開始前被記錄到 holiday_compensations_used）

    返回補償資訊或None
    """
    nurse_used = used_compensations.get(nurse.name, [])
    nurse_used_dates = set(d.date() for d in nurse_used)

    eligible = []  # [(shift_type, date)]

    # 只檢查上個月跨月排班中包含本月國定假日的記錄
    for comp_date in nurse.holiday_compensations_used:
        if comp_date.date() not in nurse_used_dates:
            # 判斷是大夜還是小夜（這裡無法確定，統一標記為「平日大小夜」）
            eligible.append(('平日大小夜', comp_date))

    if not eligible:
        return None

    eligible.sort(key=lambda item: item[1])
    shift_type, date = eligible[0]
    return {
        'date': date,
        'shift_type': shift_type,
        'holiday_name': get_holiday_name(date),
    }


@dataclass
class SkipInfo:
    """跳過資訊"""
    should_skip: bool
    reason: str
    need_find_next: bool  # 是否需要找下一個可以的班（本月內補）
    adjust_type: Optional[str] = None  # 班型調整類型
    is_compensation: bool = False


def check_should_skip_holiday_shift(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    assigned_results: List[HolidayShiftResult],
    target_year: int,
    used_compensations: Dict[str, List[datetime]],
    check_compensation: bool = True,
) -> SkipInfo:
    """
    判斷護理人員是否應該跳過這次假日班
    
    Returns:
        SkipInfo 包含：
        - should_skip: 是否跳過
        - reason: 原因
        - need_find_next: 是否需要找下一個可以的班
        - adjust_type: 班型調整類型（哺乳/55歲）
    """
    # 1. 檢查公休（直接跳過，不用補）
    skip, reason = check_leave_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, False)

    # 2. 檢查 P1/P2/移植班（直接跳過，不用補）
    skip, reason = check_p_shift_conflict(nurse, slot, target_year)
    if skip:
        return SkipInfo(True, reason, False)

    # 3. 檢查小夜週衝突（需要找下一個可以的班）
    # 注意：小夜週衝突必須在國假補償之前檢查，因為如果有小夜週衝突，
    # 這個人根本不能排這個班，不應該有任何跳過記錄
    skip, reason = check_small_night_week_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, True)

    # 4. 檢查同週末衝突（需要找下一個可以的班）
    skip, reason = check_same_weekend_conflict(nurse, slot, assigned_results)
    if skip:
        return SkipInfo(True, reason, True)

    # 5. 檢查平日大夜衝突（需要找下一個可以的班）
    skip, reason = check_night_shift_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, True)

    # 6. 檢查哺乳/55歲限制（需要調整班型）
    needs_adjust, reason, adjust_type = check_age_breastfeeding_restriction(nurse, slot)
    if needs_adjust:
        return SkipInfo(True, reason, True, adjust_type)

    # 7. 檢查國定假日補償（可跳過，不用補，但每人只能跳過一次）
    # 注意：國假補償放在最後，只有在沒有其他硬性衝突時才會觸發
    if check_compensation:
        compensation = get_holiday_compensation_for_slot(nurse, slot, used_compensations)
        if compensation:
            return SkipInfo(
                True,
                f"??????({compensation['date'].month}/{compensation['date'].day}{compensation['shift_type']})",
                False,
                is_compensation=True
            )

    return SkipInfo(False, '', False)


def schedule_holiday_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
) -> Tuple[List[HolidayShiftResult], str]:
    """
    排假日班
    
    Args:
        nurses: 護理人員列表（按照名單順序）
        target_year: 目標年份
        target_month: 目標月份
        last_assigned_index: 上個月最後輪到的人的索引（5個欄位共用）
        include_next_month_first: 是否包含下個月第一個週末
    
    Returns:
        (排班結果列表, 正常輪序最後一個人的名字)
    """
    if not nurses:
        return [], ''
    
    # 取得本月所有假日班時段
    all_slots = get_holiday_shift_slots_for_month(target_year, target_month, include_next_month_first)
    print("\n=== 假日排班前 night_shift_dates 確認 ===")
    for _n in nurses:
        nd = [f"{d.month}/{d.day}" for d in sorted(_n.night_shift_dates) if d.year==target_year and d.month==target_month]
        if nd: print(f"  {_n.name}: {nd}")
    
    print(f"\n開始假日班排班...")
    print(f"共 {len(all_slots)} 個假日班時段")
    
    # 先處理跨月補班記錄（!開頭的）
    print("\n處理跨月補班記錄（!標記）...")
    cross_month_makeup_slots = {}  # {(date, slot_type): nurse_name}
    
    for nurse in nurses:
        if nurse.previous_month_cross_month_makeup_holidays:
            for makeup in nurse.previous_month_cross_month_makeup_holidays:
                date = makeup['date']
                slot_type = makeup['slot_type']
                
                # 記錄這個時段已被這個護理人員佔用
                key = (date.date(), slot_type)
                cross_month_makeup_slots[key] = nurse.name
                
                print(f"  {nurse.name}: {makeup['original_text']} (保留)")
    
    # 結果列表
    results: List[HolidayShiftResult] = []
    
    # 當前輪值索引（5個欄位共用）
    current_index = (last_assigned_index + 1) % len(nurses)
    
    # 追蹤正常輪序最後一個人（不含補班，不含下月）
    last_normal_assigned_name = ''
    
    # 已使用的國定假日補償 {nurse_name: [已使用的國定假日日期]}
    used_compensations: Dict[str, List[datetime]] = {}
    
    # 追蹤每人因國定假日補償跳過的次數（每人只能跳過一次）
    compensation_skip_count = {nurse.name: 0 for nurse in nurses}

    cycle = 0

    def advance_index(idx: int) -> int:
        nonlocal cycle
        idx = (idx + 1) % len(nurses)
        if idx == 0:
            cycle += 1
        return idx
    
    # 待補班佇列（需要找下一個可以的班）
    # 格式: [(nurse_index, original_slot, adjust_type, has_assigned_this_month, original_cycle), ...]
    # has_assigned_this_month: 這個人本月是否已經有排過假日班
    # original_cycle: 被跳過時的 cycle（用來決定回補填入哪個欄位）
    makeup_queue: List[Tuple[int, HolidayShiftSlot, Optional[str], bool, int]] = []

    # 追蹤每個人本月是否已經有排過假日班
    assigned_this_month: Set[str] = set()

    # 從上個月帶來的「X月補」純待補班 → 加入 makeup_queue 最前面，優先排班
    # 用一個虛擬 slot 代表「任何可用的班」，adjust_type=None 表示不限班型
    print("\n處理上月帶來的假日待補班（X月補）...")
    pending_dummy_slot = None  # 用第一個本月 slot 當佔位，實際排班時不限制
    for nurse in nurses:
        if nurse.pending_holiday_makeup:
            nurse_idx = nurses.index(nurse)
            for _ in nurse.pending_holiday_makeup:
                # 用 None 當 original_slot，排班邏輯裡會特別處理
                makeup_queue.append((nurse_idx, None, None, False, 0))
                print(f"  {nurse.name} 加入假日補班佇列（無指定日期）")
    
    for slot in all_slots:
        # 0. 先檢查這個時段是否被跨月補班佔用
        # 護理長已在上個月班表中手動確認這個班，直接佔位，不做任何衝突檢查
        slot_key = (slot.date.date(), slot.shift_type_id)
        if slot_key in cross_month_makeup_slots:
            nurse_name = cross_month_makeup_slots[slot_key]
            nurse = next((n for n in nurses if n.name == nurse_name), None)
            if nurse:
                result = HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=cycle + 1,
                    is_cross_month_makeup=True,
                )
                results.append(result)
                assigned_this_month.add(nurse.name)
                print(f"  跨月固定班: {nurse.name} {slot.display_text} [欄{cycle + 1}]")
                continue  # 此時段已佔用，跳過後續輪值
        
        assigned = False

        # 2. 正常輪值
        attempts = 0
        this_slot_makeup_candidates = []
        while attempts < len(nurses):
            nurse = nurses[current_index]

            # 若此人在 makeup_queue，隨輪序補班（不搶先）
            in_makeup = [(qi, e) for qi, e in enumerate(makeup_queue) if e[0] == current_index]
            if in_makeup:
                qi, (mq_idx, mq_slot, mq_adj, mq_had, mq_cyc) = in_makeup[0]
                can_assign = True
                if mq_adj == 'day_only' and slot.shift_name != '白班': can_assign = False
                if mq_adj == 'day_or_small_night' and slot.shift_name not in ['白班', '小夜']: can_assign = False
                if can_assign:
                    h1, _ = check_small_night_week_conflict(nurse, slot)
                    h2, _ = check_same_weekend_conflict(nurse, slot, results)
                    h3, _ = check_night_shift_conflict(nurse, slot)
                    if h1 or h2 or h3: can_assign = False
                if can_assign:
                    ot = mq_slot.display_text if mq_slot else '無指定'
                    results.append(HolidayShiftResult(slot=slot, nurse=nurse, column_index=mq_cyc+1, is_makeup=True, original_slot_text=ot))
                    assigned_this_month.add(nurse.name)
                    makeup_queue.pop(qi)
                    if not slot.is_next_month: last_normal_assigned_name = nurse.name
                    print(f"  ★ 隨輪序補班: {nurse.name} {slot.display_text} (原{ot})")
                    current_index = advance_index(current_index)
                    assigned = True
                    break

            # 檢查這個護士是否在這個 slot 已有記錄（包括 is_skipped 記錄）
            # 防止同一個 slot 為同一個護士創建多個記錄
            slot_already_has_record = any(
                r.nurse.name == nurse.name and
                r.slot.date.date() == slot.date.date() and
                r.slot.shift_type_id == slot.shift_type_id
                for r in results
            )
            if slot_already_has_record:
                print(f"  跳過 {nurse.name}: 此 slot 已有該護士記錄")
                current_index = advance_index(current_index)
                attempts += 1
                continue

            skip_info = check_should_skip_holiday_shift(
                nurse, slot, results, target_year, used_compensations
            )

            if not skip_info.should_skip:
                # 可以排班
                result = HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=cycle + 1,
                )
                results.append(result)
                assigned_this_month.add(nurse.name)
                
                # 記錄正常輪序最後一個人（只記錄本月的）
                if not slot.is_next_month:
                    last_normal_assigned_name = nurse.name
                
                print(f"  排班: {nurse.name} {slot.display_text}")
                
                current_index = advance_index(current_index)
                assigned = True
                break
            else:
                print(f"  跳過 {nurse.name}: {skip_info.reason}")
                
                # 處理國定假日補償
                if skip_info.is_compensation:
                    compensation = get_holiday_compensation_for_slot(nurse, slot, used_compensations)
                    if compensation:
                        # 取得假日班的日期和平日國定假日的日期
                        slot_date = slot.date.date()  # 假日班日期
                        comp_date = compensation['date'].date()  # 平日國定假日日期
                        
                        # 關鍵判斷：假日班日期必須在平日國定假日之後，才能補休假
                        if slot_date > comp_date:
                            # 假日班在國定假日之後 → 已經上過那個班，可以補休假
                            # 使用補償跳過
                            if nurse.name not in used_compensations:
                                used_compensations[nurse.name] = []
                            used_compensations[nurse.name].append(compensation['date'])
                            compensation_skip_count[nurse.name] = compensation_skip_count.get(nurse.name, 0) + 1

                            # 計算還剩幾次補償可用
                            total_compensations = len(nurse.holiday_compensations_used)
                            used_count = len(used_compensations.get(nurse.name, []))
                            remaining = total_compensations - used_count

                            print(f"  {nurse.name} 已上過{comp_date.month}/{comp_date.day}班 → 補休假跳過")
                            print(f"    -> compensation skip (已用{used_count}次, 剩餘{remaining}次)")
                            
                            # 檢查該護士是否在待補班佇列中，如果在，使用原本被跳過時的 cycle
                            use_column_index = cycle + 1  # 預設使用當前 cycle
                            makeup_entry_idx = None
                            print(f"    -> [正常輪值] {nurse.name} 檢查待補班佇列: current_index={current_index}, 當前cycle={cycle}")
                            for i, entry in enumerate(makeup_queue):
                                entry_nurse_idx, entry_slot, _, _, entry_original_cycle = entry
                                print(f"       佇列[{i}]: nurse_idx={entry_nurse_idx}, original_cycle={entry_original_cycle}")
                                if entry_nurse_idx == current_index:
                                    use_column_index = entry_original_cycle + 1
                                    makeup_entry_idx = i
                                    print(f"    -> 在待補班佇列中找到，使用原欄位 {use_column_index}")
                                    break
                            
                            print(f"    ★★★ [正常輪值] {nurse.name} 補償跳過記錄: use_column_index={use_column_index}")
                            # 創建國定假日補償跳過記錄
                            result = HolidayShiftResult(
                                slot=slot,
                                nurse=nurse,
                                column_index=use_column_index,
                                is_skipped=True,
                                skip_reason=skip_info.reason,
                                is_holiday_compensation=True,
                                compensation_date=compensation['date'],
                                compensation_shift_type=compensation['shift_type'],
                            )
                            results.append(result)

                            # 國假補償跳過「抵消」待補班：從佇列中移除該護士的 1 筆記錄
                            # （1 次國假補償只能抵消 1 次待補班）
                            if makeup_entry_idx is not None:
                                makeup_queue.pop(makeup_entry_idx)
                                print(f"    -> 國假補償抵消待補班，從佇列中移除 {nurse.name} 的 1 筆記錄")

                            current_index = advance_index(current_index)
                            attempts += 1
                            continue
                        else:
                            # 假日班在國定假日之前或當天 → 還沒上過那個班，需要檢查其他衝突後再正常排班
                            print(f"  {nurse.name} 還沒上過{comp_date.month}/{comp_date.day}班（假日班{slot_date.month}/{slot_date.day} <= 國定假日{comp_date.month}/{comp_date.day}）→ 檢查其他衝突...")
                            # 在正常排班前，需要檢查小夜週衝突和其他衝突
                            has_small_night_conflict, sn_reason = check_small_night_week_conflict(nurse, slot)
                            has_night_conflict, night_reason = check_night_shift_conflict(nurse, slot)
                            has_weekend_conflict, weekend_reason = check_same_weekend_conflict(nurse, slot, results)

                            if has_small_night_conflict:
                                print(f"  {nurse.name} 有小夜週衝突: {sn_reason}")
                                makeup_queue.append((current_index, slot, None, nurse.name in assigned_this_month, cycle))
                                print(f"    → 加入待補班佇列")
                                current_index = advance_index(current_index)
                                attempts += 1
                                continue
                            elif has_night_conflict:
                                print(f"  {nurse.name} 有平日大夜衝突: {night_reason}")
                                makeup_queue.append((current_index, slot, None, nurse.name in assigned_this_month, cycle))
                                print(f"    → 加入待補班佇列")
                                current_index = advance_index(current_index)
                                attempts += 1
                                continue
                            elif has_weekend_conflict:
                                print(f"  {nurse.name} 有同週末衝突: {weekend_reason}")
                                makeup_queue.append((current_index, slot, None, nurse.name in assigned_this_month, cycle))
                                print(f"    → 加入待補班佇列")
                                current_index = advance_index(current_index)
                                attempts += 1
                                continue

                            # 沒有其他衝突，正常排班
                            print(f"  {nurse.name} 無其他衝突，正常排班")
                            result = HolidayShiftResult(
                                slot=slot,
                                nurse=nurse,
                                column_index=cycle + 1,
                            )
                            results.append(result)
                            assigned_this_month.add(nurse.name)

                            # 記錄正常輪序最後一個人（只記錄本月的）
                            if not slot.is_next_month:
                                last_normal_assigned_name = nurse.name

                            print(f"  排班: {nurse.name} {slot.display_text}")

                            current_index = advance_index(current_index)
                            assigned = True
                            break
                
                # 如果不需要找下一個班（直接跳過，不用補），創建跳過記錄
                # 注意：國定假日補償已經在上面處理，這裡不需要再處理
                if not skip_info.need_find_next and not skip_info.is_compensation:
                    result = HolidayShiftResult(
                        slot=slot,
                        nurse=nurse,
                        column_index=cycle + 1,
                        is_skipped=True,
                        skip_reason=skip_info.reason,
                    )
                    results.append(result)
                
                if skip_info.need_find_next:
                    had_assigned = nurse.name in assigned_this_month
                    makeup_queue.append((current_index, slot, skip_info.adjust_type, had_assigned, cycle))
                    this_slot_makeup_candidates.append((current_index, nurse))
                    print(f"    → 加入待補班佇列 [nurse_idx={current_index}, 原欄位{cycle + 1}]")
                
                current_index = advance_index(current_index)
                attempts += 1
        
        if not assigned and attempts >= len(nurses):
            if this_slot_makeup_candidates:
                chosen_idx, chosen_nurse = this_slot_makeup_candidates[0]
                for qi, e in enumerate(makeup_queue):
                    if e[0] == chosen_idx and e[1] is slot: makeup_queue.pop(qi); break
                results.append(HolidayShiftResult(slot=slot, nurse=chosen_nurse, column_index=cycle+1))
                assigned_this_month.add(chosen_nurse.name)
                if not slot.is_next_month: last_normal_assigned_name = chosen_nurse.name
                print(f"  ⚠️ 強制排班: {chosen_nurse.name} {slot.display_text}")
                current_index = advance_index(chosen_idx)
            else:
                print(f"警告: 無法為 {slot.display_text} 找到可排班的人員")
    
    # 處理待補班佇列中剩餘的人
    if makeup_queue:
        print(f"\n注意: 以下人員本月找不到合適的班:")
        for nurse_idx, original_slot, adjust_type, _, original_cycle in makeup_queue:
            nurse = nurses[nurse_idx]
            adjust_desc = f", 需{adjust_type}" if adjust_type else ""
            print(f"  - {nurse.name} (原{original_slot.display_text}{adjust_desc}) [原欄位{original_cycle + 1}]")
    
    # 標記正常輪序最後一個班次（不含補班、不含下月、不含國假補償、不含跳過記錄）
    last_normal_result = None
    for result in results:
        if not result.is_makeup and not result.slot.is_next_month and not result.is_holiday_compensation and not result.is_skipped:
            last_normal_result = result
    
    if last_normal_result:
        last_normal_result.is_last_normal = True
        last_normal_assigned_name = last_normal_result.nurse.name
    
    print(f"\n假日班正常輪序定位點: {last_normal_assigned_name}")
    return results, last_normal_assigned_name


def format_holiday_shift_result(result: HolidayShiftResult, target_month: int = None) -> str:
    """
    格式化假日班結果為 Excel 顯示格式
    
    正常班：3/8白班
    正常輪序最後一個：3/15小夜#
    補班：3/15白班  （不顯示原班次）
    跨月補班：!4/3白班  （已經是上月帶過來的，保持!標記）
    國定假日補償：補2/27
                  (原3/14小夜)
    跳過記錄（公休）：公休
                      (原3/8小夜)
    跳過記錄（P/換心）：3月大P1 或 3月小P2  （不顯示原班次）
    跳過記錄（國定假日補償）：補2/27
                              (原3/7白班)
    
    Args:
        result: 排班結果
        target_month: 目標月份（用來判斷是否需要加!標記）
    """
    # 如果是跳過記錄
    if result.is_skipped:
        if result.is_holiday_compensation:
            # 國定假日補償跳過（不用補班）
            comp_date = result.compensation_date
            return f"補{comp_date.month}/{comp_date.day}\n(原{result.slot.display_text})"
        else:
            # 其他跳過原因（公休、P/換心等）
            skip_reason = result.skip_reason
            # 判斷是否為 P/換心（格式：X月大P1、X月小P1、X月大P2、X月小P2、X月換心）
            is_p_or_transplant = ('月大P1' in skip_reason or '月小P1' in skip_reason or 
                                 '月大P2' in skip_reason or '月小P2' in skip_reason or 
                                 '月P1' in skip_reason or '月P2' in skip_reason or 
                                 '月換心' in skip_reason)
            if is_p_or_transplant:
                # P/換心：只顯示原因，不顯示原班次
                # 如果是換心，提取「X月換心」部分（去掉「(前日)」等後綴）
                if '月換心' in skip_reason:
                    import re
                    match = re.search(r'(\d+月換心)', skip_reason)
                    if match:
                        return match.group(1)
                return skip_reason
            else:
                # 公休等其他原因：顯示原因和原班次
                return f"{skip_reason}\n(原{result.slot.display_text})"
    
    # 正常的國定假日補償（已排班）
    if result.is_holiday_compensation:
        comp_date = result.compensation_date
        return f"補{comp_date.month}/{comp_date.day}\n(原{result.slot.display_text})"
    
    text = result.slot.display_text
    
    # 跨月補班（!標記）：直接顯示，不用特別處理（因為下面會根據is_makeup判斷）
    # 判斷是否需要加!標記（跨月補班）
    if target_month and result.is_makeup:
        # 補班且日期在下個月
        if result.slot.date.month > target_month:
            text = f"!{text}"
    
    # 正常輪序最後一個加 # 標註
    if result.is_last_normal:
        text += '#'
    
    # 補班不顯示原班次（根據用戶需求）
    
    return text


def get_results_by_type(
    results: List[HolidayShiftResult]
) -> Dict[str, List[HolidayShiftResult]]:
    """
    將結果按班型分類
    """
    by_type = {
        'sat_day': [],
        'sat_small_night': [],
        'sun_night': [],
        'sun_day': [],
        'sun_small_night': [],
        'weekday_holiday_day': [],
    }
    
    for result in results:
        type_id = result.slot.shift_type_id
        if type_id in by_type:
            by_type[type_id].append(result)
    
    return by_type


def print_holiday_shift_preview(
    results: List[HolidayShiftResult],
    month: int,
    sheet_name: str,
):
    """預覽假日班排班結果"""
    print(f"\n===== {month}月假日班排班結果 【{sheet_name}】 =====\n")
    
    results_by_type = get_results_by_type(results)
    
    type_names = {
        'sat_day': '六白班',
        'sat_small_night': '六小夜',
        'sun_night': '日大夜',
        'sun_day': '日白班',
        'sun_small_night': '日小夜',
        'weekday_holiday_day': '平日國假白班',
    }
    
    for type_id in ['sat_day', 'sat_small_night', 'sun_night', 'sun_day', 'sun_small_night', 'weekday_holiday_day']:
        type_results = results_by_type[type_id]
        type_name = type_names[type_id]
        print(f"\n--- {type_name} ---")
        
        if not type_results:
            print("  (無排班)")
            continue
        
        for result in type_results:
            display = format_holiday_shift_result(result)
            marks = []
            
            if result.slot.is_next_month:
                marks.append('下月')
            if result.is_makeup:
                marks.append('補班')
            if result.is_holiday_compensation:
                marks.append('國假補償')
            
            mark_str = f" 【{','.join(marks)}】" if marks else ''
            print(f"  {display}: {result.nurse.name}{mark_str}")