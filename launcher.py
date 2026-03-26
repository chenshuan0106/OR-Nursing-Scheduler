"""
BOR 護理排班系統 - 啟動器
雙擊此 exe 即可啟動網頁服務
"""
import subprocess
import sys
import os
import webbrowser
import time
import socket

def find_free_port():
    """找一個可用的 port"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port

def main():
    # 取得腳本所在目錄
    if getattr(sys, 'frozen', False):
        # 如果是打包後的 exe
        app_dir = os.path.dirname(sys.executable)
    else:
        # 如果是直接執行 Python
        app_dir = os.path.dirname(os.path.abspath(__file__))

    # 切換到應用程式目錄
    os.chdir(app_dir)

    # Streamlit app 路徑
    streamlit_app = os.path.join(app_dir, 'streamlit_app.py')

    if not os.path.exists(streamlit_app):
        print(f"錯誤: 找不到 {streamlit_app}")
        print("請確認 streamlit_app.py 與此程式在同一目錄")
        input("按 Enter 結束...")
        return

    # 使用固定 port 8501（本機專用）
    port = 8501
    url = f"http://localhost:{port}"

    print("=" * 50)
    print("  BOR 護理排班系統")
    print("  臺北榮民總醫院護理部思源手術室")
    print("=" * 50)
    print()
    print(f"正在啟動網頁服務...")
    print(f"網址: {url}")
    print()
    print("【重要】關閉此視窗即可停止服務")
    print("=" * 50)

    # 啟動 Streamlit（只綁定 localhost，不對外開放）
    cmd = [
        sys.executable, '-m', 'streamlit', 'run',
        streamlit_app,
        '--server.port', str(port),
        '--server.address', 'localhost',  # 只綁定本機
        '--server.headless', 'true',
        '--browser.gatherUsageStats', 'false',
    ]

    # 啟動程序
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    # 等待服務啟動
    time.sleep(2)

    # 自動開啟瀏覽器
    webbrowser.open(url)

    # 持續顯示輸出，直到程序結束
    try:
        for line in process.stdout:
            print(line, end='')
    except KeyboardInterrupt:
        print("\n正在停止服務...")
        process.terminate()

    process.wait()
    print("\n服務已停止")

if __name__ == '__main__':
    main()
