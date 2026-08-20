"""Is GPU transcription actually usable on this machine?

Two independent things must be true: an NVIDIA GPU the driver reports, AND the
CUDA libraries ctranslate2 loads lazily at compute time (cuBLAS 12, cuDNN 9).
A GPU without the libraries is the dangerous combination: ctranslate2's delayed
DLL load can HANG inside the Windows loader instead of raising — the worker
thread parks at 0% CPU forever while heartbeats keep the job looking alive.
Probing the DLLs with a plain ctypes load is deterministic: it loads or it
raises immediately, never hangs.
"""

import ctypes
import functools
import sys


def _cuda_dlls_present() -> bool:
    if sys.platform != "win32":
        return True  # Linux deployments manage their own CUDA setup
    try:
        ctypes.WinDLL("cublas64_12.dll")
    except OSError:
        return False
    for name in ("cudnn64_9.dll", "cudnn_ops64_9.dll", "cudnn_ops_infer64_9.dll"):
        try:
            ctypes.WinDLL(name)
            return True
        except OSError:
            continue
    return False


@functools.cache
def cuda_usable() -> bool:
    """GPU present AND its compute libraries actually loadable."""
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() < 1:
            return False
    except Exception:
        return False
    return _cuda_dlls_present()
