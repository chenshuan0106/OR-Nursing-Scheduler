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
- P1/P2 待命前後保護日（前週日、後週六、後週日，依代碼實際換算的待命範圍計算）→ 找下一個可以的班
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
from dataclasses import dataclass
import re

from models import NurseInfo, ReturnCredit
from date_utils import (
    get_month_dates,
    is_holiday,
    get_holiday_name,
    get_p_shift_dates_for_month,
    would_cause_consecutive_7_days,
    get_night_week_rest_days,
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
    is_extra_makeup: bool = False    # 是否為獨立「假日額外補班」結果
    is_return_credit: bool = False           # 是否為使用「還班」credit 抵掉這次正常輪值
    return_credit_date: Optional[datetime] = None  # 還班 credit 的來源日期
    return_credit_id: Optional[str] = None         # 使用的 ReturnCredit.credit_id


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
        blocked_dates.update(
            (first_day + timedelta(days=offset)).date()
            for offset in range((last_day - first_day).days + 1)
        )

        # 調試日誌
        slot_date = slot.date.date() if hasattr(slot.date, 'date') else slot.date
        print(f"    [小夜週衝突檢查] {nurse.name}: 小夜週={first_day.month}/{first_day.day}-{last_day.month}/{last_day.day}, "
              f"blocked={[f'{d.month}/{d.day}' for d in sorted(blocked_dates)]}, "
              f"slot={slot_date.month}/{slot_date.day}, in_blocked={slot_date in blocked_dates}")

        if slot_date in blocked_dates:
            return True, f'小夜週({first_day.month}/{first_day.day}-{last_day.month}/{last_day.day})衝突'

    return False, ''


def check_p_shift_week_boundary_conflict(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    target_year: int,
) -> Tuple[bool, str]:
    """
    檢查是否落在 P1/P2 待命月份的「前後保護日」內。

    P1/P2 代表當月每週固定幾天待命一整個月（見 config.SHIFT_PATTERNS），
    範圍靠 get_p_shift_dates_for_month() 依代碼實際換算成該月所有符合的
    日期，不是只看儲存格位置。為避免緊接著正常班別造成大夜連續 6 天或
    上班連續 7 天，需要保護待命範圍前後的空檔。

    「同一週」一律以週一至週日計算。只封鎖「跨到待命月份之外、但仍屬於
    同一個週一～週日待命週」的那幾天，不額外多封一整個完整週末：
    - 月底：待命最後一天 last_day 所屬週（週一～週日）的週日
      week_end_sunday，若晚於 last_day，封鎖 last_day 隔天到
      week_end_sunday 這段（跨到下個月、但仍是同一待命週的日子）。
    - 月初：待命第一天 first_day 所屬週（週一～週日）的週一
      week_start_monday，若早於 first_day，封鎖 week_start_monday 到
      first_day 前一天這段（跨到上個月、但仍是同一待命週的日子）。
    """
    slot_date = slot.date.date() if hasattr(slot.date, 'date') else slot.date

    for month_offset in (-1, 0, 1):
        check_month = slot.date.month + month_offset
        # 用 slot.date.year（而不是外面傳進來的 target_year）當基準：slot 本身
        # 已經是正確的日期，跨年時（例如 12 月往後看到隔年 1 月）才不會被
        # target_year 誤導，重複扣一次年份。同樣的作法見 check_same_weekend_
        # conflict()。
        check_year = slot.date.year
        if check_month < 1:
            check_month = 12
            check_year -= 1
        elif check_month > 12:
            check_month = 1
            check_year += 1

        month_key = f'{check_month}月'
        shift_types = []
        p1_value = nurse.is_p1.get(month_key)
        if p1_value:
            shift_types.append(p1_value if isinstance(p1_value, str) else '大P1')
        p2_value = nurse.is_p2.get(month_key)
        if p2_value:
            shift_types.append(p2_value if isinstance(p2_value, str) else '大P2')

        for shift_type in shift_types:
            p_dates = get_p_shift_dates_for_month(check_year, check_month, shift_type)
            if not p_dates:
                continue

            first_day = min(p_dates)
            last_day = max(p_dates)

            blocked_dates = set()

            # 月底：last_day 所屬週（週一～週日）的週日，跨到下個月的部分。
            week_end_sunday = last_day + timedelta(days=(6 - last_day.weekday()))
            d = last_day + timedelta(days=1)
            while d <= week_end_sunday:
                blocked_dates.add(d.date())
                d += timedelta(days=1)

            # 月初：first_day 所屬週（週一～週日）的週一，跨到上個月的部分。
            week_start_monday = first_day - timedelta(days=first_day.weekday())
            d = week_start_monday
            while d < first_day:
                blocked_dates.add(d.date())
                d += timedelta(days=1)

            if slot_date in blocked_dates:
                return True, f'{month_key}{shift_type}待命前後保護日'

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
    # 平日國定假日不檢查「同週末」，但仍要檢查連續 7 天。
    check_weekend = slot.shift_type_id != 'weekday_holiday_day'

    for result in assigned_results if check_weekend else []:
        # 跳過記錄不是實際值班，不算同週末衝突
        if result.is_skipped:
            continue

        # 跳過國定假日補償的結果（那個不算真的值班）
        if result.is_holiday_compensation:
            continue
        
        # 跳過平日國定假日（那個不算週末班）
        if result.slot.shift_type_id == 'weekday_holiday_day':
            continue
        
        if (
            result.nurse.name == nurse.name and
            result.slot.date.date() == slot.date.date()
        ):
            return True, f'同一天已有{result.slot.display_text}'

        if result.nurse.name == nurse.name and result.slot.weekend_key == slot.weekend_key:
            return True, f'同週末已有{result.slot.display_text}'

    # 每次挑選假日候選日期時就檢查連續 7 天，不等到輸出才攔截。
    # 工作日包含：週一至週五正常上班、P1/P2/換心、大小夜，
    # 以及前面已排定的假日班。
    slot_day = slot.date.date()
    leave_days = {
        d.date()
        for d in list(nurse.leave_dates) + list(nurse.wedding_leave_dates)
    }
    # 大夜那一週的其他平日不用上白班，不算上班。
    night_rest_days = get_night_week_rest_days(nurse.night_shift_dates)
    existing_work_dates = []
    for offset in range(-6, 7):
        day = slot.date + timedelta(days=offset)
        if (
            day.weekday() < 5
            and not is_holiday(day)
            and day.date() not in leave_days
            and day.date() not in night_rest_days
        ):
            existing_work_dates.append(day)

    existing_work_dates.extend(
        d for d in list(nurse.night_shift_dates) + list(nurse.small_night_shift_dates)
        if abs((d.date() - slot_day).days) <= 6
    )
    existing_work_dates.extend(
        result.slot.date
        for result in assigned_results
        if (
            result.nurse.name == nurse.name
            and not result.is_skipped
            and not result.is_holiday_compensation
            and abs((result.slot.date.date() - slot_day).days) <= 6
        )
    )

    for month_offset in (-1, 0, 1):
        check_month = slot.date.month + month_offset
        check_year = slot.date.year
        if check_month < 1:
            check_month = 12
            check_year -= 1
        elif check_month > 12:
            check_month = 1
            check_year += 1
        month_key = f'{check_month}月'
        shift_types = []
        p1_value = nurse.is_p1.get(month_key)
        if p1_value:
            shift_types.append(p1_value if isinstance(p1_value, str) else '大P1')
        p2_value = nurse.is_p2.get(month_key)
        if p2_value:
            shift_types.append(p2_value if isinstance(p2_value, str) else '大P2')
        if nurse.is_transplant.get(month_key):
            shift_types.append('換心')
        for shift_type in shift_types:
            existing_work_dates.extend(
                d for d in get_p_shift_dates_for_month(check_year, check_month, shift_type)
                if abs((d.date() - slot_day).days) <= 6
            )

    if would_cause_consecutive_7_days(existing_work_dates, [slot.date]):
        return True, '該日期會造成連續上班 7 天'
    
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
    current_month_key = f'{slot.date.month}月'
    if getattr(nurse, 'is_standby', {}).get(current_month_key):
        return True, f'{current_month_key}待命'

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

    p_shift_dates = []
    for month_offset in [-1, 0, 1]:
        check_month = slot.date.month + month_offset
        check_year = target_year
        if check_month < 1:
            check_month = 12
            check_year -= 1
        elif check_month > 12:
            check_month = 1
            check_year += 1

        month_key = f'{check_month}月'
        shift_types = []
        if getattr(nurse, 'is_standby', {}).get(month_key):
            if month_offset == 0:
                return True, f'{month_key}待命'
            continue
        if nurse.is_transplant.get(month_key):
            shift_types.append('換心')
        p1_value = nurse.is_p1.get(month_key)
        if p1_value:
            shift_types.append(p1_value if isinstance(p1_value, str) else '大P1')
        p2_value = nurse.is_p2.get(month_key)
        if p2_value:
            shift_types.append(p2_value if isinstance(p2_value, str) else '大P2')

        for shift_type in shift_types:
            dates = get_p_shift_dates_for_month(check_year, check_month, shift_type)
            if any(d.date() == slot.date.date() for d in dates):
                return True, f'{month_key}{shift_type}衝班'
            p_shift_dates.extend(dates)

    # 連續上班必須把原本週一至週五的正常工作日算入。
    # 例如上月最後一週大 P1（週日起班），加上週一至週五
    # 正常上班，若再排週六假日班就會連續 7 天。
    leave_days = {
        d.date()
        for d in list(nurse.leave_dates) + list(nurse.wedding_leave_dates)
    }
    night_rest_days = get_night_week_rest_days(nurse.night_shift_dates)
    surrounding_work_dates = []
    for offset in range(-6, 7):
        day = slot.date + timedelta(days=offset)
        if (
            day.weekday() < 5
            and not is_holiday(day)
            and day.date() not in leave_days
            and day.date() not in night_rest_days
        ):
            surrounding_work_dates.append(day)
    surrounding_work_dates.extend(
        d for d in list(nurse.night_shift_dates) + list(nurse.small_night_shift_dates)
        if abs((d.date() - slot.date.date()).days) <= 6
    )

    if would_cause_consecutive_7_days(
        p_shift_dates + surrounding_work_dates,
        [slot.date],
    ):
        return True, '連續上班達 7 天'

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


@dataclass
class SkipInfo:
    """跳過資訊"""
    should_skip: bool
    reason: str
    need_find_next: bool  # 是否需要找下一個可以的班（本月內補）
    adjust_type: Optional[str] = None  # 班型調整類型
    is_compensation: bool = False  # 保留欄位供舊資料相容；新邏輯不再由此自動觸發國假補償


def check_should_skip_holiday_shift(
    nurse: NurseInfo,
    slot: HolidayShiftSlot,
    assigned_results: List[HolidayShiftResult],
    target_year: int,
    used_compensations: Dict[str, List[datetime]],
) -> SkipInfo:
    """
    判斷護理人員是否應該跳過這次假日班

    注意：國定假日補償（還班）已改為必須經過 Streamlit UI 核准的
    ReturnCredit 機制（見 get_available_return_credit），不再由這裡自動
    偵測並跳過。`used_compensations` 參數僅為維持既有呼叫端相容而保留，
    這裡不再讀取它。

    Returns:
        SkipInfo 包含：
        - should_skip: 是否跳過
        - reason: 原因
        - need_find_next: 是否需要找下一個可以的班
        - adjust_type: 班型調整類型（哺乳/55歲）
    """
    # 1. 檢查 P1/P2/移植班（直接跳過，不用補）
    skip, reason = check_p_shift_conflict(nurse, slot, target_year)
    if skip:
        # P1/P2/換心當日衝班屬於身分跳過；但「連續 7 天」
        # 只是這個日期不能上，必須保留欠班並繼續找後面的班。
        need_find_next = '連續' in reason or '連七天' in reason
        return SkipInfo(True, reason, need_find_next)

    # 2. 檢查公休（直接跳過，不用補）
    skip, reason = check_leave_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, False)

    # 3. 檢查小夜週衝突（需要找下一個可以的班）
    skip, reason = check_small_night_week_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, True)

    # 4. 檢查 P1/P2 待命前後保護日衝突（需要找下一個可以的班）
    skip, reason = check_p_shift_week_boundary_conflict(nurse, slot, target_year)
    if skip:
        return SkipInfo(True, reason, True)

    # 5. 檢查同週末衝突（需要找下一個可以的班）
    skip, reason = check_same_weekend_conflict(nurse, slot, assigned_results)
    if skip:
        return SkipInfo(True, reason, True)

    # 6. 檢查平日大夜衝突（需要找下一個可以的班）
    skip, reason = check_night_shift_conflict(nurse, slot)
    if skip:
        return SkipInfo(True, reason, True)

    # 7. 檢查哺乳/55歲限制（需要調整班型）
    needs_adjust, reason, adjust_type = check_age_breastfeeding_restriction(nurse, slot)
    if needs_adjust:
        return SkipInfo(True, reason, True, adjust_type)

    return SkipInfo(False, '', False)


