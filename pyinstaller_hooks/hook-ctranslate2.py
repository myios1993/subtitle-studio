"""
Custom PyInstaller hook for ctranslate2.

Collects only the CPU-mode DLLs and data files.
Explicitly excludes CUDA/ROCm/nvidia DLLs to prevent the build from
scanning gigabytes of GPU libraries.
"""

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_data_files
import re

# Patterns for GPU DLLs we don't want to bundle
_GPU_RE = re.compile(
    r"(?i)(cuda|cublas|cudnn|cufft|curand|nvcuda|nvrtc|rocm|hip[a-z]|_rocm_sdk)"
)

_all_binaries = collect_dynamic_libs("ctranslate2")
binaries = [(src, dst) for src, dst in _all_binaries if not _GPU_RE.search(src)]

datas = collect_data_files("ctranslate2")

# Do NOT pull in torch or nvidia as hidden imports
hiddenimports = []
excludedimports = ["torch", "torchaudio", "nvidia"]
