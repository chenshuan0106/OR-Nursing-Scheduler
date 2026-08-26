"""
日期工具函數
"""
from datetime import datetime, timedelta
from typing import List, Tuple, Optional
import calendar
import logging
import re
import config

logger = logging.getLogger(__name__)

_DATE_SHAPE_RE = re.compile(r'\d{1,2}/\d{1,2}')


def _looks_like_date_attempt(text: str) -> bool:
    """判斷文字是否含有 M/D 形狀的片段。

    用來過濾掉像 '8月換心'、'*滿55歲跳大夜' 這類本來就不是日期、
    只是被丟進日期解析函式裡的狀態註記文字，避免把「本來就不該
    被當成日期」的正常內容也記成解析失敗警告。
    """
    return bool(text) and bool(_DATE_SHAPE_RE.search(text))


def roc_to_western_year(roc_year: int) -> int:
    """民國年轉西元年"""
    return roc_year + 1911


def western_to_roc_year(western_year: int) -> int:
    """西元年轉民國年"""
    return western_year - 1911


def parse_date_string(date_str: str, default_year: int = 2026) -> Optional[datetime]:
    """
    解析日期字串，支援多種格式
    例如: '2/5', '2/05', '02/5'
    """
    if not date_str or not isinstance(date_str, str):
        return None
    
    date_str = date_str.strip()
    if not date_str:
        return None

    # 嘗試解析 M/D 格式
    try:
        parts = date_str.split('/')
        if len(parts) == 2:
            month = int(parts[0])
            day = int(parts[1])
            return datetime(default_year, month, day)
    except (ValueError, IndexError):
        pass

    if _looks_like_date_attempt(date_str):
        logger.warning("parse_date_string: 無法解析日期字串 %r", date_str)
    return None


def parse_date_range(range_str: str, default_year: int = 2026) -> List[datetime]:
    """
    解析日期範圍字串
    例如: '2/2-2/8' -> [datetime(2026, 2, 2), ..., datetime(2026, 2, 8)]
    """
    if not range_str or not isinstance(range_str, str):
        return []
    
    range_str = range_str.strip()
    if not range_str:
        return []

    # 檢查是否包含範圍符號
    if '-' in range_str:
        parts = range_str.split('-')
        if len(parts) == 2:
            start_date = parse_date_string(parts[0], default_year)
            end_date = parse_date_string(parts[1], default_year)

            if start_date and end_date:
                # 處理跨年的情況
                if end_date < start_date:
                    end_date = end_date.replace(year=end_date.year + 1)

                dates = []
                current = start_date
                while current <= end_date:
                    dates.append(current)
                    current += timedelta(days=1)
                return dates

    if _looks_like_date_attempt(range_str):
        logger.warning("parse_date_range: 無法解析日期範圍 %r", range_str)
    return []


def parse_dot_separated_dates(date_str: str, default_year: int = 2026) -> List[datetime]:
    """
    解析用點分隔的日期
    例如: '2/5.2/6.2/7' -> [datetime(2026, 2, 5), datetime(2026, 2, 6), datetime(2026, 2, 7)]
    """
    if not date_str or not isinstance(date_str, str):
        return []
    
    dates = []
    # 支援日期區間: 2/23-2/27
    range_pattern = r'(\d{1,2}/\d{1,2})\s*-\s*(\d{1,2}/\d{1,2})'
    for start_str, end_str in re.findall(range_pattern, date_str):
        start_date = parse_date_string(start_str, default_year)
        end_date = parse_date_string(end_str, default_year)
        if start_date and end_date:
            if end_date < start_date:
                end_date = end_date.replace(year=end_date.year + 1)
            current = start_date
            while current <= end_date:
                dates.append(current)
                current += timedelta(days=1)
        else:
            logger.warning(
                "parse_dot_separated_dates: 區間片段無法解析 %r-%r（原始文字 %r）",
                start_str, end_str, date_str,
            )

    parts = date_str.split('.')
    for part in parts:
        stripped = part.strip()
        date = parse_date_string(stripped, default_year)
        if date:
            dates.append(date)
        elif stripped and '-' not in stripped and _looks_like_date_attempt(stripped):
            # 含 '-' 的片段可能已被上面的區間規則處理過，避免重複告警；
            # 不含 '/' 的片段（例如狀態註記文字）本來就不是日期，不用告警
            logger.warning(
                "parse_dot_separated_dates: 片段無法解析 %r（原始文字 %r）",
                stripped, date_str,
            )

    if not dates:
        return dates
    unique_dates = sorted({d.date(): d for d in dates}.values())
    return unique_dates


def format_date_to_string(date: datetime) -> str:
    """將 datetime 格式化為 M/D 格式"""
    return f"{date.month}/{date.day}"


def format_dates_to_dot_string(dates: List[datetime]) -> str:
    """將日期列表格式化為用點分隔的字串"""
    return '.'.join(format_date_to_string(d) for d in dates)


def get_weekday_name_chinese(weekday: int) -> str:
    """取得中文星期名稱 (0=週一, 6=週日)"""
    names = ['週一', '週二', '週三', '週四', '週五', '週六', '週日']
    return names[weekday]


def is_saturday(date: datetime) -> bool:
    """判斷是否為星期六"""
    return date.weekday() == 5