def is_return_credit_eligible(credit: ReturnCredit, slot: HolidayShiftSlot) -> bool:
    """
    判斷一筆還班 credit 這次是否可以使用。

    共同條件：
    - 一定要經過核准（approved）
    - 一定還沒被用過（not consumed）
    - 不消耗下個月預排的 slot（is_next_month）

    日期條件依來源不同：
    - current_month_auto：來源國假必須「已經發生」，也就是 slot 日期一定要
      晚於 source_date（不含當天）。這種 credit 是本月大夜/小夜真的排到平日
      國定假日才產生的，國假還沒到就不能提前用掉。
    - carry_over_detected／manual：若沒有指定生效日期（use_after_date is
      None），任何時候都符合日期條件，代表「還接下來第一個符合條件的正常
      班」；若有指定生效日期，只有 slot 日期「晚於」該日期才符合（不含當
      天，用 > 不是 >=）。manual 是使用者人工確認的 override，不受「來源
      國假必須已發生」限制。
    """
    if not credit.approved:
        return False
    if credit.consumed:
        return False
    if slot.is_next_month:
        return False

    slot_day = slot.date.date() if hasattr(slot.date, 'date') else slot.date

    if credit.source == 'current_month_auto':
        source_day = credit.source_date.date() if hasattr(credit.source_date, 'date') else credit.source_date
        return slot_day > source_day

    if credit.use_after_date is None:
        return True
    return slot_day > credit.use_after_date


