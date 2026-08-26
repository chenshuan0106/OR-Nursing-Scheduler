"""產生 tests/fixtures/sample_schedule.xlsx 這份合成測試用 Excel 檔。

內容為完全虛構的姓名與資料，僅用於驗證排班 pipeline 在重構前後行為一致，
不含任何真實病患或員工資料。執行方式：
    python tests/make_fixture.py
"""
import os

from openpyxl import Workbook

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
FIXTURE_PATH = os.path.join(FIXTURE_DIR, 'sample_schedule.xlsx')

HEADERS = ['主值', '公休', '大夜', '小夜週', '假日班', '備註', '補班']

# 輸入檔是「2月」的班表（代表 1月的結果），process_sheet 會排「輸入月+1」＝3月。
ROWS = [
    {'主值': '測試甲', '公休': '2/5-2/6', '大夜': '2/1.2/2.2/3', '小夜週': '', '假日班': '', '備註': '', '補班': ''},
    {'主值': '測試乙', '公休': '', '大夜': '', '小夜週': '2/4-2/6', '假日班': '', '備註': '', '補班': ''},
    {'主值': '測試丙', '公休': '2/10-2/11', '大夜': '', '小夜週': '', '假日班': '', '備註': '3月大P1', '補班': ''},
]


def build_fixture():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = '測試單位'
    ws['A1'] = '臺北榮民總醫院護理部思源手術室115年2月值班表'

    for col_idx, header in enumerate(HEADERS, 1):
        ws.cell(row=2, column=col_idx, value=header)

    for row_offset, row_data in enumerate(ROWS, start=3):
        for col_idx, header in enumerate(HEADERS, 1):
            ws.cell(row=row_offset, column=col_idx, value=row_data.get(header, ''))

    wb.save(FIXTURE_PATH)
    print(f'已建立 {FIXTURE_PATH}')


if __name__ == '__main__':
    build_fixture()
