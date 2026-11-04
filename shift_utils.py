"""
排班共用工具函數
"""
from typing import List, Dict, Tuple, Any, Optional
from datetime import datetime, timedelta
import re

from models import NurseInfo
from date_utils import (
    parse_date_string,
    parse_dot_separated_dates,
    parse_date_range,
    would_cause_consecutive_7_days,
    get_night_week_rest_days,
    get_p_shift_dates_for_month,
    is_holiday,
    is_holiday_in_own_year,
)


def extract_parenthesized_dates(
    text: str,
    default_year: int = 2026,
    reference_month: Optional[int] = None,
) -> Tuple[List[datetime], str]:
    """
      '4/2.4/3.4/4 (4/3)' -> ([4/3], '4/2.4/3.4/4')
      '4/6-4/10(4/6,4/8)' -> ([4/6,4/8], '4/6-4/10')
    """
    if not text or not isinstance(text, str):
        return [], text or ''

    extracted: List[datetime] = []

    def _replace(match: re.Match) -> str:
        inner = match.group(1)
        for date_str in re.findall(r'\d{1,2}/\d{1,2}', inner):
            dt = parse_date_string(date_str, default_year, reference_month)
            if dt:
                extracted.append(dt)
        return ''

    cleaned = re.sub(r'\(([^)]*)\)', _replace, text)
    return extracted, cleaned


def extract_month_keywords(text: str) -> List[Tuple[str, str]]:
    """
    從文字中提取月份和關鍵字
    例如: '1月大P2、2月大P1' -> [('1月', '大P2'), ('2月', '大P1')]
    例如: '2月換心' -> [('2月', '換心')]
    例如: '3月小P1' -> [('3月', '小P1')]
    """
    if not text or not isinstance(text, str):
        return []

    results = []

    # 匹配模式: X月大P1, X月大P2, X月小P1, X月小P2, X月換心, X月待命
    patterns = [
        (r'(\d+)月大P1', '大P1'),
        (r'(\d+)月大P2', '大P2'),
        (r'(\d+)月小P1', '小P1'),
        (r'(\d+)月小P2', '小P2'),
        (r'(\d+)月換心', '換心'),
        (r'(\d+)月待命', '待命'),
    ]

    for pattern, keyword in patterns:
        matches = re.findall(pattern, text)
        for month in matches:
            results.append((f'{month}月', keyword))

    return results


