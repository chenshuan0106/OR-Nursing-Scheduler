"""
BOR 護理排班系統 - 單一執行檔版本
所有程式碼都打包在 exe 內，不需要額外的 .py 檔案
"""
import sys
import os
import webbrowser
import threading
import time
import socket

def get_exe_dir():
    """取得 exe 所在目錄（用於儲存 holidays_config.json）"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.abspath(__file__))

def get_app_dir():
    """取得應用程式目錄（.py 檔案所在位置）"""
    if getattr(sys, 'frozen', False):
        # 打包後：使用 PyInstaller 的臨時目錄
        return sys._MEIPASS
    else:
        return os.path.dirname(os.path.abspath(__file__))

def open_browser(port):
    """延遲開啟瀏覽器"""
    time.sleep(3)
    webbrowser.open(f'http://localhost:{port}')

def find_free_port():
    """找一個可用的 port"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        return s.getsockname()[1]

def is_port_available(port):
    """檢查 localhost port 是否可用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('localhost', port)) != 0

def main():
    exe_dir = get_exe_dir()
    app_dir = get_app_dir()

    # 設定環境變數，讓 streamlit_app.py 知道要把 JSON 存在 exe 旁邊
    os.environ['BOR_CONFIG_DIR'] = exe_dir

    # 確保模組可以被找到
    sys.path.insert(0, app_dir)
    os.chdir(app_dir)

    # 檢查 streamlit_app.py 是否存在
    streamlit_app_path = os.path.join(app_dir, 'streamlit_app.py')

    if not os.path.exists(streamlit_app_path):
        print(f"錯誤: 找不到 {streamlit_app_path}")
        input("按 Enter 結束...")
        return

    print("=" * 50)
    print("  BOR 護理排班系統 v5.1")
    print("  臺北榮民總醫院護理部思源手術室")
    print("=" * 50)
    print()
    print("正在啟動，請稍候...")
    print()
    print("【重要】關閉此視窗即可停止服務")
    print("=" * 50)

    port = 8501 if is_port_available(8501) else find_free_port()

    # 在背景開啟瀏覽器；測試時可用 BOR_NO_BROWSER=1 關閉自動開啟。
    if os.environ.get('BOR_NO_BROWSER') != '1':
        browser_thread = threading.Thread(target=open_browser, args=(port,))
        browser_thread.daemon = True
        browser_thread.start()

    # 使用 Streamlit 的內部 API 啟動
    from streamlit.web import cli as stcli

    # 設定參數
    sys.argv = [
        'streamlit',
        'run',
        streamlit_app_path,
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
