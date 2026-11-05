# 手術室護理排班系統

手術室（BOR）護理人員的**每月值班自動排班工具**。上傳本月值班表 Excel，系統會依輪值規則自動排出大夜班、小夜週與假日班，處理補班、還班與跨月銜接，最後輸出可直接列印的排班結果 Excel。

以 Python + Streamlit 撰寫，在本機瀏覽器操作，也可打包成單一 .exe 給其他同仁使用。

## 主要功能

- **三類輪值自動排班**（依序）：大夜班 → 小夜週 → 假日班（六白班、六小夜、日大夜、日白班、日小夜）
- **輪序接續**：從上月班表最後一位往下排，並讀取上月預排的跨月班作為定位點
- **跳過與調整規則**
  - 直接跳過：換心（移植班）、公休、P1/P2 衝班、連續上班 7 天
  - 調整班別：哺乳同仁避開大小夜、55 歲以上避開大夜
  - 衝突檢查：小夜週、平日大夜、P 班待命前後保護日、同週末重複
  - 完全免排：懷孕、免輪值、離職、育嬰、安胎等身分
- **補班與還班**
  - 待補班佇列、「X月補」、指定日期補班、跨月補班
  - 大夜／小夜／假日三種額外補班，可調整補班順位
  - 還班（抵掉一次正常假日輪值），未用完會寫入備註留到下月
- **國定假日設定**：可在網頁上依年份新增、修改國定假日
- **排班前檢查與結果驗證**：上傳後先產生檢查摘要，輸出前再稽核連續上班、衝班等問題
- **Excel 輸出**：多頁籤班表、依班別上色、自動調整欄寬以符合 A4 列印；網頁上可預覽結果並查看每位同仁的排班說明

## 安裝與執行

需要 Python 3.10 以上（Windows）。

**最簡單的方式**：雙擊 `run_app.bat`，第一次會自動建立虛擬環境並安裝套件，接著開啟 http://localhost:8501 。

**手動執行**：

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

**命令列模式**（不開網頁）：

```
python main.py <本月值班表.xlsx>
```

**打包成 .exe**（需另外安裝 PyInstaller）：

```
pip install pyinstaller
pyinstaller BOR排班系統.spec
```

產出的執行檔在 `dist/`。

## 使用流程

1. **上傳本月值班表**：Excel 的 A1 標題需含「XXX年X月」（民國年），系統據此判斷排班月份
2. **檢查／調整**：確認檢查摘要，視需要設定補班、輪值起頭與還班
3. **排班並下載**：預覽結果，下載排班結果 Excel

## 專案結構

| 檔案 | 說明 |
|---|---|
| `streamlit_app.py` | 網頁操作介面 |
| `main.py` | 排班主流程（也可從命令列執行） |
| `night_shift.py` | 大夜班排班邏輯 |
| `small_night_shift.py` | 小夜週排班邏輯 |
| `holiday_shift.py` | 假日班排班、補班、還班邏輯 |
| `schedule_validation.py` | 輸出前的排班完整性稽核 |
| `excel_handler.py` | Excel 讀取與輸出格式 |
| `shift_utils.py` / `date_utils.py` | 班別判斷、日期與假日工具 |
| `models.py` | 資料模型（護理人員、還班等） |
| `config.py` / `holidays_config.json` | 班型定義、顏色、國定假日設定 |
| `launcher_standalone.py` / `BOR排班系統.spec` | 打包成單一執行檔用 |
| `run_app.bat` | Windows 一鍵啟動 |

## 資料與個資

值班表與排班結果 Excel 含有人員姓名，**不納入版本控制**（見 `.gitignore`），請另外保存。

## 版本紀錄

見 [CHANGELOG.md](CHANGELOG.md)。
