"""
BOR 護理排班系統 - 完整打包啟動器
此版本包含所有依賴，可在沒有 Python 的電腦上執行
"""
import sys
import os
import webbrowser
import threading
import time

# 設定環境
if getattr(sys, 'frozen', False):
    # 打包後的 exe
    app_dir = os.path.dirname(sys.executable)
    # 設定 Streamlit 的 static 路徑
    os.environ['STREAMLIT_BROWSER_GATHER_USAGE_STATS'] = 'false'
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))

# 切換到應用程式目錄
os.chdir(app_dir)

# 確保可以找到 streamlit_app.py
sys.path.insert(0, app_dir)

def open_browser(port):
    """延遲開啟瀏覽器"""
    time.sleep(3)
    webbrowser.open(f'http://localhost:{port}')

def main():
    print("=" * 50)
    print("  BOR 護理排班系統")
    print("  臺北榮民總醫院護理部思源手術室")
    print("=" * 50)
    print()
    print("正在啟動，請稍候...")
    print()
    print("【重要】關閉此視窗即可停止服務")
    print("=" * 50)

    port = 8501

    # 在背景開啟瀏覽器
    browser_thread = threading.Thread(target=open_browser, args=(port,))
    browser_thread.daemon = True
    browser_thread.start()

    # 使用 Streamlit 的內部 API 啟動
    from streamlit.web import cli as stcli

    # 設定參數
    sys.argv = [
        'streamlit',
        'run',
        os.path.join(app_dir, 'streamlit_app.py'),
        '--server.port', str(port),
        '--server.address', 'localhost',
        '--server.headless', 'true',
        '--browser.gatherUsageStats', 'false',
        '--global.developmentMode', 'false',
    ]

    # 啟動 Streamlit
    stcli.main()

if __name__ == '__main__':
    main()
