from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional

from openpyxl import load_workbook

SLOT_BY_WEEKDAY_SHIFT = {
    (5, '白班'): 'sat_day',
    (5, '小夜'): 'sat_small_night',
    (6, '大夜'): 'sun_night',
    (6, '白班'): 'sun_day',
    (6, '小夜'): 'sun_small_night',
}


def _split_cell_lines(value: object) -> List[str]:
    if value is None:
        return []
    text = str(value).replace('\r', '\n')
    return [line.strip() for line in text.split('\n') if line and line.strip()]


def _find_holiday_header(ws):
    for row_idx in range(1, min(ws.max_row, 10) + 1):
        row_values = [ws.cell(row_idx, c).value for c in range(1, ws.max_column + 1)]
        if '主值' in row_values or '副值' in row_values:
            name_col = None
            holiday_cols = []
            for c, value in enumerate(row_values, 1):
                if value in ('主值', '副值'):
                    name_col = c
                elif value and '假日' in str(value):
                    holiday_cols.append(c)
            if name_col and holiday_cols:
                return row_idx, name_col, holiday_cols
    return None, None, []


def _parse_slot_type(date_obj: datetime, shift_type: str) -> Optional[str]:
    slot_type = SLOT_BY_WEEKDAY_SHIFT.get((date_obj.weekday(), shift_type))
    if slot_type:
        return slot_type
    if date_obj.weekday() < 5 and shift_type == '白班':
        return 'weekday_holiday_day'
    return None


def read_cross_month_makeup_holidays_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
) -> Dict[str, List[Dict]]:
    """
    讀取假日欄中的跨月固定班與 X月補，並保留來源 holiday 欄位位置。

    回傳中的 column_hint 是「第幾個假日欄」，不是 Excel 絕對欄位索引。
    """
    wb = load_workbook(filepath, data_only=True, rich_text=False)
    ws = wb[sheet_name]

    header_row, name_col, holiday_cols = _find_holiday_header(ws)
    if not header_row or not name_col or not holiday_cols:
        wb.close()
        return {}

    fixed_pattern = re.compile(r'^!?\s*(\d{1,2})/(\d{1,2})\s*(白班|小夜|大夜)\s*$')
    pending_pattern = re.compile(r'^(\d{1,2})月補$')

    result: Dict[str, List[Dict]] = {}

    try:
        for row_idx in range(header_row + 1, ws.max_row + 1):
            raw_name = ws.cell(row=row_idx, column=name_col).value
            if raw_name is None:
                continue

            nurse_name = str(raw_name).lstrip('*').strip()
            if not nurse_name:
                continue

            for holiday_order, col_idx in enumerate(holiday_cols, start=1):
                lines = _split_cell_lines(ws.cell(row=row_idx, column=col_idx).value)

                for line in lines:
                    pending_match = pending_pattern.match(line)
                    if pending_match:
                        month_num = int(pending_match.group(1))
                        if month_num == target_month:
                            result.setdefault(nurse_name, []).append({
                                'type': 'pending',
                                'original_text': line,
                                'column_hint': holiday_order,
                                'source_row': row_idx,
                            })
                        continue

                    fixed_match = fixed_pattern.match(line)
                    if not fixed_match:
                        continue

                    month_num = int(fixed_match.group(1))
                    day_num = int(fixed_match.group(2))
                    shift_type = fixed_match.group(3)

                    # 只保留目標月第一週，避免把中後段人工內容誤判成跨月 seed
                    if month_num != target_month or day_num > 7:
                        continue

                    try:
                        date_obj = datetime(target_year, month_num, day_num)
                    except ValueError:
                        continue

                    slot_type = _parse_slot_type(date_obj, shift_type)
                    if slot_type is None:
                        continue

                    result.setdefault(nurse_name, []).append({
                        'type': 'fixed',
                        'date': date_obj,
                        'shift_type': shift_type,
                        'slot_type': slot_type,
                        'original_text': line,
                        'column_hint': holiday_order,
                        'source_row': row_idx,
                    })
    finally:
        wb.close()

    return result


def find_last_assigned_holiday_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
) -> Optional[str]:
    """
    優先順序：
    1. 明確 # 標記
    2. 由跨月固定班按 slot 順序回推最後一位
    3. 若仍沒有，再回退到舊邏輯
    """
    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    header_row, name_col, holiday_cols = _find_holiday_header(ws)
    if not header_row or not name_col or not holiday_cols:
        wb.close()
        return None

    try:
        marked_name = None
        for row_idx in range(header_row + 1, ws.max_row + 1):
            raw_name = ws.cell(row=row_idx, column=name_col).value
            if raw_name is None:
                continue
            nurse_name = str(raw_name).lstrip('*').strip()

            for col_idx in holiday_cols:
                value = ws.cell(row=row_idx, column=col_idx).value
                if value and '#' in str(value):
                    marked_name = nurse_name

        if marked_name:
            return marked_name
    finally:
        wb.close()

    seed = read_cross_month_makeup_holidays_from_excel(
        filepath,
        sheet_name,
        target_month,
        target_year,
    )

    ordered = []
    rank = {
        'sat_day': 1,
        'sat_small_night': 2,
        'sun_night': 3,
        'sun_day': 4,
        'sun_small_night': 5,
        'weekday_holiday_day': 6,
    }

    for nurse_name, records in seed.items():
        for rec in records:
            if rec['type'] != 'fixed':
                continue
            ordered.append((
                rec['date'],
                rank.get(rec['slot_type'], 999),
                rec.get('column_hint', 999),
                nurse_name,
            ))

    if ordered:
        ordered.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
        return ordered[-1][3]

    return None