def _parse_pending_makeup_token(
    token: str,
    default_year: int = 2026,
    column_index: int = 1,
    reference_month: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not token or not isinstance(token, str):
        return None

    original_text = token.strip()
    if not original_text:
        return None

    normalized = original_text.replace(' ', '').replace('　', '')
    is_targeted_makeup = bool(re.match(r'^\d{1,2}月補[@\s]*\d{1,2}/\d{1,2}', original_text.strip()))
    normalized = re.sub(r'^(?:待補班|補班|待補|補)[:：@]?', '', normalized)
    normalized = re.sub(r'^\d{1,2}月補@?', '', normalized)

    undated_match = re.match(r'^(?:(大夜|小夜|白班|假日)補|補(大夜|小夜|白班|假日)?)$', normalized or original_text.replace(' ', '').replace('\u3000', ''))
    if undated_match:
        shift_type = undated_match.group(1) or undated_match.group(2) or '假日'
        if shift_type == '假日':
            shift_type = '白班'
        return {
            'dates': [],
            'shift_type': shift_type,
            'original_text': original_text,
            'target_date': None,
            'target_shift_type': None,
            'column_index': column_index,
            'is_targeted_makeup': False,
            'is_undated_makeup': True,
        }

    shift_match = re.search(r'(大夜|小夜|白班)$', normalized)
    if not shift_match:
        return None

    shift_type = shift_match.group(1)
    normalized = normalized[:shift_match.start()]

    dates: List[datetime] = []
    target_date: Optional[datetime] = None

    range_match = re.match(r'^(\d{1,2}/\d{1,2})-(\d{1,2}/\d{1,2})$', normalized)
    if range_match:
        start_date = parse_date_string(range_match.group(1), default_year, reference_month)
        end_date = parse_date_string(range_match.group(2), default_year, reference_month)
        if start_date and end_date:
            if end_date < start_date:
                end_date = end_date.replace(year=end_date.year + 1)
            current = start_date
            while current <= end_date:
                dates.append(current)
                current += timedelta(days=1)
            target_date = dates[0] if dates else None
    else:
        date_match = re.search(r'(\d{1,2}/\d{1,2})', normalized)
        if date_match:
            dt = parse_date_string(date_match.group(1), default_year, reference_month)
            if dt:
                dates.append(dt)
                target_date = dt

    if not dates:
        if '補' not in original_text:
            return None
        return {
            'dates': [],
            'shift_type': shift_type,
            'original_text': original_text,
            'target_date': None,
            'target_shift_type': None,
            'column_index': column_index,
            'is_targeted_makeup': False,
            'is_undated_makeup': True,
        }

    return {
        'dates': dates,
        'shift_type': shift_type,
        'original_text': original_text,
        'target_date': target_date,
        'target_shift_type': shift_type,
        'column_index': column_index,
        'is_targeted_makeup': is_targeted_makeup,
    }


def extract_pending_makeup(
    text: str,
    default_year: int = 2026,
    reference_month: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    從文字中提取待補班資訊
    格式: '待補班:2/26-2/28大夜' 或 '6月補@6/14白班'

    Returns:
        [{'dates': [datetime, ...], 'shift_type': '大夜', 'original_text': ..., 'target_date': ..., 'target_shift_type': ..., 'column_index': 1}, ...]
    """
    if not text or not isinstance(text, str):
        return []

    results: List[Dict[str, Any]] = []
    tokens = re.split(r'[\n、;；]+', text)

    for idx, token in enumerate(tokens, start=1):
        record = _parse_pending_makeup_token(
            token, default_year, column_index=idx, reference_month=reference_month
        )
        if record:
            results.append(record)

    return results


def _is_undated_makeup_marker(text: str) -> bool:
    if not text or not isinstance(text, str):
        return False
    normalized = text.strip().replace(' ', '').replace('\u3000', '')
    return normalized in {'補', '待補', '補班', '待補班'}


def extract_date_ranges(
    text: str,
    default_year: int = 2026,
    reference_month: Optional[int] = None,
) -> List[datetime]:
    """
    從文字中提取所有日期範圍
    支援格式：
    - 2/23-3/8
    - 3/16-3/22
    - 用「、」或換行分隔的多段日期
    """
    if not text or not isinstance(text, str):
        return []
    
    all_dates = []
    
    # 匹配所有日期範圍 (M/D-M/D 格式)
    range_pattern = r'(\d{1,2}/\d{1,2})\s*-\s*(\d{1,2}/\d{1,2})'
    matches = re.findall(range_pattern, text)
    
    for start_str, end_str in matches:
        start_date = parse_date_string(start_str, default_year, reference_month)
        end_date = parse_date_string(end_str, default_year, reference_month)
        
        if start_date and end_date:
            # 處理跨年的情況
            if end_date < start_date:
                end_date = end_date.replace(year=end_date.year + 1)
            
            current = start_date
            while current <= end_date:
                all_dates.append(current)
                current += timedelta(days=1)
    
    return all_dates


def check_special_flags(text: str) -> Dict[str, bool]:
    """Check special flags in text."""
    flags = {
        'is_age_55_plus': False,
        'is_breastfeeding': False,
        'is_wedding': False,
    }

    if not text or not isinstance(text, str):
        return flags

    if re.search(r'55\s*(\u6b72|\u5c81)', text):
        flags['is_age_55_plus'] = True

    if '\u54fa\u4e73' in text:
        flags['is_breastfeeding'] = True

    if '\u5a5a\u5047' in text:
        flags['is_wedding'] = True

    return flags


def _get_first_value(row_data, keys):
    for key in keys:
        if key in row_data:
            return row_data.get(key, '')
    for row_key in row_data:
        for key in keys:
            if key in str(row_key):
                return row_data.get(row_key, '')
    return ''

def parse_nurse_info_from_row(
    row_data: Dict[str, Any],
    row_index: int,
    year: int = 2026,
    month: Optional[int] = None,
) -> Optional[NurseInfo]:
    """Parse nurse info from one Excel row.

    month 為欲排班月份；有給時，上月等跨年日期會依最接近的年份解析。
    """
    name_value = _get_first_value(row_data, ['\u59d3\u540d', '\u4e3b\u503c', '\u526f\u503c'])
    if not name_value or not isinstance(name_value, str):
        return None

    original_name = str(name_value).strip()
    name = original_name.lstrip('*').strip()
    if not name:
        return None

    nurse = NurseInfo(
        name=name,
        original_name=original_name,
        row_index=row_index,
    )

    name_flags = check_special_flags(original_name)
    nurse.is_breastfeeding = name_flags['is_breastfeeding']
    nurse.is_age_55_plus = name_flags['is_age_55_plus']
    nurse.is_wedding = name_flags['is_wedding']

    leave_value = str(_get_first_value(row_data, ['\u516c\u4f11', '\u4f11\u5047'])).strip()
    if leave_value and leave_value != 'nan':
        leave_value = re.sub(r'\s{3,}', '\n', leave_value)
        nurse.original_leave_text = leave_value

    remark_value = str(_get_first_value(row_data, ['\u5099\u8a3b', '\u8a3b\u8a18'])).strip()
    if remark_value and remark_value != 'nan':
        leave_value = re.sub(r'\s{3,}', '\n', leave_value)
        nurse.remarks = remark_value

    makeup_value = str(_get_first_value(row_data, ['補班', '待補班'])).strip()
    if makeup_value and makeup_value != 'nan':
        nurse.makeup_text = makeup_value

    combined_text = str(original_name) + str(leave_value) + str(remark_value) + str(nurse.makeup_text)
    if any(keyword in combined_text for keyword in ['懷孕', '免輪值', '離職', '育嬰', '安胎']):
        nurse.is_exempt = True

    field_values = []
    if leave_value and leave_value != 'nan':
        field_values.append(leave_value)
    if remark_value and remark_value != 'nan':
        field_values.append(remark_value)
    if nurse.makeup_text and nurse.makeup_text != 'nan':
        field_values.append(nurse.makeup_text)

    for field_value in field_values:
        flags = check_special_flags(field_value)
        if flags['is_age_55_plus']:
            nurse.is_age_55_plus = True
        if flags['is_breastfeeding']:
            nurse.is_breastfeeding = True
        if flags['is_wedding']:
            nurse.is_wedding = True

        keywords = extract_month_keywords(field_value)
        for month_str, keyword in keywords:
            if keyword in ['大P1', '小P1']:
                nurse.is_p1[month_str] = keyword  # 保存 '大P1' 或 '小P1'
            elif keyword in ['大P2', '小P2']:
                nurse.is_p2[month_str] = keyword  # 保存 '大P2' 或 '小P2'
            elif keyword == '換心':
                nurse.is_transplant[month_str] = True
            elif keyword == '待命':
                nurse.is_standby[month_str] = True

        pending_makeups = extract_pending_makeup(field_value, year, month)
        for makeup in pending_makeups:
            if makeup.get('is_targeted_makeup'):
                target_date = makeup.get('target_date')
                shift_type = makeup.get('target_shift_type') or makeup.get('shift_type')
                if target_date and shift_type:
                    nurse.manual_makeup_shift_blocks.append({
                        'date': target_date,
                        'shift_type': shift_type,
                        'note': makeup.get('original_text') or '補班欄指定',
                    })
                continue
            if makeup['shift_type'] == '大夜':
                nurse.pending_night_makeup.append(makeup)
            elif makeup['shift_type'] == '小夜':
                nurse.pending_small_night_makeup.append(makeup)
            elif makeup['shift_type'] in ('白班', '假日'):
                nurse.pending_holiday_makeup.append(makeup)

        # 解析婚假與公休日期
        # 特別處理同一個欄位同時包含「婚假」與「公休」的情況，例如：
        #   "婚假2/24-3/6 公休3/9-3/15"
        if field_value == leave_value:
            # 依照關鍵字切成多段，每段各自解析日期
            segments = []
            matches = list(re.finditer(r'(婚假|公休)', field_value))
            if matches:
                for i, m in enumerate(matches):
                    kind = m.group(1)  # 婚假 / 公休
                    start = m.start()
                    end = matches[i + 1].start() if i + 1 < len(matches) else len(field_value)
                    seg_text = field_value[start:end]

                    field_for_dates = re.sub(
                        r'\u5f85\u88dc\u73ed[:\s]*\d{1,2}/\d{1,2}\s*-\s*\d{1,2}/\d{1,2}(\u5927\u591c|\u5c0f\u591c)',
                        '',
                        seg_text,
                    )
                    dates = extract_date_ranges(field_for_dates, year, month)

                    if kind == '婚假':
                        nurse.wedding_leave_dates.extend(dates)
                    elif kind == '公休':
                        nurse.leave_dates.extend(dates)
            else:
                # 沒有明確關鍵字時，維持原本邏輯
                field_for_dates = re.sub(
                    r'\u5f85\u88dc\u73ed[:\s]*\d{1,2}/\d{1,2}\s*-\s*\d{1,2}/\d{1,2}(\u5927\u591c|\u5c0f\u591c)',
                    '',
                    field_value,
                )
                has_wedding_in_field = '婚假' in field_value
                dates = extract_date_ranges(field_for_dates, year, month)
                if has_wedding_in_field:
                    nurse.wedding_leave_dates.extend(dates)
                else:
                    nurse.leave_dates.extend(dates)
        else:
            # 備註欄等其他欄位維持原本邏輯
            field_for_dates = re.sub(
                r'\u5f85\u88dc\u73ed[:\s]*\d{1,2}/\d{1,2}\s*-\s*\d{1,2}/\d{1,2}(\u5927\u591c|\u5c0f\u591c)',
                '',
                field_value,
            )
            has_wedding_in_field = '婚假' in field_value
            dates = extract_date_ranges(field_for_dates, year, month)

            if has_wedding_in_field:
                nurse.wedding_leave_dates.extend(dates)
            else:
                nurse.leave_dates.extend(dates)

    if nurse.leave_dates:
        nurse.leave_dates = sorted(set(nurse.leave_dates))
    if nurse.wedding_leave_dates:
        nurse.wedding_leave_dates = sorted(set(nurse.wedding_leave_dates))

    small_night_value = str(_get_first_value(row_data, ['\u5c0f\u591c', '\u5c0f\u591c\u9031'])).strip()
    if small_night_value and small_night_value != 'nan':
        small_night_dates = extract_date_ranges(small_night_value, year, month)
        if small_night_dates:
            nurse.had_small_night_last_month = True

    night_value = str(_get_first_value(row_data, ['\u5927\u591c'])).strip()
    if night_value and night_value != 'nan':
        for month_str, keyword in extract_month_keywords(night_value):
            if keyword in ['\u5927P1', '\u5c0fP1']:
                nurse.is_p1[month_str] = keyword
            elif keyword in ['\u5927P2', '\u5c0fP2']:
                nurse.is_p2[month_str] = keyword
            elif keyword == '\u63db\u5fc3':
                nurse.is_transplant[month_str] = True
            elif keyword == '\u5f85\u547d':
                nurse.is_standby[month_str] = True

        if _is_undated_makeup_marker(night_value):
            nurse.pending_night_makeup.append({
                'dates': [],
                'shift_type': '大夜',
                'original_text': night_value,
                'target_date': None,
                'target_shift_type': None,
                'column_index': 1,
                'is_targeted_makeup': False,
                'is_undated_makeup': True,
            })
        else:
            comp_dates, night_value_clean = extract_parenthesized_dates(night_value, year, month)
            if comp_dates:
                nurse.holiday_compensations_used.extend(comp_dates)
                # 記錄來源：從大夜欄位讀取（上個月的結果，已確定）
                nurse.night_shift_holiday_compensations.extend(comp_dates)
            night_value_clean = night_value_clean.replace('#', '')
            night_dates = parse_dot_separated_dates(night_value_clean, year, month)
            nurse.night_shift_dates.extend(night_dates)
            nurse.manual_night_shift_dates.extend(night_dates)
            # 大夜欄沒有括號標註時（例如 12/31.1/1.1/2），排到平日國定假日也要還班。
            for d in night_dates:
                if is_holiday_in_own_year(d) and d.weekday() < 5 and d not in nurse.night_shift_holiday_compensations:
                    nurse.night_shift_holiday_compensations.append(d)
                    if d not in nurse.holiday_compensations_used:
                        nurse.holiday_compensations_used.append(d)

    small_night_value = str(_get_first_value(row_data, ['\u5c0f\u591c', '\u5c0f\u591c\u9031'])).strip()
    if small_night_value and small_night_value != 'nan':
        for month_str, keyword in extract_month_keywords(small_night_value):
            if keyword in ['\u5927P1', '\u5c0fP1']:
                nurse.is_p1[month_str] = keyword
            elif keyword in ['\u5927P2', '\u5c0fP2']:
                nurse.is_p2[month_str] = keyword
            elif keyword == '\u63db\u5fc3':
                nurse.is_transplant[month_str] = True
            elif keyword == '\u5f85\u547d':
                nurse.is_standby[month_str] = True

        if _is_undated_makeup_marker(small_night_value):
            nurse.pending_small_night_makeup.append({
                'dates': [],
                'shift_type': '小夜',
                'original_text': small_night_value,
                'target_date': None,
                'target_shift_type': None,
                'column_index': 1,
                'is_targeted_makeup': False,
                'is_undated_makeup': True,
            })
        else:
            comp_dates, small_night_value_clean = extract_parenthesized_dates(small_night_value, year, month)
            if comp_dates:
                nurse.holiday_compensations_used.extend(comp_dates)
                # 記錄來源：從小夜週欄位讀取（可能是預排）
                nurse.small_night_shift_holiday_compensations.extend(comp_dates)
            small_night_value_clean = small_night_value_clean.replace('#', '')
            small_dates = parse_date_range(small_night_value_clean, year, month)
            nurse.small_night_shift_dates.extend(small_dates)
            nurse.manual_small_night_shift_dates.extend(small_dates)

    return nurse

def should_skip_for_special_status(nurse: NurseInfo) -> Tuple[bool, str]:
    """
    檢查是否因特殊狀態（55歲、哺乳）而跳過
    """
    if nurse.is_age_55_plus:
        return True, '55歲以上'
    if nurse.is_breastfeeding:
        return True, '哺乳'
    return False, ''


def should_skip_for_p_shift(
    nurse: NurseInfo,
    target_month: int,
    target_dates: List[datetime] = None,
) -> Tuple[bool, str]:
    """
    檢查是否因 P1/P2/換心 而跳過
    """
    months_to_check = [target_month]
    if target_dates:
        months_to_check = sorted({d.month for d in target_dates})

    for month_num in months_to_check:
        month_key = f'{month_num}月'

        if nurse.is_transplant.get(month_key):
            return True, f'{month_key}換心'

        p1_value = nurse.is_p1.get(month_key)
        if p1_value:
            # p1_value 可能是 '大P1' 或 '小P1'，或舊格式的 True
            if isinstance(p1_value, str):
                return True, f'{month_key}{p1_value}'
            else:
                return True, f'{month_key}P1'
        
        p2_value = nurse.is_p2.get(month_key)
        if p2_value:
            # p2_value 可能是 '大P2' 或 '小P2'，或舊格式的 True
            if isinstance(p2_value, str):
                return True, f'{month_key}{p2_value}'
            else:
                return True, f'{month_key}P2'

    return False, ''


def should_skip_for_leave(
    nurse: NurseInfo,
    target_dates: List[datetime],
    check_day_before: bool = True,
) -> Tuple[bool, str]:
    """
    檢查是否因公休/婚假而跳過
    
    Args:
        nurse: 護理人員
        target_dates: 目標班別日期
        check_day_before: 是否檢查前一天（大夜班需要，小夜週不需要）
    
    Returns:
        (是否跳過, 原因)
    """
    target_date_set = set(d.date() for d in target_dates)
    first_day = min(target_dates)
    day_before_first = (first_day - timedelta(days=1)).date()
    
    # 婚假檢查（跳過不補班）
    if nurse.wedding_leave_dates:
        wedding_date_set = set(d.date() for d in nurse.wedding_leave_dates)
        
        # 婚假日期與值班日期重疊
        if wedding_date_set & target_date_set:
            return True, '婚假'
        
        # 前一天是否還在婚假
        if check_day_before and day_before_first in wedding_date_set:
            return True, '婚假'
    
    # 公休檢查
    if nurse.leave_dates:
        leave_date_set = set(d.date() for d in nurse.leave_dates)
        
        # 公休日期與值班日期重疊
        if leave_date_set & target_date_set:
            return True, '公休中'
        
        # 前一天是否還在公休
        if check_day_before and day_before_first in leave_date_set:
            return True, '公休中'
    
    return False, ''


def should_skip_for_consecutive_days(
    nurse: NurseInfo,
    target_dates: List[datetime],
    target_month: int,
    target_year: int,
    prev_month: int = None,
    next_month: int = None,
    is_night_shift: bool = False,
) -> Tuple[bool, str]:
    """
    檢查加入目標班後是否會造成連續 7 天上班。

    大夜那一週的其他平日不用上白班，不算上班（is_night_shift 表示目標班是大夜）。

    同時計入目標日期前後的正常平日班、前後月 P1/P2/換心班，以及
    已排定的大夜和小夜週，避免跨月衝突只在最終稽核才被發現。
    """
    if not target_dates:
        return False, ''

    scan_start = min(target_dates) - timedelta(days=6)
    scan_end = max(target_dates) + timedelta(days=6)
    leave_days = {
        day.date()
        for day in list(nurse.leave_dates) + list(nurse.wedding_leave_dates)
    }

    night_rest_days = get_night_week_rest_days(
        list(nurse.night_shift_dates) + (list(target_dates) if is_night_shift else [])
    )
    existing_dates = []
    current = scan_start
    while current <= scan_end:
        if (
            current.weekday() < 5
            and not is_holiday(current)
            and current.date() not in leave_days
            and current.date() not in night_rest_days
        ):
            existing_dates.append(current)
        current += timedelta(days=1)

    existing_dates.extend(
        day
        for day in list(nurse.night_shift_dates) + list(nurse.small_night_shift_dates)
        if scan_start.date() <= day.date() <= scan_end.date()
    )

    months_to_check = set()
    current = datetime(scan_start.year, scan_start.month, 1)
    while current <= scan_end:
        months_to_check.add((current.year, current.month))
        if current.month == 12:
            current = datetime(current.year + 1, 1, 1)
        else:
            current = datetime(current.year, current.month + 1, 1)

    for check_year, check_month in months_to_check:
        month_key = f'{check_month}月'
        shift_types = []
        if nurse.is_transplant.get(month_key):
            shift_types.append('換心')
        p1_value = nurse.is_p1.get(month_key)
        if p1_value:
            shift_types.append(p1_value if isinstance(p1_value, str) else '大P1')
        p2_value = nurse.is_p2.get(month_key)
        if p2_value:
            shift_types.append(p2_value if isinstance(p2_value, str) else '大P2')
        for shift_type in shift_types:
            existing_dates.extend(
                get_p_shift_dates_for_month(check_year, check_month, shift_type)
            )

    if would_cause_consecutive_7_days(existing_dates, target_dates):
        return True, '連續上班達 7 天'

    return False, ''
