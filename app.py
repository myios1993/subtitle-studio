"""
SubtitleStudio — Desktop entry point.
"""
from __future__ import annotations

# ── 最早期错误捕获（在任何其他代码之前）─────────────────────────────────────
# 使用 ctypes 原生 MessageBox，不依赖任何打包模块
def _fatal(title: str, msg: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(msg), str(title), 0x10)
    except Exception:
        pass

try:
    import argparse
    import logging
    import os
    import socket
    import sys
    import threading
    import time
    import traceback
    from pathlib import Path
except Exception as _e:
    _fatal("SubtitleStudio — 启动失败", f"stdlib 导入失败:\n{_e}")
    raise SystemExit(1)

# ── 路径解析 ──────────────────────────────────────────────────────────────

try:
    if getattr(sys, "frozen", False):
        _EXE_DIR   = Path(sys.executable).resolve().parent
        _BUNDLE_DIR = Path(sys._MEIPASS)          # type: ignore[attr-defined]
        PROJECT_ROOT = _EXE_DIR
        # 让 backend 包可以被 import
        if str(_BUNDLE_DIR) not in sys.path:
            sys.path.insert(0, str(_BUNDLE_DIR))
    else:
        PROJECT_ROOT = Path(__file__).resolve().parent
        _BUNDLE_DIR  = PROJECT_ROOT
        if str(PROJECT_ROOT) not in sys.path:
            sys.path.insert(0, str(PROJECT_ROOT))
except Exception as _e:
    _fatal("SubtitleStudio — 路径解析失败", traceback.format_exc())
    raise SystemExit(1)

# ── 日志初始化 ────────────────────────────────────────────────────────────

try:
    _LOG_DIR = Path(os.environ.get("APPDATA", str(PROJECT_ROOT))) / "SubtitleStudio"
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    _LOG_FILE = _LOG_DIR / "app.log"

    # 若旧 log 无法写入，尝试清空后重建
    try:
        _LOG_FILE.write_text("", encoding="utf-8")
    except PermissionError:
        _LOG_FILE = _LOG_DIR / f"app_{os.getpid()}.log"

    _handlers: list[logging.Handler] = [
        logging.FileHandler(_LOG_FILE, encoding="utf-8"),
    ]
    if sys.stdout is not None:
        _handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=_handlers,
    )
except Exception as _e:
    _fatal("SubtitleStudio — 日志初始化失败", traceback.format_exc())
    raise SystemExit(1)

logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info(f"SubtitleStudio 启动 — 日志文件: {_LOG_FILE}")
logger.info(f"PROJECT_ROOT : {PROJECT_ROOT}")
logger.info(f"BUNDLE_DIR   : {_BUNDLE_DIR}")
logger.info(f"frozen       : {getattr(sys, 'frozen', False)}")
logger.info(f"Python       : {sys.version}")
logger.info(f"CWD          : {os.getcwd()}")

# ── 修复 console=False 时 sys.stdout/stderr 为 None 的问题 ───────────────
# uvicorn 的 DefaultFormatter 在初始化时调用 sys.stderr.isatty()，
# 当 sys.stderr is None（PyInstaller windowed 模式）时会崩溃。
# 将 stdout/stderr 重定向到日志文件，解决这个问题。
try:
    _log_stream = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _log_stream
    if sys.stderr is None:
        sys.stderr = _log_stream
except Exception:
    pass

# ── 端口工具 ──────────────────────────────────────────────────────────────

def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) != 0


def _find_free_port(host: str, start: int = 8765) -> int:
    for p in range(start, start + 20):
        if _port_free(host, p):
            return p
    raise RuntimeError(f"端口 {start}~{start+20} 全部被占用")


# ── 后端线程 ──────────────────────────────────────────────────────────────

def _start_backend(host: str, port: int, debug: bool) -> None:
    try:
        import uvicorn
        uvicorn.run(
            "backend.main:app",
            host=host,
            port=port,
            reload=False,
            log_level="debug" if debug else "info",
            access_log=debug,
        )
    except Exception:
        logger.error(f"后端线程崩溃:\n{traceback.format_exc()}")


def _wait_for_backend(url: str, timeout: float = 30.0) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ── 错误弹窗（备用） ──────────────────────────────────────────────────────

def _show_error(message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(message), "SubtitleStudio 启动失败", 0x10)
    except Exception:
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("SubtitleStudio 启动失败", message)
            root.destroy()
        except Exception:
            pass


# ── 主函数 ────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args, _ = parser.parse_known_args()

    host = args.host
    port = args.port if _port_free(host, args.port) else _find_free_port(host, args.port)
    if port != args.port:
        logger.warning(f"端口 {args.port} 被占用，改用 {port}")

    base_url    = f"http://{host}:{port}"
    health_url  = f"{base_url}/health"

    # 启动后端
    backend_thread = threading.Thread(
        target=_start_backend,
        args=(host, port, args.debug),
        daemon=True,
        name="uvicorn-backend",
    )
    backend_thread.start()

    logger.info(f"等待后端就绪: {health_url}")
    if not _wait_for_backend(health_url, timeout=30.0):
        msg = f"后端未能在 30 秒内启动。\n日志: {_LOG_FILE}"
        logger.error(msg)
        _show_error(msg)
        sys.exit(1)
    logger.info("后端已就绪")

    # 启动 pywebview
    try:
        import webview
        _wv_ver = getattr(webview, "__version__", "unknown")
        logger.info(f"pywebview 版本: {_wv_ver}")
    except ImportError:
        msg = f"pywebview 加载失败:\n{traceback.format_exc()}\n\n日志: {_LOG_FILE}"
        logger.error(msg)
        _show_error(msg)
        sys.exit(1)

    try:
        logger.info(f"创建窗口 → {base_url}")
        webview.create_window(
            title="SubtitleStudio",
            url=base_url,
            width=1400,
            height=900,
            min_size=(900, 600),
            text_select=True,
        )
        logger.info("启动 webview 事件循环 …")
        webview.start(debug=args.debug)
        logger.info("窗口关闭，程序退出。")
    except Exception:
        msg = f"webview.start() 失败:\n{traceback.format_exc()}\n\n日志: {_LOG_FILE}"
        logger.error(msg)
        _show_error(msg)
        sys.exit(1)


if __name__ == "__main__":
    main()
