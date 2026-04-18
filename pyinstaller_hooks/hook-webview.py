"""
Custom PyInstaller hook for pywebview.

The built-in hook (inside pywebview.__pyinstaller) only collects lib/ and js/,
but omits the platforms/ subpackage which contains the actual GUI backends
(winforms.py → WebView2, edgechromium.py, mshtml.py).

This hook supplements the built-in one by explicitly collecting everything.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, collect_dynamic_libs

# Collect all data files: js/, lib/, platforms/ .py files packaged as data
datas = collect_data_files("webview")

# Collect .dll / .pyd from webview/lib/
binaries = collect_dynamic_libs("webview")

# Make sure every platforms/*.py module is bundled as importable Python code
hiddenimports = collect_submodules("webview")

# pythonnet (clr) is required by the winforms backend on Windows
try:
    import clr  # noqa
    hiddenimports += ["clr", "clr_loader"]
except ImportError:
    pass