def get_available_return_credit(
    nurse_name: str,
    sheet_name: str,
    slot: HolidayShiftSlot,
    approved_return_credits: Optional[List[ReturnCredit]],
) -> Optional[ReturnCredit]:
    """
    找出這個人這次可以使用的還班 credit（若有多筆符合資格，來源日期最舊的優先）。

    只考慮同一個 sheet_name、同一個人、且通過 is_return_credit_eligible 的 credit。
    """
    if not approved_return_credits:
        return None

    candidates = [
        credit
        for credit in approved_return_credits
        if credit.sheet_name == sheet_name
        and credit.nurse_name == nurse_name
        and is_return_credit_eligible(credit, slot)
    ]

    if not candidates:
        return None

    candidates.sort(key=lambda c: c.source_date)
    return candidates[0]


def schedule_holiday_shifts(
    nurses: List[NurseInfo],
    target_year: int,
    target_month: int,
    last_assigned_index: int = -1,
    include_next_month_first: bool = True,
    regular_start_index: int = None,
    extra_makeup_entries: Optional[List[Dict]] = None,
    sheet_name: str = '',
    approved_return_credits: Optional[List[ReturnCredit]] = None,
) -> Tuple[List[HolidayShiftResult], str]:
    """
    排假日班

    Args:
        nurses: 護理人員列表（按照名單順序）
        target_year: 目標年份
        target_month: 目標月份
        last_assigned_index: 上個月最後輪到的人的索引（5個欄位共用）
        include_next_month_first: 是否包含下個月第一個週末
        sheet_name: 頁籤名稱，用來過濾 approved_return_credits（避免跨頁籤誤用同名人員的還班資格）
        approved_return_credits: 這次排班已核准可用的還班資格清單（未核准的候選不要傳進來）

    Returns:
        (排班結果列表, 正常輪序最後一個人的名字)
    """
    if not nurses:
        return [], ''

    # 本月新產生的國假補償（current_month_auto）：若某人本月大夜/小夜「真的排到」
    # 平日國定假日，這裡直接自動建立一筆 approved=True 的 credit，不需要 UI 核准。
    # 跟「上月 Excel 帶進來的舊資格」（carry_over_detected，走 approved_return_credits，
    # 需要 UI 核准）是兩件事：
    #   - night_shift_holiday_compensations / small_night_shift_holiday_compensations
    #     只會被 Excel 解析（上月結轉）寫入，代表舊資格；
    #   - nurse.holiday_compensations_used 扣掉上面兩個 sub-list 之後剩下的日期，
    #     就是本月大夜/小夜排班「這次」新產生的補償。
    # 若使用者已經手動／核准了同一人同一天的 credit，就不再重複建立一筆
    # current_month_auto（同一筆資格只能存在一次，見規則 21）。
    already_covered = {
        (credit.sheet_name, credit.nurse_name, credit.source_date.date())
        for credit in (approved_return_credits or [])
    }
    current_month_auto_credits: List[ReturnCredit] = []
    for nurse in nurses:
        # 上月 Excel 跨月預排、但國假落在本月（例如小夜週 12/28-1/1(1/1)）的日期，
        # 不會出現在上月結轉候選（候選只收上個月日期），視同本月國假自動建立 credit。
        carry_over_dates = {
            d.date() for d in (
                list(nurse.night_shift_holiday_compensations)
                + list(nurse.small_night_shift_holiday_compensations)
            )
            if not (d.year == target_year and d.month == target_month)
        }
        for comp_date in nurse.holiday_compensations_used:
            comp_day = comp_date.date()
            if comp_day in carry_over_dates:
                continue  # 這是上月結轉的，不是本月新產生的
            if (sheet_name, nurse.name, comp_day) in already_covered:
                continue  # 使用者已經手動/核准過同一天的 credit，不重複建立
            credit_id = f"{sheet_name}|{nurse.name}|{comp_day.isoformat()}|current_month_auto"
            current_month_auto_credits.append(ReturnCredit(
                credit_id=credit_id,
                sheet_name=sheet_name,
                nurse_name=nurse.name,
                source_date=comp_date,
                source_type='本月國假',
                source='current_month_auto',
                approved=True,
            ))
            print(
                f"[本月國假補償產生] nurse={nurse.name} source_date={comp_day} "
                f"source_type=本月國假 approved=True"
            )

    all_return_credits: List[ReturnCredit] = list(approved_return_credits or []) + current_month_auto_credits

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
    deferred_cross_month_slots = {}  # {(date, slot_type): nurse_name}
    deferred_small_night_queue: List[int] = []
    pending_notice_count = sum(
        len(getattr(nurse, 'holiday_pending_notice', []))
        for nurse in nurses
    )

    nurse_index_by_name = {nurse.name: idx for idx, nurse in enumerate(nurses)}
    extra_makeup_queue = []
    for order, entry in enumerate(extra_makeup_entries or [], 1):
        if entry.get('shift_type') != '假日班':
            continue
        nurse_idx = nurse_index_by_name.get(entry.get('nurse_name'))
        if nurse_idx is not None:
            extra_makeup_queue.append((order, nurse_idx, entry))
    
    for nurse in nurses:
        if nurse.previous_month_cross_month_makeup_holidays:
            for makeup in nurse.previous_month_cross_month_makeup_holidays:
                date = makeup['date']
                slot_type = makeup['slot_type']
                
                # 記錄這個時段已被這個護理人員佔用
                key = (date.date(), slot_type)
                if pending_notice_count >= 2 and slot_type == 'sat_small_night':
                    deferred_cross_month_slots[key] = nurse.name
                elif pending_notice_count >= 2 and slot_type == 'sun_night':
                    deferred_small_night_queue.append(nurses.index(nurse))
                else:
                    cross_month_makeup_slots[key] = nurse.name
                
                print(f"  {nurse.name}: {makeup['original_text']} (保留)")
    
    # 結果列表
    results: List[HolidayShiftResult] = []

    # 上月最後假日欄中保留下來的 P/換心/原班記錄，先佔住本月對應欄位。
    first_current_slot = next((slot for slot in all_slots if not slot.is_next_month), all_slots[0])
    for nurse in nurses:
        for record in getattr(nurse, 'previous_month_holiday_skip_records', []):
            column_index = int(record.get('column_index') or 1)
            original_text = record.get('original_text') or ''
            results.append(HolidayShiftResult(
                slot=first_current_slot,
                nurse=nurse,
                column_index=column_index,
                is_skipped=True,
                skip_reason=original_text,
                original_slot_text='跨月欄位保留',
            ))
            print(f"  跨月跳過保留: {nurse.name} {original_text} [欄{column_index}]")
    
    # 當前輪值索引（5個欄位共用）
    current_index = (
        regular_start_index % len(nurses)
        if regular_start_index is not None and regular_start_index >= 0
        else (last_assigned_index + 1) % len(nurses)
    )
    
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

    def has_cross_month_record_in_column(nurse_name: str, column_index: int) -> bool:
        return any(
            result.nurse.name == nurse_name and
            result.column_index == column_index and
            result.is_cross_month_makeup
            for result in results
        )

    # 待補班佇列（需要找下一個可以的班）
    # 格式: [(nurse_index, original_slot, adjust_type, has_assigned_this_month, original_cycle), ...]
    # has_assigned_this_month: 這個人本月是否已經有排過假日班
    # original_cycle: 被跳過時的 cycle（用來決定回補填入哪個欄位）
    makeup_queue: List[Tuple[int, HolidayShiftSlot, Optional[str], bool, int]] = []
    deferred_priority_queue: List[int] = []

    # 追蹤每個人本月是否已經有排過假日班
    assigned_this_month: Set[str] = set()

    # 假日欄位配置：第 1 欄（Excel 上的 F）固定保留給「上月結轉」的東西
    # （跨月欄位保留、!固定班延後、還班等），不屬於本月自己的輪序。
    # 本月「自己的」正常輪值／補班回補一律從第 2 欄開始算，
    # 也就是 cycle 0 對應第 2 欄、cycle 1 對應第 3 欄……
    # 這個常數只用來把 cycle 換算成 Excel 欄位，不影響 cycle 本身怎麼推進。
    # 上月已預排本月的固定班（例如 1/1白班）寫在哪一欄，本月就從那一欄接著輪。
    cross_month_columns = [
        int(makeup.get('column_index') or 0)
        for nurse in nurses
        for makeup in nurse.previous_month_cross_month_makeup_holidays
        if makeup.get('column_index')
    ]
    ROTATION_COLUMN_OFFSET = min(cross_month_columns) if cross_month_columns else 2

    def has_same_day_assignment(nurse: NurseInfo, slot: HolidayShiftSlot) -> bool:
        return any(
            result.nurse.name == nurse.name
            and not result.is_skipped
            and result.slot.date.date() == slot.date.date()
            for result in results
        )

    def can_assign_makeup(nurse: NurseInfo, slot: HolidayShiftSlot) -> bool:
        if has_same_day_assignment(nurse, slot):
            print(f"  補班暫不排 {nurse.name}: 同一天已有假日班")
            return False

        skip_info = check_should_skip_holiday_shift(
            nurse, slot, results, target_year, used_compensations
        )
        if skip_info.should_skip:
            print(f"  補班暫不排 {nurse.name}: {skip_info.reason}")
            return False

        has_small_night_conflict, sn_reason = check_small_night_week_conflict(nurse, slot)
        if has_small_night_conflict:
            print(f"  補班暫不排 {nurse.name}: {sn_reason}")
            return False

        has_weekend_conflict, weekend_reason = check_same_weekend_conflict(nurse, slot, results)
        if has_weekend_conflict:
            print(f"  補班暫不排 {nurse.name}: {weekend_reason}")
            return False

        has_night_conflict, night_reason = check_night_shift_conflict(nurse, slot)
        if has_night_conflict:
            print(f"  補班暫不排 {nurse.name}: {night_reason}")
            return False

        return True

    # 從上個月帶來的「X月補」純待補班 → 固定跨月補班處理完後優先排班
    print("\n處理上月帶來的假日待補班（X月補）...")
    pending_priority_queue: List[Tuple[int, int, str, Optional[datetime], Optional[str], Optional[str], bool]] = []
    for nurse in nurses:
        pending_records = getattr(nurse, 'pending_holiday_makeup', None) or getattr(nurse, 'holiday_pending_notice', [])
        if pending_records:
            nurse_idx = nurses.index(nurse)
            for record in pending_records:
                column_index = int(record.get('column_index') or 1)
                original_text = record.get('original_text') or f'{target_month}月補'
                target_date = record.get('target_date')
                target_slot_type = record.get('target_slot_type')
                target_shift_type = record.get('target_shift_type')
                is_manual = bool(record.get('is_manual'))
                pending_priority_queue.append((nurse_idx, column_index, original_text, target_date, target_slot_type, target_shift_type, is_manual))
                print(f"  {nurse.name} 加入假日補班優先佇列（{original_text}，欄{column_index}）")

    manual_slots = []
    for _nurse_idx, _column_index, _original_text, target_date, _target_slot_type, target_shift_type, is_manual in pending_priority_queue:
        if not (is_manual and target_date and target_shift_type):
            continue
        target_day = target_date.date() if hasattr(target_date, 'date') else target_date
        has_slot = any(
            (slot.date.date() if hasattr(slot.date, 'date') else slot.date) == target_day
            and slot.shift_name == target_shift_type
            for slot in all_slots
        )
        if has_slot:
            continue
        manual_slots.append(HolidayShiftSlot(
            date=target_date,
            shift_type_id=f"manual_{target_shift_type}",
            shift_name=target_shift_type,
            weekday=target_date.weekday(),
            weekend_key=get_weekend_key(target_date),
            is_next_month=target_date.month != target_month,
        ))
    if manual_slots:
        slot_order = {item['id']: item['order'] for item in HOLIDAY_SHIFT_TYPES}
        all_slots.extend(manual_slots)
        all_slots.sort(key=lambda slot: (slot.date, slot_order.get(slot.shift_type_id, 99)))
    
    for slot in all_slots:

        # 0. 先檢查這個時段是否被跨月補班佔用
        # 護理長已在上個月班表中手動確認這個班，直接佔位，不做任何衝突檢查
        slot_key = (slot.date.date(), slot.shift_type_id)
        if slot_key in deferred_cross_month_slots:
            nurse_name = deferred_cross_month_slots[slot_key]
            nurse_idx = next((idx for idx, nurse in enumerate(nurses) if nurse.name == nurse_name), None)
            if nurse_idx is not None:
                deferred_priority_queue.append(nurse_idx)
                print(f"  延後跨月固定班: {nurse_name} {slot.display_text}")
            continue

        if slot_key in cross_month_makeup_slots:
            nurse_name = cross_month_makeup_slots[slot_key]
            nurse = next((n for n in nurses if n.name == nurse_name), None)
            if nurse:
                if has_same_day_assignment(nurse, slot):
                    print(f"  跳過跨月固定班 {nurse.name} {slot.display_text}: 同一天已有假日班")
                    nurse = None
            if nurse:
                result = HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=cycle + ROTATION_COLUMN_OFFSET,
                    is_cross_month_makeup=True,
                )
                results.append(result)
                print(f"  跨月固定班: {nurse.name} {slot.display_text} [欄{cycle + ROTATION_COLUMN_OFFSET}]")
                continue  # 此時段已佔用，跳過後續輪值
        
        assigned = False

        # 1. 假日額外補班：在假日待補與一般輪序前優先搶可排時段。
        if extra_makeup_queue and slot.date.year == target_year and slot.date.month == target_month:
            for queue_idx, (order, nurse_idx, entry) in enumerate(list(extra_makeup_queue)):
                nurse = nurses[nurse_idx]
                if not can_assign_makeup(nurse, slot):
                    continue
                results.append(HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=1,
                    is_makeup=True,
                    original_slot_text=entry.get('source') or '假日額外補班',
                    is_extra_makeup=True,
                ))
                extra_makeup_queue.pop(queue_idx)
                print(f"  ★ 假日額外補班: {nurse.name} {slot.display_text} (順位 {order})")
                assigned = True
                break
        if assigned:
            continue

        # 2. 上月帶來的「X月補」優先補班：固定跨月日期班排完後，先找第一個不衝突的格子
        if pending_priority_queue:
            for pending_idx, (nurse_idx, column_index, original_text, target_date, target_slot_type, target_shift_type, is_manual) in enumerate(list(pending_priority_queue)):
                if target_date:
                    if slot.date.date() != target_date.date():
                        continue
                    if target_slot_type and slot.shift_type_id != target_slot_type:
                        continue
                    if target_shift_type and slot.shift_name != target_shift_type:
                        continue
                nurse = nurses[nurse_idx]
                if has_same_day_assignment(nurse, slot):
                    print(f"  跳過 {nurse.name} {slot.display_text}: 同一天已有假日班")
                    continue
                if is_manual or can_assign_makeup(nurse, slot):
                    # 這個補班格子本來會兌現，先看看有沒有已生效的還班 credit 可以直接
                    # 蓋掉這次補班——蓋掉之後這筆欠班義務視為已了結，不會再回到佇列裡找下一格。
                    used_credit = get_available_return_credit(
                        nurse.name, sheet_name, slot, all_return_credits
                    )
                    if used_credit:
                        # 還班蓋掉的是這筆補班義務本身，不是這個 slot──這班還是需要
                        # 有人真的上班，所以不能 break、不能設 assigned=True，
                        # 要 continue 檢查佇列裡下一筆，讓這個 slot 繼續往下找人。
                        used_credit.consumed = True
                        results.append(HolidayShiftResult(
                            slot=slot,
                            nurse=nurse,
                            column_index=column_index,
                            is_skipped=True,
                            skip_reason=f"還班({used_credit.source_date.month}/{used_credit.source_date.day})",
                            is_return_credit=True,
                            return_credit_date=used_credit.source_date,
                            return_credit_id=used_credit.credit_id,
                        ))
                        pending_priority_queue.pop(pending_idx)
                        tag = '本月國假自動補償' if used_credit.source == 'current_month_auto' else '還班蓋過補班'
                        print(
                            f"  [{tag}] nurse={nurse.name} credit={used_credit.source_date.date()} "
                            f"original_slot={slot.display_text}（原欠班：{original_text}，此班仍需有人真正上班，繼續往下找）[欄{column_index}]"
                        )
                        continue
                    results.append(HolidayShiftResult(
                        slot=slot,
                        nurse=nurse,
                        column_index=column_index,
                        is_makeup=True,
                        original_slot_text=original_text,
                    ))
                    pending_priority_queue.pop(pending_idx)
                    force_text = "手動指定" if is_manual else "優先補班"
                    print(f"  ★ {force_text}: {nurse.name} {slot.display_text} (原{original_text}) [欄{column_index}]")
                    assigned = True
                    break
            if assigned:
                continue

        # 3. 正常輪值
        attempts = 0
        this_slot_makeup_candidates = []
        if deferred_priority_queue and slot.date.day > 7:
            deferred_idx = deferred_priority_queue[0]
            nurse = nurses[deferred_idx]
            has_small_night_conflict, _ = check_small_night_week_conflict(nurse, slot)
            has_weekend_conflict, _ = check_same_weekend_conflict(nurse, slot, results)
            has_night_conflict, _ = check_night_shift_conflict(nurse, slot)
            if not (has_small_night_conflict or has_weekend_conflict or has_night_conflict):
                results.append(HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=1,
                    is_makeup=True,
                    original_slot_text='跨月固定班延後'
                ))
                deferred_priority_queue.pop(0)
                print(f"  ★ 延後跨月補班: {nurse.name} {slot.display_text}")
                assigned = True
                continue

        if pending_notice_count >= 2 and deferred_small_night_queue and slot.shift_name == '小夜' and slot.date.day > 7:
            deferred_idx = deferred_small_night_queue[0]
            nurse = nurses[deferred_idx]
            has_small_night_conflict, _ = check_small_night_week_conflict(nurse, slot)
            has_weekend_conflict, _ = check_same_weekend_conflict(nurse, slot, results)
            has_night_conflict, _ = check_night_shift_conflict(nurse, slot)
            if not (has_small_night_conflict or has_weekend_conflict or has_night_conflict):
                results.append(HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=1,
                    is_makeup=True,
                    original_slot_text='跨月大夜改補小夜'
                ))
                deferred_small_night_queue.pop(0)
                print(f"  ★ 跨月大夜改補小夜: {nurse.name} {slot.display_text}")
                assigned = True
                continue

        for queued_idx, (mq_idx, mq_slot, mq_adj, _mq_had, mq_cyc) in enumerate(list(makeup_queue)):
            nurse = nurses[mq_idx]
            if mq_adj == 'day_only' and slot.shift_name != '白班':
                continue
            if mq_adj == 'day_or_small_night' and slot.shift_name not in ['白班', '小夜']:
                continue
            if has_same_day_assignment(nurse, slot):
                continue

            skip_info = check_should_skip_holiday_shift(
                nurse, slot, results, target_year, used_compensations
            )
            if skip_info.should_skip:
                continue

            has_small_night_conflict, _ = check_small_night_week_conflict(nurse, slot)
            has_weekend_conflict, _ = check_same_weekend_conflict(nurse, slot, results)
            has_night_conflict, _ = check_night_shift_conflict(nurse, slot)
            if has_small_night_conflict or has_weekend_conflict or has_night_conflict:
                continue

            original_text = mq_slot.display_text if mq_slot else '輪序暫跳'
            used_credit = get_available_return_credit(nurse.name, sheet_name, slot, all_return_credits)
            if used_credit:
                # 還班蓋掉的是這筆補班義務本身，不是這個 slot──這班還是需要有人真的
                # 上班，所以不能 break、不能設 assigned=True，continue 檢查佇列裡
                # 下一筆，讓這個 slot 繼續往下找人。
                used_credit.consumed = True
                results.append(HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=mq_cyc + ROTATION_COLUMN_OFFSET,
                    is_skipped=True,
                    skip_reason=f"還班({used_credit.source_date.month}/{used_credit.source_date.day})",
                    is_return_credit=True,
                    return_credit_date=used_credit.source_date,
                    return_credit_id=used_credit.credit_id,
                ))
                makeup_queue.pop(queued_idx)
                tag = '本月國假自動補償' if used_credit.source == 'current_month_auto' else '還班蓋過補班'
                print(
                    f"  [{tag}] nurse={nurse.name} credit={used_credit.source_date.date()} "
                    f"original_slot={slot.display_text}（原欠班：{original_text}，此班仍需有人真正上班，繼續往下找）"
                )
                continue
            results.append(HolidayShiftResult(
                slot=slot,
                nurse=nurse,
                column_index=mq_cyc + ROTATION_COLUMN_OFFSET,
                is_makeup=True,
                original_slot_text=original_text,
            ))
            makeup_queue.pop(queued_idx)
            print(f"  輪序回補: {nurse.name} {slot.display_text} (原 {original_text})")
            assigned = True
            break

        if assigned:
            continue

        while attempts < len(nurses):
            nurse = nurses[current_index]

            # 上月已預排本月的跨月固定班（例如 !1/1白班）就算這一輪輪過，不再排同欄。
            if has_cross_month_record_in_column(nurse.name, cycle + ROTATION_COLUMN_OFFSET):
                print(f"  跳過 {nurse.name}: 第{cycle + ROTATION_COLUMN_OFFSET}欄已有跨月固定班")
                current_index = advance_index(current_index)
                attempts += 1
                continue

            if any(
                r.nurse.name == nurse.name
                and r.column_index == cycle + ROTATION_COLUMN_OFFSET
                and r.is_skipped
                and r.original_slot_text == '跨月欄位保留'
                for r in results
            ):
                print(f"  跳過 {nurse.name}: 第{cycle + ROTATION_COLUMN_OFFSET}欄已有跨月跳過記錄")
                current_index = advance_index(current_index)
                attempts += 1
                continue

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
                    used_credit = get_available_return_credit(nurse.name, sheet_name, slot, all_return_credits)
                    if used_credit:
                        # 還班蓋掉的是這個人的補班義務，不是這個 slot 本身──這班還是需要
                        # 有人真的上班，所以不能 break，要當跳過處理，繼續往下找人。
                        used_credit.consumed = True
                        results.append(HolidayShiftResult(
                            slot=slot,
                            nurse=nurse,
                            column_index=mq_cyc + ROTATION_COLUMN_OFFSET,
                            is_skipped=True,
                            skip_reason=f"還班({used_credit.source_date.month}/{used_credit.source_date.day})",
                            is_return_credit=True,
                            return_credit_date=used_credit.source_date,
                            return_credit_id=used_credit.credit_id,
                        ))
                        makeup_queue.pop(qi)
                        tag = '本月國假自動補償' if used_credit.source == 'current_month_auto' else '還班蓋過補班'
                        print(
                            f"  [{tag}] nurse={nurse.name} credit={used_credit.source_date.date()} "
                            f"original_slot={slot.display_text}（原欠班：{ot}，此班仍需有人真正上班，繼續往下找）"
                        )
                        current_index = advance_index(current_index)
                        attempts += 1
                        continue
                    else:
                        results.append(HolidayShiftResult(slot=slot, nurse=nurse, column_index=mq_cyc+ROTATION_COLUMN_OFFSET, is_makeup=True, original_slot_text=ot))
                        makeup_queue.pop(qi)
                        print(f"  ★ 隨輪序補班: {nurse.name} {slot.display_text} (原{ot})")
                        assigned = True
                        break

            # 檢查這個護士是否在這個 slot 已有記錄（包括 is_skipped 記錄）
            # 防止同一個 slot 為同一個護士創建多個記錄
            # 還班記錄不算：還班抵掉的是補班欠班，這個人本輪仍可正常上這班。
            slot_already_has_record = any(
                r.nurse.name == nurse.name and
                r.slot.date.date() == slot.date.date() and
                r.slot.shift_type_id == slot.shift_type_id and
                not r.is_return_credit
                for r in results
            )
            if slot_already_has_record:
                print(f"  跳過 {nurse.name}: 此 slot 已有該護士記錄")
                current_index = advance_index(current_index)
                attempts += 1
                continue

            if has_same_day_assignment(nurse, slot):
                print(f"  跳過 {nurse.name}: 補班欄同日占用")
                makeup_queue.append((current_index, slot, None, False, cycle))
                print("    加入輪序暫跳佇列")
                current_index = advance_index(current_index)
                attempts += 1
                continue

            skip_info = check_should_skip_holiday_shift(
                nurse, slot, results, target_year, used_compensations
            )

            if not skip_info.should_skip:
                has_small_night_conflict, sn_reason = check_small_night_week_conflict(nurse, slot)
                has_weekend_conflict, weekend_reason = check_same_weekend_conflict(nurse, slot, results)
                has_night_conflict, night_reason = check_night_shift_conflict(nurse, slot)
                if has_small_night_conflict or has_weekend_conflict or has_night_conflict:
                    reason = sn_reason or weekend_reason or night_reason
                    print(f"  跳過 {nurse.name}: {reason}")
                    makeup_queue.append((current_index, slot, None, False, cycle))
                    print("    加入輪序暫跳佇列")
                    current_index = advance_index(current_index)
                    attempts += 1
                    continue

                # 可以排班：這個 slot 本來就可以正常排這個人。
                # 在真正排下去之前，先看看他有沒有已生效的還班/國假補償 credit
                # 可以抵掉這次正常輪值（current_month_auto 不需要核准就能用；
                # carry_over_detected 一定要先被 UI 核准過才會出現在這份清單裡）。
                used_credit = get_available_return_credit(
                    nurse.name, sheet_name, slot, all_return_credits
                )

                if used_credit:
                    # 還班蓋掉的是「這個人」這次的正常輪值，不是這個 slot 本身──
                    # 這班還是需要有人真的上班，所以這裡要當成「跳過」處理：
                    # 只前進 current_index、attempts，continue 讓輪序找下一個人來真正
                    # 上這班，不能 break（break 會讓這個 slot 直接被當成已經排完，沒人上）。
                    used_credit.consumed = True
                    result = HolidayShiftResult(
                        slot=slot,
                        nurse=nurse,
                        column_index=cycle + ROTATION_COLUMN_OFFSET,
                        is_skipped=True,  # 這一格不是真的上班，跟舊版國假補償一樣不佔用該班別的排他性
                        skip_reason=f"還班({used_credit.source_date.month}/{used_credit.source_date.day})",
                        is_return_credit=True,
                        return_credit_date=used_credit.source_date,
                        return_credit_id=used_credit.credit_id,
                    )
                    results.append(result)
                    if used_credit.source == 'current_month_auto':
                        print(
                            f"  [本月國假自動補償] nurse={nurse.name} credit={used_credit.source_date.date()} "
                            f"original_slot={slot.display_text} column={cycle + ROTATION_COLUMN_OFFSET}"
                            f"（此班仍需有人真正上班，繼續往下找）"
                        )
                    else:
                        log_tag = '上月還班使用' if used_credit.source == 'carry_over_detected' else '手動還班使用'
                        print(
                            f"  [{log_tag}] nurse={nurse.name} credit={used_credit.source_date.date()} "
                            f"original_slot={slot.display_text} use_after_date={used_credit.use_after_date} "
                            f"cycle={cycle} column={cycle + ROTATION_COLUMN_OFFSET}"
                            f"（此班仍需有人真正上班，繼續往下找）"
                        )
                    current_index = advance_index(current_index)
                    attempts += 1
                    continue

                # 有 credit 但日期條件還沒到（例如國假還沒發生、或還沒過 use_after_date）
                # 就正常排班，credit 保留，等下一次真正符合條件的正常輪值再用。
                pending_credits = [
                    c for c in all_return_credits
                    if c.sheet_name == sheet_name and c.nurse_name == nurse.name
                    and c.approved and not c.consumed
                ]
                if pending_credits:
                    print(
                        f"  [還班尚未生效] nurse={nurse.name} "
                        f"slot={slot.display_text} "
                        f"credits={[(c.source_date.date(), c.source) for c in pending_credits]}"
                    )

                result = HolidayShiftResult(
                    slot=slot,
                    nurse=nurse,
                    column_index=cycle + ROTATION_COLUMN_OFFSET,
                )
                results.append(result)
                assigned_this_month.add(nurse.name)

                # 記錄正常輪序最後一個人（只記錄本月的）
                last_normal_assigned_name = nurse.name

                print(f"  排班: {nurse.name} {slot.display_text}")

                current_index = advance_index(current_index)
                assigned = True
                break
            else:
                print(f"  跳過 {nurse.name}: {skip_info.reason}")

                # 直接跳過不用補。P/換心保留文字提醒；公休/婚假以 '-' 標記。
                if not skip_info.need_find_next:
                    result = HolidayShiftResult(
                        slot=slot,
                        nurse=nurse,
                        column_index=cycle + ROTATION_COLUMN_OFFSET,
                        is_skipped=True,
                        skip_reason='-' if skip_info.reason in ['公休', '婚假'] else skip_info.reason,
                    )
                    results.append(result)

                if skip_info.need_find_next:
                    makeup_queue.append((current_index, slot, skip_info.adjust_type, False, cycle))
                    this_slot_makeup_candidates.append((current_index, nurse))
                    print(f"    → 加入待補班佇列 [nurse_idx={current_index}, 原欄位{cycle + ROTATION_COLUMN_OFFSET}]")
                
                current_index = advance_index(current_index)
                attempts += 1
        
        if not assigned and attempts >= len(nurses):
            if this_slot_makeup_candidates:
                chosen_idx, chosen_nurse = this_slot_makeup_candidates[0]
                for qi, e in enumerate(makeup_queue):
                    if e[0] == chosen_idx and e[1] is slot: makeup_queue.pop(qi); break
                results.append(HolidayShiftResult(slot=slot, nurse=chosen_nurse, column_index=cycle+ROTATION_COLUMN_OFFSET))
                assigned_this_month.add(chosen_nurse.name)
                last_normal_assigned_name = chosen_nurse.name
                print(f"  警告: 強制排班: {chosen_nurse.name} {slot.display_text}")
                current_index = advance_index(chosen_idx)
            else:
                print(f"警告: 無法為 {slot.display_text} 找到可排班的人員")
    
    # 處理優先待補佇列中剩餘的人：本月找不到合適格子時，延到下月補並保留原欄位
    if pending_priority_queue:
        next_month = target_month + 1 if target_month < 12 else 1
        pending_text = f'{next_month}月補'
        fallback_slot = next((slot for slot in all_slots if not slot.is_next_month), all_slots[0])
        print(f"\n注意: 以下上月待補班本月找不到合適的班:")
        for nurse_idx, column_index, original_text, _target_date, _target_slot_type, _target_shift_type, _is_manual in pending_priority_queue:
            nurse = nurses[nurse_idx]
            results.append(HolidayShiftResult(
                slot=fallback_slot,
                nurse=nurse,
                column_index=column_index,
                is_skipped=True,
                skip_reason=pending_text,
            ))
            print(f"  - {nurse.name} ({original_text}) [欄{column_index}] → {pending_text}")

    # 處理待補班佇列中剩餘的人
    if makeup_queue:
        print(f"\n注意: 以下人員本月找不到合適的班:")
        for nurse_idx, original_slot, adjust_type, _, original_cycle in makeup_queue:
            nurse = nurses[nurse_idx]
            adjust_desc = f", 需{adjust_type}" if adjust_type else ""
            if original_slot:
                next_month = target_month + 1 if target_month < 12 else 1
                pending_month = next_month
                next_month_key = f'{next_month}月'
                if (
                    nurse.is_transplant.get(next_month_key)
                    or nurse.is_p1.get(next_month_key)
                    or nurse.is_p2.get(next_month_key)
                ):
                    pending_month = next_month + 1 if next_month < 12 else 1

                pending_text = f'{pending_month}月補'
                if nurse.is_transplant.get(next_month_key) or nurse.is_p1.get(next_month_key) or nurse.is_p2.get(next_month_key):
                    pending_text = f'{pending_month}月補'

                results.append(HolidayShiftResult(
                    slot=original_slot,
                    nurse=nurse,
                    column_index=original_cycle + ROTATION_COLUMN_OFFSET,
                    is_skipped=True,
                    skip_reason=pending_text,
                ))
                print(f"  - {nurse.name} (原{original_slot.display_text}{adjust_desc}) [原欄位{original_cycle + ROTATION_COLUMN_OFFSET}] → {pending_text}")
            else:
                print(f"  - {nurse.name} (原無指定{adjust_desc}) [原欄位{original_cycle + ROTATION_COLUMN_OFFSET}]")

    # 標記正常輪序最後一個班次（不含補班、不含下月、不含國假補償、不含跳過記錄）
    last_normal_result = None
    for result in results:
        if not result.is_makeup and not result.is_holiday_compensation and not result.is_skipped:
            last_normal_result = result
    
    if last_normal_result:
        last_normal_result.is_last_normal = True
        last_normal_assigned_name = last_normal_result.nurse.name
    
    print(f"\n假日班正常輪序定位點: {last_normal_assigned_name}")
    # 防禦：同一日期、同一假日班別只能有一位人員。
    # 跳過/補償記錄是輪序說明，不視為實際佔用該班別。
    unique_results: List[HolidayShiftResult] = []
    occupied_slots = set()
    for result in results:
        if result.is_skipped:
            unique_results.append(result)
            continue
        slot_key = (result.slot.date.date(), result.slot.shift_type_id)
        if slot_key in occupied_slots:
            print(f"  假日班重複防護：略過 {result.nurse.name} {result.slot.display_text}")
            continue
        occupied_slots.add(slot_key)
        unique_results.append(result)

    return unique_results, last_normal_assigned_name


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
        if result.is_return_credit:
            # 還班：使用 return_credit_date 這筆 credit，抵掉這次正常輪值
            credit_date = result.return_credit_date
            return f"補{credit_date.month}/{credit_date.day}\n(原{result.slot.display_text})"
        if result.is_holiday_compensation:
            # 國定假日補償跳過（不用補班）
            comp_date = result.compensation_date
            return f"補{comp_date.month}/{comp_date.day}\n原{result.slot.display_text}"
        else:
            # 其他跳過原因（公休、P/換心等）
            skip_reason = result.skip_reason
            if skip_reason == '-':
                return '-'
            if re.match(r'^\d+月補$', skip_reason):
                return skip_reason
            if result.original_slot_text == '跨月欄位保留':
                dated_shift_match = re.match(r'^(\d{1,2})/\d{1,2}(白班|小夜|大夜)$', skip_reason)
                if dated_shift_match and target_month is not None:
                    source_month = int(dated_shift_match.group(1))
                    if source_month != target_month:
                        return '-'
                identity_month_match = re.search(r'(\d+)月(?:大P1|小P1|大P2|小P2|P1|P2|換心|待命)', skip_reason)
                if identity_month_match and target_month is not None:
                    source_month = int(identity_month_match.group(1))
                    if source_month != target_month:
                        return '-'
                return skip_reason
            # 判斷是否為 P/換心/待命（格式：X月大P1、X月小P1、X月大P2、X月小P2、X月換心、X月待命）
            is_p_or_transplant = ('月大P1' in skip_reason or '月小P1' in skip_reason or 
                                 '月大P2' in skip_reason or '月小P2' in skip_reason or 
                                 '月P1' in skip_reason or '月P2' in skip_reason or 
                                 '月換心' in skip_reason or '月待命' in skip_reason)
            if is_p_or_transplant:
                # P/換心：只顯示原因，不顯示原班次
                # 如果是換心，提取「X月換心」部分（去掉「(前日)」等後綴）
                if '月換心' in skip_reason:
                    match = re.search(r'(\d+月換心)', skip_reason)
                    if match:
                        return match.group(1)
                if '月待命' in skip_reason:
                    match = re.search(r'(\d+月待命)', skip_reason)
                    if match:
                        return match.group(1)
                return skip_reason
            else:
                # 公休等其他原因：顯示原因和原班次
                return f"{skip_reason}\n(原{result.slot.display_text})"
    
    # 正常的國定假日補償（已排班）
    if result.is_holiday_compensation:
        comp_date = result.compensation_date
        return f"補{comp_date.month}/{comp_date.day}\n原{result.slot.display_text}"
    
    text = result.slot.display_text
    
    # 下月與定位點狀態由儲存格樣式/核對表呈現，正式假日欄不額外加 ! 或 #。
    
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
