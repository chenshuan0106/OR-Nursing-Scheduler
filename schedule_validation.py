"""輸出前的排班完整性稽核。"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from calendar import monthrange
from typing import Dict, List, Tuple

from date_utils import (
    get_night_shift_groups_for_month,
    get_p_shift_dates_for_month,
    is_holiday,
)
from small_night_shift import get_small_night_shift_groups_for_month


def _result_nurse_name(result) -> str:
    nurse = result.get('nurse') if isinstance(result, dict) else result.nurse
    return nurse.name


def _consecutive_seven_ranges(days) -> List[Tuple]:
    """回傳每段連續 7 天的起訖日；同一長區間只回報一次。"""
    ordered = sorted(set(days))
    ranges = []
    run_start = None
    previous = None
    for day in ordered:
        if previous is None or day - previous != timedelta(days=1):
            if run_start is not None and (previous - run_start).days + 1 >= 7:
                ranges.append((run_start, previous))
            run_start = day
        previous = day
    if run_start is not None and (previous - run_start).days + 1 >= 7:
        ranges.append((run_start, previous))
    return ranges


def validate_schedule_results(all_results: Dict, year: int, month: int) -> List[str]:
    """
    稽核所有頁籤。無問題回傳空清單，由呼叫端保持靜默。
    """
    errors: List[str] = []

    for sheet_name, sheet in all_results.items():
        assignments = []  # (date, shift, nurse, source)

        for result in sheet.get('night_results', []):
            for day in result.get('dates', []):
                assignments.append((day.date(), '大夜', _result_nurse_name(result), '正常大夜'))
        for result in sheet.get('small_night_results', []):
            for day in result.get('dates', []):
                assignments.append((day.date(), '小夜', _result_nurse_name(result), '正常小夜'))
        for result in sheet.get('extra_makeup_results', []):
            holiday_result = result.get('holiday_result')
            shift = (
                holiday_result.slot.shift_name
                if holiday_result
                else ('小夜' if result.get('shift_type') == '小夜週' else result.get('shift_type'))
            )
            for day in result.get('dates', []):
                assignments.append((day.date(), shift, _result_nurse_name(result), '額外補班'))
        for result in sheet.get('holiday_results', []):
            if result.is_skipped:
                continue
            assignments.append((result.slot.date.date(), result.slot.shift_name, result.nurse.name, '假日班'))

        # 必排班組完整性：只檢查至少有一天落在目標月份的固定大夜／
        # 小夜週日期。下月純預排班不列為本月交付阻擋。
        expected_night_days = {
            day.date()
            for group_dates, _group_type in get_night_shift_groups_for_month(year, month)
            if any(day.year == year and day.month == month for day in group_dates)
            for day in group_dates
        }
        expected_small_days = {
            day.date()
            for group_dates, _group_type in get_small_night_shift_groups_for_month(year, month)
            if any(day.year == year and day.month == month for day in group_dates)
            for day in group_dates
        }
        actual_night_days = {
            day.date()
            for result in sheet.get('night_results', [])
            for day in result.get('dates', [])
        }
        actual_small_days = {
            day.date()
            for result in sheet.get('small_night_results', [])
            for day in result.get('dates', [])
        }
        for result in sheet.get('extra_makeup_results', []):
            shift_type = result.get('shift_type')
            target = (
                actual_night_days
                if shift_type == '大夜'
                else actual_small_days if shift_type in ('小夜', '小夜週') else None
            )
            if target is not None:
                target.update(day.date() for day in result.get('dates', []))
        for nurse in sheet.get('nurses', []):
            for block in getattr(nurse, 'manual_makeup_shift_blocks', []):
                day = block.get('date')
                if not day:
                    continue
                if block.get('shift_type') == '大夜':
                    actual_night_days.add(day.date())
                elif block.get('shift_type') in ('小夜', '小夜週'):
                    actual_small_days.add(day.date())

        missing_night = sorted(expected_night_days - actual_night_days)
        missing_small = sorted(expected_small_days - actual_small_days)
        if missing_night:
            errors.append(
                f"【{sheet_name}】大夜班未排滿："
                + '、'.join(f'{day.month}/{day.day}' for day in missing_night)
            )
        if missing_small:
            errors.append(
                f"【{sheet_name}】小夜週未排滿："
                + '、'.join(f'{day.month}/{day.day}' for day in missing_small)
            )

        # 同日期、同班別不可有不同人員。
        slot_people = defaultdict(set)
        for day, shift, nurse_name, _source in assignments:
            slot_people[(day, shift)].add(nurse_name)
        for (day, shift), names in sorted(slot_people.items()):
            if len(names) > 1:
                errors.append(
                    f'【{sheet_name}】{day.month}/{day.day} {shift}重複排班：' + '、'.join(sorted(names))
                )

        # 同一人在同一天不可同時有不同班別。
        nurse_day_shifts = defaultdict(set)
        for day, shift, nurse_name, _source in assignments:
            nurse_day_shifts[(nurse_name, day)].add(shift)
        for (nurse_name, day), shifts in sorted(nurse_day_shifts.items()):
            if len(shifts) > 1:
                errors.append(
                    f'【{sheet_name}】{nurse_name} 於 {day.month}/{day.day} 班別衝突：' + '、'.join(sorted(shifts))
                )

        # 每筆使用者指定的額外補班都必須有對應結果。
        requested = Counter()
        for entry in sheet.get('extra_makeup_entries', []) or []:
            name = entry.get('nurse_name')
            shift = entry.get('shift_type') or '自動'
            if name:
                requested[(name, shift)] += 1
        produced = Counter(
            (_result_nurse_name(result), result.get('shift_type'))
            for result in sheet.get('extra_makeup_results', [])
        )
        for (name, shift), count in requested.items():
            if shift == '自動':
                actual = produced[(name, '大夜')] + produced[(name, '小夜週')]
            else:
                actual = produced[(name, shift)]
            if actual < count:
                errors.append(f'【{sheet_name}】{name} 的{shift}額外補班未完成（需 {count}、已排 {actual}）')

        # 工作日包含排班結果與 P1/P2/換心固定班，並保留跨月日期。
        work_days = defaultdict(set)
        for day, _shift, nurse_name, _source in assignments:
            work_days[nurse_name].add(day)
        for nurse in sheet.get('nurses', []):
            # 讀入的上月與跨月班也要納入，才能檢出月初/月底連續 7 天。
            for day in (
                list(getattr(nurse, 'night_shift_dates', []))
                + list(getattr(nurse, 'small_night_shift_dates', []))
                + list(getattr(nurse, 'last_month_cross_month_night_dates', []))
                + list(getattr(nurse, 'last_month_cross_month_small_night_dates', []))
            ):
                work_days[nurse.name].add(day.date())
            relevant_year_months = {(day.year, day.month) for day in work_days[nurse.name]}
            if not relevant_year_months:
                relevant_year_months = {(year, month)}

            # 特殊班記錄可能在上月，即使當月沒有其他輸出日期也必須讀入。
            status_months = set()
            for status_map in (nurse.is_p1, nurse.is_p2, nurse.is_transplant):
                for month_key, enabled in status_map.items():
                    if not enabled:
                        continue
                    try:
                        status_month = int(str(month_key).replace('月', ''))
                    except ValueError:
                        continue
                    status_year = min(
                        (year - 1, year, year + 1),
                        key=lambda candidate_year: abs(
                            (candidate_year - year) * 12 + status_month - month
                        ),
                    )
                    status_months.add((status_year, status_month))
            relevant_year_months.update(status_months)

            for work_year, work_month in relevant_year_months:
                month_key = f'{work_month}月'
                shift_types = []
                if nurse.is_p1.get(month_key):
                    shift_types.append(nurse.is_p1[month_key] if isinstance(nurse.is_p1[month_key], str) else '大P1')
                if nurse.is_p2.get(month_key):
                    shift_types.append(nurse.is_p2[month_key] if isinstance(nurse.is_p2[month_key], str) else '大P2')
                if nurse.is_transplant.get(month_key):
                    shift_types.append('換心')
                for shift_type in shift_types:
                    work_days[nurse.name].update(
                        d.date() for d in get_p_shift_dates_for_month(work_year, work_month, shift_type)
                    )

            # 一般週一至週五為正常工作日；公休、婚假及國定假日不計入。
            # 前後各多看 7 天，才能檢出跨月的連七天。
            target_first = datetime(year, month, 1)
            target_last = datetime(year, month, monthrange(year, month)[1])
            known_days = work_days[nurse.name]
            scan_first = min([target_first.date()] + list(known_days)) - timedelta(days=7)
            scan_last = max([target_last.date()] + list(known_days)) + timedelta(days=7)
            leave_days = {
                d.date()
                for d in list(nurse.leave_dates) + list(nurse.wedding_leave_dates)
            }
            current = scan_first
            while current <= scan_last:
                current_dt = datetime(current.year, current.month, current.day)
                if current.weekday() < 5 and current not in leave_days and not is_holiday(current_dt):
                    work_days[nurse.name].add(current)
                current += timedelta(days=1)

        for nurse_name, days in sorted(work_days.items()):
            for start, end in _consecutive_seven_ranges(days):
                errors.append(
                    f'【{sheet_name}】{nurse_name} 連續上班 {(end - start).days + 1} 天：'
                    f'{start.month}/{start.day}～{end.month}/{end.day}'
                )

    return errors
