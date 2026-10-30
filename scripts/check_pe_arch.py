"""Check a Windows artifact's actual PE machine type, not its filename."""
import argparse
import struct
from pathlib import Path

MACHINES = {"x64": 0x8664, "arm64": 0xAA64}


def pe_machine(path):
    with Path(path).open("rb") as binary:
        if binary.read(2) != b"MZ":
            raise ValueError(f"Not a PE executable: {path}")
        binary.seek(0x3C)
        offset = binary.read(4)
        if len(offset) != 4:
            raise ValueError(f"Truncated DOS header: {path}")
        binary.seek(struct.unpack("<I", offset)[0])
        header = binary.read(6)
        if len(header) != 6 or header[:4] != b"PE\0\0":
            raise ValueError(f"Invalid PE header: {path}")
        return struct.unpack("<H", header[4:])[0]


def check_architecture(path, architecture):
    machine = pe_machine(path)
    if machine != MACHINES[architecture]:
        raise ValueError(f"{path}: machine 0x{machine:04x}, expected {architecture} "
                         f"(0x{MACHINES[architecture]:04x})")
    return machine


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=MACHINES)
    parser.add_argument("paths", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.paths:
        check_architecture(path, args.architecture)
        print(f"{path}: {args.architecture}")


if __name__ == "__main__":
    main()