def is_holiday(date: datetime) -> bool:
    """判斷是否為國定假日"""
    date_key = f"{date.month}/{date.day}"
    return date_key in config.get_holidays()


def get_holiday_name(date: datetime) -> Optional[str]:
    """取得國定假日名稱"""
    date_key = f"{date.month}/{date.day}"
    return config.get_holidays().get(date_key)


def get_month_dates(year: int, month: int) -> List[datetime]:
    """取得指定月份的所有日期"""
    _, last_day = calendar.monthrange(year, month)
    return [datetime(year, month, day) for day in range(1, last_day + 1)]


def get_night_shift_groups_for_month(year: int, month: int) -> List[Tuple[List[datetime], str]]:
    """
    取得指定月份的大夜班組別
    返回: [(日期列表, 組別類型), ...]
    組別類型: 'mon-wed' (週一到週三) 或 'thu-sat' (週四到週六)
    
    注意：
    - 月底可能會跨月，例如 3/30(一)、3/31(二)、4/1(三)
    - 月初不完整的組別會被跳過（例如4/1單獨一天不會成為組別）
    """
    groups = []
    
    # 取得當月日期
    dates = get_month_dates(year, month)
    
    # 計算下個月
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1
    
    current_group = []
    current_type = None
    
    # 找到第一個完整組別的開始（週一或週四）
    first_complete_idx = 0
    if dates:
        first_weekday = dates[0].weekday()
        # 如果第一天不是週一(0)或週四(3)，跳過直到找到完整組別開始
        if first_weekday not in [0, 3]:
            for idx, date in enumerate(dates):
                if date.weekday() in [0, 3]:  # 週一或週四
                    first_complete_idx = idx
                    break
    
    for date in dates[first_complete_idx:]:
        weekday = date.weekday()
        
        # 週一到週三
        if weekday in [0, 1, 2]:
            if current_type == 'thu-sat' and current_group:
                groups.append((current_group, current_type))
                current_group = []
            current_type = 'mon-wed'
            current_group.append(date)
            if weekday == 2:  # 週三，結束這一組
                groups.append((current_group, current_type))
                current_group = []
                current_type = None
        
        # 週四到週六
        elif weekday in [3, 4, 5]:
            if current_type == 'mon-wed' and current_group:
                groups.append((current_group, current_type))
                current_group = []
            current_type = 'thu-sat'
            current_group.append(date)
            if weekday == 5:  # 週六，結束這一組
                groups.append((current_group, current_type))
                current_group = []
                current_type = None
    
    # 處理月底未結束的組（可能需要跨月補齊）
    if current_group and current_type:
        last_date = current_group[-1]
        last_weekday = last_date.weekday()
        
        if current_type == 'mon-wed':
            # 週一～三的組，需要補到週三
            # 例如：3/30(一)、3/31(二) → 需要補 4/1(三)
            days_to_add = 2 - last_weekday  # 補到週三(2)
            for i in range(1, days_to_add + 1):
                next_date = last_date + timedelta(days=i)
                current_group.append(next_date)
        
        elif current_type == 'thu-sat':
            # 週四～六的組，需要補到週六
            days_to_add = 5 - last_weekday  # 補到週六(5)
            for i in range(1, days_to_add + 1):
                next_date = last_date + timedelta(days=i)
                current_group.append(next_date)
        
        groups.append((current_group, current_type))
    
    return groups


def dates_overlap(dates1: List[datetime], dates2: List[datetime]) -> bool:
    """檢查兩個日期列表是否有重疊"""
    set1 = set(d.date() for d in dates1)
    set2 = set(d.date() for d in dates2)
    return bool(set1 & set2)


def would_cause_consecutive_7_days(
    existing_dates: List[datetime],
    new_dates: List[datetime]
) -> bool:
    """
    檢查加入新日期後是否會造成連續7天上班
    """
    if not existing_dates and not new_dates:
        return False
    
    all_dates = set(d.date() for d in existing_dates + new_dates)
    
    # 對所有日期排序
    sorted_dates = sorted(all_dates)
    
    if len(sorted_dates) < 7:
        return False
    
    # 檢查是否有連續7天
    for i in range(len(sorted_dates) - 6):
        consecutive = True
        for j in range(6):
            if (sorted_dates[i + j + 1] - sorted_dates[i + j]).days != 1:
                consecutive = False
                break
        if consecutive:
            return True
    
    return False


def get_p_shift_dates_for_month(
    year: int,
    month: int,
    shift_type: str  # '大P1', '大P2', '小P1', '小P2', '換心'
) -> List[datetime]:
    """
    取得指定月份的 P1/P2/換心 班的上班日期
    """
    from config import SHIFT_PATTERNS

    if shift_type not in SHIFT_PATTERNS:
        logger.warning("get_p_shift_dates_for_month: 未知的班別類型 %r", shift_type)
        return []
    
    pattern = SHIFT_PATTERNS[shift_type]
    weekdays = pattern['weekdays']
    
    dates = get_month_dates(year, month)
    
    # 換心班只有平日（且非國定假日）
    if shift_type == '換心':
        return [d for d in dates if d.weekday() in weekdays and not is_holiday(d)]
    
    return [d for d in dates if d.weekday() in weekdays]
