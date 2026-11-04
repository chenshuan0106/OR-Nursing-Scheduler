"""
護理排班系統主程式
使用方式: python main.py <輸入Excel路徑>

排班順序：
1. 大夜班
2. 小夜週
3. 假日班（需要大夜和小夜的結果來檢查衝突）
"""
import sys
import os
import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Any

import config
from date_utils import (
    roc_to_western_year,
    format_dates_to_dot_string,
)
from models import NurseInfo, ReturnCredit, make_return_credit_id
from shift_utils import parse_nurse_info_from_row
from night_shift import schedule_night_shifts, should_skip_night_shift
from small_night_shift import schedule_small_night_shifts, should_skip_small_night_shift, get_small_night_shift_groups_for_month
from date_utils import get_night_shift_groups_for_month, is_saturday, is_holiday
from holiday_shift import (
    schedule_holiday_shifts,
    print_holiday_shift_preview,
)
from excel_handler import (
    read_nurses_from_excel,
    get_sheet_names,
    find_last_assigned_from_excel,
    find_last_assigned_small_night_from_excel,
    find_last_assigned_holiday_from_excel,
    create_schedule_excel_multi_sheet,
)


def parse_title_year_month(filepath: str) -> Tuple[Optional[int], Optional[int]]:
    """
    從 Excel 標題解析民國年和月份
    例如: '臺北榮民總醫院護理部思源手術室115年2月值班表' -> (115, 2)
    """
    from openpyxl import load_workbook
    
    wb = load_workbook(filepath, read_only=True)
    ws = wb.active
    title = ws['A1'].value
    wb.close()
    
    if not title:
        return None, None
    
    # 匹配 XXX年X月 或 XXX年XX月
    match = re.search(r'(\d+)年(\d+)月', title)
    if match:
        roc_year = int(match.group(1))
        month = int(match.group(2))
        return roc_year, month
    
    return None, None


def find_last_assigned_index(nurses: List[NurseInfo], last_assigned_name: str) -> int:
    """找到上個月最後排班的人在列表中的索引"""
    if not last_assigned_name:
        return -1
    for idx, nurse in enumerate(nurses):
        if nurse.name == last_assigned_name:
            return idx
    return -1


def _get_next_nurse_name(nurses: List[NurseInfo], last_assigned_name: str) -> str:
    """依名單順序取得定位點後下一位應起排的人。"""
    if not nurses:
        return ''

    last_index = find_last_assigned_index(nurses, last_assigned_name)
    if last_index < 0:
        return nurses[0].name

    return nurses[(last_index + 1) % len(nurses)].name


def _get_previous_nurse_name(nurses: List[NurseInfo], last_assigned_name: str) -> str:
    """Get the previous nurse in list order; small night rotates upward."""
    if not nurses:
        return ''

    last_index = find_last_assigned_index(nurses, last_assigned_name)
    if last_index < 0:
        return nurses[-1].name

    return nurses[(last_index - 1) % len(nurses)].name


def _read_previous_entries_by_nurse(
    filepath: str,
    sheet_name: str,
) -> Dict[str, Dict[str, str]]:
    """讀取上月表中每個人的原始大夜、小夜週、假日欄內容，供排班前核對。"""
    from openpyxl import load_workbook

    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    try:
        def header_text(value):
            return re.sub(r'\s+', '', str(value or ''))

        header_row = None
        name_col = None
        night_col = None
        small_night_col = None
        holiday_cols = []

        for row_idx in range(1, 10):
            row_values = [cell.value for cell in ws[row_idx]]
            normalized_row_values = [header_text(value) for value in row_values]
            if '主值' in normalized_row_values or '副值' in normalized_row_values:
                header_row = row_idx
                for col_idx, value in enumerate(row_values, 1):
                    normalized_value = header_text(value)
                    if normalized_value in ['主值', '副值']:
                        name_col = col_idx
                    elif normalized_value == '大夜':
                        night_col = col_idx
                    elif normalized_value == '小夜週':
                        small_night_col = col_idx
                    elif normalized_value == '假日':
                        holiday_cols.append(col_idx)
                break

        if not header_row or not name_col:
            return {}

        entries = {}
        for row_idx in range(header_row + 1, ws.max_row + 1):
            raw_name = ws.cell(row=row_idx, column=name_col).value
            if not raw_name:
                continue
            name = str(raw_name).strip().lstrip('*')
            holiday_values = [
                str(ws.cell(row=row_idx, column=col_idx).value).strip()
                for col_idx in holiday_cols
                if ws.cell(row=row_idx, column=col_idx).value
            ]
            entries[name] = {
                'night': str(ws.cell(row=row_idx, column=night_col).value).strip() if night_col and ws.cell(row=row_idx, column=night_col).value else '',
                'small_night': str(ws.cell(row=row_idx, column=small_night_col).value).strip() if small_night_col and ws.cell(row=row_idx, column=small_night_col).value else '',
                'holiday': '\n'.join(holiday_values),
            }
        return entries
    finally:
        wb.close()


