# Copyright (c) 2026 UltraSemi Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""Read the build artifacts which define an AirBurn image's layout."""

import pickle
import re
import sys
from pathlib import Path

from elftools.elf.elffile import ELFFile


def read_flash_layout(edt):
    # An alias disambiguates boards with multiple SPI NOR devices. A disabled
    # controller is allowed: the bootloader can access flash without a Zephyr driver.
    flash = next((n for n in edt.nodes if "airburn-flash" in n.aliases), None)
    if flash is None:
        candidates = [n for n in edt.nodes if "jedec,spi-nor" in n.compats
                      and any("fixed-partitions" in c.compats for c in n.children.values())]
        if len(candidates) != 1:
            raise ValueError("define one SPI NOR partition table or the airburn-flash alias")
        flash = candidates[0]
    if "artinchip,airburn-nand-map" in flash.compats:
        raw = flash.props["flash"].val
        if "xtx,xt26g01c" not in raw.compats or raw.props["jedec-id"].val != bytes([11, 17]):
            raise ValueError("D213 NAND packaging requires XT26G01C ID 0b 11")
        capacity = flash.props["size-bytes"].val
        alignment = flash.props["erase-block-size"].val
        if (capacity != raw.props["size-bytes"].val or alignment != 131072 or
                raw.props["write-block-size"].val != 2048):
            raise ValueError("NAND mapping geometry differs from XT26G01C")
    elif "jedec,spi-nor" not in flash.compats:
        raise ValueError("airburn-flash must reference a jedec,spi-nor device")
    else:
        bits = flash.props.get("size")
        if bits is None or bits.val <= 0 or bits.val % 8:
            raise ValueError("SPI NOR size must specify a positive capacity in bits")
        capacity = bits.val // 8
        alignment = 2048
    tables = [n for n in flash.children.values() if "fixed-partitions" in n.compats]
    if len(tables) != 1:
        raise ValueError("AirBurn flash must have one fixed-partitions node")
    partitions = {}
    end = 0
    nodes = [n for n in tables[0].children.values() if n.status == "okay"]
    for node in nodes:
        if len(node.regs) != 1 or "label" not in node.props:
            raise ValueError(f"{node.path}: expected label and one reg entry")
    for node in sorted(nodes, key=lambda n: n.regs[0].addr):
        name = node.props["label"].val
        offset, size = node.regs[0].addr, node.regs[0].size
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in partitions:
            raise ValueError(f"invalid or duplicate partition label: {name}")
        if offset < end or size <= 0 or offset + size > capacity:
            raise ValueError(f"partition {name} overlaps or exceeds flash capacity")
        if offset % alignment or size % alignment:
            raise ValueError(f"partition {name} must be aligned to {alignment}-byte blocks")
        partitions[name] = {"offset": hex(offset), "size": hex(size)}
        end = offset + size
    missing = {"spl", "env", "env_r"} - partitions.keys()
    if missing:
        raise ValueError(f"missing required AirBurn partitions: {', '.join(sorted(missing))}")
    return {"size": hex(capacity), "partitions": partitions}


def load_build_edt(build_dir: Path, zephyr_base: Path | None = None):
    if zephyr_base is None:
        cache = (build_dir / "CMakeCache.txt").read_text()
        match = re.search(r"^ZEPHYR_BASE:[^=]+=(.+)$", cache, re.MULTILINE)
        if not match:
            raise ValueError("cannot locate ZEPHYR_BASE; pass --zephyr-base")
        zephyr_base = Path(match[1])
    sys.path.insert(0, str(zephyr_base / "scripts/dts/python-devicetree/src"))
    # edt.pickle is a trusted local Zephyr build artifact, not an external input.
    with (build_dir / "zephyr/edt.pickle").open("rb") as source:
        return pickle.load(source)


def load_flash_layout(build_dir: Path, zephyr_base: Path | None = None):
    return read_flash_layout(load_build_edt(build_dir, zephyr_base))


def read_firmware_addresses(elf_path: Path, bin_path: Path):
    binary = bin_path.read_bytes()
    with elf_path.open("rb") as source:
        elf = ELFFile(source)
        if elf.header["e_machine"] != "EM_RISCV":
            raise ValueError("AirBurn profiles require a RISC-V ELF")
        segments = [s for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]
        sections = []
        executable = []
        for section in elf.iter_sections():
            if not section["sh_flags"] & 2 or section["sh_type"] == "SHT_NOBITS" or not section["sh_size"]:
                continue
            matches = [s for s in segments if s.section_in_segment(section)]
            if len(matches) != 1:
                raise ValueError(f"cannot determine load address for ELF section {section.name}")
            segment = matches[0]
            address = segment["p_paddr"] + section["sh_addr"] - segment["p_vaddr"]
            sections.append((address, section.data()))
            if section["sh_flags"] & 4:
                executable.append((address, address + section["sh_size"]))
        if not sections:
            raise ValueError("ELF has no loadable data")
        load = min(address for address, _ in sections)
        end = max(address + len(data) for address, data in sections)
        entry = int(elf.header["e_entry"])
        if end - load != len(binary) or any(
            binary[address - load:address - load + len(data)] != data for address, data in sections
        ):
            raise ValueError("zephyr.bin does not match the ELF load layout")
        if not any(start <= entry < stop for start, stop in executable):
            raise ValueError("ELF entry is not in executable load data; this layout needs a different FIT mapping")
        if not 0 <= load <= entry < 2**32 or end > 2**32:
            raise ValueError("firmware addresses do not fit the 32-bit FIT profile")
        return load, entry


def read_build_identity(build_dir: Path, requested_board: str | None = None):
    """Use configured board and generated version headers, never a default board."""
    cache = (build_dir / "CMakeCache.txt").read_text()
    match = re.search(r"^BOARD:[^=]+=(.+)$", cache, re.MULTILINE)
    if not match:
        raise ValueError("missing BOARD in CMakeCache.txt")
    target = match.group(1).strip()
    board = target.split("/")[0].split("@")[0]
    if requested_board is not None and requested_board not in (board, target):
        raise ValueError(f"requested board {requested_board!r} does not match build {target!r}")
    headers = build_dir / "zephyr/include/generated/zephyr"
    app_header = headers / "app_version.h"
    header = app_header if app_header.is_file() else headers / "version.h"
    prefix = "APP" if app_header.is_file() else "KERNEL"
    contents = header.read_text()
    version_match = re.search(rf'^#define\s+{prefix}_VERSION_STRING\s+"([^"\n]+)"',
                              contents, re.MULTILINE)
    if not version_match:
        raise ValueError(f"missing {prefix}_VERSION_STRING in {header}")
    version = version_match.group(1)
    tweak = re.search(rf'^#define\s+{prefix}_TWEAK\s+(\d+)', contents, re.MULTILINE)
    if tweak and int(tweak.group(1)):
        version += "+" + str(int(tweak.group(1)))
    if not re.fullmatch(r"[A-Za-z0-9_-]+", board):
        raise ValueError(f"invalid build board: {board!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", version) or len(version) >= 64:
        raise ValueError(f"invalid AirBurn version: {version!r}")
    return board, version
