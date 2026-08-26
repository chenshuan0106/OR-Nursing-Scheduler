"""shift_utils.py 的單元測試：涵蓋成功解析案例，以及目前會靜默失敗
（回傳 None/[]）並在 Stage 1 加上 logging.warning 的案例。"""
import logging
from datetime import datetime

import shift_utils as su


def test_extract_parenthesized_dates_success():
    dates, cleaned = su.extract_parenthesized_dates('4/2.4/3.4/4 (4/3)', default_year=2026)
    assert dates == [datetime(2026, 4, 3)]
    assert '4/2.4/3.4/4' in cleaned


def test_extract_parenthesized_dates_malformed_token_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='shift_utils'):
        dates, _ = su.extract_parenthesized_dates('4/2 (13/99)', default_year=2026)
    assert dates == []
    assert any('括號內日期無法解析' in record.message for record in caplog.records)


def test_extract_month_keywords_success():
    # 結果依 patterns 清單的順序分組（先找完所有大P1，再找大P2...），
    # 不是依文字中出現的先後順序。
    assert su.extract_month_keywords('1月大P2、2月大P1') == [('2月', '大P1'), ('1月', '大P2')]
    assert su.extract_month_keywords('2月換心') == [('2月', '換心')]


def test_extract_month_keywords_no_match_is_silent():
    assert su.extract_month_keywords('身體不適，暫時請假') == []


def test_extract_date_ranges_success():
    dates = su.extract_date_ranges('2/23-2/25', default_year=2026)
    assert dates == [datetime(2026, 2, 23), datetime(2026, 2, 24), datetime(2026, 2, 25)]


def test_extract_date_ranges_malformed_bound_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='shift_utils'):
        dates = su.extract_date_ranges('13/99-14/1', default_year=2026)
    assert dates == []
    assert any('範圍片段無法解析' in record.message for record in caplog.records)


def test_check_special_flags():
    flags = su.check_special_flags('已滿55歲，另有哺乳中，本月婚假')
    assert flags == {'is_age_55_plus': True, 'is_breastfeeding': True, 'is_wedding': True}
    assert su.check_special_flags('') == {
        'is_age_55_plus': False, 'is_breastfeeding': False, 'is_wedding': False,
    }


def test_extract_pending_makeup_dated():
    records = su.extract_pending_makeup('待補班:2/26-2/28大夜', default_year=2026)
    assert len(records) == 1
    record = records[0]
    assert record['shift_type'] == '大夜'
    assert record['dates'] == [datetime(2026, 2, 26), datetime(2026, 2, 27), datetime(2026, 2, 28)]


def test_extract_pending_makeup_undated_marker():
    records = su.extract_pending_makeup('補', default_year=2026)
    assert len(records) == 1
    assert records[0]['is_undated_makeup'] is True


def test_parse_nurse_info_from_row_basic():
    row_data = {
        '姓名': '王小明',
        '公休': '2/5-2/6',
        '備註': '',
        '補班': '',
        '大夜': '',
        '小夜': '',
        '_row_index': 2,
    }
    nurse = su.parse_nurse_info_from_row(row_data, row_data['_row_index'], year=2026)
    assert nurse is not None
    assert nurse.name == '王小明'
    assert nurse.leave_dates == [datetime(2026, 2, 5), datetime(2026, 2, 6)]


def test_parse_nurse_info_from_row_manual_makeup_shift_blocks_does_not_crash():
    # 回歸測試：'N月補@M/D班別' 這種指定日期的補班格式，過去會因為
    # NurseInfo 缺少 manual_makeup_shift_blocks 欄位而在這裡拋出 AttributeError。
    row_data = {
        '姓名': '陳小華',
        '公休': '',
        '備註': '6月補@6/14白班',
        '補班': '',
        '大夜': '',
        '小夜': '',
        '_row_index': 3,
    }
    nurse = su.parse_nurse_info_from_row(row_data, row_data['_row_index'], year=2026)
    assert nurse is not None
    assert len(nurse.manual_makeup_shift_blocks) == 1
    assert nurse.manual_makeup_shift_blocks[0]['shift_type'] == '白班'


def test_parse_nurse_info_from_row_blank_name_after_strip_logs_warning(caplog):
    row_data = {
        '姓名': '*',
        '_row_index': 5,
    }
    with caplog.at_level(logging.WARNING, logger='shift_utils'):
        nurse = su.parse_nurse_info_from_row(row_data, row_data['_row_index'], year=2026)
    assert nurse is None
    assert any('姓名欄位去除符號後為空' in record.message for record in caplog.records)
