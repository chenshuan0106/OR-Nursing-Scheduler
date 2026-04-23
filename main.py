from holiday_shift import (
    schedule_holiday_shifts,
    build_holiday_seed_state,
    print_holiday_shift_preview,
    format_holiday_shift_result,
)
from excel_handler import (
    read_cross_month_makeup_holidays_from_excel,
    find_last_assigned_holiday_from_excel,
)

def _attach_cross_month_holiday_records(nurses, cross_month_records):
    for nurse in nurses:
        setattr(nurse, 'previous_month_cross_month_makeup_holidays', [])
        setattr(nurse, 'pending_holiday_makeup', [])

        for rec in cross_month_records.get(nurse.name, []):
            if rec['type'] == 'fixed':
                nurse.previous_month_cross_month_makeup_holidays.append(rec)
            else:
                nurse.pending_holiday_makeup.append(rec)


def process_sheet(
    filepath: str,
    sheet_name: str,
    year: int,
    month: int,
    last_assigned_night_name: str = None,
    last_assigned_small_night_name: str = None,
    last_assigned_holiday_name: str = None,
):
    # ... 你原本的讀護士 / 大夜 / 小夜流程維持 ...
    nurses_data = read_nurses_from_excel(filepath, sheet_name)

    nurses = []
    for row_data in nurses_data:
        nurse = parse_nurse_info_from_row(row_data, row_data.get('_row_index', 0), year)
        if nurse:
            nurses.append(nurse)

    # 先讀跨月假日資料
    cross_month_records = read_cross_month_makeup_holidays_from_excel(
        filepath,
        sheet_name,
        target_month=month,
        target_year=year,
    )
    _attach_cross_month_holiday_records(nurses, cross_month_records)

    # ... 大夜 / 小夜流程照舊 ...

    # 假日定位點
    if not last_assigned_holiday_name:
        last_assigned_holiday_name = find_last_assigned_holiday_from_excel(
            filepath,
            sheet_name,
            month,
            year,
        )

    last_holiday_index = -1
    if last_assigned_holiday_name:
        for idx, nurse in enumerate(nurses):
            if nurse.name == last_assigned_holiday_name:
                last_holiday_index = idx
                break

    # 先建 seed state，再排假日班
    holiday_seed_state = build_holiday_seed_state(
        nurses=nurses,
        target_year=year,
        target_month=month,
    )

    holiday_results, holiday_last_normal_name = schedule_holiday_shifts(
        nurses,
        year,
        month,
        last_holiday_index,
        seed_state=holiday_seed_state,
    )

    print_holiday_shift_preview(holiday_results, month, sheet_name)

    return (
        nurses,
        night_results,
        small_night_results,
        holiday_results,
        night_last_normal_name,
        small_night_last_normal_name,
        holiday_last_normal_name,
        night_identity_skipped,
        small_night_identity_skipped,
    )
