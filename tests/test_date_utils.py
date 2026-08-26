"""date_utils.py 的單元測試：涵蓋成功解析案例，以及目前會靜默失敗（回傳 None/[]）
並在 Stage 1 加上 logging.warning 的案例。"""
import logging
from datetime import datetime

import date_utils


def test_parse_date_string_success():
    assert date_utils.parse_date_string('2/5', default_year=2026) == datetime(2026, 2, 5)
    assert date_utils.parse_date_string('02/05', default_year=2026) == datetime(2026, 2, 5)


def test_parse_date_string_blank_input_is_silent():
    assert date_utils.parse_date_string('') is None
    assert date_utils.parse_date_string(None) is None
    assert date_utils.parse_date_string('   ') is None


def test_parse_date_string_malformed_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='date_utils'):
        result = date_utils.parse_date_string('13/45', default_year=2026)
    assert result is None
    assert any('無法解析日期字串' in record.message for record in caplog.records)


def test_parse_date_range_success():
    dates = date_utils.parse_date_range('2/2-2/4', default_year=2026)
    assert dates == [datetime(2026, 2, 2), datetime(2026, 2, 3), datetime(2026, 2, 4)]


def test_parse_date_range_malformed_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='date_utils'):
        result = date_utils.parse_date_range('2/2~2/4', default_year=2026)
    assert result == []
    assert any('無法解析日期範圍' in record.message for record in caplog.records)


def test_parse_dot_separated_dates_success():
    dates = date_utils.parse_dot_separated_dates('2/5.2/6.2/7', default_year=2026)
    assert dates == [datetime(2026, 2, 5), datetime(2026, 2, 6), datetime(2026, 2, 7)]


def test_parse_dot_separated_dates_with_embedded_range():
    dates = date_utils.parse_dot_separated_dates('2/23-2/25', default_year=2026)
    assert dates == [datetime(2026, 2, 23), datetime(2026, 2, 24), datetime(2026, 2, 25)]


def test_parse_dot_separated_dates_embedded_range_does_not_double_warn(caplog):
    # 2/23-2/25 應該只被區間規則處理一次，不該因為整段又出現在 '.'-split 結果裡
    # 而被誤判為「片段無法解析」再告警一次。
    with caplog.at_level(logging.WARNING, logger='date_utils'):
        date_utils.parse_dot_separated_dates('2/23-2/25', default_year=2026)
    assert not any('片段無法解析' in record.message for record in caplog.records)


def test_parse_dot_separated_dates_malformed_part_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='date_utils'):
        dates = date_utils.parse_dot_separated_dates('2/5.13/99', default_year=2026)
    assert dates == [datetime(2026, 2, 5)]
    assert any('片段無法解析' in record.message for record in caplog.records)


def test_get_p_shift_dates_for_month_unknown_type_logs_warning(caplog):
    with caplog.at_level(logging.WARNING, logger='date_utils'):
        result = date_utils.get_p_shift_dates_for_month(2026, 3, '不存在的班別')
    assert result == []
    assert any('未知的班別類型' in record.message for record in caplog.records)


def test_status_annotations_do_not_look_like_dates_and_stay_silent(caplog):
    # 迴歸測試：'8月換心'、'*滿55歲跳大夜' 這類寫在大夜/小夜週欄位的狀態
    # 註記文字，本來就不是日期，不該被記成「無法解析日期」警告。
    non_date_texts = ['8月換心', '8月P1', '7月小P1', '*滿55歲跳大夜']
    with caplog.at_level(logging.WARNING):
        for text in non_date_texts:
            assert date_utils.parse_date_string(text, default_year=2026) is None
            assert date_utils.parse_date_range(text, default_year=2026) == []
            assert date_utils.parse_dot_separated_dates(text, default_year=2026) == []
    assert caplog.records == []


def test_is_holiday_uses_active_holiday_table():
    original = date_utils.config.get_holidays()
    try:
        date_utils.config.set_holidays({'3/1': '測試假日'})
        assert date_utils.is_holiday(datetime(2026, 3, 1)) is True
        assert date_utils.get_holiday_name(datetime(2026, 3, 1)) == '測試假日'
        assert date_utils.is_holiday(datetime(2026, 3, 2)) is False
    finally:
        date_utils.config.set_holidays(original)
