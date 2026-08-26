"""
護理排班系統 - Streamlit 網頁介面
"""
import streamlit as st
import tempfile
import os
import io
import json
import calendar
import hashlib
from datetime import date, timedelta
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
        pre_check = results.get('pre_schedule_check', {})
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
        sortable_available = False
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
            sortable_available = True
            st.caption("拖曳清單項目可調整補班志願序。")
        except Exception:
            st.caption("拖曳排序元件尚未載入；目前可先用上移 / 下移調整順序。")

        st.markdown("**目前補班志願序**")
        for idx, item in enumerate(list(st.session_state[priority_key])):
            if sortable_available:
                row_col1, row_col2 = st.columns([5, 1])
                with row_col1:
                    st.write(f"{idx + 1}. {item.get('nurse_name')} / {item.get('source') or '待補班'}")
                with row_col2:
                    if st.button("刪除", key=f"priority_makeup_delete_{shift_type}_{sheet_name}_{idx}", use_container_width=True):
                        removed_item = entries.pop(idx)
                        if removed_item.get('source_type') == 'detected':
                            st.session_state[removed_key].append(removed_item.get('id'))
                        st.rerun()
            else:
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
            sortable_available = False
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
                sortable_available = True
                st.caption("拖曳清單項目可調整額外補班優先順序。")
            except Exception:
                st.caption("拖曳排序元件尚未載入；目前可先用上移 / 下移調整順序。")

            st.markdown("**目前額外補班順序**")
            for idx, item in enumerate(list(st.session_state[extra_key])):
                if sortable_available:
                    row_col1, row_col2 = st.columns([5, 1])
                    with row_col1:
                        st.write(f"{idx + 1}. {item.get('nurse_name')}")
                    with row_col2:
                        if st.button("刪除", key=f"extra_makeup_delete_{shift_type}_{sheet_name}_{idx}", use_container_width=True):
                            entries.pop(idx)
                            st.rerun()
                else:
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

    def render_pre_schedule_checks(pre_checks):
        st.header("🔎 排班前檢查")
        st.caption("先看總覽，有需要再進入自填班、補班或跨月資料調整。")

        for sheet_name, check in pre_checks.items():
            nurses = check.get('nurses', [])
            carryover_by_nurse = check.get('carryover_by_nurse', {})
            carryover_records_by_nurse = check.get('carryover_records_by_nurse', {})
            previous_entries = check.get('previous_entries', {})

            with st.expander(f"📄 {sheet_name}", expanded=True):
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
                    with st.expander("標記還班（上月上過國定假日，跳過一次）", expanded=False):
                        key = f"return_skip_{sheet_name}"
                        if key not in st.session_state:
                            st.session_state[key] = []
                        selected_return_skip = st.multiselect(
                            "選擇要標記還班的人員",
                            options=nurse_names,
                            default=st.session_state.get(key, []),
                            key=f"return_skip_select_{sheet_name}",
                        )
                        st.session_state[key] = selected_return_skip
                        st.caption("標記者在本月遇到假日班時可跳過一次（系統會自動消耗標記）。")

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
    
    def render_shift_preview(results, month):
        tab_night, tab_small, tab_holiday = st.tabs(["大夜", "小夜週", "假日班"])

        with tab_night:
            rows = []
            for result in results.get('night_results', []):
                rows.append({
                    "日期": format_night_shift_result(result),
                    "姓名": result['nurse'].name,
                    "類型": (
                        "第二輪"
                        if result.get('is_second_round')
                        else ("補班" if result.get('is_makeup') else "正常")
                    ),
                    "狀態": _status_tags(
                        "下月" if result.get('is_next_month') else '',
                        "第二輪" if result.get('is_second_round') else '',
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

        st.divider()
        st.success("✅ 排班完成！")

        st.header("📥 下載結果")
        with st.container(border=True):
            st.download_button(
                label=f"下載結果 {run['output_filename']}",
                data=run['output_bytes'],
                file_name=run['output_filename'],
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )

            show_logs = st.checkbox("顯示詳細 log", value=False, key="show_logs")
            log_container = st.expander("📝 排班詳細日誌", expanded=show_logs)
            with log_container:
                if show_logs:
                    log_text = run.get('log_text', '')
                    st.text(log_text[-5000:] if log_text else '')
                else:
                    st.caption("勾選「顯示詳細 log」後顯示")

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
    with st.container(border=True):
        st.markdown("**上傳前請先確認**")
        st.info(
            "1. 需要補班的人，請在 Excel 的「補班」欄填「補」或「待補班」即可；不確定日期時不用先寫日期，系統會先找可補的空格。\n\n"
            "2. 上傳後請看「排班前檢查」，確認大夜、小夜週、假日班的常規起頭人員是否正確；需要時可手動指定起頭。\n\n"
            "3. 新增人員可直接接在 Excel 名單最下面一列；刪除人員請刪除整列，避免留下空白資料。\n\n"
            "4. 請先檢查下個月假日班、大P、小P、換心、待命等資訊是否已填好，系統會依這些欄位判斷是否跳過或改補。"
        )
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

                # 檢查對應年份的國定假日設定
                year_str = str(year)
                holidays_for_year = st.session_state.all_holidays.get(year_str)
                month_holidays = {}
                holiday_metric_text = '未設定'
                if holidays_for_year is not None:
                    month_holidays = {
                        k: v for k, v in holidays_for_year.items()
                        if int(k.split('/')[0]) == month
                    }
                    if month_holidays:
                        holiday_metric_text = '、'.join([
                            f"{k}({v})" for k, v in sorted(
                                month_holidays.items(),
                                key=lambda x: int(x[0].split('/')[1])
                            )
                        ])
                    else:
                        holiday_metric_text = '無'

                # 顯示檔案資訊
                with st.container(border=True):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("📅 民國年", f"{roc_year} 年")
                    with col2:
                        st.metric("📆 月份", f"{month} 月")
                    with col3:
                        st.metric("🎌 國定假日", holiday_metric_text)

                    # 取得頁籤
                    sheet_names = get_sheet_names(tmp_path)
                    st.info(f"📑 找到 {len(sheet_names)} 個頁籤: {', '.join(sheet_names)}")

                    if holidays_for_year is None:
                        st.warning(f"⚠️ 尚未設定 {year} 年的國定假日，請在左側新增")

                st.divider()

                @st.cache_data(show_spinner=False)
                def _cached_pre_schedule_check(file_bytes, sheet_name, year, month):
                    # 用檔案內容當快取鍵：同一份檔案在使用者操作其他元件、
                    # 觸發 Streamlit 整頁重跑時，不用每次都重新解析 Excel。
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as cache_file:
                        cache_file.write(file_bytes)
                        cache_tmp_path = cache_file.name
                    try:
                        return build_pre_schedule_check(cache_tmp_path, sheet_name, year, month)
                    finally:
                        try:
                            os.unlink(cache_tmp_path)
                        except Exception:
                            pass

                pre_schedule_checks = {}
                with st.spinner("正在建立排班前檢查..."):
                    for sheet_name in sheet_names:
                        try:
                            pre_schedule_checks[sheet_name] = _cached_pre_schedule_check(
                                uploaded_file.getvalue(),
                                sheet_name,
                                year,
                                month,
                            )
                        except Exception as e:
                            st.error(f"建立 {sheet_name} 排班前檢查時發生錯誤: {e}")

                if pre_schedule_checks:
                    render_pre_schedule_checks(pre_schedule_checks)

                st.divider()

                # 排班按鈕
                if st.button("🚀 開始排班", type="primary", use_container_width=True):

                    # 🔴 關鍵：套用使用者設定的假日清單為目前作用中的假日表
                    year_str = str(year)
                    if year_str in st.session_state.all_holidays:
                        config.set_holidays(st.session_state.all_holidays[year_str])
                    else:
                        config.set_holidays({})
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
                            manual_past_makeup_entries = get_manual_past_makeup_entries(sheet_name)
                            manual_shift_entries = get_manual_shift_entries(sheet_name)
                            priority_makeup_entries = get_priority_makeup_entries(sheet_name)
                            extra_makeup_entries = (
                                get_extra_makeup_entries(sheet_name)
                                + manual_makeup_entries_to_extra(manual_past_makeup_entries)
                            )
                            manual_makeup_note = st.session_state.get(_makeup_note_key(sheet_name), '').strip()
                            pre_schedule_check['manual_makeup_note'] = manual_makeup_note

                            # 捕捉這個頁籤的輸出
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
                                        return_skip_names=st.session_state.get(f"return_skip_{sheet_name}", []),
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
                                        'manual_anchor_overrides': manual_anchor_overrides,
                                        'manual_shift_entries': manual_shift_entries,
                                        'priority_makeup_entries': priority_makeup_entries,
                                        'extra_makeup_entries': extra_makeup_entries,
                                        'manual_makeup_note': manual_makeup_note,
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

                    # 產生輸出檔案前做靜默稽核；只在有問題時顯示並停止輸出。
                    from schedule_validation import validate_schedule_results
                    validation_errors = validate_schedule_results(all_results, year, month)
                    if validation_errors:
                        status_text.text("⛔ 排班稽核未通過，已停止輸出")
                        st.error("發現排班衝突：\n\n" + "\n\n".join(f"- {item}" for item in validation_errors))
                        st.stop()

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

