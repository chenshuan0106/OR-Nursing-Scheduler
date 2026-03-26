"""
護理人員資料模型
"""
from typing import List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field

# 全域設定：完全免排班的關鍵字清單
EXEMPT_KEYWORDS = ['懷孕', '免值班', '免輪值', '交叉訓練', '不參與值班', '不加入輪值']

@dataclass
class NurseInfo:
    """護理人員資料"""
    name: str
    original_name: str  # 包含 * 號的原始名字
    row_index: int  # Excel 中的列索引
    
    # 基本資訊
    leave_dates: List[datetime] = field(default_factory=list)  # 公休日期
    wedding_leave_dates: List[datetime] = field(default_factory=list)  # 婚假日期
    original_leave_text: str = ''  # 原始公休欄位文字
    remarks: str = ''  # 備註
    
    # 新增：完全免排班標記與原因
    is_exempt: bool = False
    exempt_reason: str = ''

    # 特殊狀態（從所有欄位檢索得到）
    is_breastfeeding: bool = False  # 哺乳
    is_age_55_plus: bool = False    # 55歲以上
    is_wedding: bool = False        # 婚嫁（整月跳過）
    is_transplant: Dict[str, bool] = field(default_factory=dict)  # {月份: True} 換心班
    is_p1: Dict[str, str] = field(default_factory=dict)  # {月份: '大P1'/'小P1'} 保存大/小資訊
    is_p2: Dict[str, str] = field(default_factory=dict)  # {月份: '大P2'/'小P2'} 保存大/小資訊
    
    # 排班結果
    night_shift_dates: List[datetime] = field(default_factory=list)  # 大夜班日期
    small_night_shift_dates: List[datetime] = field(default_factory=list)  # 小夜週日期
    
    # 上月跨月的排班（有#標記的）
    last_month_cross_month_night_dates: List[datetime] = field(default_factory=list)  # 上月跨月的大夜日期
    last_month_cross_month_small_night_dates: List[datetime] = field(default_factory=list)  # 上月跨月的小夜週日期
    
    # 假日班排班結果
    holiday_shift_results: Dict[str, List] = field(default_factory=dict)

    # 上個月是否已輪過小夜（從備註欄讀取 X/X-X/X小夜 格式）
    had_small_night_last_month: bool = False

    # 待補班資訊（從備註欄讀取）
    pending_night_makeup: List[Dict] = field(default_factory=list)  # [{'dates': [...], 'shift_type': '大夜'}, ...]
    pending_small_night_makeup: List[Dict] = field(default_factory=list)  # 小夜待補班
    pending_holiday_makeup: List[Dict] = field(default_factory=list)  # 假日班待補班

    # 本月新產生的待補班（因公休跳過，輸出時寫入備註）
    new_pending_makeup: List[Dict] = field(default_factory=list)  # [{'dates': [...], 'shift_type': '大夜'}, ...]

    # 國定假日補償資訊
    holiday_compensations_used: List[datetime] = field(default_factory=list)  # 已使用的國定假日日期
    night_shift_holiday_compensations: List[datetime] = field(default_factory=list)
    small_night_shift_holiday_compensations: List[datetime] = field(default_factory=list)

    # 上月的跨月補班記錄
    previous_month_cross_month_makeup_holidays: List[Dict] = field(default_factory=list)

    def __post_init__(self):
        # 1. 優化：確保預設為 dict/list 的欄位如果是 None，會被安全地初始化
        # (雖然 default_factory 通常不會給 None，但若從外部直接傳入 None 還是會出錯，加強防呆)
        self.is_transplant = self.is_transplant or {}
        self.is_p1 = self.is_p1 or {}
        self.is_p2 = self.is_p2 or {}
        self.holiday_shift_results = self.holiday_shift_results or {}
        self.holiday_compensations_used = self.holiday_compensations_used or []

        # 2. 新增邏輯：自動判斷是否「完全免排班」
        # 我們將所有的備註與公休欄位文字合併檢查，若包含任何免排班關鍵字就標記起來
        combined_text = f"{self.remarks} {self.original_leave_text}".replace(" ", "")
        for keyword in EXEMPT_KEYWORDS:
            if keyword in combined_text:
                self.is_exempt = True
                self.exempt_reason = keyword
                break