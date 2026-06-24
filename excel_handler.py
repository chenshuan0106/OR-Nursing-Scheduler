"""
Excel 讀寫處理模組
支援大夜班、小夜週、假日班的讀取和輸出
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.styles.colors import Color
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
import re
import unicodedata

from config import COLORS
from date_utils import (
    is_holiday,
    is_saturday,
    parse_date_string,
)


def get_sheet_names(filepath: str) -> List[str]:
    """取得 Excel 所有頁籤名稱"""
    wb = load_workbook(filepath, read_only=True)
    names = wb.sheetnames
    wb.close()
    return names


def read_nurses_from_excel(filepath: str, sheet_name: str = None) -> List[Dict[str, Any]]:
    """
    從 Excel 讀取護理人員資料
    
    Returns:
        [{'主值': '名字', '公休': '2/1-2/7', '大夜': '...', '小夜週': '...', '備註': '...', '_row_index': 行號}, ...]
    """
    wb = load_workbook(filepath, data_only=True)
    
    if sheet_name:
        ws = wb[sheet_name]
    else:
        ws = wb.active
    
    # 找到表頭列（通常在第2或第3列）
    header_row = None
    headers = {}
    
    for row_idx in range(1, 10):  # 檢查前10列
        row_values = [cell.value for cell in ws[row_idx]]
        # 檢查是否有「主值」或「副值」欄位
        if '主值' in row_values or '副值' in row_values:
            header_row = row_idx
            for col_idx, value in enumerate(row_values, 1):
                if value:
                    headers[value] = col_idx
            break
    
    if not header_row:
        wb.close()
        return []
    
    # 讀取資料列
    nurses_data = []
    
    for row_idx in range(header_row + 1, ws.max_row + 1):
        row_data = {'_row_index': row_idx}
        
        for header_name, col_idx in headers.items():
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            row_data[header_name] = cell_value if cell_value else ''
        
        # 檢查是否有名字（主值或副值）
        name = row_data.get('主值', row_data.get('副值', ''))
        if name and str(name).strip():
            nurses_data.append(row_data)
    
    wb.close()
    return nurses_data


def find_last_assigned_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
    nurse_names=None
) -> Optional[str]:
    """
    從 Excel 大夜欄找到上個月最後排班的人
    
    邏輯：找大夜欄中日期屬於「上個月」的最後一筆資料
    """
    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    
    # 計算上個月
    prev_month = target_month - 1 if target_month > 1 else 12
    prev_year = target_year if target_month > 1 else target_year - 1
    
    # 找到表頭
    header_row = None
    name_col = None
    night_col = None
    
    for row_idx in range(1, 10):
        row_values = [cell.value for cell in ws[row_idx]]
        if '主值' in row_values or '副值' in row_values:
            header_row = row_idx
            for col_idx, value in enumerate(row_values, 1):
                if value == '主值' or value == '副值':
                    name_col = col_idx
                elif value == '大夜':
                    night_col = col_idx
            break
    
    if not header_row or not name_col or not night_col:
        wb.close()
        return None

    marked_name = None
    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        night_value = ws.cell(row=row_idx, column=night_col).value

        if not name or not night_value:
            continue

        name = str(name).strip().lstrip('*')
        value_str = str(night_value).strip()
        if '#' in value_str:
            marked_name = name

    if marked_name:
    # 新增：確認這個人還在本月名單
        if nurse_names and marked_name not in nurse_names:
            print(f"'#' 標記的人 {marked_name} 不在本月名單，改用 fallback")
        else:
            return marked_name
    
    # 找上個月最後排班的人
    last_assigned = None
    last_date = None
    
    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        night_value = ws.cell(row=row_idx, column=night_col).value
        
        if not name or not night_value:
            continue
        
        name = str(name).strip().lstrip('*')
        night_str = str(night_value).strip()
        night_str = re.sub(r'\([^)]*\)', '', night_str).strip()
        
        # 解析大夜欄的日期（格式可能是 "2/24.2/25.2/26" 或 "2/24-2/26"）
        dates = []
        
        # 嘗試解析點分隔格式
        if '.' in night_str:
            parts = night_str.split('.')
            for part in parts:
                date = parse_date_string(part.strip(), target_year)
                if date:
                    dates.append(date)
        # 嘗試解析範圍格式
        elif '-' in night_str:
            from date_utils import parse_date_range
            dates = parse_date_range(night_str, target_year)
        else:
            date = parse_date_string(night_str, target_year)
            if date:
                dates.append(date)
        
        # 檢查是否有上個月的日期
        for date in dates:
            if date.month == prev_month:
                if last_date is None or date > last_date:
                    last_date = date
                    last_assigned = name
    
    wb.close()
    return last_assigned


def find_last_assigned_small_night_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
) -> Optional[str]:
    """
    從 Excel 小夜週欄找到上個月最後排班的人
    
    邏輯：找小夜週欄中日期屬於「上個月」的最後一筆資料
    """
    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]
    
    # 計算上個月
    prev_month = target_month - 1 if target_month > 1 else 12
    prev_year = target_year if target_month > 1 else target_year - 1
    
    # 找到表頭
    header_row = None
    name_col = None
    small_night_col = None
    
    for row_idx in range(1, 10):
        row_values = [cell.value for cell in ws[row_idx]]
        if '主值' in row_values or '副值' in row_values:
            header_row = row_idx
            for col_idx, value in enumerate(row_values, 1):
                if value == '主值' or value == '副值':
                    name_col = col_idx
                elif value == '小夜週':
                    small_night_col = col_idx
            break
    
    if not header_row or not name_col or not small_night_col:
        wb.close()
        return None

    marked_name = None
    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        small_night_value = ws.cell(row=row_idx, column=small_night_col).value

        if not name or not small_night_value:
            continue

        name = str(name).strip().lstrip('*')
        value_str = str(small_night_value).strip()
        if '#' in value_str:
            marked_name = name

    if marked_name:
        wb.close()
        return marked_name
    
    # 找上個月最後排班的人
    last_assigned = None
    last_date = None
    
    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        small_night_value = ws.cell(row=row_idx, column=small_night_col).value
        
        if not name or not small_night_value:
            continue
        
        name = str(name).strip().lstrip('*')
        small_night_str = str(small_night_value).strip()
        small_night_str = re.sub(r'\([^)]*\)', '', small_night_str).strip()
        
        # 解析小夜週欄的日期（格式通常是 "2/24-2/28"）
        dates = []
        if '-' in small_night_str:
            from date_utils import parse_date_range
            dates = parse_date_range(small_night_str, target_year)
        
        # 檢查是否有上個月的日期
        for date in dates:
            if date.month == prev_month:
                if last_date is None or date > last_date:
                    last_date = date
                    last_assigned = name
    
    wb.close()
    return last_assigned


def find_last_assigned_holiday_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
) -> Optional[str]:
    """
    從 Excel 假日班欄位找到上個月最後排班的人

    邏輯（依優先順序）：
    1. 找有 # 標記的人（明確定位點）
    2. 找假日欄中屬於「目標月份（本月）」日期的最後一筆
       上個月排班時會預排本月第一週的班（跨月班），
       這些跨月班代表上個月輪值的真正結束點。
       例如：5月班表假日欄出現 6/6白班、6/7小夜 → 最後那人是5月的定位點
    3. 若無跨月班，退回找上個月日期的最後一筆（舊邏輯備用）
    """
    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]

    # 找到表頭
    header_row = None
    name_col = None
    holiday_cols = []

    for row_idx in range(1, 10):
        row_values = [cell.value for cell in ws[row_idx]]
        if '主值' in row_values or '副值' in row_values:
            header_row = row_idx
            for col_idx, value in enumerate(row_values, 1):
                if value == '主值' or value == '副值':
                    name_col = col_idx
                elif value == '假日':
                    holiday_cols.append(col_idx)
            break

    if not header_row or not name_col or not holiday_cols:
        wb.close()
        return None

    # 1. 找 # 標記
    marked_name = None
    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        if not name:
            continue
        name = str(name).strip().lstrip('*')
        for col_pos, col_idx in enumerate(holiday_cols, 1):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if not cell_value:
                continue
            if '#' in str(cell_value):
                marked_name = name

    if marked_name:
        wb.close()
        return marked_name

    # 2. 找假日欄中屬於「目標月份（本月）」的跨月班日期
    cross_month_candidates = []  # [(date, col_idx, shift_priority, name)]
    pending_makeup_count = 0

    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        if not name:
            continue
        name = str(name).strip().lstrip('*')

        for col_pos, col_idx in enumerate(holiday_cols):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if not cell_value:
                continue
            cell_str = str(cell_value).strip()
            if re.match(rf'^{target_month}月補$', cell_str):
                pending_makeup_count += 1
            # 排除純文字記錄（換心、P班、公休說明等）
            if any(kw in cell_str for kw in ['換心', 'P1', 'P2', '補班', '月補']):
                continue
            if cell_str.startswith('公休') or cell_str.startswith('婚假'):
                continue
            # 提取第一個日期
            date_match = re.search(r'(\d{1,2}/\d{1,2})', cell_str)
            if date_match:
                date = parse_date_string(date_match.group(1), target_year)
                # 跨月預告班只排下個月第一週（最多到第7日），加上 day <= 7 避免把當月正常班誤判
                if date and date.month == target_month and date.day <= 7:
                    shift_priority = 0
                    if '大夜' in cell_str:
                        shift_priority = 1
                    elif '白班' in cell_str:
                        shift_priority = 2
                    elif '小夜' in cell_str:
                        shift_priority = 3
                    cross_month_candidates.append((date, col_pos, shift_priority, name))

    if cross_month_candidates:
        latest_date = max(item[0] for item in cross_month_candidates)
        latest_candidates = [item for item in cross_month_candidates if item[0] == latest_date]

        if len(latest_candidates) >= 3:
            desired_shift_priority = 1 if pending_makeup_count >= 2 else 2
            selected = next(
                (item for item in latest_candidates if item[2] == desired_shift_priority),
                sorted(latest_candidates, key=lambda x: x[2])[-1],
            )
        else:
            selected = sorted(cross_month_candidates, key=lambda x: (x[0], x[1], x[2]))[-1]

        last_name = selected[3]
        print(f"假日班定位點（跨月班）: {last_name} (找到 {len(cross_month_candidates)} 筆跨月記錄)")
        wb.close()
        return last_name

    # 3. 退回舊邏輯：找上個月日期的最後一筆
    prev_month = target_month - 1 if target_month > 1 else 12
    last_assigned = None
    last_date = None

    for row_idx in range(header_row + 1, ws.max_row + 1):
        name = ws.cell(row=row_idx, column=name_col).value
        if not name:
            continue
        name = str(name).strip().lstrip('*')

        for col_pos, col_idx in enumerate(holiday_cols, 1):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if not cell_value:
                continue
            cell_str = str(cell_value).strip()
            date_match = re.search(r'(\d{1,2}/\d{1,2})', cell_str)
            if date_match:
                date = parse_date_string(date_match.group(1), target_year)
                if date and date.month == prev_month:
                    if last_date is None or date > last_date:
                        last_date = date
                        last_assigned = name

    wb.close()
    return last_assigned


def create_schedule_excel_multi_sheet(
    output_path: str,
    title: str,
    all_results: Dict[str, Dict],
    year: int,
    month: int,
):
    """
    建立多頁籤的排班結果 Excel
    
    all_results 格式:
    {
        '頁籤名': {
            'nurses': [NurseInfo, ...],
            'night_results': [{'nurse': NurseInfo, 'dates': [...], ...}, ...],
            'small_night_results': [...],
            'holiday_results': [...],  # 假日班結果
            'night_last_normal': '名字',
            'small_night_last_normal': '名字',
            'holiday_last_normal': '名字',
        },
        ...
    }
    """
    wb = Workbook()
    
    # 移除預設的工作表
    default_sheet = wb.active
    
    pre_schedule_checks = {
        sheet_name: results.get('pre_schedule_check')
        for sheet_name, results in all_results.items()
        if results.get('pre_schedule_check')
    }

    first_sheet = True

    if pre_schedule_checks:
        default_sheet.title = '排班前核對'
        _write_pre_schedule_check_sheet(default_sheet, title, pre_schedule_checks, year, month)
        first_sheet = False
    
    for sheet_name, results in all_results.items():
        output_sheet_name = _format_output_sheet_name(sheet_name, year, month)
        if first_sheet:
            ws = default_sheet
            ws.title = output_sheet_name
            first_sheet = False
        else:
            ws = wb.create_sheet(title=output_sheet_name)
        
        _write_schedule_sheet(
            ws, title, results, year, month, sheet_name
        )
    
    wb.save(output_path)


def _format_output_sheet_name(sheet_name: str, year: int, month: int) -> str:
    """Use the scheduled target month in output sheet tabs."""
    target_prefix = f"{year - 1911}.{month:02d}"
    match = re.match(r'^\d{2,3}\.\d{1,2}(.*)$', sheet_name)
    if match:
        return f"{target_prefix}{match.group(1)}"[:31]

    match = re.match(r'^\d{2,3}年\d{1,2}月(.*)$', sheet_name)
    if match:
        return f"{year - 1911}年{month}月{match.group(1)}"[:31]

    return sheet_name[:31]


def _write_pre_schedule_check_sheet(
    ws,
    title: str,
    pre_schedule_checks: Dict[str, Dict],
    year: int,
    month: int,
):
    """
    寫入排班前核對表。

    內容保留欲排班月份的人名順序，並標出從上個月 Excel 欄位抓到的
    大夜、小夜週、假日班定位點與下一位起排人。
    """
    base_font_name = '標楷體'
    header_font = Font(name=base_font_name, bold=True)
    title_font = Font(name=base_font_name, bold=True, size=14)
    normal_font = Font(name=base_font_name)
    marker_font = Font(name=base_font_name, bold=True, color='FF0000')
    warning_font = Font(name=base_font_name, color='FF0000')
    header_fill = PatternFill(start_color='DDDDDD', end_color='DDDDDD', fill_type='solid')
    section_fill = PatternFill(start_color='EAF2F8', end_color='EAF2F8', fill_type='solid')
    marker_fill = PatternFill(start_color='FFF2CC', end_color='FFF2CC', fill_type='solid')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)

    detail_columns = [
        '頁籤', '順序', 'Excel列', '護理人員',
        '上月大夜欄', '上月小夜週欄', '上月假日欄',
        '上月帶入班',
        '大夜定位', '大夜下一位',
        '小夜定位', '小夜下一位',
        '假日定位', '假日下一位',
        '備註',
    ]
    summary_columns = [
        '頁籤', '人數',
        '上月最後大夜', '大夜下一位',
        '上月最後小夜', '小夜下一位',
        '上月最後假日', '假日下一位',
        '補班說明', '警示',
    ]

    max_col = max(len(detail_columns), len(summary_columns))
    ws['A1'] = f'{title} - 排班前核對'
    ws['A1'].font = title_font
    ws['A1'].alignment = center_align
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)

    ws['A2'] = f'目標月份：{year}年{month}月'
    ws['A2'].font = normal_font
    ws['A2'].alignment = center_align
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=max_col)

    def _locator_note(nurses, last_name: str, label: str) -> str:
        names = {nurse.name for nurse in nurses}
        if not last_name:
            return f'{label}未找到，將從第一位開始'
        if last_name not in names:
            return f'{label}定位點「{last_name}」不在本月名單，將從第一位開始'
        return ''

    # 摘要區
    row = 4
    ws.cell(row=row, column=1, value='定位點摘要')
    ws.cell(row=row, column=1).font = header_font
    ws.cell(row=row, column=1).fill = section_fill
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(summary_columns))

    row += 1
    for col_idx, col_name in enumerate(summary_columns, 1):
        cell = ws.cell(row=row, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = center_align

    row += 1
    for sheet_name, check in pre_schedule_checks.items():
        nurses = check.get('nurses', [])
        notes = [
            _locator_note(nurses, check.get('last_night', ''), '大夜'),
            _locator_note(nurses, check.get('last_small_night', ''), '小夜週'),
            _locator_note(nurses, check.get('last_holiday', ''), '假日班'),
        ]
        note_text = '；'.join(note for note in notes if note)
        values = [
            sheet_name,
            len(nurses),
            check.get('last_night', '') or '未找到',
            check.get('next_night', ''),
            check.get('last_small_night', '') or '未找到',
            check.get('next_small_night', ''),
            check.get('last_holiday', '') or '未找到',
            check.get('next_holiday', ''),
            check.get('manual_makeup_note', ''),
            note_text,
        ]
        for col_idx, value in enumerate(values, 1):
            cell = ws.cell(row=row, column=col_idx, value=value)
            cell.font = warning_font if col_idx == len(values) and note_text else normal_font
            cell.border = border
            cell.alignment = center_align
        row += 1

    # 明細區
    row += 2
    ws.cell(row=row, column=1, value='名單順序明細')
    ws.cell(row=row, column=1).font = header_font
    ws.cell(row=row, column=1).fill = section_fill
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=len(detail_columns))

    row += 1
    detail_header_row = row
    for col_idx, col_name in enumerate(detail_columns, 1):
        cell = ws.cell(row=row, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = center_align

    row += 1
    for sheet_name, check in pre_schedule_checks.items():
        nurses = check.get('nurses', [])
        last_night = check.get('last_night', '')
        last_small_night = check.get('last_small_night', '')
        last_holiday = check.get('last_holiday', '')
        next_night = check.get('next_night', '')
        next_small_night = check.get('next_small_night', '')
        next_holiday = check.get('next_holiday', '')
        carryover_by_nurse = check.get('carryover_by_nurse', {})
        previous_entries = check.get('previous_entries', {})

        for order, nurse in enumerate(nurses, 1):
            is_last_night = nurse.name == last_night
            is_last_small_night = nurse.name == last_small_night
            is_last_holiday = nurse.name == last_holiday
            notes = []
            if nurse.name == next_night:
                notes.append('大夜下一位')
            if nurse.name == next_small_night:
                notes.append('小夜週下一位')
            if nurse.name == next_holiday:
                notes.append('假日班下一位')

            values = [
                sheet_name,
                order,
                nurse.row_index,
                nurse.name,
                previous_entries.get(nurse.name, {}).get('night', ''),
                previous_entries.get(nurse.name, {}).get('small_night', ''),
                previous_entries.get(nurse.name, {}).get('holiday', ''),
                '\n'.join(carryover_by_nurse.get(nurse.name, [])),
                '上月最後' if is_last_night else '',
                '起排' if nurse.name == next_night else '',
                '上月最後' if is_last_small_night else '',
                '起排' if nurse.name == next_small_night else '',
                '上月最後' if is_last_holiday else '',
                '起排' if nurse.name == next_holiday else '',
                '、'.join(notes),
            ]
            for col_idx, value in enumerate(values, 1):
                cell = ws.cell(row=row, column=col_idx, value=value)
                is_marker_cell = col_idx in [8, 9, 10, 11, 12, 13, 14] and value
                cell.font = marker_font if is_marker_cell else normal_font
                if is_marker_cell:
                    cell.fill = marker_fill
                cell.border = border
                cell.alignment = center_align
            row += 1

    widths = [16, 8, 10, 14, 18, 18, 22, 18, 12, 12, 12, 12, 12, 12, 24]
    for col_idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = ws.cell(row=detail_header_row + 1, column=1)


def _is_all_dates_other_month(text: str, year: int, month: int) -> bool:
    """
    判斷文字中的所有日期是否都屬於上個月或下個月（非當月）
    支援格式：
    - 日期範圍：4/6-4/12
    - 點分隔日期：4/2.4/3.4/4
    - 補班格式：補4/3(原4/5白班) 或 補2/27(原4/5白班)
      注意：對於補班格式，判斷的是「(原X/X班)」中的日期，而不是「補X/X」中的日期
    """
    if not text:
        return False
    
    from date_utils import parse_date_string, parse_dot_separated_dates
    
    # 計算上個月和下個月
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    # 分割成多行
    lines = text.split('\n')
    all_dates = []
    
    # 先檢查整個文字中是否有「(原X/X班)」格式
    has_original_date = False
    for line in lines:
        if not line.strip():
            continue
        original_match = re.search(r'\(原\s*(\d{1,2}/\d{1,2})', line)
        if original_match:
            has_original_date = True
            break
    
    # 如果有「(原X/X班)」格式，只提取這些日期，不提取「補X/X」中的日期
    for line in lines:
        if not line.strip():
            continue
        
        # 優先檢查補班格式中的「(原X/X班)」日期（例如：補2/27(原4/5白班) 或 補2/27\n(原4/5白班)）
        # 注意：判斷的是「(原X/X班)」中的日期，而不是「補X/X」中的日期
        original_match = re.search(r'\(原\s*(\d{1,2}/\d{1,2})', line)
        if original_match:
            date_str = original_match.group(1)
            date = parse_date_string(date_str, year)
            if date:
                all_dates.append(date)
                continue
        
        # 如果沒有「(原X/X班)」格式，才檢查其他格式
        if not has_original_date:
            # 1. 檢查日期範圍格式（例如：4/6-4/12）
            date_range_match = re.search(r'(\d{1,2}/\d{1,2})\s*-\s*(\d{1,2}/\d{1,2})', line)
            if date_range_match:
                start_str = date_range_match.group(1)
                end_str = date_range_match.group(2)
                start_date = parse_date_string(start_str, year)
                end_date = parse_date_string(end_str, year)
                
                if start_date and end_date:
                    all_dates.extend([start_date, end_date])
                    continue
            
            # 2. 檢查點分隔日期格式（例如：4/2.4/3.4/4）
            dot_dates_match = re.search(r'(\d{1,2}/\d{1,2}(?:\.\d{1,2}/\d{1,2})+)', line)
            if dot_dates_match:
                dates_str = dot_dates_match.group(1)
                dates = parse_dot_separated_dates(dates_str, year)
                if dates:
                    all_dates.extend(dates)
                    continue
            
            # 3. 檢查「補X/X」格式（但只有在沒有「(原X/X班)」格式時才提取）
            # 注意：如果已經有「(原X/X班)」格式，就不應該提取「補X/X」中的日期
            if not has_original_date:
                makeup_match = re.search(r'補(\d{1,2}/\d{1,2})', line)
                if makeup_match:
                    date_str = makeup_match.group(1)
                    date = parse_date_string(date_str, year)
                    if date:
                        all_dates.append(date)
                        continue
    
    # 如果沒有找到任何日期，返回 False
    if not all_dates:
        return False
    
    # 檢查所有日期是否都屬於下個月（非當月且非上個月）
    # 注意：只判斷下個月，上個月的補班應該用其他顏色（黑色）
    result = all(d.month == next_month and d.year == next_year for d in all_dates)
    return result


def _to_inline_font(font):
    if isinstance(font, InlineFont):
        return font
    if font is None:
        return InlineFont()
    if isinstance(font, str):
        return InlineFont(rFont=font)
    color = font.color
    if isinstance(color, str):
        color = Color(rgb=color)
    return InlineFont(
        rFont=font.name,
        sz=font.size,
        b=font.bold,
        i=font.italic,
        color=color,
    )


def _build_night_rich_text(results, base_font, saturday_font, holiday_font, gray_font):
    base_font = _to_inline_font(base_font)
    saturday_font = _to_inline_font(saturday_font)
    holiday_font = _to_inline_font(holiday_font)
    gray_font = _to_inline_font(gray_font)
    parts = []
    for result_idx, result in enumerate(results):
        if result_idx > 0:
            parts.append(TextBlock(base_font, "\n"))
        dates = result.get('dates', [])
        is_next_month = result.get('is_next_month', False)
        
        # 如果是下個月的 cost，全部用灰色（包括點號）
        if is_next_month:
            for i, date in enumerate(dates):
                if i > 0:
                    parts.append(TextBlock(gray_font, "."))
                parts.append(TextBlock(gray_font, f"{date.month}/{date.day}"))
        else:
            # 當月的 cost，根據日期特性設置顏色
            for i, date in enumerate(dates):
                if i > 0:
                    parts.append(TextBlock(base_font, "."))
                if is_holiday(date):
                    font = holiday_font
                elif is_saturday(date):
                    font = saturday_font
                else:
                    font = base_font
                parts.append(TextBlock(font, f"{date.month}/{date.day}"))
        
        comp_dates = [d for d in dates if is_holiday(d) and d.weekday() < 5]
        if comp_dates:
            comp_unique = sorted({d.date(): d for d in comp_dates}.values())
            comp_text = ','.join(f"{d.month}/{d.day}" for d in comp_unique)
            comp_font = gray_font if is_next_month else base_font
            parts.append(TextBlock(comp_font, f" ({comp_text})"))

        if result.get('is_last_normal'):
            parts.append(TextBlock(base_font, "#"))
    return CellRichText(parts)


def _build_small_night_rich_text(results, base_font, holiday_font, gray_font, target_month):
    """建立小夜週的富文本格式，處理跨月灰色"""
    base_font = _to_inline_font(base_font)
    holiday_font = _to_inline_font(holiday_font)
    gray_font = _to_inline_font(gray_font)
    parts = []
    
    for result_idx, result in enumerate(results):
        if result_idx > 0:
            parts.append(TextBlock(base_font, "\n"))
        
        dates = result.get('dates', [])
        if not dates:
            continue
        
        first_date = dates[0]
        last_date = dates[-1]
        
        # 判斷整個 cost 是否為下個月
        is_next_month = result.get('is_next_month', False)
        
        if is_next_month:
            # 整個 cost 都用灰色
            parts.append(TextBlock(gray_font, f"{first_date.month}/{first_date.day}"))
            parts.append(TextBlock(gray_font, "-"))
            parts.append(TextBlock(gray_font, f"{last_date.month}/{last_date.day}"))
        else:
            # 屬於當月的 cost，逐個日期檢查是否為國定假日
            # 首日期
            if is_holiday(first_date):
                parts.append(TextBlock(holiday_font, f"{first_date.month}/{first_date.day}"))
            else:
                parts.append(TextBlock(base_font, f"{first_date.month}/{first_date.day}"))
            
            # 連接符號
            parts.append(TextBlock(base_font, "-"))
            
            # 尾日期
            if is_holiday(last_date):
                parts.append(TextBlock(holiday_font, f"{last_date.month}/{last_date.day}"))
            else:
                parts.append(TextBlock(base_font, f"{last_date.month}/{last_date.day}"))
        
        # 加上 # 標記（如果是正常輪序最後一個）
        comp_dates = [d for d in dates if is_holiday(d) and d.weekday() < 5]
        if comp_dates:
            comp_unique = sorted({d.date(): d for d in comp_dates}.values())
            comp_text = ','.join(f"{d.month}/{d.day}" for d in comp_unique)
            comp_font = gray_font if is_next_month else base_font
            parts.append(TextBlock(comp_font, f" ({comp_text})"))

        if result.get('is_last_normal'):
            parts.append(TextBlock(base_font, "#"))
    
    return CellRichText(parts)


def _get_cell_text(value) -> str:
    if isinstance(value, CellRichText):
        return ''.join(block.text for block in value)
    if value is None:
        return ''
    return str(value)


def _is_p_or_transplant_text(text) -> bool:
    if not text:
        return False
    text = str(text)
    return (
        '月大P1' in text or '月小P1' in text or
        '月大P2' in text or '月小P2' in text or
        '月P1' in text or '月P2' in text or
        '月換心' in text or '月待命' in text
    )


def _has_future_month_label(text, target_month: int) -> bool:
    if not text:
        return False
    text = str(text)
    for match in re.finditer(r'(\d{1,2})月', text):
        if int(match.group(1)) > target_month:
            return True
    return False


def _apply_identity_font(ws, green_font, gray_font, target_month: int):
    for row in ws.iter_rows():
        for cell in row:
            text = _get_cell_text(cell.value)
            if _has_future_month_label(text, target_month):
                cell.font = gray_font
            elif _is_p_or_transplant_text(text):
                cell.font = green_font


def _text_display_width(text: str) -> int:
    width = 0
    for ch in text:
        if unicodedata.east_asian_width(ch) in ('F', 'W'):
            width += 2
        else:
            width += 1
    return width


def _write_schedule_sheet(
    ws,
    title: str,
    results: Dict,
    year: int,
    month: int,
    sheet_name: str,
):
    """
    寫入單一頁籤的排班結果
    """
    nurses = results.get('nurses', [])
    night_results = results.get('night_results', [])
    small_night_results = results.get('small_night_results', [])
    holiday_results = results.get('holiday_results', [])
    night_identity_skipped = results.get('night_identity_skipped', set())
    small_night_identity_skipped = results.get('small_night_identity_skipped', set())
    manual_makeup_entries = results.get('manual_makeup_entries', [])
    
    # 定義欄位
    columns = [
        '主值', '公休', '備註', '大夜', '小夜週', '補班',
        '假日', '假日', '假日', '假日'
    ]
    
    # 樣式定義 - 使用標楷體
    base_font_name = '標楷體'
    header_font = Font(name=base_font_name, bold=True)
    header_fill = PatternFill(start_color='DDDDDD', end_color='DDDDDD', fill_type='solid')
    border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )
    center_align = Alignment(horizontal='center', vertical='center', wrap_text=True)
    no_wrap_center_align = Alignment(horizontal='center', vertical='center', wrap_text=False)
    
    # 顏色 - 全部使用標楷體
    red_font = Font(name=base_font_name, color='FF0000')
    blue_font = Font(name=base_font_name, color='0000FF')
    green_font = Font(name=base_font_name, color='008000')
    gray_font = Font(name=base_font_name, color='A0A0A0')
    black_font = Font(name=base_font_name, color='000000')
    normal_font = Font(name=base_font_name)
    
    # 寫入標題
    ws['A1'] = title
    ws['A1'].font = Font(name=base_font_name, bold=True, size=14)
    ws['A1'].alignment = center_align
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
    
    # 寫入表頭
    header_row = 2
    for col_idx, col_name in enumerate(columns, 1):
        cell = ws.cell(row=header_row, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.border = border
        cell.alignment = center_align
    
    # 建立護理人員到結果的對照表
    nurse_night_results = {}  # {name: [result, ...]}
    for result in night_results:
        name = result['nurse'].name
        if name not in nurse_night_results:
            nurse_night_results[name] = []
        nurse_night_results[name].append(result)
    
    nurse_small_night_results = {}
    for result in small_night_results:
        name = result['nurse'].name
        if name not in nurse_small_night_results:
            nurse_small_night_results[name] = []
        nurse_small_night_results[name].append(result)

    manual_makeup_text_by_nurse = {}
    for entry in manual_makeup_entries:
        nurse_name = entry.get('nurse_name')
        target_date = entry.get('date')
        shift_type = entry.get('shift_type')
        if not nurse_name or not target_date or not shift_type:
            continue
        note_text = (entry.get('note') or '手動新增欠班').strip()
        text = f"{target_date.month}月補@{target_date.month}/{target_date.day}{shift_type} ({note_text})"
        manual_makeup_text_by_nurse.setdefault(nurse_name, []).append(text)
    
    # 假日班結果：按輪次分配到欄位
    # 邏輯：根據 column_index 分配
    holiday_assignments = {}

    if holiday_results and nurses:
        for result in holiday_results:
            name = result.nurse.name
            new_is_comp = getattr(result, 'is_holiday_compensation', False)
            col_num = getattr(result, 'column_index', 1)
            if col_num < 1:
                col_num = 1
            if col_num > 4:
                col_num = 4
            key = (name, col_num)
            existing = holiday_assignments.get(key)
            if not existing:
                holiday_assignments[key] = [result]
            else:
                existing_is_comp = any(getattr(r, 'is_holiday_compensation', False) for r in existing)
                existing_has_normal = any(not getattr(r, 'is_holiday_compensation', False) for r in existing)
                # 同一格允許補休+正常班（擠在一起），避免重複塞相同類型
                if new_is_comp and existing_is_comp:
                    continue
                if (not new_is_comp) and existing_has_normal:
                    continue
                existing.append(result)

    # ????????
    data_row = header_row + 1
    
    for nurse in nurses:
        row_idx = data_row
        
        # 1. 主值
        cell = ws.cell(row=row_idx, column=1, value=nurse.original_name)
        cell.border = border
        cell.font = normal_font
        cell.alignment = center_align
        
        # 2. 公休
        leave_text = nurse.original_leave_text
        if leave_text:
            leave_text = re.sub(r'\s*[\u3001\uff0c,;\uff1b]\s*', '\n', leave_text)

        # 補上「非本月」的 P班/換心 前綴（這些資訊來自大夜/小夜週等欄位，不在公休欄）
        # 例如：測試甲公休='6/8-6/21'，但 is_p1={'5月':'大P1'} → 輸出 '5月大P1\n6/8-6/21'
        prev_month_labels = []
        month_key_current = f'{month}月'
        # 收集所有非本月的 P班/換心 標記
        for mk, pv in nurse.is_p1.items():
            if mk != month_key_current:
                label = f'{mk}{pv}' if isinstance(pv, str) else f'{mk}P1'
                # 避免重複（已在 leave_text 中存在的不再加）
                if leave_text and label in leave_text:
                    continue
                prev_month_labels.append((mk, label))
        for mk, pv in nurse.is_p2.items():
            if mk != month_key_current:
                label = f'{mk}{pv}' if isinstance(pv, str) else f'{mk}P2'
                if leave_text and label in leave_text:
                    continue
                prev_month_labels.append((mk, label))
        for mk, tv in nurse.is_transplant.items():
            if mk != month_key_current and tv:
                label = f'{mk}換心'
                if leave_text and label in leave_text:
                    continue
                prev_month_labels.append((mk, label))
        for mk, tv in getattr(nurse, 'is_standby', {}).items():
            if mk != month_key_current and tv:
                label = f'{mk}待命'
                if leave_text and label in leave_text:
                    continue
                prev_month_labels.append((mk, label))
        # 按月份排序，讓較早的月份在前
        if prev_month_labels:
            def month_sort_key(item):
                mk_str = item[0]  # e.g. '5月'
                try:
                    return int(mk_str.replace('月', ''))
                except Exception:
                    return 99
            prev_month_labels.sort(key=month_sort_key)
            prefix = '\n'.join(lbl for _, lbl in prev_month_labels)
            if leave_text:
                leave_text = prefix + '\n' + leave_text
            else:
                leave_text = prefix
        
        # 檢查是否包含「滿55歲跳大夜」或「55歲跳大夜」等相關文字
        has_age_55_restriction = False
        if leave_text and ('滿55歲跳大夜' in leave_text or '55歲跳大夜' in leave_text or '55歲以上跳大夜' in leave_text):
            has_age_55_restriction = True
            purple_font = Font(name='標楷體', color=COLORS['purple'])
        else:
            purple_font = None
        
        # 判斷是否所有日期都是上個月或下個月（優先級低於紫色）
        is_other_month_leave = _is_all_dates_other_month(leave_text, year, month) if leave_text else False
        
        cell = ws.cell(row=row_idx, column=2, value=leave_text)
        cell.border = border
        # 優先使用紫色（滿55歲跳大夜），其次灰色（上個月或下個月），最後預設
        if purple_font:
            cell.font = purple_font
        elif is_other_month_leave:
            cell.font = gray_font
        else:
            cell.font = normal_font
        cell.alignment = center_align
        
        # 3. 備註（全部綠色）
        # 先收集已補的待補班日期（從排班結果中）
        completed_makeup_dates = set()
        
        # 檢查大夜班補班結果
        if nurse.name in nurse_night_results:
            for result in nurse_night_results[nurse.name]:
                if result.get('is_makeup') and result.get('original_dates'):
                    # 將原始日期轉換為集合以便比較
                    original_dates_set = set(result['original_dates'])
                    completed_makeup_dates.add(('大夜', tuple(sorted(original_dates_set))))
        
        # 檢查小夜班補班結果
        if nurse.name in nurse_small_night_results:
            for result in nurse_small_night_results[nurse.name]:
                if result.get('is_makeup') and result.get('original_dates'):
                    original_dates_set = set(result['original_dates'])
                    completed_makeup_dates.add(('小夜', tuple(sorted(original_dates_set))))
        
        # 處理備註文字：移除已補的待補班資訊
        remarks_text = nurse.remarks if nurse.remarks else ''
        if remarks_text:
            # 從備註中提取所有待補班資訊
            from shift_utils import extract_pending_makeup
            pending_makeups = extract_pending_makeup(remarks_text, year)
            
            # 檢查每個待補班是否已補
            for makeup in pending_makeups:
                makeup_dates_set = set(makeup['dates'])
                makeup_key = (makeup['shift_type'], tuple(sorted(makeup_dates_set)))
                
                # 如果已補，從備註文字中移除
                if makeup_key in completed_makeup_dates:
                    first_date = min(makeup['dates'])
                    last_date = max(makeup['dates'])
                    date_range = f"{first_date.month}/{first_date.day}-{last_date.month}/{last_date.day}"
                    # 移除待補班文字（支援多種格式）
                    pattern = rf'待補班[:\s]*{re.escape(date_range)}{re.escape(makeup["shift_type"])}'
                    remarks_text = re.sub(pattern, '', remarks_text, flags=re.IGNORECASE)
                    # 移除多餘的換行和空白
                    remarks_text = re.sub(r'\n\s*\n', '\n', remarks_text).strip()
        
        remarks_parts = []
        if remarks_text.strip():
            remarks_parts.append(remarks_text.strip())
        
        # 新產生的待補班
        if nurse.new_pending_makeup:
            for makeup in nurse.new_pending_makeup:
                first_date = min(makeup['dates'])
                last_date = max(makeup['dates'])
                date_range = f"{first_date.month}/{first_date.day}-{last_date.month}/{last_date.day}"
                remarks_parts.append(f"待補班:{date_range}{makeup['shift_type']}")
        
        final_remarks_text = '\n'.join(remarks_parts) if remarks_parts else ''
        green_font = Font(name='標楷體', color=COLORS['p_shift_green'])
        
        # 判斷是否所有日期都是上個月或下個月
        is_other_month_remarks = _is_all_dates_other_month(final_remarks_text, year, month) if final_remarks_text else False
        
        cell = ws.cell(row=row_idx, column=3, value=final_remarks_text)
        cell.border = border
        # 如果是上個月或下個月，使用灰色；否則使用綠色
        cell.font = gray_font if is_other_month_remarks else green_font
        cell.alignment = center_align

        # 補班（專屬欄位，放在小夜週與第一個假日欄中間）
        makeup_text = nurse.makeup_text or ''
        manual_makeup_lines = manual_makeup_text_by_nurse.get(nurse.name, [])
        if manual_makeup_lines:
            existing_lines = [line.strip() for line in makeup_text.splitlines() if line.strip()]
            for line in manual_makeup_lines:
                if line not in existing_lines:
                    existing_lines.append(line)
            makeup_text = '\n'.join(existing_lines)
        if makeup_text:
            makeup_text = re.sub(r'\s*[、,;；\n]+\s*', '\n', makeup_text).strip()
            from shift_utils import extract_pending_makeup
            completed_makeup_dates = set()
            if nurse.name in nurse_night_results:
                for result in nurse_night_results[nurse.name]:
                    if result.get('is_makeup') and result.get('original_dates'):
                        completed_makeup_dates.add(('大夜', tuple(sorted(set(result['original_dates'])))))
            if nurse.name in nurse_small_night_results:
                for result in nurse_small_night_results[nurse.name]:
                    if result.get('is_makeup') and result.get('original_dates'):
                        completed_makeup_dates.add(('小夜', tuple(sorted(set(result['original_dates'])))))
            pending_makeups = extract_pending_makeup(makeup_text, year)
            for makeup in pending_makeups:
                makeup_dates_set = set(makeup['dates'])
                makeup_key = (makeup['shift_type'], tuple(sorted(makeup_dates_set)))
                if makeup_key in completed_makeup_dates:
                    first_date = min(makeup['dates'])
                    last_date = max(makeup['dates'])
                    date_range = f"{first_date.month}/{first_date.day}-{last_date.month}/{last_date.day}"
                    pattern = rf'待補班[:\s]*{re.escape(date_range)}{re.escape(makeup["shift_type"])}'
                    makeup_text = re.sub(pattern, '', makeup_text, flags=re.IGNORECASE)
                    makeup_text = re.sub(r'\n\s*\n', '\n', makeup_text).strip()

        makeup_cell = ws.cell(row=row_idx, column=6, value=makeup_text)
        makeup_cell.border = border
        makeup_cell.font = gray_font if makeup_text and _is_all_dates_other_month(makeup_text, year, month) else normal_font
        makeup_cell.alignment = center_align
        
        # 取得當月身分（P1/P2/換心）
        month_key = f'{month}月'
        nurse_identity = None
        if nurse.is_transplant.get(month_key):
            nurse_identity = f'{month_key}換心'
        elif getattr(nurse, 'is_standby', {}).get(month_key):
            nurse_identity = f'{month_key}待命'
        elif nurse.is_p1.get(month_key):
            p1_value = nurse.is_p1[month_key]
            if isinstance(p1_value, str):
                nurse_identity = f'{month_key}{p1_value}'  # 例如：3月大P1
            else:
                nurse_identity = f'{month_key}P1'
        elif nurse.is_p2.get(month_key):
            p2_value = nurse.is_p2[month_key]
            if isinstance(p2_value, str):
                nurse_identity = f'{month_key}{p2_value}'  # 例如：3月大P2
            else:
                nurse_identity = f'{month_key}P2'
        
        # 4. 大夜
        night_cell = ws.cell(row=row_idx, column=4)
        if nurse.name in nurse_night_results:
            # 有排班結果，顯示排班結果
            night_cell.value = _build_night_rich_text(
                nurse_night_results[nurse.name],
                normal_font,
                blue_font,
                red_font,
                gray_font,
            )
            night_cell.font = normal_font
        elif nurse.name in night_identity_skipped and has_age_55_restriction:
            # 有輪到但因55歲被跳過，顯示紫色標記
            night_cell.value = '滿55歲跳大夜'
            night_cell.font = purple_font
        elif nurse.name in night_identity_skipped and nurse_identity:
            # 有輪到但因身分別被跳過，顯示綠色身分
            night_cell.value = nurse_identity
            night_cell.font = green_font
        else:
            # 沒有輪到或沒有身分別，留空白
            night_cell.value = ''
            night_cell.font = normal_font
        night_cell.border = border
        night_cell.alignment = no_wrap_center_align
        
        # 5. 小夜週
        small_night_cell = ws.cell(row=row_idx, column=5)
        if nurse.name in nurse_small_night_results:
            # 有排班結果，顯示排班結果
            small_night_cell.value = _build_small_night_rich_text(
                nurse_small_night_results[nurse.name],
                normal_font,
                red_font,
                gray_font,
                month,
            )
            small_night_cell.font = normal_font
        elif nurse.name in small_night_identity_skipped and nurse_identity:
            # 有輪到但因身分別被跳過，顯示綠色身分
            small_night_cell.value = nurse_identity
            small_night_cell.font = green_font
        else:
            # 沒有輪到或沒有身分別，留空白
            small_night_cell.value = ''
            small_night_cell.font = normal_font
        small_night_cell.border = border
        small_night_cell.alignment = no_wrap_center_align
        
        # 7, 8, 9, 10. 假日班四個欄位
        for col_num in [1, 2, 3, 4]:
            col_idx = 6 + col_num  # 第 7, 8, 9, 10 欄
            holiday_cell = ws.cell(row=row_idx, column=col_idx)
            result_list = holiday_assignments.get((nurse.name, col_num))
            
            # 檢查是否有國定假日補償（優先顯示補休假資訊）
            has_holiday_compensation = False
            if result_list:
                for result in result_list:
                    if result.is_holiday_compensation:
                        has_holiday_compensation = True
                        break
            
            if has_holiday_compensation:
                # 如果有國定假日補償，顯示補休假資訊（優先於身分）
                from holiday_shift import format_holiday_shift_result
                texts = [format_holiday_shift_result(r, month) for r in result_list]
                holiday_text = '\n'.join(texts)
                holiday_cell.value = holiday_text
                
                # 判斷補班日期是否為下個月（判斷「(原X/X班)」中的日期）
                is_next_month_holiday = _is_all_dates_other_month(holiday_text, year, month) if holiday_text else False
                
                if is_next_month_holiday:
                    # 如果原班日期是下個月，使用灰色
                    holiday_cell.font = gray_font
                else:
                    # 如果原班日期是當月，使用黑色
                    holiday_cell.font = black_font
            elif result_list:
                # 有排班結果（含跳過記錄），顯示排班結果
                from holiday_shift import format_holiday_shift_result
                texts = [format_holiday_shift_result(r, month) for r in result_list]
                holiday_text = '\n'.join(texts)
                holiday_cell.value = holiday_text

                first_result = result_list[0]
                # 判斷補班日期是否為下個月（判斷「(原X/X班)」中的日期）
                is_next_month_holiday = _is_all_dates_other_month(holiday_text, year, month) if holiday_text else False
                has_p_or_transplant_text = (
                    '月大P1' in holiday_text or '月小P1' in holiday_text or
                    '月大P2' in holiday_text or '月小P2' in holiday_text or
                    '月P1' in holiday_text or '月P2' in holiday_text or
                    '月換心' in holiday_text or '月待命' in holiday_text
                )

                if has_p_or_transplant_text:
                    holiday_cell.font = green_font
                elif first_result.is_skipped:
                    # 跳過記錄：P/換心用綠色，其他用黑色
                    skip_reason = first_result.skip_reason
                    is_p_or_transplant = ('月大P1' in skip_reason or '月小P1' in skip_reason or
                                          '月大P2' in skip_reason or '月小P2' in skip_reason or
                                          '月P1' in skip_reason or '月P2' in skip_reason or
                                          '月換心' in skip_reason or '月待命' in skip_reason)
                    holiday_cell.font = green_font if is_p_or_transplant else black_font
                elif first_result.slot.is_next_month or is_next_month_holiday:
                    holiday_cell.font = gray_font
                else:
                    holiday_cell.font = red_font
            else:
                # 沒有排班結果，留空白
                holiday_cell.value = ''
                holiday_cell.font = normal_font
            holiday_cell.border = border
            holiday_cell.alignment = center_align
        
        data_row += 1
    
    # 調整欄寬 - 確保所有欄位都能容納兩行文字
    # 主值, 公休, 備註, 大夜, 小夜週, 假日, 假日, 假日, 假日
    _apply_identity_font(ws, green_font, gray_font, month)

    max_col = len(columns)
    max_row = data_row - 1
    column_widths = [0] * max_col

    # 計算每欄的最大寬度（考慮兩行文字）
    for row in range(header_row, max_row + 1):
        for col in range(1, max_col + 1):
            value = ws.cell(row=row, column=col).value
            text = _get_cell_text(value)
            lines = text.splitlines() if text else ['']
            # 計算兩行文字的最大寬度
            if len(lines) >= 2:
                # 如果有兩行或更多，取前兩行的最大寬度
                two_line_width = max(_text_display_width(line) for line in lines[:2])
                max_len = max(max(_text_display_width(line) for line in lines), two_line_width)
            else:
                # 如果只有一行，假設第二行也是同樣寬度（模擬兩行）
                single_line_width = max(_text_display_width(line) for line in lines) if lines else 0
                max_len = single_line_width * 1.2  # 稍微增加寬度以容納兩行
            if max_len > column_widths[col - 1]:
                column_widths[col - 1] = max_len

    min_widths = [8, 12, 16, 14, 12, 12, 12, 12, 12]
    max_width = None
    width_by_col = []
    for col_idx, max_len in enumerate(column_widths, 1):
        min_width = min_widths[col_idx - 1] if col_idx - 1 < len(min_widths) else 10
        # 確保寬度足夠容納兩行文字，加上一些邊距
        width = max(max_len + 2, min_width) + 5
        if max_width is not None:
            width = min(width, max_width)
        ws.column_dimensions[get_column_letter(col_idx)].width = width
        width_by_col.append(width)

    # 設定所有資料列的列高為兩行的高度（統一大小）
    two_line_height = 24 * 2  # 兩行的高度
    for row in range(header_row, max_row + 1):
        ws.row_dimensions[row].height = two_line_height

def read_cross_month_makeup_holidays_from_excel(
    filepath: str,
    sheet_name: str,
    target_month: int,
    target_year: int,
) -> Dict[str, List[Dict]]:
    """
    從 Excel 假日班欄位讀取跨月補班記錄。

    支援格式：
    1. 有日期有班別：「6/7白班」、「6/6小夜」（舊格式 !6/7白班 也相容）
       → 鎖定該時段，回傳 type='fixed'
    2. 純待補班：「6月補」
       → 加入待補班佇列，回傳 type='pending'
    3. 指定日期待補班：「6月補@6/14白班」或「6月補 6/14」
       → 只在指定日期/班別嘗試補班，回傳 type='pending'

    Returns:
        {
            'nurse_name': [
                {'type': 'fixed', 'date': datetime, 'shift_type': '白班',
                 'slot_type': 'sat_day', 'original_text': '6/7白班'},
                {'type': 'pending', 'original_text': '6月補'},
                {'type': 'pending', 'original_text': '6月補@6/14白班',
                 'target_date': datetime, 'target_slot_type': 'sun_day'},
            ],
            ...
        }
    """
    from datetime import datetime
    import re

    wb = load_workbook(filepath, data_only=True)
    ws = wb[sheet_name]

    # 找到表頭
    header_row = None
    name_col = None
    holiday_cols = []

    for row_idx in range(1, 10):
        row_values = [cell.value for cell in ws[row_idx]]
        if '主值' in row_values or '副值' in row_values:
            header_row = row_idx
            for col_idx, value in enumerate(row_values, 1):
                if value == '主值' or value == '副值':
                    name_col = col_idx
                elif value and '假日' in str(value):
                    holiday_cols.append(col_idx)
            break

    if not header_row or not name_col or not holiday_cols:
        wb.close()
        return {}

    def _parse_slot_type(date, shift_type):
        """根據日期星期和班別判斷 slot_type"""
        weekday = date.weekday()
        if weekday == 5:  # 週六
            if shift_type == '白班':
                return 'sat_day'
            elif shift_type == '小夜':
                return 'sat_small_night'
        elif weekday == 6:  # 週日
            if shift_type == '大夜':
                return 'sun_night'
            elif shift_type == '白班':
                return 'sun_day'
            elif shift_type == '小夜':
                return 'sun_small_night'
        else:  # 平日國定假日
            if shift_type == '白班':
                return 'weekday_holiday_day'
            elif shift_type == '小夜':
                return 'weekday_holiday_small_night'
            elif shift_type == '大夜':
                return 'weekday_holiday_night'
        return None

    def _cross_month_skip_column(col_pos: int) -> int:
        # 上月最後一個假日欄承接本月第一個假日區塊，輸出時落在本月假日第 1 欄。
        if col_pos == len(holiday_cols):
            return 1
        return col_pos

    def _pending_makeup_column(col_pos: int) -> int:
        # 「X月補」不是原欄位保留，而是上月待補班在本月找位置補。
        # 若它寫在上月最後一個假日欄，本月輸出從第 2 欄承接，避免壓到第一個假日欄。
        if col_pos == len(holiday_cols):
            return 2 if len(holiday_cols) >= 4 else 1
        return _cross_month_skip_column(col_pos)

    nurse_makeups = {}
    prev_month = target_month - 1 if target_month > 1 else 12
    prev_year = target_year if target_month > 1 else target_year - 1

    for row_idx in range(header_row + 1, ws.max_row + 1):
        name_cell = ws.cell(row=row_idx, column=name_col).value
        if not name_cell:
            continue

        name = str(name_cell).lstrip('*').strip()
        if not name:
            continue

        first_holiday_text = ''
        if holiday_cols:
            first_holiday_value = ws.cell(row=row_idx, column=holiday_cols[0]).value
            first_holiday_text = str(first_holiday_value).strip() if first_holiday_value is not None else ''

        for col_pos, col_idx in enumerate(holiday_cols, 1):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if not cell_value:
                continue

            text = str(cell_value).strip()

            # 上月表第一個假日欄若已標示「-」，代表這個月左表第一格要保留為空班。
            if text == '-' and col_pos == 1:
                if name not in nurse_makeups:
                    nurse_makeups[name] = []
                nurse_makeups[name].append({
                    'type': 'skip',
                    'original_text': '-',
                    'column_index': 1,
                })
                print(f"  讀取跨月跳過記錄: {name} {text}")
                continue

            # --- 格式1：待補班「X月補」，可選指定日期：「X月補@M/D班別」或「X月補 M/D班別」---
            pending_match = re.match(r'^(\d+)月補(?:[@\s]+(\d{1,2})/(\d{1,2})\s*(白班|小夜|大夜)?)?$', text)
            if pending_match:
                month_num = int(pending_match.group(1))
                if month_num == target_month:
                    target_date = None
                    target_slot_type = None
                    target_shift_type = None
                    if pending_match.group(2) and pending_match.group(3):
                        preferred_month = int(pending_match.group(2))
                        preferred_day = int(pending_match.group(3))
                        target_shift_type = pending_match.group(4)
                        if preferred_month == target_month:
                            try:
                                target_date = datetime(target_year, preferred_month, preferred_day)
                                if target_shift_type:
                                    target_slot_type = _parse_slot_type(target_date, target_shift_type)
                                    if not target_slot_type:
                                        target_date = None
                            except ValueError:
                                target_date = None
                                target_slot_type = None
                    if name not in nurse_makeups:
                        nurse_makeups[name] = []
                    pending_record = {
                        'type': 'pending',
                        'original_text': text,
                        'column_index': _pending_makeup_column(col_pos),
                    }
                    if target_date:
                        pending_record['target_date'] = target_date
                    if target_slot_type:
                        pending_record['target_slot_type'] = target_slot_type
                    if target_shift_type:
                        pending_record['target_shift_type'] = target_shift_type
                    nurse_makeups[name].append(pending_record)
                    print(f"  讀取假日待補班: {name} {text}")
                continue

            # --- 格式1.5：上月最後假日欄的 P/換心/原班，帶到本月對應欄位當跳過記錄 ---
            prev_identity_match = re.match(rf'^({prev_month})月(大P1|小P1|大P2|小P2|換心|待命)$', text)
            if prev_identity_match:
                if col_pos == len(holiday_cols):
                    if name not in nurse_makeups:
                        nurse_makeups[name] = []
                    nurse_makeups[name].append({
                        'type': 'skip',
                        'original_text': text,
                        'column_index': _cross_month_skip_column(col_pos),
                    })
                    print(f"  讀取跨月跳過記錄: {name} {text}")
                continue

            # --- 格式2：有日期有班別「6/7白班」或舊格式「!6/7白班」---
            content = text.lstrip('!').strip()
            match = re.match(r'^(\d+)/(\d+)\s*(白班|小夜|大夜)$', content)
            if match:
                month_num = int(match.group(1))
                day = int(match.group(2))
                shift_type = match.group(3)

                if month_num == target_month:
                    try:
                        date = datetime(target_year, month_num, day)
                        slot_type = _parse_slot_type(date, shift_type)
                        if slot_type:
                            if name not in nurse_makeups:
                                nurse_makeups[name] = []
                            nurse_makeups[name].append({
                                'type': 'fixed',
                                'date': date,
                                'shift_type': shift_type,
                                'slot_type': slot_type,
                                'original_text': text,
                                'column_index': _cross_month_skip_column(col_pos),
                            })
                            print(f"  讀取跨月補班(有日期): {name} {text}")
                    except ValueError:
                        pass

    wb.close()
    return nurse_makeups