def _read_plain_holiday_makeup_markers(
    filepath: str,
    sheet_name: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Read plain 補/待補 markers from any holiday column."""
    from openpyxl import load_workbook

    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    try:
        def header_text(value):
            return re.sub(r'\s+', '', str(value or ''))

        header_row = None
        name_col = None
        small_night_col = None
        holiday_cols = []

        for row_idx in range(1, 10):
            row_values = [cell.value for cell in ws[row_idx]]
            normalized_row_values = [header_text(value) for value in row_values]
            if '主值' in normalized_row_values or '副值' in normalized_row_values:
                header_row = row_idx
                for col_idx, value in enumerate(row_values, 1):
                    normalized_value = header_text(value)
                    if normalized_value in ['主值', '副值']:
                        name_col = col_idx
                    elif normalized_value == '小夜週':
                        small_night_col = col_idx
                    elif normalized_value == '假日':
                        holiday_cols.append(col_idx)
                break

        if header_row and not holiday_cols and small_night_col:
            holiday_cols = list(range(small_night_col + 1, ws.max_column + 1))

        if not header_row or not name_col or not holiday_cols:
            return {}

        markers = {}
        for row_idx in range(header_row + 1, ws.max_row + 1):
            raw_name = ws.cell(row=row_idx, column=name_col).value
            if not raw_name:
                continue
            name = str(raw_name).strip().lstrip('*')
            for col_pos, col_idx in enumerate(holiday_cols, 1):
                cell_value = ws.cell(row=row_idx, column=col_idx).value
                normalized = str(cell_value or '').strip().replace(' ', '').replace('\u3000', '')
                if normalized not in {'補', '待補', '補班', '待補班'}:
                    continue
                markers.setdefault(name, []).append({
                    'type': 'pending',
                    'original_text': str(cell_value).strip(),
                    'column_index': col_pos,
                    'is_undated_makeup': True,
                })
        return markers
    finally:
        wb.close()


def build_pre_schedule_check(
    filepath: str,
    sheet_name: str,
    year: int,
    month: int,
) -> Dict[str, Any]:
    """
    排班前建立核對資料。

    這份資料會保留「欲排班月份」讀到的人名順序，並標出系統從上個月
    Excel 欄位抓到的大夜、小夜週、假日班定位點，方便排班前確認格子和順序。
    """
    nurses_data = read_nurses_from_excel(filepath, sheet_name)

    nurses = []
    for row_data in nurses_data:
        nurse = parse_nurse_info_from_row(row_data, row_data.get('_row_index', 0), year, month)
        if nurse:
            nurses.append(nurse)

    nurse_names = [nurse.name for nurse in nurses]
    last_night = find_last_assigned_from_excel(filepath, sheet_name, month, year, nurse_names)
    last_small_night = find_last_assigned_small_night_from_excel(filepath, sheet_name, month, year)
    last_holiday = find_last_assigned_holiday_from_excel(filepath, sheet_name, month, year)
    from excel_handler import read_cross_month_makeup_holidays_from_excel
    cross_month_makeups = read_cross_month_makeup_holidays_from_excel(filepath, sheet_name, month, year)
    plain_holiday_makeups = _read_plain_holiday_makeup_markers(filepath, sheet_name)
    for nurse_name, records in plain_holiday_makeups.items():
        existing = cross_month_makeups.setdefault(nurse_name, [])
        existing_keys = {
            (record.get('type'), record.get('original_text'), record.get('column_index'))
            for record in existing
        }
        for record in records:
            if any(
                existing_record.get('type') == 'pending'
                and existing_record.get('original_text') == record.get('original_text')
                and existing_record.get('is_undated_makeup')
                for existing_record in existing
            ):
                continue
            key = (record.get('type'), record.get('original_text'), record.get('column_index'))
            if key not in existing_keys:
                existing.append(record)
                existing_keys.add(key)
    previous_entries = _read_previous_entries_by_nurse(filepath, sheet_name)

    def _is_plain_makeup_marker(value: str) -> bool:
        normalized = str(value or '').strip().replace(' ', '').replace('\u3000', '')
        return normalized in {'補', '待補', '補班', '待補班'}

    for nurse in nurses:
        previous_entry = previous_entries.get(nurse.name, {})
        if _is_plain_makeup_marker(previous_entry.get('night')):
            if not any(item.get('is_undated_makeup') for item in nurse.pending_night_makeup):
                nurse.pending_night_makeup.append({
                    'dates': [],
                    'shift_type': '大夜',
                    'original_text': previous_entry.get('night'),
                    'target_date': None,
                    'target_shift_type': None,
                    'column_index': 1,
                    'is_targeted_makeup': False,
                    'is_undated_makeup': True,
                })
        if _is_plain_makeup_marker(previous_entry.get('small_night')):
            if not any(item.get('is_undated_makeup') for item in nurse.pending_small_night_makeup):
                nurse.pending_small_night_makeup.append({
                    'dates': [],
                    'shift_type': '小夜',
                    'original_text': previous_entry.get('small_night'),
                    'target_date': None,
                    'target_shift_type': None,
                    'column_index': 1,
                    'is_targeted_makeup': False,
                    'is_undated_makeup': True,
                })
        for holiday_record in plain_holiday_makeups.get(nurse.name, []):
            if not any(
                item.get('original_text') == holiday_record.get('original_text')
                and item.get('column_index') == holiday_record.get('column_index')
                for item in nurse.pending_holiday_makeup
            ):
                nurse.pending_holiday_makeup.append(dict(holiday_record))

    carryover_by_nurse = {}
    carryover_records_by_nurse = {}
    for nurse_name, records in cross_month_makeups.items():
        carryover_by_nurse[nurse_name] = [record.get('original_text', '') for record in records if record.get('original_text')]
        indexed_records = []
        pending_index = 0
        for record in records:
            record_copy = dict(record)
            if record_copy.get('type') == 'pending':
                record_copy['pending_index'] = pending_index
                pending_index += 1
            indexed_records.append(record_copy)
        carryover_records_by_nurse[nurse_name] = indexed_records

    return {
        'sheet_name': sheet_name,
        'year': year,
        'month': month,
        'nurses': nurses,
        'carryover_by_nurse': carryover_by_nurse,
        'carryover_records_by_nurse': carryover_records_by_nurse,
        'previous_entries': previous_entries,
        'last_night': last_night or '',
        'last_small_night': last_small_night or '',
        'last_holiday': last_holiday or '',
        'next_night': _get_next_nurse_name(nurses, last_night),
        'next_small_night': _get_previous_nurse_name(nurses, last_small_night),
        'next_holiday': _get_next_nurse_name(nurses, last_holiday),
    }


def get_return_credit_candidates_from_nurses(
    sheet_name: str,
    nurses: List[NurseInfo],
    target_year: int = None,
    target_month: int = None,
    previous_entries: Optional[Dict[str, Dict[str, str]]] = None,
) -> List[ReturnCredit]:
    """
    從已解析的護理人員清單找出「上月結轉」還班候選（source='carry_over_detected'）。

    候選只是「Excel 裡有這筆舊資料」，approved 預設 False，
    一定要經過 Streamlit UI 核准才會真正影響排班。
    來源限定為上月 Excel 大夜/小夜週欄位括號內解析出來的國定假日日期
    （night_shift_holiday_compensations / small_night_shift_holiday_compensations），
    這些在排班前（不用真的跑一次排班）就能確定，適合拿來給使用者核准。

    注意：本月排班過程中「這次新產生」的國假補償（大夜/小夜真的排到本月的
    平日國定假日）是另一種來源 source='current_month_auto'，approved 自動
    是 True、不需要 UI 核准，由 schedule_holiday_shifts() 在排班當下直接
    建立與使用，不會出現在這份「排班前」候選清單裡。

    只保留來源日期落在「上個月」（target_month - 1）的候選：Excel 裡的
    大夜/小夜週欄位是逐月往前抄的歷史記錄，若這次讀到的來源日期比上個月
    還早（例如本次用 10 月表排 11 月，卻抓到 9 月的補X/X），代表那筆資格
    在上個月自己排班時就已經有機會核准/用掉，不該在這次又冒出來當候選，
    否則使用者每個月都要重新忽略同一批舊資料。若沒帶 target_year/
    target_month（呼叫端尚未提供），維持不過濾以相容舊行為。
    """
    prev_month = None
    prev_year = None
    if target_year is not None and target_month is not None:
        prev_month = target_month - 1 if target_month > 1 else 12
        prev_year = target_year if target_month > 1 else target_year - 1

    candidates: List[ReturnCredit] = []
    for nurse in nurses:
        seen_dates = set()
        # 上月假日欄已寫「補1/1(原1/9小夜)」＝這筆還班上個月已經用掉，不再列為候選
        # （跨月保留的大夜/小夜週，例如 12/31.1/1.1/2 (1/1)，下個月會再讀到一次）
        holiday_text = ((previous_entries or {}).get(nurse.name) or {}).get('holiday', '')

        def already_used(day):
            return re.search(rf'補{day.month}/{day.day}(?!\d)\s*\(?原', holiday_text) is not None

        for source_type, dates in (
            ('大夜', nurse.night_shift_holiday_compensations),
            ('小夜', nurse.small_night_shift_holiday_compensations),
        ):
            for d in dates:
                day = d.date() if hasattr(d, 'date') else d
                if prev_month is not None and not (day.month == prev_month and day.year == prev_year):
                    print(f"[上月結轉候選-過濾] sheet={sheet_name} nurse={nurse.name} source_date={day} 不在上個月，略過")
                    continue
                if already_used(day):
                    print(f"[上月結轉候選-已使用] sheet={sheet_name} nurse={nurse.name} source_date={day} 上月假日欄已還，略過")
                    continue
                dedupe_key = (day, source_type)
                if dedupe_key in seen_dates:
                    continue
                seen_dates.add(dedupe_key)
                credit_id = make_return_credit_id(sheet_name, nurse.name, d, source_type)
                candidates.append(ReturnCredit(
                    credit_id=credit_id,
                    sheet_name=sheet_name,
                    nurse_name=nurse.name,
                    source_date=d,
                    source_type=source_type,
                    source='carry_over_detected',
                    approved=False,
                ))
                print(f"[上月結轉候選] sheet={sheet_name} nurse={nurse.name} source_date={day} source_type={source_type}")

        # 備註欄「還班未還:補12/25(...)」：之前沒還掉的還班（例如整月 P 班/換心），
        # 不受「只看上個月」限制，一樣要給使用者核准。
        if target_year is None or target_month is None:
            continue
        seen_days = {key[0] for key in seen_dates}
        for m in re.finditer(r'還班未還[:：]\s*補(\d{1,2})/(\d{1,2})', nurse.remarks or ''):
            source_month, source_day = int(m.group(1)), int(m.group(2))
            source_year = target_year if source_month < target_month else target_year - 1
            try:
                d = datetime(source_year, source_month, source_day)
            except ValueError:
                continue
            if d.date() in seen_days:
                continue
            seen_days.add(d.date())
            candidates.append(ReturnCredit(
                credit_id=make_return_credit_id(sheet_name, nurse.name, d, '備註欠還班'),
                sheet_name=sheet_name,
                nurse_name=nurse.name,
                source_date=d,
                source_type='備註欠還班',
                source='carry_over_detected',
                approved=False,
            ))
            print(f"[備註欠還班候選] sheet={sheet_name} nurse={nurse.name} source_date={d.date()}")
    return candidates


def print_night_shift_preview(results: List[Dict], month: int, sheet_name: str):
    """預覽大夜班排班結果"""
    print(f"\n===== {month}月大夜班排班結果 【{sheet_name}】 =====\n")
    print(f"{'日期':<20} {'護理人員':<10} {'類型':<10} {'標記':<25}")
    print("-" * 70)

    for result in results:
        date_str = format_dates_to_dot_string(result['dates'])
        nurse_name = result['nurse'].name
        group_type = '週一～三' if result['group_type'] == 'mon-wed' else '週四～六'

        marks = []

        # 下個月標記
        if result.get('is_next_month'):
            marks.append('【下月】')

        # 補班標記
        if result.get('is_makeup'):
            marks.append('【補班】')

        if result.get('is_second_round'):
            marks.append('【第二輪】')

        if result.get('has_saturday'):
            marks.append('星期六(藍)')
        if result.get('has_holiday'):
            holiday_strs = [f"{d.month}/{d.day}" for d in result['holiday_dates']]
            marks.append(f"國定假日(紅):{','.join(holiday_strs)}")

        mark_str = ', '.join(marks) if marks else '-'

        print(f"{date_str:<20} {nurse_name:<10} {group_type:<10} {mark_str}")


def print_small_night_shift_preview(results: List[Dict], month: int, sheet_name: str):
    """預覽小夜班排班結果"""
    print(f"\n===== {month}月小夜班排班結果 【{sheet_name}】 =====\n")
    print(f"{'日期':<20} {'護理人員':<10} {'標記':<25}")
    print("-" * 55)

    for result in results:
        date_str = format_dates_to_dot_string(result['dates'])
        nurse_name = result['nurse'].name

        marks = []

        # 下個月標記
        if result.get('is_next_month'):
            marks.append('【下月】')

        # 補班標記
        if result.get('is_makeup'):
            marks.append('【補班】')

        if result.get('has_holiday'):
            holiday_strs = [f"{d.month}/{d.day}" for d in result['holiday_dates']]
            marks.append(f"國定假日(紅):{','.join(holiday_strs)}")

        mark_str = ', '.join(marks) if marks else '-'

        print(f"{date_str:<20} {nurse_name:<10} {mark_str}")


def _apply_manual_shift_entries(
    nurses: List[NurseInfo],
    manual_shift_entries: Optional[List[Dict[str, Any]]],
) -> None:
    if not manual_shift_entries:
        return

    from datetime import datetime as dt, timedelta

    for entry in manual_shift_entries:
        nurse_name = entry.get('nurse_name')
        shift_type = entry.get('shift_type')
        start_value = entry.get('start_date') or entry.get('date')
        end_value = entry.get('end_date') or start_value
        if not nurse_name or shift_type not in ('大夜', '小夜週') or not start_value:
            continue

        nurse = next((n for n in nurses if n.name == nurse_name), None)
        if not nurse:
            continue

        def as_datetime(value):
            if isinstance(value, dt):
                return value
            return dt(value.year, value.month, value.day)

        start_date = as_datetime(start_value)
        end_date = as_datetime(end_value)
        if end_date < start_date:
            start_date, end_date = end_date, start_date

        dates = []
        current = start_date
        while current <= end_date:
            dates.append(current)
            current += timedelta(days=1)

        if shift_type == '大夜':
            nurse.manual_night_shift_dates.extend(dates)
        else:
            nurse.manual_small_night_shift_dates.extend(dates)

        print(
            f"  手動自填{shift_type}: {nurse.name} "
            f"{start_date.month}/{start_date.day}-{end_date.month}/{end_date.day}"
        )


def _apply_priority_makeup_entries(
    nurses: List[NurseInfo],
    priority_makeup_entries: Optional[List[Dict[str, Any]]],
    month: int,
) -> None:
    """Apply UI-sorted, undated makeup requests before regular rotation."""
    if not priority_makeup_entries:
        return

    requested_shift_types = {entry.get('shift_type') for entry in priority_makeup_entries}
    if '大夜' in requested_shift_types:
        for nurse in nurses:
            nurse.pending_night_makeup = []
    if {'小夜', '小夜週'} & requested_shift_types:
        for nurse in nurses:
            nurse.pending_small_night_makeup = []
    if {'假日', '假日班', '白班'} & requested_shift_types:
        for nurse in nurses:
            nurse.pending_holiday_makeup = []
            nurse.holiday_pending_notice = []

    nurse_by_name = {nurse.name: nurse for nurse in nurses}
    for order, entry in enumerate(priority_makeup_entries, 1):
        nurse_name = entry.get('nurse_name')
        shift_type = entry.get('shift_type')
        nurse = nurse_by_name.get(nurse_name)
        if not nurse or not shift_type:
            continue

        original_text = f"排序補班第{order}順位"
        makeup_record = {
            'dates': [],
            'shift_type': shift_type,
            'original_text': original_text,
            'priority_order': order,
            'is_manual_priority': True,
        }

        if shift_type == '大夜':
            nurse.pending_night_makeup.append(makeup_record)
        elif shift_type in ('小夜', '小夜週'):
            small_record = dict(makeup_record)
            small_record['shift_type'] = '小夜'
            nurse.pending_small_night_makeup.append(small_record)
        elif shift_type in ('假日', '假日班', '白班'):
            holiday_record = {
                'dates': [],
                'shift_type': '假日',
                'original_text': original_text,
                'column_index': 1,
                'type': 'pending',
                'is_manual_priority': True,
            }
            nurse.pending_holiday_makeup.append(holiday_record)
            nurse.holiday_pending_notice.append(holiday_record)

        print(f"  排序補班第{order}順位: {nurse.name} / {shift_type} / {month}月自動找空位")


def _night_groups_with_next_month(year: int, month: int) -> List[Tuple[List, str]]:
    groups = get_night_shift_groups_for_month(year, month)
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    next_groups = get_night_shift_groups_for_month(next_year, next_month)
    if next_groups:
        last_group_dates = groups[-1][0] if groups else []
        last_group_date_set = {d.date() for d in last_group_dates}
        for group_dates, group_type in next_groups:
            if not ({d.date() for d in group_dates} & last_group_date_set):
                groups.append((group_dates, group_type))
                break
    return groups


def _small_night_groups_with_next_month(year: int, month: int) -> List[Tuple[List, str]]:
    groups = get_small_night_shift_groups_for_month(year, month)
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    next_groups = get_small_night_shift_groups_for_month(next_year, next_month)
    if next_groups:
        last_group_dates = groups[-1][0] if groups else []
        last_group_date_set = {d.date() for d in last_group_dates}
        for group_dates, group_type in next_groups:
            if not ({d.date() for d in group_dates} & last_group_date_set):
                groups.append((group_dates, group_type))
                break
    return groups


def _schedule_extra_makeup_entries(
    nurses: List[NurseInfo],
    extra_makeup_entries: Optional[List[Dict[str, Any]]],
    year: int,
    month: int,
    scheduled_night_results: Optional[List[Dict]] = None,
    scheduled_small_night_results: Optional[List[Dict]] = None,
    target_shift_type: Optional[str] = None,
) -> List[Dict]:
    """Schedule optional extra makeup into an independent output column.
    
    target_shift_type: If specified, only process entries for this shift type ('大夜', '小夜週', etc.)
    """
    if not extra_makeup_entries:
        return []

    nurse_by_name = {nurse.name: nurse for nurse in nurses}
    prev_month = month - 1 if month > 1 else 12
    next_month = month + 1 if month < 12 else 1
    results = []
    used_by_shift = {
        '大夜': set(),
        '小夜週': set(),
    }
    for result in scheduled_night_results or []:
        result_dates = result.get('dates') or []
        if result_dates:
            used_by_shift['大夜'].add(tuple(d.date() for d in result_dates))
    for result in scheduled_small_night_results or []:
        result_dates = result.get('dates') or []
        if result_dates:
            used_by_shift['小夜週'].add(tuple(d.date() for d in result_dates))

    for order, entry in enumerate(extra_makeup_entries, 1):
        nurse = nurse_by_name.get(entry.get('nurse_name'))
        shift_type = entry.get('shift_type')
        if not nurse:
            continue
        candidate_shift_types = ['大夜', '小夜週'] if shift_type in (None, '', '自動') else [shift_type]
        candidate_shift_types = [
            candidate_shift_type
            for candidate_shift_type in candidate_shift_types
            if candidate_shift_type in ('大夜', '小夜週')
        ]
        # 如果指定了目標班別，只處理該班別的額外補班
        if target_shift_type:
            candidate_shift_types = [s for s in candidate_shift_types if s == target_shift_type]
        if not candidate_shift_types:
            continue

        assigned = False
        for candidate_shift_type in candidate_shift_types:
            groups = (
                _night_groups_with_next_month(year, month)
                if candidate_shift_type == '大夜'
                else _small_night_groups_with_next_month(year, month)
            )
            for group_dates, group_type in groups:
                # 額外補班是過去積欠的班，必須盡快在目標月份完成。
                # 不使用跨月班組，也不借用下月第一組預排。
                if any(d.year != year or d.month != month for d in group_dates):
                    continue
                group_key = tuple(d.date() for d in group_dates)
                if group_key in used_by_shift[candidate_shift_type]:
                    continue

                group_days = {d.date() for d in group_dates}
                assigned_days = {
                    d.date()
                    for d in getattr(nurse, 'night_shift_dates', []) + getattr(nurse, 'small_night_shift_dates', [])
                }
                if group_days & assigned_days:
                    print(f"  額外補班跳過 {nurse.name} / {candidate_shift_type}: 已有大夜或小夜週")
                    continue

                if candidate_shift_type == '大夜':
                    should_skip, reason = should_skip_night_shift(
                        nurse, group_dates, month, year, prev_month, next_month
                    )
                else:
                    should_skip, reason = should_skip_small_night_shift(
                        nurse, group_dates, month, year, prev_month, next_month
                    )
                if should_skip:
                    print(f"  額外補班跳過 {nurse.name} / {candidate_shift_type}: {reason}")
                    continue

                is_next_month_group = all(d.month == next_month for d in group_dates)
                holiday_dates = [d for d in group_dates if is_holiday(d)]
                result = {
                    'nurse': nurse,
                    'dates': group_dates,
                    'group_type': group_type,
                    'shift_type': candidate_shift_type,
                    'has_saturday': any(is_saturday(d) for d in group_dates),
                    'has_holiday': len(holiday_dates) > 0,
                    'holiday_dates': holiday_dates,
                    'is_makeup': True,
                    'is_extra_makeup': True,
                    'is_next_month': is_next_month_group,
                    'priority_order': order,
                }
                results.append(result)
                used_by_shift[candidate_shift_type].add(group_key)
                if candidate_shift_type == '大夜':
                    nurse.night_shift_dates.extend(group_dates)
                else:
                    nurse.small_night_shift_dates.extend(group_dates)
                print(f"  額外補班: {nurse.name} / {candidate_shift_type} / {format_dates_to_dot_string(group_dates)}")
                assigned = True
                break
            if assigned:
                break

        if not assigned:
            print(f"  額外補班未排入: {nurse.name} / {shift_type or '自動'}")

    results.sort(key=lambda result: (min(result['dates']) if result.get('dates') else datetime.max, result.get('priority_order', 0)))
    return results


def process_sheet(
    filepath: str,
    sheet_name: str,
    year: int,
    month: int,
    last_assigned_night_name: str = None,
    last_assigned_small_night_name: str = None,
    last_assigned_holiday_name: str = None,
    regular_start_night_name: str = None,
    regular_start_small_night_name: str = None,
    regular_start_holiday_name: str = None,
    manual_shift_entries: Optional[List[Dict[str, Any]]] = None,
    priority_makeup_entries: Optional[List[Dict[str, Any]]] = None,
    extra_makeup_entries: Optional[List[Dict[str, Any]]] = None,
    manual_makeup_note: str = '',
    approved_return_credits: Optional[List[ReturnCredit]] = None,
) -> Tuple[List[NurseInfo], List[Dict], List[Dict], List, List[Dict], str, str, str, set, set]:
    """
    處理單一頁籤的排班

    Returns:
        (nurses, night_shift_results, small_night_shift_results, holiday_shift_results,
         night_last_normal_name, small_night_last_normal_name, holiday_last_normal_name)
    """
    print(f"\n--- 處理頁籤: {sheet_name} ---")
    if manual_makeup_note:
        print(f"補班說明: {manual_makeup_note}")

    nurses_data = read_nurses_from_excel(filepath, sheet_name)

    # 解析護理人員資訊
    nurses = []
    for row_data in nurses_data:
        nurse = parse_nurse_info_from_row(row_data, row_data.get('_row_index', 0), year, month)
        if nurse:
            nurses.append(nurse)

    print(f"讀取到 {len(nurses)} 位護理人員")

    # 將手動新增的過去欠班紀錄套用到護理人員資料中
    _apply_manual_shift_entries(nurses, manual_shift_entries)

    # 清除「預排」的國假補償記錄，保留「已確定」的記錄
    # 判斷方式：看小夜週的開始日期是否在上個月
    # - 小夜週開始日期在上個月（跨月班）→ 已確定，保留
    # - 小夜週開始日期在本月 → 預排，清除
    for nurse in nurses:
        # 從大夜欄位讀取的補償 → 全部保留（上個月的結果，已確定）
        night_comps = nurse.night_shift_holiday_compensations
        if night_comps:
            print(f"  保留 {nurse.name} 的大夜班國假補償: {[f'{d.month}/{d.day}' for d in night_comps]}")

        # 從小夜週欄位讀取的補償：
        # - 本月國定假日（例如 6/19）保留，之後可依規則跳假日值班一次。
        # - 上月跨入的國定假日（例如 5/1）不再影響本月假日輪序。
        small_night_comps = nurse.small_night_shift_holiday_compensations
        if small_night_comps and nurse.small_night_shift_dates:
            # 取得小夜週的開始日期
            first_small_night_date = min(nurse.small_night_shift_dates)
            print(f"  檢查 {nurse.name} 的小夜週國假補償: {[f'{d.month}/{d.day}' for d in small_night_comps]} (小夜週開始於 {first_small_night_date.month}/{first_small_night_date.day})")
            for d in small_night_comps:
                if d.month != month and d in nurse.holiday_compensations_used:
                    nurse.holiday_compensations_used.remove(d)
                    print(f"    移除非本月補償 {d.month}/{d.day}")

    # 讀取跨月補班記錄（!開頭的假日班）
    print("讀取跨月補班記錄...")
    from excel_handler import read_cross_month_makeup_holidays_from_excel
    cross_month_makeups = read_cross_month_makeup_holidays_from_excel(filepath, sheet_name, month, year)
    
    # 填充到每個護理人員
    # cross_month_makeups 的每筆記錄有 type: 'fixed' 或 'pending'
    for nurse in nurses:
        if nurse.name in cross_month_makeups:
            records = cross_month_makeups[nurse.name]
            # 有日期的跨月補班 → previous_month_cross_month_makeup_holidays
            fixed = [r for r in records if r['type'] == 'fixed']
            if fixed:
                nurse.previous_month_cross_month_makeup_holidays = fixed
            skip_records = [r for r in records if r['type'] == 'skip']
            if skip_records:
                nurse.previous_month_holiday_skip_records = skip_records
            pending = [r for r in records if r['type'] == 'pending']
            for p in pending:
                print(f"  {nurse.name} 有假日待補，將在有日期跨月補班後優先排入: {p['original_text']}")
            if pending:
                nurse.holiday_pending_notice = pending
                nurse.pending_holiday_makeup.extend(pending)

    _apply_priority_makeup_entries(nurses, priority_makeup_entries, month)

    # 如果沒有護理人員，直接返回空結果
    if not nurses:
        print(f"  頁籤 {sheet_name} 沒有護理人員資料，跳過")
        return [], [], [], [], [], '', '', '', set(), set()

    # 初始化結果容器
    all_extra_makeup_results = []

    # ===========================
    # 大夜班流程：額外補班 → 補班 → 定位點輪續
    # ===========================
    print("\n========== 大夜班排班流程 ==========")
    
    # 1. 大夜班額外補班
    print("開始大夜班額外補班...")
    night_extra_makeup_results = _schedule_extra_makeup_entries(
        nurses,
        extra_makeup_entries,
        year,
        month,
        scheduled_night_results=[],
        scheduled_small_night_results=[],
        target_shift_type='大夜',
    )
    all_extra_makeup_results.extend(night_extra_makeup_results)
    
    # 2. 大夜班定位點輪續（包含補班，補班已在 _apply_priority_makeup_entries 中標記）
    if not last_assigned_night_name:
        last_assigned_night_name = find_last_assigned_from_excel(filepath, sheet_name, month, year)
        if last_assigned_night_name:
            print(f"從 Excel 讀取到大夜班上個月最後排班: {last_assigned_night_name}")

    last_night_index = find_last_assigned_index(nurses, last_assigned_night_name)
    regular_night_start_index = find_last_assigned_index(nurses, regular_start_night_name)
    if last_assigned_night_name and last_night_index >= 0:
        print(f"大夜班上個月最後排班: {last_assigned_night_name} (索引: {last_night_index})")
    elif last_assigned_night_name:
        print(f"警告: 找不到 {last_assigned_night_name}，從第一個人開始排")

    print("開始大夜班定位點輪續排班（含補班）...")
    night_results, night_last_normal_name, night_identity_skipped = schedule_night_shifts(
        nurses,
        year,
        month,
        last_night_index,
        regular_start_index=regular_night_start_index,
        reserved_results=night_extra_makeup_results,
    )
    print_night_shift_preview(night_results, month, sheet_name)

    # ===========================
    # 小夜班流程：額外補班 → 補班 → 定位點輪續
    # ===========================
    print("\n========== 小夜班排班流程 ==========")
    
    # 1. 小夜班額外補班
    print("開始小夜班額外補班...")
    small_night_extra_makeup_results = _schedule_extra_makeup_entries(
        nurses,
        extra_makeup_entries,
        year,
        month,
        scheduled_night_results=night_results,
        scheduled_small_night_results=[],
        target_shift_type='小夜週',
    )
    all_extra_makeup_results.extend(small_night_extra_makeup_results)
    
    # 2. 小夜班定位點輪續（包含補班）
    if not last_assigned_small_night_name:
        last_assigned_small_night_name = find_last_assigned_small_night_from_excel(filepath, sheet_name, month, year)
        if last_assigned_small_night_name:
            print(f"從 Excel 讀取到小夜班上個月最後排班: {last_assigned_small_night_name}")

    last_small_night_index = find_last_assigned_index(nurses, last_assigned_small_night_name)
    regular_small_night_start_index = find_last_assigned_index(nurses, regular_start_small_night_name)
    if last_assigned_small_night_name and last_small_night_index >= 0:
        print(f"小夜班上個月最後排班: {last_assigned_small_night_name} (索引: {last_small_night_index})")
    elif last_assigned_small_night_name:
        print(f"警告: 找不到 {last_assigned_small_night_name}，從第一個人開始排")

    print("開始小夜班定位點輪續排班（含補班）...")
    small_night_results, small_night_last_normal_name, small_night_identity_skipped = schedule_small_night_shifts(
        nurses,
        year,
        month,
        last_small_night_index,
        regular_start_index=regular_small_night_start_index,
        reserved_results=small_night_extra_makeup_results,
    )
    print_small_night_shift_preview(small_night_results, month, sheet_name)

    # ===========================
    # 假日班流程：額外補班 → 補班 → 定位點輪續
    # ===========================
    print("\n========== 假日班排班流程 ==========")
    
    # 1. 假日班額外補班
    print("開始假日班額外補班...")
    
    # 2. 假日班定位點輪續（包含補班）
    if not last_assigned_holiday_name:
        last_assigned_holiday_name = find_last_assigned_holiday_from_excel(filepath, sheet_name, month, year)
        if last_assigned_holiday_name:
            print(f"從 Excel 讀取到假日班上個月最後排班: {last_assigned_holiday_name}")

    last_holiday_index = find_last_assigned_index(nurses, last_assigned_holiday_name)
    regular_holiday_start_index = find_last_assigned_index(nurses, regular_start_holiday_name)
    if last_assigned_holiday_name and last_holiday_index >= 0:
        print(f"假日班上個月最後排班: {last_assigned_holiday_name} (索引: {last_holiday_index})")
    elif last_assigned_holiday_name:
        print(f"警告: 找不到 {last_assigned_holiday_name}，從第一個人開始排")

    print("開始假日班定位點輪續排班（含補班）...")
    # 還班 credit 只能給同一個 sheet 使用，避免不同頁籤剛好同名的人互相誤用。
    sheet_return_credits = [
        credit for credit in (approved_return_credits or [])
        if credit.sheet_name == sheet_name
    ]
    holiday_results, holiday_last_normal_name = schedule_holiday_shifts(
        nurses,
        year,
        month,
        last_holiday_index,
        regular_start_index=regular_holiday_start_index,
        extra_makeup_entries=extra_makeup_entries,
        sheet_name=sheet_name,
        approved_return_credits=sheet_return_credits,
    )
    holiday_extra_makeup_results = []
    regular_holiday_results = []
    for result in holiday_results:
        if result.is_extra_makeup:
            holiday_extra_makeup_results.append({
                'nurse': result.nurse,
                'dates': [result.slot.date],
                'shift_type': '假日班',
                'holiday_result': result,
                'is_extra_makeup': True,
            })
        else:
            regular_holiday_results.append(result)
    holiday_results = regular_holiday_results
    all_extra_makeup_results.extend(holiday_extra_makeup_results)
    print_holiday_shift_preview(holiday_results, month, sheet_name)

    return (
        nurses,
        night_results,
        small_night_results,
        holiday_results,
        all_extra_makeup_results,
        night_last_normal_name,
        small_night_last_normal_name,
        holiday_last_normal_name,
        night_identity_skipped,
        small_night_identity_skipped,
    )


def main():
    """主程式"""
    input_file = None

    # 解析命令列參數
    if len(sys.argv) >= 2:
        input_file = sys.argv[1]

    if not input_file or not os.path.exists(input_file):
        print("使用方式: python main.py <輸入Excel路徑>")
        print("範例: python main.py 115年3月值班表.xlsx")
        return

    # 從 Excel 標題自動讀取年份和月份
    roc_year, input_month = parse_title_year_month(input_file)
    # 計算目標月份（輸出月 = 輸入月 + 1）
    if input_month == 12:
        month = 1
        year = roc_to_western_year(roc_year) + 1
        roc_year = roc_year + 1
    else:
        month = input_month + 1
        year = roc_to_western_year(roc_year)

    if roc_year is None or month is None:
        print("錯誤: 無法從 Excel 標題解析年份和月份")
        print("請確認標題格式為: XXX年X月值班表")
        return

    # 轉換年份
    year = roc_to_western_year(roc_year)

    print(f"護理排班系統")
    print(f"目標月份: 民國 {roc_year} 年 {month} 月 (西元 {year} 年)")
    print(f"國定假日數量: {len(config.HOLIDAYS_2026)} 天")
    print(f"\n讀取檔案: {input_file}")

    # 取得所有頁籤
    sheet_names = get_sheet_names(input_file)
    print(f"找到 {len(sheet_names)} 個頁籤: {sheet_names}")

    # 處理每個頁籤
    all_results = {}

    for sheet_name in sheet_names:
        pre_schedule_check = build_pre_schedule_check(
            input_file,
            sheet_name,
            year,
            month,
        )

        # process_sheet 會自動從 Excel 讀取上月最後排班者
        (
            nurses,
            night_results,
            small_night_results,
            holiday_results,
            extra_makeup_results,
            night_last_normal,
            small_night_last_normal,
            holiday_last_normal,
            night_identity_skipped,
            small_night_identity_skipped,
        ) = process_sheet(
            input_file,
            sheet_name,
            year,
            month,
        )

        all_results[sheet_name] = {
            'nurses': nurses,
            'night_results': night_results,
            'small_night_results': small_night_results,
            'holiday_results': holiday_results,
            'extra_makeup_results': extra_makeup_results,
            'night_last_normal': night_last_normal,
            'small_night_last_normal': small_night_last_normal,
            'holiday_last_normal': holiday_last_normal,
            'night_identity_skipped': night_identity_skipped,
            'small_night_identity_skipped': small_night_identity_skipped,
            'pre_schedule_check': pre_schedule_check,
            'extra_makeup_entries': [],
        }

    # 輸出到新的 Excel（多頁籤）
    output_file = f"排班結果_{roc_year}年{month}月.xlsx"
    title = f"臺北榮民總醫院護理部思源手術室{roc_year}年{month}月值班表"

    from schedule_validation import validate_schedule_results
    validation_errors = validate_schedule_results(all_results, year, month)
    if validation_errors:
        print("\n排班稽核未通過，已停止輸出：")
        for error in validation_errors:
            print(f"  - {error}")
        return

    create_schedule_excel_multi_sheet(output_file, title, all_results, year, month)
    print(f"\n排班結果已輸出到: {output_file}")

    # 輸出摘要
    print("\n" + "=" * 70)
    print("排班摘要")
    print("=" * 70)
    
    for sheet_name, results in all_results.items():
        print(f"\n【{sheet_name}】")
        print(f"  護理人員數: {len(results['nurses'])}")
        print(f"  大夜班次數: {len(results['night_results'])}")
        print(f"  小夜週次數: {len(results['small_night_results'])}")
        print(f"  假日班次數: {len(results['holiday_results'])}")
        print(f"  定位點:")
        print(f"    大夜: {results['night_last_normal']}")
        print(f"    小夜週: {results['small_night_last_normal']}")
        print(f"    假日班: {results['holiday_last_normal']}")


if __name__ == '__main__':
    main()
