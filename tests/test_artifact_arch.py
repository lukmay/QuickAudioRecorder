"""Architecture validation rejects mislabeled or malformed artifacts."""
import importlib.util
import struct
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "check_pe_arch", Path(__file__).resolve().parents[1] / "scripts/check_pe_arch.py")
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


@pytest.mark.parametrize("architecture,machine", [("x64", 0x8664), ("arm64", 0xAA64)])
def test_actual_machine_type_is_checked(tmp_path, architecture, machine):
    data = bytearray(134)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<H", data, 132, machine)
    path = tmp_path / "misleading-name.exe"
    path.write_bytes(data)
    assert check.check_architecture(path, architecture) == machine
    other = "arm64" if architecture == "x64" else "x64"
    with pytest.raises(ValueError, match="expected"):
        check.check_architecture(path, other)


@pytest.mark.parametrize("data", [b"", b"not an exe", b"MZ", b"MZ" + bytes(100)])
def test_invalid_binary_is_rejected(tmp_path, data):
    path = tmp_path / "invalid.pyd"
    path.write_bytes(data)
    with pytest.raises(ValueError):
        check.pe_machine(path)
