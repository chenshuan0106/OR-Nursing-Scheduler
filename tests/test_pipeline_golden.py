"""端到端 golden-snapshot 測試。

用合成的測試 Excel（tests/fixtures/sample_schedule.xlsx，不含任何真實
病患/員工資料）跑過 build_pre_schedule_check 和 process_sheet 的完整流程，
把結果序列化成穩定的 JSON 結構，與存好的 golden snapshot 比對。

用途：每個重構階段完成後執行本測試，snapshot 應該完全不變——
一旦不同，代表該階段的變更影響了實際排班行為（不只是程式結構）。

若要在「刻意改變行為」之後更新 snapshot，執行：
    python tests/test_pipeline_golden.py --update
"""
import dataclasses
import json
import os
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from main import build_pre_schedule_check, process_sheet
from models import NurseInfo

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'sample_schedule.xlsx')
SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), 'fixtures', 'golden_snapshot.json')

SHEET_NAME = '測試單位'
YEAR = 2026
MONTH = 3  # 輸入檔是 2 月的班表，process_sheet 排「輸入月+1」= 3 月


def _to_jsonable(value):
    """把測試結果轉成穩定、與物件記憶體位置無關的 JSON 結構。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, NurseInfo):
        return value.name
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def _serialize_nurse(nurse: NurseInfo):
    return {
        'name': nurse.name,
        'is_exempt': nurse.is_exempt,
        'exempt_reason': nurse.exempt_reason,
        'is_p1': _to_jsonable(nurse.is_p1),
        'is_p2': _to_jsonable(nurse.is_p2),
        'is_transplant': _to_jsonable(nurse.is_transplant),
        'leave_dates': _to_jsonable(nurse.leave_dates),
        'wedding_leave_dates': _to_jsonable(nurse.wedding_leave_dates),
        'night_shift_dates': _to_jsonable(nurse.night_shift_dates),
        'small_night_shift_dates': _to_jsonable(nurse.small_night_shift_dates),
        'pending_night_makeup': _to_jsonable(nurse.pending_night_makeup),
        'pending_small_night_makeup': _to_jsonable(nurse.pending_small_night_makeup),
        'pending_holiday_makeup': _to_jsonable(nurse.pending_holiday_makeup),
        'manual_makeup_shift_blocks': _to_jsonable(nurse.manual_makeup_shift_blocks),
    }


def _run_pipeline():
    check = build_pre_schedule_check(FIXTURE_PATH, SHEET_NAME, YEAR, MONTH)
    (
        nurses,
        night_results,
        small_night_results,
        holiday_results,
        all_extra_makeup_results,
        night_last_normal_name,
        small_night_last_normal_name,
        holiday_last_normal_name,
        night_identity_skipped,
        small_night_identity_skipped,
    ) = process_sheet(FIXTURE_PATH, SHEET_NAME, YEAR, MONTH)

    return {
        'pre_schedule_check': {
            'nurse_names': [n.name for n in check['nurses']],
            'last_night': check['last_night'],
            'last_small_night': check['last_small_night'],
            'last_holiday': check['last_holiday'],
            'next_night': check['next_night'],
            'next_small_night': check['next_small_night'],
            'next_holiday': check['next_holiday'],
        },
        'process_sheet': {
            'nurses': [_serialize_nurse(n) for n in nurses],
            'night_results': [_to_jsonable(r) for r in night_results],
            'small_night_results': [_to_jsonable(r) for r in small_night_results],
            'holiday_results_count': len(holiday_results),
            'holiday_results_sample': [_to_jsonable(r) for r in holiday_results[:5]],
            'all_extra_makeup_results_count': len(all_extra_makeup_results),
            'night_last_normal_name': night_last_normal_name,
            'small_night_last_normal_name': small_night_last_normal_name,
            'holiday_last_normal_name': holiday_last_normal_name,
            'night_identity_skipped': sorted(night_identity_skipped),
            'small_night_identity_skipped': sorted(small_night_identity_skipped),
        },
    }


def test_pipeline_matches_golden_snapshot():
    assert os.path.exists(SNAPSHOT_PATH), (
        f'找不到 golden snapshot：{SNAPSHOT_PATH}。'
        '請先執行 `python tests/test_pipeline_golden.py --update` 建立基準快照。'
    )
    actual = _run_pipeline()
    with open(SNAPSHOT_PATH, 'r', encoding='utf-8') as f:
        expected = json.load(f)
    assert actual == expected, (
        'Pipeline 輸出與 golden snapshot 不一致——代表這次變更影響了實際排班行為，'
        '不只是程式結構。若這是刻意的行為調整，請執行 '
        '`python tests/test_pipeline_golden.py --update` 更新快照。'
    )


if __name__ == '__main__':
    if '--update' in sys.argv:
        result = _run_pipeline()
        os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
        with open(SNAPSHOT_PATH, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, sort_keys=True)
        print(f'已更新 golden snapshot: {SNAPSHOT_PATH}')
    else:
        print('用 pytest 執行本檔案來跑測試；用 --update 來更新 golden snapshot。')
