"""
護理排班系統 - Streamlit 網頁介面
"""
import streamlit as st
import tempfile
import os
import io
import json
import calendar
from datetime import date, datetime
from contextlib import redirect_stdout

# 設定頁面
st.set_page_config(
    page_title="BOR 護理排班系統",
    page_icon="🏥",
    layout="wide"
)

# 國定假日儲存檔案路徑
def get_holidays_file_path():
    """取得國定假日設定檔路徑（與 exe 同目錄）"""
    # 優先使用環境變數指定的目錄（由 launcher 設定）
    config_dir = os.environ.get('BOR_CONFIG_DIR')
    if config_dir:
        return os.path.join(config_dir, 'holidays_config.json')

    # 否則使用預設邏輯
    import sys
    if getattr(sys, 'frozen', False):
        app_dir = os.path.dirname(sys.executable)
    else:
        app_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(app_dir, 'holidays_config.json')

def load_all_holidays():
    """從 JSON 檔案載入所有年份的國定假日設定"""
    filepath = get_holidays_file_path()
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_all_holidays(all_holidays):
    """儲存所有年份的國定假日設定到 JSON 檔案"""
    filepath = get_holidays_file_path()
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(all_holidays, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        st.error(f"儲存失敗: {e}")
        return False

# 預設的國定假日範本（可作為新年份的參考）
DEFAULT_HOLIDAYS_TEMPLATE = {
    
    '2026': {
        '1/1': '元旦',
        '2/14': '除夕前',
        '2/15': '除夕前',
        '2/16': '除夕',
        '2/17': '春節',
        '2/18': '初二',
        '2/19': '初三',
        '2/20': '初四',
        '2/27': '228連假',
        '2/28': '228和平紀念日',
        '4/3': '兒童節連假',
        '4/4': '兒童節',
        '4/5': '清明節',
        '4/6': '清明連假',
        '5/1': '勞動節',
        '6/19': '端午節',
        '9/25': '中秋節',
        '9/28': '孔子誕辰紀念日',
        '10/9': '國慶連假',
        '10/10': '國慶日',
        '10/25': '臺灣光復暨金門古寧頭大捷紀念日',
        '10/26': '臺灣光復暨金門古寧頭大捷紀念日連假',
        '12/25': '行憲紀念日'
    },
    '2027': {
        '1/1': '元旦',
        '2/4': '除夕前小年夜',
        '2/5': '除夕',
        '2/6': '春節',
        '2/7': '初二',
        '2/8': '初三',
        '2/28': '228和平紀念日',
        '3/1': '228和平紀念日補假',
        '4/4': '兒童節',
        '4/5': '清明節',
        '4/6': '兒童節連假',
        '4/30': '勞動節連假',
        '5/1': '勞動節',
        '6/9': '端午節',
        '9/15': '中秋節',
        '9/28': '孔子誕辰紀念日',
        '10/10': '國慶日',
        '10/11': '國慶連假',
        '10/25': '臺灣光復暨金門古寧頭大捷紀念日',
        '12/24': '行憲紀念日補假',
        '12/25': '行憲紀念日',
        '12/31': '元旦逢例假日補放假'
    },
}

# 標題
st.title("🏥 BOR 護理排班系統")
st.markdown("**臺北榮民總醫院護理部思源手術室**")
st.markdown("---")

# 匯入排班邏輯
try:
    import config
    from config import HOLIDAYS_2026
    from date_utils import roc_to_western_year
    from excel_handler import get_sheet_names, create_schedule_excel_multi_sheet
    from main import parse_title_year_month, process_sheet, build_pre_schedule_check
    from night_shift import format_night_shift_result
    from small_night_shift import format_small_night_shift_result
    from holiday_shift import format_holiday_shift_result
    modules_loaded = True
except ImportError as e:
    modules_loaded = False
    st.error(f"❌ 無法載入排班模組: {e}")
    st.info("請確認所有相關檔案都在同一目錄下")

if modules_loaded:
    # 初始化 session state
    if 'all_holidays' not in st.session_state:
        # 先嘗試從檔案載入
        saved_holidays = load_all_holidays()
        if saved_holidays:
            st.session_state.all_holidays = saved_holidays
        else:
            # 使用預設範本
            st.session_state.all_holidays = dict(DEFAULT_HOLIDAYS_TEMPLATE)

    if 'selected_year' not in st.session_state:
        st.session_state.selected_year = '2026'

    def _nurse_name(nurse):
        return getattr(nurse, 'name', '')

    def _dates_text(dates):
        if not dates:
            return ''
        return '.'.join(f"{d.month}/{d.day}" for d in dates)

    def _status_tags(*items):
        return '、'.join([item for item in items if item]) or '-'

    def _manual_anchor_key(sheet_name, shift_type):
        return f"manual_anchor_{shift_type}_{sheet_name}"

    def _get_manual_anchor(sheet_name, shift_type):
        value = st.session_state.get(_manual_anchor_key(sheet_name, shift_type), '自動判斷')
        return None if value == '自動判斷' else value

    def get_manual_anchor_overrides(sheet_name):
        return {
            'night': _get_manual_anchor(sheet_name, 'night'),
            'small_night': _get_manual_anchor(sheet_name, 'small_night'),
            'holiday': _get_manual_anchor(sheet_name, 'holiday'),
        }

    def _holiday_makeup_override_key(nurse_name, record):
        pending_index = int(record.get('pending_index') or 0)
        return f"{nurse_name}|{record.get('original_text', '')}|{record.get('column_index') or 1}|{pending_index}"

    def _holiday_makeup_ui_key(sheet_name, nurse_name, record, field):
        return f"holiday_makeup_{field}_{sheet_name}_{nurse_name}_{record.get('pending_index') or 0}_{record.get('column_index') or 1}"

    def get_holiday_makeup_date_overrides(sheet_name, pre_check):
        overrides = {}
        records_by_nurse = pre_check.get('carryover_records_by_nurse', {})
        default_min_date = date(int(pre_check.get('year')), int(pre_check.get('month')), 1)
        for nurse_name, records in records_by_nurse.items():
            for record in records:
                if record.get('type') != 'pending':
                    continue
                enabled_key = _holiday_makeup_ui_key(sheet_name, nurse_name, record, 'enabled')
                date_key = _holiday_makeup_ui_key(sheet_name, nurse_name, record, 'date')
                shift_key = _holiday_makeup_ui_key(sheet_name, nurse_name, record, 'shift')
                target_date = st.session_state.get(date_key)
                target_shift = st.session_state.get(shift_key)
                default_dt = record.get('target_date')
                default_date = default_dt.date() if hasattr(default_dt, 'date') else default_min_date
                default_shift = record.get('target_shift_type') or '自動'
                is_enabled = st.session_state.get(enabled_key)
                date_changed = target_date and target_date != default_date
                shift_changed = target_shift and target_shift != default_shift
                if not (is_enabled or date_changed or shift_changed):
                    continue

                override = {}
                if target_date:
                    override['target_date'] = target_date
                if target_shift and target_shift != '自動':
                    override['target_shift_type'] = target_shift
                if override:
                    overrides[_holiday_makeup_override_key(nurse_name, record)] = override
        return overrides

    def build_quality_notes(results):
        notes = []
        pre_check = results.get('pre_schedule_check', {})
        nurse_names = {_nurse_name(nurse) for nurse in results.get('nurses', [])}
        manual_overrides = results.get('manual_anchor_overrides') or {}

        if not nurse_names:
            notes.append(("error", "未讀到可排班護理人員，請檢查姓名欄位與表頭。"))

        for label, key in [('大夜', 'last_night'), ('小夜週', 'last_small_night'), ('假日班', 'last_holiday')]:
            last_name = pre_check.get(key)
            if last_name and last_name not in nurse_names:
                notes.append(("warning", f"{label}上月定位點 `{last_name}` 不在本月名單內，系統會從名單第一位重新推算。"))

        if not results.get('night_results'):
            notes.append(("warning", "本月沒有產生大夜班結果。"))
        if not results.get('small_night_results'):
            notes.append(("warning", "本月沒有產生小夜週結果。"))
        if not results.get('holiday_results'):
            notes.append(("info", "本月沒有產生假日班結果。"))

        pending = []
        for nurse in results.get('nurses', []):
            for item in getattr(nurse, 'new_pending_makeup', []):
                pending.append(f"{nurse.name}：{item.get('shift_type', '')} 原{_dates_text(item.get('dates', []))}")
        if pending:
            notes.append(("warning", "本月尚有待補班：" + "；".join(pending)))

        night_skipped = sorted(results.get('night_identity_skipped') or [])
        small_skipped = sorted(results.get('small_night_identity_skipped') or [])
        if night_skipped:
            notes.append(("info", "大夜因身分/特殊條件跳過：" + "、".join(night_skipped)))
        if small_skipped:
            notes.append(("info", "小夜週因身分/特殊條件跳過：" + "、".join(small_skipped)))

        manual_labels = []
        if manual_overrides.get('night'):
            manual_labels.append(f"大夜 `{manual_overrides['night']}`")
        if manual_overrides.get('small_night'):
            manual_labels.append(f"小夜週 `{manual_overrides['small_night']}`")
        if manual_overrides.get('holiday'):
            manual_labels.append(f"假日班 `{manual_overrides['holiday']}`")
        if manual_labels:
            notes.append(("info", "本次使用手動定位點：" + "、".join(manual_labels)))

        return notes

    def render_quality_notes(results):
        notes = build_quality_notes(results)
        if not notes:
            st.success("品質檢查未發現明顯異常。")
            return

        for level, message in notes:
            if level == "error":
                st.error(message)
            elif level == "warning":
                st.warning(message)
            else:
                st.info(message)

    def render_pre_schedule_checks(pre_checks):
        st.header("🔎 排班前檢查")
        st.caption("先確認系統讀到的輪值定位點與跨月資料，沒問題再開始排班。")

        for sheet_name, check in pre_checks.items():
            nurses = check.get('nurses', [])
            carryover_by_nurse = check.get('carryover_by_nurse', {})
            previous_entries = check.get('previous_entries', {})

            with st.expander(f"📄 {sheet_name}", expanded=True):
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("護理人員", len(nurses))
                with col2:
                    st.metric("大夜接續", check.get('next_night') or '-')
                with col3:
                    next_small_name = check.get('next_small_night') or '-'
                    st.metric("小夜週接續", next_small_name)
                with col4:
                    st.metric("假日班接續", check.get('next_holiday') or '-')

                st.markdown("**上月定位點**")
                st.markdown(
                    f"- 大夜: `{check.get('last_night') or '未找到'}`\n"
                    f"- 小夜週: `{check.get('last_small_night') or '未找到'}`\n"
                    f"- 假日班: `{check.get('last_holiday') or '未找到'}`"
                )

                st.markdown("**手動覆寫定位點（選填）**")
                st.caption("選到的人會被視為上月最後一位，本次排班會從下一位開始。未選則使用 Excel 自動判斷。")
                nurse_names = [nurse.name for nurse in nurses]
                anchor_options = ['自動判斷'] + nurse_names
                anchor_cols = st.columns(3)
                anchor_settings = [
                    ('大夜', 'night', check.get('last_night')),
                    ('小夜週', 'small_night', check.get('last_small_night')),
                    ('假日班', 'holiday', check.get('last_holiday')),
                ]
                for col, (label, shift_type, auto_value) in zip(anchor_cols, anchor_settings):
                    key = _manual_anchor_key(sheet_name, shift_type)
                    if st.session_state.get(key) not in anchor_options:
                        st.session_state[key] = '自動判斷'
                    with col:
                        st.selectbox(
                            label,
                            options=anchor_options,
                            key=key,
                            help=f"目前自動判斷：{auto_value or '未找到'}",
                        )

                if carryover_by_nurse:
                    rows = [
                        {"姓名": name, "跨月補班/備註": "；".join(items)}
                        for name, items in carryover_by_nurse.items()
                    ]
                    st.markdown("**跨月補班資料**")
                    st.table(rows)

                if carryover_by_nurse:
                    pending_records = [
                        (name, record)
                        for name, records in check.get('carryover_records_by_nurse', {}).items()
                        for record in records
                        if record.get('type') == 'pending'
                    ]
                    if pending_records:
                        st.markdown("**X月補指定日期（選填）**")
                        st.caption("不勾指定時，維持原本自動找第一個可補格子的規則。")
                        _, last_day = calendar.monthrange(int(check.get('year')), int(check.get('month')))
                        min_makeup_date = date(int(check.get('year')), int(check.get('month')), 1)
                        max_makeup_date = date(int(check.get('year')), int(check.get('month')), last_day)
                        for nurse_name, record in pending_records:
                            default_dt = record.get('target_date')
                            default_date = default_dt.date() if hasattr(default_dt, 'date') else min_makeup_date
                            default_shift = record.get('target_shift_type') or '自動'
                            date_key = _holiday_makeup_ui_key(sheet_name, nurse_name, record, 'date')
                            shift_key = _holiday_makeup_ui_key(sheet_name, nurse_name, record, 'shift')
                            if st.session_state.get(date_key) is None:
                                st.session_state[date_key] = default_date
                            if st.session_state.get(shift_key) is None:
                                st.session_state[shift_key] = default_shift

                            col_name, col_enabled, col_date, col_shift = st.columns([2, 1, 1, 1])
                            with col_name:
                                st.write(f"{nurse_name}: {record.get('original_text', '')}")
                            with col_enabled:
                                st.checkbox(
                                    "指定",
                                    key=_holiday_makeup_ui_key(sheet_name, nurse_name, record, 'enabled'),
                                )
                            with col_date:
                                st.date_input(
                                    "日期",
                                    min_value=min_makeup_date,
                                    max_value=max_makeup_date,
                                    key=date_key,
                                )
                            with col_shift:
                                st.selectbox(
                                    "班別",
                                    options=['自動', '白班', '小夜', '大夜'],
                                    key=shift_key,
                                )

                missing_previous = [nurse.name for nurse in nurses if nurse.name not in previous_entries]
                if missing_previous:
                    st.warning("下列人員沒有讀到既有班別欄位資料：" + "、".join(missing_previous))
                elif nurses:
                    st.success("已讀到所有人員的既有班別欄位資料。")

    def render_shift_preview(results, month):
        tab_night, tab_small, tab_holiday = st.tabs(["大夜", "小夜週", "假日班"])

        with tab_night:
            rows = []
            for result in results.get('night_results', []):
                rows.append({
                    "日期": format_night_shift_result(result),
                    "姓名": result['nurse'].name,
                    "類型": "補班" if result.get('is_makeup') else "正常",
                    "狀態": _status_tags(
                        "下月" if result.get('is_next_month') else '',
                        "定位點" if result.get('is_last_normal') else '',
                    ),
                })
            st.table(rows or [{"日期": "-", "姓名": "-", "類型": "無資料", "狀態": "-"}])

        with tab_small:
            rows = []
            for result in results.get('small_night_results', []):
                rows.append({
                    "日期": format_small_night_shift_result(result),
                    "姓名": result['nurse'].name,
                    "類型": "補班" if result.get('is_makeup') else "正常",
                    "狀態": _status_tags(
                        "下月" if result.get('is_next_month') else '',
                        "定位點" if result.get('is_last_normal') else '',
                    ),
                })
            st.table(rows or [{"日期": "-", "姓名": "-", "類型": "無資料", "狀態": "-"}])

        with tab_holiday:
            rows = []
            for result in results.get('holiday_results', []):
                rows.append({
                    "班別": format_holiday_shift_result(result, month),
                    "姓名": result.nurse.name,
                    "欄位": result.slot.shift_name,
                    "狀態": _status_tags(
                        "下月" if result.slot.is_next_month else '',
                        "補班" if result.is_makeup else '',
                        "國假補償" if result.is_holiday_compensation else '',
                        "跳過" if result.is_skipped else '',
                        "定位點" if result.is_last_normal else '',
                    ),
                })
            st.table(rows or [{"班別": "-", "姓名": "-", "欄位": "無資料", "狀態": "-"}])

    def render_last_run():
        run = st.session_state.get('last_run')
        if not run:
            return

        st.markdown("---")
        st.success("✅ 排班完成！")

        show_logs = st.checkbox("顯示詳細 log", value=False, key="show_logs")
        log_container = st.expander("📝 排班詳細日誌", expanded=show_logs)
        with log_container:
            if show_logs:
                log_text = run.get('log_text', '')
                st.text(log_text[-5000:] if log_text else '')
            else:
                st.caption("勾選「顯示詳細 log」後顯示")

        st.header("📊 排班摘要")
        for sheet_name, results in run['all_results'].items():
            with st.expander(f"📄 {sheet_name}", expanded=True):
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("護理人員數", len(results['nurses']))
                with col2:
                    st.metric("大夜班排班數", len(results['night_results']))
                with col3:
                    st.metric("小夜班排班數", len(results['small_night_results']))
                with col4:
                    st.metric("假日班排班數", len(results['holiday_results']))

                st.markdown("**定位點（下月接續）：**")
                st.markdown(f"- 大夜: `{results['night_last_normal']}`")
                st.markdown(f"- 小夜週: `{results['small_night_last_normal']}`")
                st.markdown(f"- 假日班: `{results['holiday_last_normal']}`")

                st.markdown("**品質檢查：**")
                render_quality_notes(results)

                st.markdown("**結果預覽：**")
                render_shift_preview(results, run.get('month'))

        st.markdown("---")
        st.header("📥 下載結果")
        st.download_button(
            label=f"下載結果 {run['output_filename']}",
            data=run['output_bytes'],
            file_name=run['output_filename'],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

    # ========== 側邊欄：國定假日設定 ==========
    with st.sidebar:
        st.header("📅 國定假日設定")

        # 年份選擇
        available_years = sorted(st.session_state.all_holidays.keys())

        # 確保有可選的年份
        if not available_years:
            available_years = ['2026']
            st.session_state.all_holidays['2026'] = dict(DEFAULT_HOLIDAYS_TEMPLATE.get('2026', {}))

        # 新增年份區域
        st.subheader("📆 選擇/新增年份")

        col1, col2 = st.columns([2, 1])
        with col1:
            selected_year = st.selectbox(
                "選擇年份",
                options=available_years,
                index=available_years.index(st.session_state.selected_year) if st.session_state.selected_year in available_years else 0,
                key="year_selector"
            )
            st.session_state.selected_year = selected_year

        with col2:
            new_year = st.text_input("新增", placeholder="如:2028", max_chars=4, key="new_year_input")

        if st.button("➕ 新增年份", use_container_width=True):
            if new_year and new_year.isdigit() and len(new_year) == 4:
                if new_year not in st.session_state.all_holidays:
                    # 複製最近一年的設定作為範本
                    if available_years:
                        template_year = max(available_years)
                        st.session_state.all_holidays[new_year] = dict(st.session_state.all_holidays[template_year])
                    else:
                        st.session_state.all_holidays[new_year] = {}
                    st.session_state.selected_year = new_year
                    save_all_holidays(st.session_state.all_holidays)
                    st.success(f"✅ 已新增 {new_year} 年")
                    st.rerun()
                else:
                    st.warning(f"⚠️ {new_year} 年已存在")
            else:
                st.warning("請輸入4位數年份")

        st.markdown("---")

        # 取得當前年份的假日
        current_year = st.session_state.selected_year
        if current_year not in st.session_state.all_holidays:
            st.session_state.all_holidays[current_year] = {}

        current_holidays = st.session_state.all_holidays[current_year]

        st.subheader(f"🗓️ {current_year} 年國定假日")
        st.caption("修改後會自動儲存")

        # 顯示目前的假日列表
        holidays_to_remove = []

        # 按月份分組顯示
        holidays_by_month = {}
        for date_str, name in sorted(current_holidays.items(),
                                      key=lambda x: (int(x[0].split('/')[0]), int(x[0].split('/')[1]))):
            month = int(date_str.split('/')[0])
            if month not in holidays_by_month:
                holidays_by_month[month] = []
            holidays_by_month[month].append((date_str, name))

        # 顯示各月份的假日
        if holidays_by_month:
            for month in sorted(holidays_by_month.keys()):
                with st.expander(f"📆 {month} 月 ({len(holidays_by_month[month])} 天)", expanded=False):
                    for date_str, name in holidays_by_month[month]:
                        col1, col2 = st.columns([3, 1])
                        with col1:
                            st.write(f"**{date_str}** - {name}")
                        with col2:
                            if st.button("🗑️", key=f"del_{current_year}_{date_str}", help=f"刪除 {date_str}"):
                                holidays_to_remove.append(date_str)
        else:
            st.info("尚無國定假日，請新增")

        # 處理刪除
        for date_str in holidays_to_remove:
            del st.session_state.all_holidays[current_year][date_str]
            save_all_holidays(st.session_state.all_holidays)
            st.rerun()

        st.markdown("---")

        # 新增假日區域
        st.subheader("➕ 新增假日")

        col1, col2 = st.columns(2)
        with col1:
            new_month = st.selectbox("月份", range(1, 13), key="new_month")
        with col2:
            new_day = st.selectbox("日期", range(1, 32), key="new_day")

        new_name = st.text_input("假日名稱", placeholder="例如: 颱風假", key="new_name")

        if st.button("✅ 新增假日", use_container_width=True):
            if new_name:
                new_date_str = f"{new_month}/{new_day}"
                _, last_day = calendar.monthrange(int(current_year), new_month)
                if new_day > last_day:
                    st.warning(f"⚠️ {current_year}/{new_month} 沒有 {new_day} 日")
                elif new_date_str in st.session_state.all_holidays[current_year]:
                    st.warning(f"⚠️ {new_date_str} 已存在")
                else:
                    st.session_state.all_holidays[current_year][new_date_str] = new_name
                    save_all_holidays(st.session_state.all_holidays)
                    st.success(f"✅ 已新增 {new_date_str} {new_name}")
                    st.rerun()
            else:
                st.warning("請輸入假日名稱")

        st.markdown("---")

        # 工具按鈕
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 重設此年", use_container_width=True, help="重設為預設值"):
                if current_year in DEFAULT_HOLIDAYS_TEMPLATE:
                    st.session_state.all_holidays[current_year] = dict(DEFAULT_HOLIDAYS_TEMPLATE[current_year])
                else:
                    st.session_state.all_holidays[current_year] = {}
                save_all_holidays(st.session_state.all_holidays)
                st.success("已重設")
                st.rerun()

        with col2:
            if st.button("🗑️ 刪除此年", use_container_width=True, help="刪除整年設定"):
                if len(st.session_state.all_holidays) > 1:
                    del st.session_state.all_holidays[current_year]
                    st.session_state.selected_year = list(st.session_state.all_holidays.keys())[0]
                    save_all_holidays(st.session_state.all_holidays)
                    st.success("已刪除")
                    st.rerun()
                else:
                    st.warning("至少需保留一個年份")

        # 統計
        st.markdown("---")
        st.metric(f"📊 {current_year}年假日數", f"{len(current_holidays)} 天")

    # ========== 主區域 ==========

    # 檔案上傳區
    st.header("📁 上傳值班表")
    uploaded_file = st.file_uploader(
        "選擇 Excel 檔案",
        type=['xlsx', 'xls'],
        help="請上傳包含護理人員資料的值班表 Excel 檔案"
    )

    if uploaded_file is not None:
        # 儲存上傳的檔案到臨時位置
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

        try:
            # 解析年月
            roc_year, input_month = parse_title_year_month(tmp_path)

            if roc_year is None or input_month is None:
                st.error("❌ 無法從 Excel 標題解析年份和月份")
                st.info("請確認標題格式為: XXX年X月值班表")
            else:
                # 計算目標月份：上傳本月值班表，系統產生下個月排班表
                if input_month == 12:
                    month = 1
                    year = roc_to_western_year(roc_year) + 1
                    roc_year += 1
                else:
                    month = input_month + 1
                    year = roc_to_western_year(roc_year)

                year = roc_to_western_year(roc_year)

                # 顯示檔案資訊
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("📅 民國年", f"{roc_year} 年")
                with col2:
                    st.metric("📆 月份", f"{month} 月")
                with col3:
                    st.metric("🗓️ 西元年", f"{year} 年")

                # 取得頁籤
                sheet_names = get_sheet_names(tmp_path)
                st.info(f"📑 找到 {len(sheet_names)} 個頁籤: {', '.join(sheet_names)}")

                # 檢查對應年份的國定假日設定
                year_str = str(year)
                if year_str in st.session_state.all_holidays:
                    holidays_for_year = st.session_state.all_holidays[year_str]
                    month_holidays = {k: v for k, v in holidays_for_year.items()
                                      if int(k.split('/')[0]) == month}
                    if month_holidays:
                        holiday_str = ", ".join([f"{k}({v})" for k, v in sorted(month_holidays.items(),
                                                 key=lambda x: int(x[0].split('/')[1]))])
                        st.success(f"🎌 {month}月國定假日: {holiday_str}")
                    else:
                        st.info(f"📅 {month}月沒有國定假日")
                else:
                    st.warning(f"⚠️ 尚未設定 {year} 年的國定假日，請在左側新增")

                st.markdown("---")

                pre_schedule_checks = {}
                with st.spinner("正在建立排班前檢查..."):
                    for sheet_name in sheet_names:
                        try:
                            pre_schedule_checks[sheet_name] = build_pre_schedule_check(
                                tmp_path,
                                sheet_name,
                                year,
                                month,
                            )
                        except Exception as e:
                            st.error(f"建立 {sheet_name} 排班前檢查時發生錯誤: {e}")

                if pre_schedule_checks:
                    render_pre_schedule_checks(pre_schedule_checks)

                st.markdown("---")

                # 排班按鈕
                if st.button("🚀 開始排班", type="primary", use_container_width=True):

                    # 🔴 關鍵：動態更新 config 模組的假日設定
                    year_str = str(year)
                    if year_str in st.session_state.all_holidays:
                        config.HOLIDAYS_2026 = st.session_state.all_holidays[year_str]
                    else:
                        config.HOLIDAYS_2026 = {}
                        st.warning("⚠️ 使用空的國定假日設定")

                    # 建立進度條和狀態
                    progress_bar = st.progress(0)
                    status_text = st.empty()

                    # 用於捕捉輸出的區域
                    log_container = st.container()

                    all_results = {}
                    total_sheets = len(sheet_names)

                    # 捕捉 print 輸出
                    log_output = io.StringIO()

                    with log_container:
                        for idx, sheet_name in enumerate(sheet_names):
                            status_text.text(f"正在處理: {sheet_name} ({idx + 1}/{total_sheets})")
                            progress_bar.progress((idx + 1) / (total_sheets + 1))
                            pre_schedule_check = pre_schedule_checks.get(sheet_name) or build_pre_schedule_check(
                                tmp_path,
                                sheet_name,
                                year,
                                month,
                            )
                            manual_anchor_overrides = get_manual_anchor_overrides(sheet_name)
                            holiday_makeup_date_overrides = get_holiday_makeup_date_overrides(sheet_name, pre_schedule_check)

                            # 捕捉這個頁籤的輸出
                            sheet_output = io.StringIO()
                            with redirect_stdout(sheet_output):
                                try:
                                    (
                                        nurses,
                                        night_results,
                                        small_night_results,
                                        holiday_results,
                                        night_last_normal,
                                        small_night_last_normal,
                                        holiday_last_normal,
                                        night_identity_skipped,
                                        small_night_identity_skipped,
                                    ) = process_sheet(
                                        tmp_path,
                                        sheet_name,
                                        year,
                                        month,
                                        last_assigned_night_name=manual_anchor_overrides.get('night'),
                                        last_assigned_small_night_name=manual_anchor_overrides.get('small_night'),
                                        last_assigned_holiday_name=manual_anchor_overrides.get('holiday'),
                                        holiday_makeup_date_overrides=holiday_makeup_date_overrides,
                                    )

                                    all_results[sheet_name] = {
                                        'nurses': nurses,
                                        'night_results': night_results,
                                        'small_night_results': small_night_results,
                                        'holiday_results': holiday_results,
                                        'night_last_normal': night_last_normal,
                                        'small_night_last_normal': small_night_last_normal,
                                        'holiday_last_normal': holiday_last_normal,
                                        'night_identity_skipped': night_identity_skipped,
                                        'small_night_identity_skipped': small_night_identity_skipped,
                                        'pre_schedule_check': pre_schedule_check,
                                        'manual_anchor_overrides': manual_anchor_overrides,
                                        'holiday_makeup_date_overrides': holiday_makeup_date_overrides,
                                    }
                                except Exception as e:
                                    st.error(f"處理頁籤 {sheet_name} 時發生錯誤: {e}")
                                    import traceback
                                    st.code(traceback.format_exc())
                                    continue

                            # 顯示這個頁籤的輸出
                            output_text = sheet_output.getvalue()
                            if False:
                                st.text(output_text[-5000:])
                            log_output.write(output_text)

                    # 產生輸出檔案
                    status_text.text("正在產生排班結果...")
                    progress_bar.progress(0.9)

                    # 使用臨時檔案儲存結果
                    output_filename = f"排班結果_{roc_year}年{month}月.xlsx"
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as output_tmp:
                        output_path = output_tmp.name

                    title = f"臺北榮民總醫院護理部思源手術室{roc_year}年{month}月值班表"
                    create_schedule_excel_multi_sheet(output_path, title, all_results, year, month)

                    progress_bar.progress(1.0)
                    status_text.text("✅ 排班完成！")

                    with open(output_path, 'rb') as f:
                        output_bytes = f.read()

                    st.session_state.last_run = {
                        'all_results': all_results,
                        'output_bytes': output_bytes,
                        'output_filename': output_filename,
                        'log_text': log_output.getvalue(),
                        'month': month,
                    }

                    # 清理臨時輸出檔案
                    try:
                        os.unlink(output_path)
                    except:
                        pass

        except Exception as e:
            st.error(f"❌ 處理檔案時發生錯誤: {e}")
            import traceback
            st.code(traceback.format_exc())

        finally:
            # 清理臨時上傳檔案
            try:
                os.unlink(tmp_path)
            except:
                pass

        if st.session_state.get('last_run'):
            render_last_run()

    else:
        # 沒有上傳檔案時顯示提示
        st.info("請上傳 Excel 值班表以開始排班")
