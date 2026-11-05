"""CPU-only OIDN filter; does not import or start Blender."""
import ctypes as C
from pathlib import Path
import sys
import time
import numpy as np


def library_path():
    candidates = [Path(__file__).resolve().parents[2] / 'work/runtime/extracted/Blender/Blender.app/Contents/Resources/lib/libOpenImageDenoise.dylib',
                  Path('/Applications/Blender.app/Contents/Resources/lib/libOpenImageDenoise.dylib'),
                  Path(sys.executable).resolve().parents[3] / 'lib/libOpenImageDenoise.dylib']
    for path in candidates:
        if path.is_file():
            return path
    raise RuntimeError('Bundled OpenImageDenoise library not found')


def denoise_array(pixels):
    """Denoise scene-linear HDR RGB. Preserve alpha and return filter duration."""
    rgb = np.ascontiguousarray(pixels[..., :3], dtype=np.float32)
    if not np.isfinite(rgb).all():
        raise ValueError('Non-finite input lightmap')
    h, w, _ = rgb.shape
    result = np.empty_like(rgb)
    lib = C.CDLL(str(library_path()))
    def bind(name, restype, *argtypes):
        fn = getattr(lib, name); fn.restype = restype; fn.argtypes = list(argtypes)
        return fn
    new_device = bind('oidnNewDevice', C.c_void_p, C.c_int)
    commit_device = bind('oidnCommitDevice', None, C.c_void_p)
    new_filter = bind('oidnNewFilter', C.c_void_p, C.c_void_p, C.c_char_p)
    set_image = bind('oidnSetSharedFilterImage', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_int,
                     C.c_size_t, C.c_size_t, C.c_size_t, C.c_size_t, C.c_size_t)
    set_bool = bind('oidnSetFilterBool', None, C.c_void_p, C.c_char_p, C.c_bool)
    commit_filter = bind('oidnCommitFilter', None, C.c_void_p)
    execute = bind('oidnExecuteFilter', None, C.c_void_p)
    release_filter = bind('oidnReleaseFilter', None, C.c_void_p)
    release_device = bind('oidnReleaseDevice', None, C.c_void_p)
    get_error = bind('oidnGetDeviceError', C.c_int, C.c_void_p, C.POINTER(C.c_char_p))
    def check(device):
        message = C.c_char_p()
        code = get_error(device, C.byref(message))
        if code:
            raise RuntimeError('OIDN: ' + (message.value or b'unknown error').decode())
    t = time.perf_counter()
    device = new_device(1)  # OIDN_DEVICE_TYPE_CPU, never auto-select Metal.
    filt = None
    try:
        check(device)
        if not device:
            raise RuntimeError('OIDN CPU device unavailable')
        commit_device(device); check(device)
        filt = new_filter(device, b'RT'); check(device)
        if not filt:
            raise RuntimeError('OIDN filter unavailable')
        # OIDN_FORMAT_FLOAT3 = 3; default strides mean packed RGB float32.
        set_image(filt, b'color', rgb.ctypes.data, 3, w, h, 0, 0, 0)
        set_image(filt, b'output', result.ctypes.data, 3, w, h, 0, 0, 0)
        set_bool(filt, b'hdr', True)
        commit_filter(filt); check(device)
        execute(filt); check(device)
        if not np.isfinite(result).all():
            raise RuntimeError('Non-finite denoised lightmap')
        output = np.array(pixels, dtype=np.float32, copy=True)
        output[..., :3] = np.maximum(result, 0)
        return output, round(time.perf_counter()-t, 3)
    finally:
        if filt:
            release_filter(filt)
        if device:
            release_device(device)
