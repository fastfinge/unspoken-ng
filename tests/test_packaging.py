import ctypes
from ctypes import wintypes
from pathlib import Path
import struct
import sys

import pytest


DLL_PATH = (
    Path(__file__).resolve().parents[1]
    / "addon"
    / "globalPlugins"
    / "Unspoken"
    / "soft_oal.dll"
)
IMAGE_FILE_MACHINE_AMD64 = 0x8664
VS_FIXEDFILEINFO_SIGNATURE = 0xFEEF04BD


class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD),
        ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD),
        ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD),
        ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileFlagsMask", wintypes.DWORD),
        ("dwFileFlags", wintypes.DWORD),
        ("dwFileOS", wintypes.DWORD),
        ("dwFileType", wintypes.DWORD),
        ("dwFileSubtype", wintypes.DWORD),
        ("dwFileDateMS", wintypes.DWORD),
        ("dwFileDateLS", wintypes.DWORD),
    ]


def _coff_machine(path):
    with path.open("rb") as dll:
        if dll.read(2) != b"MZ":
            raise AssertionError(f"{path} does not have a DOS MZ header")
        dll.seek(0x3C)
        pe_offset = struct.unpack("<I", dll.read(4))[0]
        dll.seek(pe_offset)
        if dll.read(4) != b"PE\0\0":
            raise AssertionError(f"{path} does not have a PE signature")
        return struct.unpack("<H", dll.read(2))[0]


def _file_version(path):
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ctypes.WinError()

    info = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, info):
        raise ctypes.WinError()

    fixed_info_address = ctypes.c_void_p()
    fixed_info_size = wintypes.UINT()
    if not version.VerQueryValueW(
        info,
        "\\",
        ctypes.byref(fixed_info_address),
        ctypes.byref(fixed_info_size),
    ):
        raise ctypes.WinError()
    if fixed_info_size.value < ctypes.sizeof(VS_FIXEDFILEINFO):
        raise AssertionError("VS_VERSION_INFO does not contain VS_FIXEDFILEINFO")

    fixed_info = ctypes.cast(
        fixed_info_address,
        ctypes.POINTER(VS_FIXEDFILEINFO),
    ).contents
    if fixed_info.dwSignature != VS_FIXEDFILEINFO_SIGNATURE:
        raise AssertionError("VS_FIXEDFILEINFO has an invalid signature")

    return (
        fixed_info.dwFileVersionMS >> 16,
        fixed_info.dwFileVersionMS & 0xFFFF,
        fixed_info.dwFileVersionLS >> 16,
        fixed_info.dwFileVersionLS & 0xFFFF,
    )


def test_soft_oal_is_x64():
    assert _coff_machine(DLL_PATH) == IMAGE_FILE_MACHINE_AMD64


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows version.dll")
def test_soft_oal_version():
    # The fixed-info resource stores the displayed 1.25.1 version as 1.25.1.0.
    assert _file_version(DLL_PATH) == (1, 25, 1, 0)
