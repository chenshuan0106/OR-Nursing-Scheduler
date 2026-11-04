"""
護理人員資料模型
"""
from typing import List, Dict, Optional
from datetime import datetime, date
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
    makeup_text: str = ''  # 補班文字
    
    # 新增：完全免排班標記與原因
    is_exempt: bool = False
    exempt_reason: str = ''

    # 特殊狀態（從所有欄位檢索得到）
    is_breastfeeding: bool = False  # 哺乳
    is_age_55_plus: bool = False    # 55歲以上
    is_wedding: bool = False        # 婚嫁（整月跳過）
    is_transplant: Dict[str, bool] = field(default_factory=dict)  # {月份: True} 換心班
    is_standby: Dict[str, bool] = field(default_factory=dict)  # {月份: True} 待命
    is_p1: Dict[str, str] = field(default_factory=dict)  # {月份: '大P1'/'小P1'} 保存大/小資訊
    is_p2: Dict[str, str] = field(default_factory=dict)  # {月份: '大P2'/'小P2'} 保存大/小資訊
    
    # 排班結果
    night_shift_dates: List[datetime] = field(default_factory=list)  # 大夜班日期
    small_night_shift_dates: List[datetime] = field(default_factory=list)  # 小夜週日期
    manual_night_shift_dates: List[datetime] = field(default_factory=list)  # 大夜欄自填日期
    manual_small_night_shift_dates: List[datetime] = field(default_factory=list)  # 小夜週欄自填日期
    
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
        self.is_standby = self.is_standby or {}
        self.is_p1 = self.is_p1 or {}
        self.is_p2 = self.is_p2 or {}
        self.holiday_shift_results = self.holiday_shift_results or {}
        self.holiday_compensations_used = self.holiday_compensations_used or []

        # 2. 新增邏輯：自動判斷是否「完全免排班」
        # 我們將所有的備註與公休欄位文字合併檢查，若包含任何免排班關鍵字就標記起來
        combined_text = f"{self.remarks} {self.original_leave_text} {self.makeup_text}".replace(" ", "")
        for keyword in EXEMPT_KEYWORDS:
            if keyword in combined_text:
                self.is_exempt = True
                self.exempt_reason = keyword
                break


def make_return_credit_id(sheet_name: str, nurse_name: str, source_date: datetime, source_type: str) -> str:
    """建立還班 credit 的唯一識別碼（同一人可有多筆不同來源日期/來源類型的 credit）"""
    source_day = source_date.date() if hasattr(source_date, 'date') else source_date
    return f"{sheet_name}|{nurse_name}|{source_day.isoformat()}|{source_type}"


@dataclass
class ReturnCredit:
    """
    還班資格（return credit）：代表某位護理人員有一筆可以抵掉「一次」未來假日
    正常輪值的資格。

    還班 ≠ 補班：
    - 補班（makeup_queue）：之前有一班因衝突沒排到，之後要另外找一班補回來（多上一班）。
    - 還班（ReturnCredit）：有一筆資格，可以抵掉一次之後的正常假日輪值（少上一次班）。

    `source` 有三種，approved 的預設值與 eligibility 判斷都不一樣：

    - 'current_month_auto'：本月大夜/小夜排班「這次真的排到」平日國定假日，動態產生。
      建立時就是 approved=True，不需要 UI 核准；但因為來源國假必須「已經發生」，
      eligibility 要求 slot 日期晚於 source_date（不能提前使用還沒到的國假）。
    - 'carry_over_detected'：從上月 Excel 解析出來的舊還班/補償資格，只是候選，
      approved 預設 False，一定要經過 Streamlit UI 核准才能用；核准後跟 manual
      走一樣的 use_after_date 邏輯。
    - 'manual'：使用者在 Streamlit 手動指定，建立時就是 approved=True，屬於人工
      override，不受「來源國假必須已發生」限制，只受自己設定的 use_after_date 限制。

    detection（偵測到候選）、approval（使用者核准或自動核准）、consumption（排班
    核心真正使用）三層要分開；一旦 approved=True，三種來源在排班核心的「消耗」
    邏輯完全相同（只有 eligibility 的日期判斷式不同），沒有任何 bypass path。
    """
    credit_id: str
    sheet_name: str
    nurse_name: str
    source_date: datetime          # 這筆資格的來源日期（例如本月/上月排到的國定假日）
    source_type: str                # 來源班別說明，例如 '大夜'、'小夜'、'手動'
    source: str                     # 'current_month_auto' / 'carry_over_detected' / 'manual'
    approved: bool = False
    consumed: bool = False
    use_after_date: Optional[date] = None  # None = 下一個符合條件的正常班就使用；
                                            # 有值 = 只有 slot 日期「晚於」此日期才能使用
                                            # （'current_month_auto' 不使用這個欄位，
                                            # 它的日期條件固定是「晚於 source_date」）
    note: str = ''
