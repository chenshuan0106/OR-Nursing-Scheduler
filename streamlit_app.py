"""
護理排班系統 - Streamlit 網頁介面
"""
import streamlit as st
import pandas
import tempfile
import os
import io
import json
import calendar
import hashlib
import re
from datetime import date, datetime, timedelta
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
    from main import (
        parse_title_year_month,
        process_sheet,
        build_pre_schedule_check,
        get_return_credit_candidates_from_nurses,
    )
    from models import ReturnCredit, make_return_credit_id
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

    # 還班（return credit）狀態：
    # - approved_return_credits：這次排班已核准可用的 credit，key 是 credit_id，
    #   不管是系統偵測後核准、還是使用者手動新增，一律存在這裡，排班核心只認這份清單。
    # - detected_return_credit_candidates：每個頁籤系統偵測到、但「尚未核准」的候選，
    #   只用來畫勾選 UI，跟 approved_return_credits 分開，避免 Excel 有資料就等於自動核准。
    if 'approved_return_credits' not in st.session_state:
        st.session_state.approved_return_credits = {}
    if 'detected_return_credit_candidates' not in st.session_state:
        st.session_state.detected_return_credit_candidates = {}

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

    def _manual_past_makeup_key(sheet_name):
        return f"manual_past_makeup_{sheet_name}"

    def _manual_shift_key(sheet_name):
        return f"manual_shift_entries_{sheet_name}"

    def _priority_makeup_key(sheet_name, shift_type=None):
        suffix = f"_{shift_type}" if shift_type else ""
        return f"priority_makeup_entries{suffix}_{sheet_name}"

    def _removed_priority_makeup_key(sheet_name, shift_type):
        return f"removed_priority_makeup_entries_{shift_type}_{sheet_name}"

    def _extra_makeup_key(sheet_name, shift_type):
        return f"extra_makeup_entries_{shift_type}_{sheet_name}"

    def get_manual_shift_entries(sheet_name):
        return list(st.session_state.get(_manual_shift_key(sheet_name), []))

    def get_priority_makeup_entries(sheet_name):
        entries = []
        for shift_type in ("大夜", "小夜週", "假日班"):
            entries.extend(st.session_state.get(_priority_makeup_key(sheet_name, shift_type), []))
        entries.extend(st.session_state.get(_priority_makeup_key(sheet_name), []))
        return list(entries)

    def get_extra_makeup_entries(sheet_name):
        entries = []
        for shift_type in ("大夜", "小夜週", "假日班"):
            entries.extend(st.session_state.get(_extra_makeup_key(sheet_name, shift_type), []))
        return list(entries)

    def _date_span(start_date, end_date):
        if not start_date or not end_date:
            return []
        first_date = min(start_date, end_date)
        last_date = max(start_date, end_date)
        current = first_date
        dates = []
        while current <= last_date:
            dates.append(current)
            current += timedelta(days=1)
        return dates

    def _default_manual_shift_end(start_date, shift_type, max_date):
        days = 2 if shift_type == '大夜' else 4
        return min(start_date + timedelta(days=days), max_date)

    def _manual_shift_style(shift_type):
        styles = {
            '大夜': {
                'bg': '#EAF2FF',
                'border': '#4C7DFF',
                'text': '#244A9B',
                'chip_bg': '#D7E5FF',
            },
            '小夜週': {
                'bg': '#EAF7EF',
                'border': '#2F9E66',
                'text': '#1F6B45',
                'chip_bg': '#D5F0DF',
            },
        }
        return styles.get(shift_type, styles['大夜'])

    def _render_manual_shift_panel(nurse_name, shift_type, start_date, end_date, day_count):
        style = _manual_shift_style(shift_type)
        person = nurse_name if nurse_name != '請選擇' else '未選人員'
        st.markdown(
            f"""
            <div style="
                background:{style['bg']};
                border-left:5px solid {style['border']};
                border-radius:8px;
                padding:12px 14px;
                margin:6px 0 12px 0;
            ">
              <div style="font-size:13px;color:{style['text']};font-weight:700;margin-bottom:6px;">目前要固定的班</div>
              <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;">
                <span style="background:{style['chip_bg']};color:{style['text']};padding:4px 9px;border-radius:999px;font-size:13px;font-weight:700;">{shift_type}</span>
                <span style="font-size:16px;font-weight:700;color:#1F2937;">{person}</span>
                <span style="font-size:15px;color:#374151;">{start_date.strftime('%m/%d')} - {end_date.strftime('%m/%d')}</span>
                <span style="font-size:13px;color:#6B7280;">共 {day_count} 天</span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    def _manual_shift_card_html(item, day_count):
        shift_type = item['shift_type']
        style = _manual_shift_style(shift_type)
        return f"""
        <div style="
            background:{style['bg']};
            border:1px solid {style['chip_bg']};
            border-left:5px solid {style['border']};
            border-radius:8px;
            padding:10px 12px;
            min-height:54px;
        ">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap;">
            <div>
              <span style="font-size:15px;font-weight:700;color:#1F2937;">{item['nurse_name']}</span>
              <span style="margin-left:8px;background:{style['chip_bg']};color:{style['text']};padding:3px 8px;border-radius:999px;font-size:12px;font-weight:700;">{shift_type}</span>
            </div>
            <span style="font-size:12px;color:#6B7280;">{day_count} 天</span>
          </div>
          <div style="margin-top:5px;font-size:13px;color:#374151;">
            {item['start_date'].strftime('%Y/%m/%d')} - {item['end_date'].strftime('%Y/%m/%d')}
          </div>
        </div>
        """

    def _manual_shift_warnings(entries, nurses):
        warnings = []
        occupied_dates = {}
        nurse_by_name = {nurse.name: nurse for nurse in nurses}

        for idx, entry in enumerate(entries):
            nurse_name = entry.get('nurse_name')
            shift_type = entry.get('shift_type')
            start_date = entry.get('start_date')
            end_date = entry.get('end_date')
            entry_dates = _date_span(start_date, end_date)
            label = f"{nurse_name} {shift_type} {start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')}"

            for target_date in entry_dates:
                date_key = target_date.strftime('%m/%d')
                if target_date in occupied_dates:
                    warnings.append(f"{date_key} 已同時出現在 `{occupied_dates[target_date]}` 和 `{label}`。")
                else:
                    occupied_dates[target_date] = label

            nurse = nurse_by_name.get(nurse_name)
            if not nurse:
                continue

            leave_dates = {d.date() for d in getattr(nurse, 'leave_dates', [])}
            wedding_dates = {d.date() for d in getattr(nurse, 'wedding_leave_dates', [])}
            conflict_dates = [
                target_date for target_date in entry_dates
                if target_date in leave_dates or target_date in wedding_dates
            ]
            if conflict_dates:
                date_text = '、'.join(d.strftime('%m/%d') for d in conflict_dates)
                warnings.append(f"`{label}` 包含 {nurse_name} 的公休/婚假：{date_text}。")

            for other_idx, other in enumerate(entries):
                if other_idx <= idx or other.get('nurse_name') != nurse_name:
                    continue
                overlap = set(entry_dates) & set(_date_span(other.get('start_date'), other.get('end_date')))
                if overlap:
                    date_text = '、'.join(d.strftime('%m/%d') for d in sorted(overlap))
                    warnings.append(f"{nurse_name} 的手動自填互相重疊：{date_text}。")

        return warnings

    def _pending_makeup_rows(nurses, shift_type=None):
        rows = []
        for nurse in nurses:
            pending_items = []
            if shift_type in (None, '大夜'):
                pending_items.extend(getattr(nurse, 'pending_night_makeup', []))
            if shift_type in (None, '小夜'):
                pending_items.extend(getattr(nurse, 'pending_small_night_makeup', []))

            for item in pending_items:
                rows.append({
                    '姓名': nurse.name,
                    '班別': item.get('shift_type') or '',
                    '原日期': _dates_text(item.get('dates', [])),
                    '來源': item.get('original_text') or '待補班',
                })
        return rows

    def get_manual_past_makeup_entries(sheet_name):
        return list(st.session_state.get(_manual_past_makeup_key(sheet_name), []))

    def manual_makeup_entries_to_extra(entries):
        extra_entries = []
        for idx, entry in enumerate(entries):
            if not entry.get('nurse_name'):
                continue
            extra_entries.append({
                'id': f"manual_makeup|auto|{entry.get('nurse_name')}|{idx}",
                'nurse_name': entry.get('nurse_name'),
                'shift_type': '假日班',
                'source': entry.get('note') or '手動新增補班',
                'source_type': 'manual_makeup',
            })
        return extra_entries

    def _makeup_note_key(sheet_name):
        return f"makeup_note_{sheet_name}"

    def build_quality_notes(results):
        notes = []
        pre_check = results.get('pre_schedule_check') or {}
        nurse_names = {_nurse_name(nurse) for nurse in results.get('nurses', [])}
        manual_overrides = results.get('manual_anchor_overrides') or {}
        manual_shift_entries = results.get('manual_shift_entries') or []
        priority_makeup_entries = results.get('priority_makeup_entries') or []
        manual_makeup_note = (results.get('manual_makeup_note') or '').strip()

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
            notes.append(("info", "本次使用常規起頭：" + "、".join(manual_labels)))
        if manual_shift_entries:
            shift_labels = []
            for entry in manual_shift_entries:
                start_date = entry.get('start_date')
                end_date = entry.get('end_date') or start_date
                if start_date and end_date:
                    shift_labels.append(
                        f"{entry.get('nurse_name')} `{entry.get('shift_type')}` "
                        f"{start_date.strftime('%m/%d')}-{end_date.strftime('%m/%d')}"
                    )
            if shift_labels:
                notes.append(("info", "本次使用手動自填班：" + "、".join(shift_labels)))

        if priority_makeup_entries:
            makeup_labels = [
                f"{idx + 1}. {entry.get('nurse_name')} `{entry.get('shift_type')}`"
                for idx, entry in enumerate(priority_makeup_entries)
            ]
            notes.append(("info", "本次使用排序補班清單：" + "、".join(makeup_labels)))

        if manual_makeup_note:
            notes.append(("info", "本次補班說明：" + manual_makeup_note))

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

    def _detected_priority_makeup_entries(nurses, shift_type, carryover_records_by_nurse=None):
        entries = []
        seen = set()
        seen_people_sources = set()
        for nurse in nurses:
            if shift_type == "大夜":
                records = getattr(nurse, 'pending_night_makeup', [])
            elif shift_type == "小夜週":
                records = getattr(nurse, 'pending_small_night_makeup', [])
            else:
                records = getattr(nurse, 'pending_holiday_makeup', [])

            for record_idx, record in enumerate(records):
                source = record.get('original_text') or 'Excel 已偵測待補'
                person_source_key = (nurse.name, source)
                if person_source_key in seen_people_sources:
                    continue
                seen_people_sources.add(person_source_key)
                entry_id = f"detected|{shift_type}|{nurse.name}|{record_idx}|{source}"
                seen.add(entry_id)
                entries.append({
                    'id': entry_id,
                    'nurse_name': nurse.name,
                    'shift_type': shift_type,
                    'source': source,
                    'source_type': 'detected',
                })

        if shift_type == "假日班" and carryover_records_by_nurse:
            for nurse_name, records in carryover_records_by_nurse.items():
                pending_idx = 0
                for record in records:
                    if record.get('type') != 'pending':
                        continue
                    source = record.get('original_text') or '跨月假日待補'
                    person_source_key = (nurse_name, source)
                    if person_source_key in seen_people_sources:
                        pending_idx += 1
                        continue
                    seen_people_sources.add(person_source_key)
                    entry_id = f"detected|{shift_type}|{nurse_name}|holiday|{pending_idx}|{source}"
                    pending_idx += 1
                    if entry_id in seen:
                        continue
                    entries.append({
                        'id': entry_id,
                        'nurse_name': nurse_name,
                        'shift_type': shift_type,
                        'source': source,
                        'source_type': 'detected',
                    })

        return entries

    def _sync_detected_priority_makeup(sheet_name, shift_type, detected_entries):
        priority_key = _priority_makeup_key(sheet_name, shift_type)
        removed_key = _removed_priority_makeup_key(sheet_name, shift_type)
        signature_key = f"{priority_key}_detected_signature"
        removed_ids = set(st.session_state.get(removed_key, []))
        active_detected_entries = [
            item for item in detected_entries
            if item.get('id') not in removed_ids
        ]
        signature = tuple(item.get('id') for item in active_detected_entries)
        if priority_key not in st.session_state:
            st.session_state[priority_key] = []

        if st.session_state.get(signature_key) == signature:
            return

        existing_ids = {item.get('id') for item in st.session_state[priority_key]}
        for item in active_detected_entries:
            if item.get('id') not in existing_ids:
                st.session_state[priority_key].append(item)
        st.session_state[signature_key] = signature

    def render_priority_makeup_section(sheet_name, nurses, nurse_names, shift_type, carryover_records_by_nurse=None):
        detected_entries = _detected_priority_makeup_entries(nurses, shift_type, carryover_records_by_nurse)
        _sync_detected_priority_makeup(sheet_name, shift_type, detected_entries)

        priority_key = _priority_makeup_key(sheet_name, shift_type)
        removed_key = _removed_priority_makeup_key(sheet_name, shift_type)
        if removed_key not in st.session_state:
            st.session_state[removed_key] = []
        entries = st.session_state[priority_key]
        entry_ids = {item.get('id') for item in entries}
        active_detected_entries = [
            item for item in detected_entries
            if item.get('id') in entry_ids
        ]

        st.markdown(f"**{shift_type}補班人員清單**")
        st.caption("先帶入系統已偵測到的待補人員，也可手動新增；不需要輸入補班日期，排序就是本班別補班優先順序。")

        if active_detected_entries:
            with st.expander(f"已偵測到的{shift_type}待補", expanded=True):
                st.table([
                    {
                        "姓名": item.get('nurse_name'),
                        "來源": item.get('source') or '待補班',
                    }
                    for item in active_detected_entries
                ])
        else:
            st.info(f"目前沒有從 Excel 偵測到{shift_type}待補人員。")

        with st.expander("手動新增補班人員", expanded=False):
            add_col1, add_col2 = st.columns([2, 1])
            with add_col1:
                selected_makeup_nurse = st.selectbox(
                    "選擇人員",
                    options=["請選擇"] + nurse_names,
                    key=f"priority_makeup_nurse_{shift_type}_{sheet_name}",
                )
            with add_col2:
                st.write("")
                if st.button(f"加入{shift_type}補班", key=f"add_priority_makeup_{shift_type}_{sheet_name}", use_container_width=True):
                    if selected_makeup_nurse == "請選擇":
                        st.warning("請先選擇人員。")
                    else:
                        manual_count = sum(1 for item in entries if item.get('source_type') == 'manual') + 1
                        entry = {
                            'id': f"manual|{shift_type}|{selected_makeup_nurse}|{manual_count}",
                            'nurse_name': selected_makeup_nurse,
                            'shift_type': shift_type,
                            'source': '手動新增',
                            'source_type': 'manual',
                        }
                        entries.append(entry)
                        st.success(f"已加入：{selected_makeup_nurse} / {shift_type}")
                        st.rerun()

        if not entries:
            st.info(f"目前{shift_type}補班清單是空的。")
            return

        label_to_entry = {
            f"{idx + 1}. {item.get('nurse_name')} ({item.get('source') or '待補班'})": item
            for idx, item in enumerate(entries)
        }
        try:
            from streamlit_sortables import sort_items
            sortable_signature = hashlib.md5(
                "|".join(str(item.get('id')) for item in entries).encode("utf-8")
            ).hexdigest()[:10]
            sorted_labels = sort_items(
                list(label_to_entry.keys()),
                direction="vertical",
                key=f"priority_makeup_sortable_{shift_type}_{sheet_name}_{sortable_signature}",
            )
            sorted_entries = [label_to_entry[label] for label in sorted_labels if label in label_to_entry]
            if sorted_entries != entries:
                st.session_state[priority_key] = sorted_entries
                st.rerun()
            st.caption("拖曳清單項目可調整補班志願序。")
        except Exception:
            st.caption("拖曳排序元件尚未載入；目前可先用上移 / 下移調整順序。")

        st.markdown("**目前補班志願序**")
        for idx, item in enumerate(list(st.session_state[priority_key])):
            row_col1, row_col2, row_col3, row_col4 = st.columns([4, 1, 1, 1])
            with row_col1:
                st.write(f"{idx + 1}. {item.get('nurse_name')} / {item.get('source') or '待補班'}")
            with row_col2:
                if st.button("上移", key=f"priority_makeup_up_{shift_type}_{sheet_name}_{idx}", disabled=idx == 0, use_container_width=True):
                    entries[idx - 1], entries[idx] = entries[idx], entries[idx - 1]
                    st.rerun()
            with row_col3:
                if st.button("下移", key=f"priority_makeup_down_{shift_type}_{sheet_name}_{idx}", disabled=idx == len(entries) - 1, use_container_width=True):
                    entries[idx + 1], entries[idx] = entries[idx], entries[idx + 1]
                    st.rerun()
            with row_col4:
                if st.button("刪除", key=f"priority_makeup_delete_{shift_type}_{sheet_name}_{idx}", use_container_width=True):
                    removed_item = entries.pop(idx)
                    if removed_item.get('source_type') == 'detected':
                        st.session_state[removed_key].append(removed_item.get('id'))
                    st.rerun()

        if st.button(f"清除{shift_type}補班清單", key=f"priority_makeup_clear_{shift_type}_{sheet_name}", use_container_width=True):
            st.session_state[removed_key].extend(
                item.get('id')
                for item in st.session_state[priority_key]
                if item.get('source_type') == 'detected'
            )
            st.session_state[priority_key] = []
            st.rerun()

    def render_extra_makeup_section(sheet_name, nurse_names, shift_type):
        extra_key = _extra_makeup_key(sheet_name, shift_type)
        if extra_key not in st.session_state:
            st.session_state[extra_key] = []

        with st.expander(f"額外補班（{shift_type}，選填）", expanded=False):
            st.caption("只指定人員，不指定日期。系統會自動找可排班位，結果寫入 Excel 的「額外補班」欄，不占用正常大夜/小夜週欄位。")
            entries = st.session_state[extra_key]

            add_col1, add_col2 = st.columns([2, 1])
            with add_col1:
                selected_extra_nurse = st.selectbox(
                    "選擇額外補班人員",
                    options=["請選擇"] + nurse_names,
                    key=f"extra_makeup_nurse_{shift_type}_{sheet_name}",
                )
            with add_col2:
                st.write("")
                if st.button(f"加入額外補班", key=f"add_extra_makeup_{shift_type}_{sheet_name}", use_container_width=True):
                    if selected_extra_nurse == "請選擇":
                        st.warning("請先選擇人員。")
                    else:
                        manual_count = len(entries) + 1
                        entries.append({
                            'id': f"extra|{shift_type}|{selected_extra_nurse}|{manual_count}",
                            'nurse_name': selected_extra_nurse,
                            'shift_type': shift_type,
                            'source': '額外補班',
                        })
                        st.rerun()

            if not entries:
                st.info(f"目前尚未新增{shift_type}額外補班。")
                return

            label_to_entry = {
                f"{idx + 1}. {item.get('nurse_name')}": item
                for idx, item in enumerate(entries)
            }
            try:
                from streamlit_sortables import sort_items
                sorted_labels = sort_items(
                    list(label_to_entry.keys()),
                    direction="vertical",
                    key=f"extra_makeup_sortable_{shift_type}_{sheet_name}",
                )
                sorted_entries = [label_to_entry[label] for label in sorted_labels if label in label_to_entry]
                if sorted_entries != entries:
                    st.session_state[extra_key] = sorted_entries
                    st.rerun()
                st.caption("拖曳清單項目可調整額外補班優先順序。")
            except Exception:
                st.caption("拖曳排序元件尚未載入；目前可先用上移 / 下移調整順序。")

            st.markdown("**目前額外補班順序**")
            for idx, item in enumerate(list(st.session_state[extra_key])):
                row_col1, row_col2, row_col3, row_col4 = st.columns([4, 1, 1, 1])
                with row_col1:
                    st.write(f"{idx + 1}. {item.get('nurse_name')}")
                with row_col2:
                    if st.button("上移", key=f"extra_makeup_up_{shift_type}_{sheet_name}_{idx}", disabled=idx == 0, use_container_width=True):
                        entries[idx - 1], entries[idx] = entries[idx], entries[idx - 1]
                        st.rerun()
                with row_col3:
                    if st.button("下移", key=f"extra_makeup_down_{shift_type}_{sheet_name}_{idx}", disabled=idx == len(entries) - 1, use_container_width=True):
                        entries[idx + 1], entries[idx] = entries[idx], entries[idx + 1]
                        st.rerun()
                with row_col4:
                    if st.button("刪除", key=f"extra_makeup_delete_{shift_type}_{sheet_name}_{idx}", use_container_width=True):
                        entries.pop(idx)
                        st.rerun()

            if st.button(f"清除{shift_type}額外補班", key=f"extra_makeup_clear_{shift_type}_{sheet_name}", use_container_width=True):
                st.session_state[extra_key] = []
                st.rerun()

    # ------------------------------------------------------------------
    # 還班（return credit）UI
    #
    # 三層分開：
    #   detected_return_credit_candidates[sheet_name] -> 系統偵測到但「尚未核准」的候選
    #   approved_return_credits[credit_id]             -> 已核准可用（系統偵測核准 + 手動指定）
    # 排班核心只讀 approved_return_credits，偵測到的候選一定要使用者勾選才會進去。
    # ------------------------------------------------------------------

    _RETURN_CREDIT_SOURCE_LABEL = {
        'carry_over_detected': '上月結轉',
        'manual': '手動指定',
        'current_month_auto': '本月自動',
    }
    _RETURN_CREDIT_EFFECTIVE_OPTIONS = ["接下來第一班", "指定日期後"]

    def get_return_credit_candidates(sheet_name, check):
        # 上月結轉候選只解析一次、存進 session_state，之後重跑直接沿用。
        cache = st.session_state.detected_return_credit_candidates
        if sheet_name not in cache:
            cache[sheet_name] = get_return_credit_candidates_from_nurses(
                sheet_name,
                check.get('nurses') or [],
                check.get('year'),
                check.get('month'),
                check.get('previous_entries'),
            )
        return cache[sheet_name]

    def render_return_credit_section(sheet_names, pre_schedule_checks):
        st.caption(
            "還班＝抵掉一次之後的正常假日輪值（不是補班，不會多上一班）。"
            "本月新產生的國假補償會自動套用，這裡只處理「上月結轉」與「手動新增」。"
        )

        approved = st.session_state.approved_return_credits  # Dict[str, ReturnCredit]

        all_candidates = []
        for sheet_name in sheet_names:
            check = pre_schedule_checks.get(sheet_name)
            if not check:
                continue
            all_candidates.extend(get_return_credit_candidates(sheet_name, check))

        # ---------------- 上月結轉候選：一張表 + 一個送出按鈕 ----------------
        # 全部包在 st.form 裡：改欄位（核准/生效方式/生效日期）不會整頁重跑，
        # 只有按「套用核准設定」才會處理一次，這是「改一個設定就要等很久」的主要解法。
        with st.container(border=True):
            st.subheader("① 上月結轉候選")
            if not all_candidates:
                st.caption("目前沒有從 Excel 偵測到上月結轉的還班候選。")
            else:
                candidate_by_id = {c.credit_id: c for c in all_candidates}
                rows = []
                for c in all_candidates:
                    existing = approved.get(c.credit_id)
                    rows.append({
                        "credit_id": c.credit_id,
                        "核准": existing is not None,
                        "頁籤": c.sheet_name,
                        "姓名": c.nurse_name,
                        "來源日期": f"{c.source_date.month}/{c.source_date.day}",
                        "來源類型": c.source_type,
                        "生效方式": (
                            "指定日期後" if (existing and existing.use_after_date) else "接下來第一班"
                        ),
                        "生效日期": (existing.use_after_date if existing else None) or c.source_date.date(),
                    })
                candidates_df = pandas.DataFrame(rows).set_index('credit_id')
                with st.form("carry_over_return_credit_form"):
                    edited_df = st.data_editor(
                        candidates_df,
                        hide_index=True,
                        width="stretch",
                        key="carry_over_return_credit_editor",
                        column_config={
                            "核准": st.column_config.CheckboxColumn("核准", width="small"),
                            "頁籤": st.column_config.TextColumn("頁籤", disabled=True),
                            "姓名": st.column_config.TextColumn("姓名", disabled=True),
                            "來源日期": st.column_config.TextColumn("來源日期", disabled=True),
                            "來源類型": st.column_config.TextColumn("來源類型", disabled=True),
                            "生效方式": st.column_config.SelectboxColumn(
                                "生效方式", options=_RETURN_CREDIT_EFFECTIVE_OPTIONS, required=True
                            ),
                            "生效日期": st.column_config.DateColumn(
                                "生效日期（生效方式選「指定日期後」才會用到）"
                            ),
                        },
                    )
                    submitted = st.form_submit_button("套用核准設定", type="primary", width="stretch")
                if submitted:
                    changed = 0
                    for credit_id, row in edited_df.iterrows():
                        candidate = candidate_by_id.get(credit_id)
                        if candidate is None:
                            continue
                        if row['核准']:
                            use_after = row['生效日期'] if row['生效方式'] == '指定日期後' else None
                            approved[credit_id] = ReturnCredit(
                                credit_id=credit_id,
                                sheet_name=candidate.sheet_name,
                                nurse_name=candidate.nurse_name,
                                source_date=candidate.source_date,
                                source_type=candidate.source_type,
                                source='carry_over_detected',
                                approved=True,
                                use_after_date=use_after,
                            )
                            print(
                                f"[上月還班核准] nurse={candidate.nurse_name} credit={candidate.source_date.date()} "
                                f"use_after_date={use_after} approved=True source=carry_over_detected"
                            )
                            changed += 1
                        elif credit_id in approved:
                            del approved[credit_id]
                    st.success(f"已套用核准設定（{changed} 筆核准中）。")

        # ---------------- 手動指定還班 ----------------
        # 頁籤放表單外面，選了立刻換人員清單；其餘欄位放表單裡一起送出，
        # 避免調日期/切換選項時每次都整頁重跑。
        with st.container(border=True):
            st.subheader("② 手動指定還班")
            manual_sheet = st.selectbox("頁籤", options=sheet_names, key="manual_return_credit_sheet")
            manual_nurse_names = [n.name for n in (pre_schedule_checks.get(manual_sheet, {}).get('nurses') or [])]

            check_for_default = pre_schedule_checks.get(manual_sheet, {})
            default_year = check_for_default.get('year') or date.today().year
            default_month = check_for_default.get('month') or date.today().month
            prev_month = default_month - 1 if default_month > 1 else 12
            prev_year = default_year if default_month > 1 else default_year - 1
            default_source_date = date(prev_year, prev_month, 1)

            with st.form("manual_return_credit_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                with col1:
                    manual_nurse = st.selectbox(
                        "人員", options=["請選擇"] + manual_nurse_names, key="manual_return_credit_nurse"
                    )
                    manual_source_date = st.date_input(
                        "還班來源日期（預設上個月月初，請務必改成實際日期）",
                        value=default_source_date,
                        key="manual_return_credit_source_date",
                    )
                with col2:
                    manual_mode = st.radio(
                        "使用方式",
                        _RETURN_CREDIT_EFFECTIVE_OPTIONS,
                        index=0,
                        key="manual_return_credit_mode",
                        horizontal=True,
                    )
                    manual_use_after_date = st.date_input(
                        "指定日期後才使用（不含當天；選「接下來第一班」時這欄不生效）",
                        value=default_source_date,
                        key="manual_return_credit_use_after_date",
                    )
                manual_note = st.text_input("說明", key="manual_return_credit_note")
                submitted_manual = st.form_submit_button("＋ 加入還班", type="primary", width="stretch")

            if submitted_manual:
                if manual_nurse == "請選擇":
                    st.warning("請先選擇人員。")
                else:
                    months_away = abs(
                        (manual_source_date.year * 12 + manual_source_date.month)
                        - (default_year * 12 + default_month)
                    )
                    if months_away > 2:
                        st.warning(
                            f"⚠️ 來源日期（{manual_source_date}）離本次排班月份（{default_year}/{default_month}）"
                            f"有點遠，請確認不是忘記改掉預設值。這筆還是已加入，若不對請到下面移除。"
                        )
                    source_dt = datetime(manual_source_date.year, manual_source_date.month, manual_source_date.day)
                    credit_id = make_return_credit_id(manual_sheet, manual_nurse, source_dt, '手動')
                    duplicate = any(
                        c.sheet_name == manual_sheet
                        and c.nurse_name == manual_nurse
                        and c.source_date.date() == source_dt.date()
                        for c in approved.values()
                    )
                    if duplicate:
                        st.warning("此還班資格已存在，不重複新增。")
                    else:
                        use_after = manual_use_after_date if manual_mode == "指定日期後" else None
                        approved[credit_id] = ReturnCredit(
                            credit_id=credit_id,
                            sheet_name=manual_sheet,
                            nurse_name=manual_nurse,
                            source_date=source_dt,
                            source_type='手動',
                            source='manual',
                            approved=True,
                            use_after_date=use_after,
                            note=manual_note,
                        )
                        print(
                            f"[手動還班] nurse={manual_nurse} credit={source_dt.date()} "
                            f"use_after_date={use_after} approved=True source=manual"
                        )
                        st.success(f"已加入：{manual_nurse}｜來源 {manual_source_date.month}/{manual_source_date.day}")

        # ---------------- 本次已核准還班 ----------------
        with st.container(border=True):
            st.subheader(f"✅ 本次已核准還班（{len(approved)} 筆）")
            if not approved:
                st.caption("目前沒有已核准的還班資格。")
            else:
                approved_rows = []
                for credit_id, credit in approved.items():
                    effective_text = (
                        "接下來第一班" if credit.use_after_date is None else f"{credit.use_after_date} 之後"
                    )
                    approved_rows.append({
                        "credit_id": credit_id,
                        "移除": False,
                        "姓名": credit.nurse_name,
                        "頁籤": credit.sheet_name,
                        "來源日期": f"{credit.source_date.month}/{credit.source_date.day}（{credit.source_type}）",
                        "生效": effective_text,
                        "來源": _RETURN_CREDIT_SOURCE_LABEL.get(credit.source, credit.source),
                        "說明": credit.note,
                    })
                approved_df = pandas.DataFrame(approved_rows).set_index('credit_id')
                with st.form("approved_return_credit_remove_form"):
                    edited_approved_df = st.data_editor(
                        approved_df,
                        hide_index=True,
                        width="stretch",
                        key="approved_return_credit_editor",
                        column_config={
                            "移除": st.column_config.CheckboxColumn("移除", width="small"),
                            "姓名": st.column_config.TextColumn(disabled=True),
                            "頁籤": st.column_config.TextColumn(disabled=True),
                            "來源日期": st.column_config.TextColumn(disabled=True),
                            "生效": st.column_config.TextColumn(disabled=True),
                            "來源": st.column_config.TextColumn(disabled=True),
                            "說明": st.column_config.TextColumn(disabled=True),
                        },
                    )
                    submitted_remove = st.form_submit_button("套用移除", width="stretch")
                if submitted_remove:
                    removed = 0
                    for credit_id, row in edited_approved_df.iterrows():
                        if row['移除'] and credit_id in approved:
                            del approved[credit_id]
                            removed += 1
                    if removed:
                        st.success(f"已移除 {removed} 筆。")
                    else:
                        st.info("沒有勾選要移除的項目。")

    def render_pre_schedule_checks(pre_checks):
        st.caption("系統已自動帶入 Excel 讀到的待補班；需要時可調整補班順序、常規起頭人員或補班說明。")

        for sheet_name, check in pre_checks.items():
            nurses = check.get('nurses', [])
            carryover_by_nurse = check.get('carryover_by_nurse', {})
            carryover_records_by_nurse = check.get('carryover_records_by_nurse', {})
            previous_entries = check.get('previous_entries', {})

            with st.expander(f"📄 {sheet_name}", expanded=len(pre_checks) == 1):
                nurse_names = [nurse.name for nurse in nurses]
                missing_previous = [nurse.name for nurse in nurses if nurse.name not in previous_entries]
                manual_shift_key = _manual_shift_key(sheet_name)
                if manual_shift_key not in st.session_state:
                    st.session_state[manual_shift_key] = []

                def _overview_start_value(shift_type, auto_value):
                    manual_value = st.session_state.get(_manual_anchor_key(sheet_name, shift_type), '自動判斷')
                    return manual_value if manual_value in nurse_names else (auto_value or '-')

                _, month_last_day = calendar.monthrange(int(check.get('year')), int(check.get('month')))
                min_shift_date = date(int(check.get('year')), int(check.get('month')), 1)
                next_month = int(check.get('month')) + 1 if int(check.get('month')) < 12 else 1
                next_year = int(check.get('year')) if int(check.get('month')) < 12 else int(check.get('year')) + 1
                _, next_month_last_day = calendar.monthrange(next_year, next_month)
                max_shift_date_with_next = date(next_year, next_month, next_month_last_day)

                for priority_shift_type in ("大夜", "小夜週", "假日班"):
                    _sync_detected_priority_makeup(
                        sheet_name,
                        priority_shift_type,
                        _detected_priority_makeup_entries(
                            nurses,
                            priority_shift_type,
                            carryover_records_by_nurse,
                        ),
                    )

                overview_tab, night_tab, small_tab, holiday_tab, note_tab = st.tabs([
                    "總覽",
                    "大夜補班",
                    "小夜補班",
                    "假日班",
                    "補班說明",
                ])

                with overview_tab:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("護理人員", len(nurses))
                    with col2:
                        st.metric("大夜接續", _overview_start_value('night', check.get('next_night')))
                    with col3:
                        st.metric("小夜週接續", _overview_start_value('small_night', check.get('next_small_night')))
                    with col4:
                        st.metric("假日班接續", _overview_start_value('holiday', check.get('next_holiday')))

                    pending_makeup_rows = _pending_makeup_rows(nurses)
                    priority_makeup_entries = get_priority_makeup_entries(sheet_name)
                    if pending_makeup_rows:
                        with st.expander("已讀到的大夜 / 小夜待補", expanded=False):
                            st.table(pending_makeup_rows)
                    if priority_makeup_entries:
                        with st.expander("本次排序補班清單", expanded=True):
                            st.table([
                                {
                                    "志願序": idx + 1,
                                    "姓名": item.get('nurse_name'),
                                    "班別": item.get('shift_type'),
                                }
                                for idx, item in enumerate(priority_makeup_entries)
                            ])
                    if missing_previous:
                        st.warning("下列人員沒有讀到既有班別欄位資料：" + "、".join(missing_previous))
                    elif nurses:
                        st.success("已讀到所有人員的既有班別欄位資料。")

                    st.markdown("**上月定位點**")
                    st.markdown(
                        f"- 大夜: `{check.get('last_night') or '未找到'}`\n"
                        f"- 小夜週: `{check.get('last_small_night') or '未找到'}`\n"
                        f"- 假日班: `{check.get('last_holiday') or '未找到'}`"
                    )

                    st.markdown("**常規排班起頭人員（選填）**")
                    st.caption("補班會先排入空格；剩下的常規班會從這裡選到的人開始。未選則使用 Excel 自動判斷。")
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

                    manual_anchor_messages = []
                    for label, shift_type, _ in anchor_settings:
                        value = st.session_state.get(_manual_anchor_key(sheet_name, shift_type))
                        if value and value != '自動判斷':
                            manual_anchor_messages.append(f"{label}: `{value}`")
                    if manual_anchor_messages:
                        st.info("常規起頭已選：" + "、".join(manual_anchor_messages))

                with night_tab:
                    render_priority_makeup_section(
                        sheet_name,
                        nurses,
                        nurse_names,
                        "大夜",
                    )
                    render_extra_makeup_section(sheet_name, nurse_names, "大夜")

                with small_tab:
                    render_priority_makeup_section(
                        sheet_name,
                        nurses,
                        nurse_names,
                        "小夜週",
                    )
                    render_extra_makeup_section(sheet_name, nurse_names, "小夜週")

                with holiday_tab:
                    render_priority_makeup_section(
                        sheet_name,
                        nurses,
                        nurse_names,
                        "假日班",
                        carryover_records_by_nurse,
                    )
                    st.markdown("---")
                    st.markdown("**假日額外補班**")
                    st.caption("只指定人員，不指定日期或班別；系統會在假日待補與一般輪序前，優先尋找可排班位。")
                    manual_past_makeup_key = _manual_past_makeup_key(sheet_name)
                    if manual_past_makeup_key not in st.session_state:
                        st.session_state[manual_past_makeup_key] = []
    
                    edit_index_key = f"edit_past_makeup_index_{sheet_name}"
                    if edit_index_key not in st.session_state:
                        st.session_state[edit_index_key] = None
                    loaded_edit_index_key = f"loaded_edit_past_makeup_index_{sheet_name}"
                    current_edit_index = st.session_state.get(edit_index_key)
                    if current_edit_index is not None and st.session_state.get(loaded_edit_index_key) != current_edit_index:
                        entries = st.session_state.get(manual_past_makeup_key, [])
                        if 0 <= current_edit_index < len(entries):
                            item = entries[current_edit_index]
                            st.session_state[f"past_makeup_nurse_{sheet_name}"] = item.get('nurse_name', '請選擇')
                            st.session_state[f"past_makeup_note_{sheet_name}"] = item.get('note', '')
                            st.session_state[loaded_edit_index_key] = current_edit_index

                    with st.expander("新增 / 編輯 假日額外補班", expanded=False):
                        st.caption("只選人員即可；系統會自動在可排的假日班中安排額外補班。")
                        add_col1, add_col2 = st.columns([2, 2])
                        with add_col1:
                            selected_past_makeup_nurse = st.selectbox(
                                "選擇人員",
                                options=["請選擇"] + nurse_names,
                                key=f"past_makeup_nurse_{sheet_name}",
                            )
                        with add_col2:
                            selected_past_makeup_note = st.text_input(
                                "說明（選填）",
                                key=f"past_makeup_note_{sheet_name}",
                                placeholder="例如：手動新增補班",
                            )
    
                        action_col1, action_col2, action_col3 = st.columns([1, 1, 1])
                        with action_col1:
                            if st.button("加入/更新補班", key=f"save_past_makeup_{sheet_name}", use_container_width=True):
                                if selected_past_makeup_nurse == "請選擇":
                                    st.warning("請先選擇人員再新增補班。")
                                else:
                                    to_save = {
                                        'nurse_name': selected_past_makeup_nurse,
                                        'note': selected_past_makeup_note.strip() or '手動新增補班',
                                    }
                                    if st.session_state[edit_index_key] is None:
                                        st.session_state[manual_past_makeup_key].append(to_save)
                                        st.success(f"已新增假日額外補班：{selected_past_makeup_nurse}")
                                    else:
                                        st.session_state[manual_past_makeup_key][st.session_state[edit_index_key]] = to_save
                                        st.success("已更新補班需求。")
                                        st.session_state[edit_index_key] = None
                                        st.session_state[loaded_edit_index_key] = None
    
                        with action_col2:
                            if st.button("取消編輯", key=f"cancel_edit_past_makeup_{sheet_name}", use_container_width=True):
                                st.session_state[edit_index_key] = None
                                st.session_state[loaded_edit_index_key] = None
    
                        with action_col3:
                            if st.button("清除本頁面補班需求", key=f"clear_past_makeup_{sheet_name}", use_container_width=True):
                                st.session_state[manual_past_makeup_key] = []
                                st.session_state[edit_index_key] = None
                                st.session_state[loaded_edit_index_key] = None
                                st.rerun()
    
                        st.markdown("**目前假日額外補班順序**")
                        entries = st.session_state[manual_past_makeup_key]
                        if entries:
                            for idx, item in enumerate(entries):
                                row_col1, row_col2, row_col3 = st.columns([2, 3, 1])
                                with row_col1:
                                    st.write(item.get('nurse_name', ''))
                                with row_col2:
                                    st.write(item.get('note', ''))
                                with row_col3:
                                    if st.button("編輯", key=f"edit_past_makeup_{sheet_name}_{idx}", use_container_width=True):
                                        st.session_state[edit_index_key] = idx
                                        st.session_state[loaded_edit_index_key] = None
                                        st.rerun()
                                    if st.button("刪除", key=f"delete_past_makeup_{sheet_name}_{idx}", use_container_width=True):
                                        entries.pop(idx)
                                        st.success("已刪除補班需求。")
                                        st.rerun()
                        else:
                            st.info("目前尚未新增任何手動補班需求。")
    
                with note_tab:
                    st.markdown("**補班說明（選填）**")
                    st.caption("若 Excel 補班欄未能完整描述補班需求，可在此填寫本次排班的補班備註。")
                    makeup_text_key = _makeup_note_key(sheet_name)
                    if makeup_text_key not in st.session_state:
                        st.session_state[makeup_text_key] = ''
                    st.text_area(
                        "補班說明",
                        key=makeup_text_key,
                        height=100,
                        placeholder="例如：請優先補班 5/10-5/12 小夜；或：本週補班以補大夜為主。",
                    )
    
    # ------------------------------------------------------------------
    # 排班結果：跟 Excel 一樣的表格 + 點一列看「為什麼這樣排」
    # ------------------------------------------------------------------

    def _excel_color(color):
        """openpyxl 顏色（'FFFF0000' / '00A0A0A0'）轉成 CSS；沒有指定就回傳 None。"""
        rgb = getattr(color, 'rgb', None)
        if isinstance(rgb, str) and len(rgb) in (6, 8):
            return '#' + rgb[-6:]
        return None

    def _excel_cell_html(cell):
        """把一格 Excel 轉成 HTML，保留每一段文字自己的顏色（例如灰色的上月日期、紅色的國定假日）。"""
        import html as html_lib
        from openpyxl.cell.rich_text import CellRichText

        value = cell.value
        cell_color = _excel_color(cell.font.color) if cell.font else None
        cell_bold = bool(cell.font and cell.font.b)

        def span(text, color=None, bold=False):
            text = html_lib.escape(text).replace('\n', '<br>')
            style = ''
            if color:
                style += f'color:{color};'
            if bold:
                style += 'font-weight:700;'
            return f'<span style="{style}">{text}</span>' if style else text

        if value is None:
            return ''
        if isinstance(value, CellRichText):
            parts = []
            for block in value:
                if isinstance(block, str):
                    parts.append(span(block, cell_color, cell_bold))
                else:
                    font = block.font
                    color = _excel_color(font.color) if font is not None and font.color is not None else cell_color
                    bold = bool(font.b) if font is not None and font.b is not None else cell_bold
                    parts.append(span(block.text, color, bold))
            return ''.join(parts)
        return span(str(value), cell_color, cell_bold)

    def _excel_table_html(ws, header_row, last_col, data_rows):
        """把排班頁籤畫成跟 Excel 一樣的表格（白底、灰表頭、框線、標楷體、每段文字的顏色）。"""
        def cell_tag(tag, cell, extra_style=''):
            fill = None
            if cell.fill is not None and cell.fill.fill_type == 'solid':
                fill = _excel_color(cell.fill.fgColor)
            style = (
                'border:1px solid #000000;padding:3px 8px;text-align:center;vertical-align:middle;'
                'white-space:nowrap;color:#000000;'
            ) + extra_style + (f'background:{fill};' if fill else 'background:#FFFFFF;')
            return f'<{tag} style="{style}">{_excel_cell_html(cell)}</{tag}>'

        rows_html = []
        header_cells = ''.join(
            cell_tag('th', ws.cell(row=header_row, column=col)) for col in range(1, last_col + 1)
        )
        rows_html.append(f'<tr>{header_cells}</tr>')
        for row_idx in data_rows:
            cells = ''.join(cell_tag('td', ws.cell(row=row_idx, column=col)) for col in range(1, last_col + 1))
            rows_html.append(f'<tr>{cells}</tr>')

        title = _excel_cell_html(ws.cell(row=1, column=1))
        return f"""
        <div style="overflow-x:auto;">
          <div style="background:#FFFFFF;color:#000000;padding:12px;border-radius:6px;display:inline-block;min-width:100%;box-sizing:border-box;">
            <div style="text-align:center;font-family:'DFKai-SB','標楷體',serif;font-size:17px;margin-bottom:6px;">{title}</div>
            <table style="border-collapse:collapse;margin:0 auto;font-family:'DFKai-SB','標楷體',serif;font-size:15px;">
              {''.join(rows_html)}
            </table>
          </div>
        </div>
        """

    def read_output_grids(output_bytes):
        """
        把輸出的 Excel 讀回來（每個排班頁籤一張），網頁上看到的就是 Excel 的內容。
        回傳 (頁籤名, 文字表格 DataFrame, 跟 Excel 同樣上色的 HTML 表格)。
        """
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(output_bytes), data_only=True, rich_text=True)
        grids = []
        for ws in wb.worksheets:
            header_row = None
            headers = []
            for row_idx in range(1, 6):
                values = [ws.cell(row=row_idx, column=col).value for col in range(1, ws.max_column + 1)]
                if values and str(values[0] or '').strip() in ('主值', '副值'):
                    header_row = row_idx
                    headers = [str(value or '').strip() for value in values]
                    break
            if not header_row:
                continue
            while headers and not headers[-1]:
                headers.pop()
            duplicated = {h for h in headers if headers.count(h) > 1}
            seen = {}
            columns = []
            for h in headers:
                seen[h] = seen.get(h, 0) + 1
                columns.append(f"{h}{seen[h]}" if h in duplicated else h)
            rows = []
            data_rows = []
            for row_idx in range(header_row + 1, ws.max_row + 1):
                values = [ws.cell(row=row_idx, column=col).value for col in range(1, len(columns) + 1)]
                if not values[0]:
                    continue
                data_rows.append(row_idx)
                rows.append([str(v).replace('\n', ' ') if v is not None else '' for v in values])
            grids.append((
                ws.title.strip(),
                pandas.DataFrame(rows, columns=columns),
                _excel_table_html(ws, header_row, len(columns), data_rows),
            ))
        return grids

    _LOG_SECTIONS = (
        ('大夜班排班流程', '大夜'),
        ('小夜班排班流程', '小夜週'),
        ('假日班排班流程', '假日班'),
    )
    _LOG_PREFIXES = (
        ('跳過 {name}:', '⏭️ 跳過：'),
        ('補班暫不排 {name}:', '⏸️ 補班暫不排：'),
        ('輪序回補: {name}', '🔁 補班：'),
        ('隨輪序補班: {name}', '🔁 補班：'),
        ('補班: {name}', '🔁 補班：'),
        ('排班: {name}', '✅ 排到：'),
        ('正常輪序: {name}', '✅ 排到：'),
    )

    def _friendly_log_line(line, name):
        if line.startswith('['):
            # 還班相關的結構化 log 轉成白話；其他 [除錯] 行不顯示
            tag = line[1:line.find(']')] if ']' in line else ''
            if '還班' not in tag and '補償' not in tag:
                return None
            if tag == '還班未還':
                return '🎫 ' + line.split(']', 1)[1].replace(name, '').strip() + ' → 已寫入備註「還班未還」'
            if tag == '還班尚未生效':
                return '🎫 有還班，但這班還沒到生效日期，照常排班'
            credit = re.search(r'credit=\d{4}-(\d{2})-(\d{2})', line)
            slot = re.search(r'original_slot=([^\s（(]+)', line)
            if not credit:
                return None
            text = f"🎫 用還班（補{int(credit.group(1))}/{int(credit.group(2))}）抵掉"
            return text + (f" {slot.group(1)}" if slot else ' 這次輪值')
        if "['" in line:
            return None
        line = line.lstrip('★ ').strip()
        for prefix, label in _LOG_PREFIXES:
            prefix = prefix.format(name=name)
            if line.startswith(prefix):
                return label + line[len(prefix):].strip(' :：')
        return '・' + line

    def explain_nurse(log_text, name):
        """從排班 log 挑出跟這個人有關的行，依大夜 / 小夜週 / 假日班分組。"""
        sections = {'讀取上月資料': [], '大夜': [], '小夜週': [], '假日班': []}
        section = '讀取上月資料'
        slot = ''
        in_summary = False
        for raw in (log_text or '').splitlines():
            line = raw.strip()
            for marker, label in _LOG_SECTIONS:
                if marker in line:
                    section, slot, in_summary = label, '', False
            if line.startswith('=====') and '排班結果' in line:
                in_summary = True  # 後面是結果預覽表，跟 Excel 重複
                continue
            if line.startswith('▶ 時段'):
                slot = line.replace('▶ 時段', '').strip()
                continue
            if in_summary or name not in line:
                continue
            text = _friendly_log_line(line, name)
            if not text:
                continue
            if section == '假日班' and slot and slot not in text:
                text = f"**{slot}**　{text}"
            if text not in sections[section]:
                sections[section].append(text)
        return sections

    def render_nurse_explanation(sheet_label, row, log_text):
        name = str(row.iloc[0]).lstrip('*').strip()
        with st.container(border=True):
            st.markdown(f"#### 🔍 {name}：為什麼這樣排？")
            st.dataframe(pandas.DataFrame([row]), hide_index=True, width="stretch")
            sections = explain_nurse(log_text, name)
            shown = [(label, items) for label, items in sections.items() if items]
            if not shown:
                st.caption("這個人在本次排班沒有特別紀錄（沒有輪到或沒有被跳過）。")
                return
            cols = st.columns(len(shown))
            for col, (label, items) in zip(cols, shown):
                with col:
                    st.markdown(f"**{label}**")
                    st.markdown('\n'.join(f"- {item}" for item in items))

    def render_last_run():
        run = st.session_state.get('last_run')
        if not run:
            return

        st.subheader("③ 排班結果")
        st.success(f"✅ 排班完成（{run.get('finished_at', '')}）")
        st.download_button(
            label=f"⬇️ 下載 {run['output_filename']}",
            data=run['output_bytes'],
            file_name=run['output_filename'],
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            use_container_width=True
        )

        all_results = run.get('all_results') or {}
        sheet_logs = run.get('sheet_logs') or {}
        grids = read_output_grids(run['output_bytes'])
        sheet_keys = list(all_results.keys())
        if not grids:
            return

        tabs = st.tabs([title for title, _, _ in grids])
        for idx, (tab, (title, grid, table_html)) in enumerate(zip(tabs, grids)):
            sheet_key = sheet_keys[idx] if idx < len(sheet_keys) else None
            with tab:
                if sheet_key and all_results.get(sheet_key):
                    notes = build_quality_notes(all_results[sheet_key])
                    warnings = [msg for level, msg in notes if level in ('error', 'warning')]
                    infos = [msg for level, msg in notes if level == 'info']
                    for msg in warnings:
                        st.warning(msg)
                    if infos:
                        st.caption('　'.join(f"ℹ️ {msg}" for msg in infos))
                # 跟下載的 Excel 同樣上色（整張表一次顯示完，不用在表格內捲動）
                st.markdown(table_html, unsafe_allow_html=True)

                names = [str(name) for name in grid.iloc[:, 0]]
                selected = st.selectbox(
                    "🔍 選一位人員，看他這個月為什麼這樣排（跳過原因、補班、還班）",
                    options=names,
                    index=None,
                    placeholder="選擇人員…",
                    key=f"result_explain_{idx}",
                )
                if selected:
                    render_nurse_explanation(title, grid.iloc[names.index(selected)], sheet_logs.get(sheet_key, ''))

        with st.expander("📝 完整排班日誌（進階）", expanded=False):
            log_text = run.get('log_text', '')
            st.text(log_text[-20000:] if log_text else '')

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

    def render_steps(stage):
        labels = ['① 上傳本月值班表', '② 檢查 / 調整', '③ 排班並下載']
        parts = []
        for idx, label in enumerate(labels, 1):
            if idx < stage:
                parts.append(f"✅ {label}")
            elif idx == stage:
                parts.append(f"👉 **{label}**")
            else:
                parts.append(f"⚪ {label}")
        st.markdown('　→　'.join(parts))

    def render_health_check(sheet_names, pre_schedule_checks, year, holidays_missing):
        """一張表看完每個頁籤的狀態，下面只列出「需要你處理」的事。"""
        rows = []
        todo = []
        if holidays_missing:
            todo.append(('error', f"尚未設定 {year} 年國定假日 → 請先在左側「國定假日設定」新增，否則國定假日不會排假日班。"))

        approved = st.session_state.approved_return_credits
        for sheet_name in sheet_names:
            check = pre_schedule_checks.get(sheet_name)
            if not check:
                todo.append(('error', f"{sheet_name.strip()}：讀取失敗，請確認 Excel 格式。"))
                continue
            nurses = check.get('nurses') or []
            label = sheet_name.strip()
            anchors = get_manual_anchor_overrides(sheet_name)
            pending = sum(
                len(getattr(nurse, attr, []))
                for nurse in nurses
                for attr in ('pending_night_makeup', 'pending_small_night_makeup', 'pending_holiday_makeup')
            )
            candidates = get_return_credit_candidates(sheet_name, check)
            waiting = [c for c in candidates if c.credit_id not in approved]
            approved_count = sum(1 for c in approved.values() if c.sheet_name == sheet_name)

            rows.append({
                '頁籤': label,
                '人數': len(nurses),
                '大夜從': anchors.get('night') or check.get('next_night') or '名單第一位',
                '小夜週從': anchors.get('small_night') or check.get('next_small_night') or '名單第一位',
                '假日班從': anchors.get('holiday') or check.get('next_holiday') or '名單第一位',
                '待補班': f"{pending} 筆" if pending else '-',
                '還班': (f"{approved_count} 已核准" if approved_count else '-') + (f"／{len(waiting)} 待核准" if waiting else ''),
            })

            if waiting:
                names = '、'.join(sorted({c.nurse_name for c in waiting}))
                todo.append(('warning', f"{label}：{names} 有上月還班尚未核准 → 到「🎫 還班」分頁勾選，不核准就不會套用。"))
            missing_previous = [n.name for n in nurses if n.name not in (check.get('previous_entries') or {})]
            if missing_previous:
                todo.append(('warning', f"{label}：{'、'.join(missing_previous)} 沒有讀到大夜 / 小夜週等欄位資料，請確認 Excel。"))
            for key, shift_label, anchor_key in (
                ('last_night', '大夜', 'night'),
                ('last_small_night', '小夜週', 'small_night'),
                ('last_holiday', '假日班', 'holiday'),
            ):
                if not check.get(key) and not anchors.get(anchor_key):
                    todo.append(('warning', f"{label}：找不到上月{shift_label}最後一位，會從名單第一位開始 → 可到「🔁 補班與起頭」指定。"))

        st.dataframe(pandas.DataFrame(rows), hide_index=True, width="stretch", height="content")
        st.caption("「大夜從 / 小夜週從 / 假日班從」＝本月常規輪序的第一位；「待補班」已自動排入補班清單。")
        if todo:
            st.markdown("**需要你處理：**")
            for level, msg in todo:
                (st.error if level == 'error' else st.warning)(msg)
        else:
            st.success("沒有需要處理的項目，可以直接按「🚀 開始排班」。")

    def run_schedule(tmp_path, sheet_names, pre_schedule_checks, year, month, roc_year, include_pre_check_sheet):
        import copy

        year_str = str(year)
        config.HOLIDAYS_2026 = st.session_state.all_holidays.get(year_str, {})

        progress_bar = st.progress(0)
        status_text = st.empty()
        all_results = {}
        sheet_logs = {}
        total_sheets = len(sheet_names)

        for idx, sheet_name in enumerate(sheet_names):
            status_text.text(f"正在處理: {sheet_name} ({idx + 1}/{total_sheets})")
            progress_bar.progress((idx + 1) / (total_sheets + 1))
            pre_schedule_check = pre_schedule_checks.get(sheet_name) or build_pre_schedule_check(
                tmp_path, sheet_name, year, month,
            )
            manual_anchor_overrides = get_manual_anchor_overrides(sheet_name)
            manual_past_makeup_entries = get_manual_past_makeup_entries(sheet_name)
            manual_shift_entries = get_manual_shift_entries(sheet_name)
            priority_makeup_entries = get_priority_makeup_entries(sheet_name)
            extra_makeup_entries = (
                get_extra_makeup_entries(sheet_name)
                + manual_makeup_entries_to_extra(manual_past_makeup_entries)
            )
            manual_makeup_note = st.session_state.get(_makeup_note_key(sheet_name), '').strip()
            pre_schedule_check['manual_makeup_note'] = manual_makeup_note
            # 排班會把用掉的還班標成 consumed；傳副本進去，重按「開始排班」時已核准的還班才不會失效。
            approved_credits = copy.deepcopy(list(st.session_state.approved_return_credits.values()))

            sheet_output = io.StringIO()
            with redirect_stdout(sheet_output):
                try:
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
                        tmp_path,
                        sheet_name,
                        year,
                        month,
                        regular_start_night_name=manual_anchor_overrides.get('night'),
                        regular_start_small_night_name=manual_anchor_overrides.get('small_night'),
                        regular_start_holiday_name=manual_anchor_overrides.get('holiday'),
                        manual_shift_entries=manual_shift_entries,
                        priority_makeup_entries=priority_makeup_entries,
                        extra_makeup_entries=extra_makeup_entries,
                        manual_makeup_note=manual_makeup_note,
                        approved_return_credits=approved_credits,
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
                        'pre_schedule_check': pre_schedule_check if include_pre_check_sheet else None,
                        'manual_anchor_overrides': manual_anchor_overrides,
                        'manual_shift_entries': manual_shift_entries,
                        'priority_makeup_entries': priority_makeup_entries,
                        'extra_makeup_entries': extra_makeup_entries,
                        'manual_makeup_note': manual_makeup_note,
                    }
                except Exception as e:
                    import traceback
                    st.error(f"處理頁籤 {sheet_name} 時發生錯誤: {e}")
                    st.code(traceback.format_exc())
            sheet_logs[sheet_name] = sheet_output.getvalue()

        # 產生輸出檔案前做稽核；有問題就停止輸出。
        from schedule_validation import validate_schedule_results
        validation_errors = validate_schedule_results(all_results, year, month)
        if validation_errors:
            status_text.text("⛔ 排班稽核未通過，已停止輸出")
            st.error("發現排班衝突：\n\n" + "\n\n".join(f"- {item}" for item in validation_errors))
            return

        status_text.text("正在產生排班結果...")
        progress_bar.progress(0.9)
        output_filename = f"排班結果_{roc_year}年{month}月.xlsx"
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as output_tmp:
            output_path = output_tmp.name
        title = f"臺北榮民總醫院護理部思源手術室{roc_year}年{month}月值班表"
        create_schedule_excel_multi_sheet(output_path, title, all_results, year, month)
        with open(output_path, 'rb') as f:
            output_bytes = f.read()
        try:
            os.unlink(output_path)
        except OSError:
            pass

        progress_bar.empty()
        status_text.empty()
        st.session_state.last_run = {
            'all_results': all_results,
            'output_bytes': output_bytes,
            'output_filename': output_filename,
            'log_text': '\n'.join(sheet_logs.values()),
            'sheet_logs': sheet_logs,
            'month': month,
            'finished_at': datetime.now().strftime('%H:%M:%S'),
        }

    steps_placeholder = st.empty()

    st.subheader("① 上傳本月值班表")
    with st.expander("第一次使用？上傳前請確認這 4 點", expanded=False):
        st.markdown(
            "1. 需要補班的人，在要補的那一格（大夜、小夜週或假日欄）填「補」即可，不用寫日期。\n"
            "2. 新增人員接在名單最下面；刪除人員請刪整列，不要留空白列。\n"
            "3. 下個月的大P / 小P / 換心 / 待命、公休、婚假都要先填好，系統靠這些判斷跳過或改補。\n"
            "4. 左側「國定假日設定」要有排班那一年的假日。"
        )
    uploaded_file = st.file_uploader(
        "選擇 Excel 檔案（系統會排出「下個月」的班）",
        type=['xlsx', 'xls'],
    )

    stage = 1
    if uploaded_file is not None:
        # 檔案簽章：同一份上傳只寫一次暫存檔、只建立一次排班前檢查；
        # 之後點任何設定造成的整頁重跑都直接沿用，不重新解析 Excel。
        file_signature = (uploaded_file.name, uploaded_file.size, getattr(uploaded_file, 'file_id', None))

        if st.session_state.get('_uploaded_file_signature') != file_signature:
            # 先寫好新檔、更新 session_state，最後才刪舊檔，避免重跑中途被打斷時指向已刪除的檔案。
            old_tmp_path = st.session_state.get('_uploaded_tmp_path')
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name

            st.session_state['_uploaded_file_signature'] = file_signature
            st.session_state['_uploaded_tmp_path'] = tmp_path
            st.session_state['_pre_schedule_checks'] = None
            st.session_state['_pre_schedule_checks_key'] = None
            # 換了一份新檔案：上一份的還班核准、候選、排班結果都不沿用。
            st.session_state.approved_return_credits = {}
            st.session_state.detected_return_credit_candidates = {}
            st.session_state.pop('last_run', None)

            if old_tmp_path and old_tmp_path != tmp_path and os.path.exists(old_tmp_path):
                try:
                    os.remove(old_tmp_path)
                except OSError:
                    pass
        else:
            tmp_path = st.session_state['_uploaded_tmp_path']
            if not os.path.exists(tmp_path):
                with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp_file:
                    tmp_file.write(uploaded_file.getvalue())
                    tmp_path = tmp_file.name
                st.session_state['_uploaded_tmp_path'] = tmp_path
                st.session_state['_pre_schedule_checks'] = None
                st.session_state['_pre_schedule_checks_key'] = None

        try:
            roc_year, input_month = parse_title_year_month(tmp_path)

            if roc_year is None or input_month is None:
                st.error("❌ 無法從 Excel 標題解析年份和月份，請確認標題格式為：XXX年X月值班表")
            else:
                # 上傳本月值班表，系統產生下個月排班表
                if input_month == 12:
                    month = 1
                    roc_year += 1
                else:
                    month = input_month + 1
                year = roc_to_western_year(roc_year)

                holidays_for_year = st.session_state.all_holidays.get(str(year))
                config.HOLIDAYS_2026 = holidays_for_year or {}
                config.HOLIDAYS_BY_YEAR = st.session_state.all_holidays
                month_holidays = sorted(
                    ((k, v) for k, v in (holidays_for_year or {}).items() if int(k.split('/')[0]) == month),
                    key=lambda x: int(x[0].split('/')[1]),
                )
                holiday_text = '、'.join(f"{k} {v}" for k, v in month_holidays) or '無'
                if holidays_for_year is None:
                    holiday_text = '⚠️ 未設定'

                sheet_names = get_sheet_names(tmp_path)

                pre_schedule_checks_key = (tmp_path, year, month, tuple(sheet_names))
                if (
                    st.session_state.get('_pre_schedule_checks') is not None
                    and st.session_state.get('_pre_schedule_checks_key') == pre_schedule_checks_key
                ):
                    pre_schedule_checks = st.session_state['_pre_schedule_checks']
                else:
                    pre_schedule_checks = {}
                    with st.spinner("正在讀取值班表..."):
                        for sheet_name in sheet_names:
                            try:
                                pre_schedule_checks[sheet_name] = build_pre_schedule_check(
                                    tmp_path, sheet_name, year, month,
                                )
                            except Exception as e:
                                st.error(f"讀取 {sheet_name} 時發生錯誤: {e}")
                    st.session_state['_pre_schedule_checks'] = pre_schedule_checks
                    st.session_state['_pre_schedule_checks_key'] = pre_schedule_checks_key

                stage = 3 if st.session_state.get('last_run') else 2
                st.subheader(f"② 檢查 / 調整：排 {roc_year} 年 {month} 月")
                st.markdown(f"🎌 本月國定假日：{holiday_text}")

                check_tab, makeup_tab, credit_tab = st.tabs(["✅ 檢查摘要", "🔁 補班與起頭", "🎫 還班"])
                with check_tab:
                    render_health_check(sheet_names, pre_schedule_checks, year, holidays_for_year is None)
                with makeup_tab:
                    if pre_schedule_checks:
                        render_pre_schedule_checks(pre_schedule_checks)
                with credit_tab:
                    if pre_schedule_checks:
                        render_return_credit_section(sheet_names, pre_schedule_checks)

                run_col, option_col = st.columns([3, 1])
                with option_col:
                    include_pre_check_sheet = st.checkbox(
                        "Excel 附「排班前核對」頁", value=False, key="include_pre_check_sheet"
                    )
                with run_col:
                    clicked = st.button(
                        "🔄 重新排班" if st.session_state.get('last_run') else "🚀 開始排班",
                        type="primary",
                        use_container_width=True,
                    )
                if clicked:
                    st.session_state.pop('last_run', None)
                    run_schedule(tmp_path, sheet_names, pre_schedule_checks, year, month, roc_year, include_pre_check_sheet)
                    if st.session_state.get('last_run'):
                        st.rerun()  # 重畫一次，讓步驟列、按鈕文字和結果一致

        except Exception as e:
            st.error(f"❌ 處理檔案時發生錯誤: {e}")
            import traceback
            st.code(traceback.format_exc())

        if st.session_state.get('last_run'):
            st.markdown("---")
            render_last_run()

    with steps_placeholder.container():
        render_steps(stage)
