#!/usr/bin/env python3

# Copyright (c) 2026 UltraSemi Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AirBurn container helpers for PBP -> MCUboot -> signed Zephyr.

Vendor bootloader.bin is used only by the RAM-resident USB updater.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parent
PACK_DIR = SCRIPTS_DIR / "pack"

MK_IMAGE = SCRIPTS_DIR / "mk_image.py"
GEN_PARTITION_TABLE = SCRIPTS_DIR / "gen_partition_table.py"
MK_PRIVATE_RESOURCE = SCRIPTS_DIR / "mk_private_resource.py"
MK_PREBOOTPROG = SCRIPTS_DIR / "mk_prebootprog.py"

REQUIRED_TOOLS = (
    MK_IMAGE,
    GEN_PARTITION_TABLE,
    MK_PRIVATE_RESOURCE,
    MK_PREBOOTPROG,
    SCRIPTS_DIR / "mkenvimage",
)


@dataclass(frozen=True)
class AirBurnProfile:
    board: str
    platform: str
    product: str
    description: str
    pack_dir: Path
    env_file: Path
    pbp_name: str
    psram_ram: str
    spl_ram: str
    resource_private_key: str
    resource_pbp_key: str
    bootloader_load_address: str
    bootloader_entry_point: str
    bootloader_run_in_dram: str | None = None
    pbp_file: Path | None = None
    version: str = "1.0.0"

    @property
    def image_name(self) -> str:
        """Intermediate filename imposed by the vendor packer."""
        return f"{self.platform}_{self.product}_v{self.version}.img"

    @property
    def pack_files(self) -> dict[str, Path]:
        """Staged filename to its source asset; also drives prerequisites."""
        return {
            "bootloader.bin": self.pack_dir / "bootloader.bin",
            "env.txt": self.env_file,
            "pbp_cfg.json": self.pack_dir / "pbp_cfg.json",
            self.pbp_name: self.pbp_file or self.pack_dir / self.pbp_name,
        }

    @property
    def required_pack(self) -> tuple[Path, ...]:
        return tuple(self.pack_files.values())


PROFILE = AirBurnProfile(
    board="ultrasemi_d12p_usbdisp_demo88_nor_5_5",
    platform="d12p",
    product="demo88-nor",
    description="MCUboot for UltraSemi D12P USB Display Demo88 NOR 5.5-inch",
    pack_dir=PACK_DIR / "d12p" / "demo88-nor",
    env_file=PACK_DIR / "common" / "env.txt",
    pbp_name="d12p.pbp",
    pbp_file=PACK_DIR / "d12p" / "common" / "d12p.pbp",
    psram_ram="0x30043000",
    spl_ram="0x406c0000",
    resource_private_key="private2",
    resource_pbp_key="pbp2",
    bootloader_load_address="0x406c0000",
    bootloader_entry_point="0x406c0100",
    bootloader_run_in_dram="false",
)


def select_profile(board: str) -> AirBurnProfile:
    if board != PROFILE.board:
        raise ValueError(f"unsupported board '{board}'; supported: {PROFILE.board}")
    return PROFILE


def run(command: list[str | Path]) -> None:
    subprocess.run([str(arg) for arg in command], check=True)


def run_python(script: Path, *args: str | Path) -> None:
    run([sys.executable, script, *args])


def copy_required(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"required file not found: {source}")
    shutil.copy2(source, destination)


def write_config(path: Path, profile: AirBurnProfile, flash_layout: dict,
                 addresses: tuple[int, int]) -> None:
    pbp_cfg_bin = "pbp_cfg.bin"
    config = {
        "spi-nor": flash_layout,
        "image": {
            "info": {
                "platform": profile.platform,
                "product": profile.product,
                "version": profile.version,
                "media": {"type": "spi-nor", "device_id": 0},
            },
            "updater": {
                "psram": {
                    "file": "usbupg-psram-init.aic",
                    "attr": ["required", "run"],
                    "ram": profile.psram_ram,
                },
                "spl": {
                    "file": "bootloader.aic",
                    "attr": ["required", "run"],
                    "ram": profile.spl_ram,
                },
            },
            "target": {
                "spl": {
                    "file": "mcuboot.aic",
                    "attr": ["mtd", "required"],
                    "part": ["spl"],
                },
                "env": {
                    "file": "env.bin",
                    "attr": ["mtd", "optional"],
                    "part": ["env"],
                },
                "env_r": {
                    "file": "env.bin",
                    "attr": ["mtd", "optional"],
                    "part": ["env_r"],
                },
            },
        },
        "pre-process": {
            "aicimage": {},
            "uboot_env": {
                "env.bin": {
                    "file": "env.txt",
                    "size": "4096",
                    "redundant": "enable",
                }
            },
        },
    }
    if "rodata" in flash_layout["partitions"]:
        config["image"]["target"]["rodata"] = {
            "file": "rodata.fatfs",
            "attr": ["mtd", "optional"],
            "part": ["rodata"],
        }
    if "data" in flash_layout["partitions"]:
        config["image"]["target"]["data"] = {
            "file": "data.lfs",
            "attr": ["mtd", "optional"],
            "part": ["data"],
        }

    resource = {
        profile.resource_private_key: pbp_cfg_bin,
        profile.resource_pbp_key: profile.pbp_name,
    }
    config["pre-process"]["aicimage"]["usbupg-psram-init.aic"] = {
        "head_ver": "0x00010001",
        "resource": resource,
    }
    # USB updater runs in RAM; it is never the persistent boot target.
    config["pre-process"]["aicimage"]["bootloader.aic"] = {
        "head_ver": "0x00010001",
        "loader": {
            "file": "bootloader.bin",
            "load address": profile.bootloader_load_address,
            "entry point": profile.bootloader_entry_point,
            "run in dram": profile.bootloader_run_in_dram,
        },
        "resource": resource,
    }
    config["pre-process"]["aicimage"]["mcuboot.aic"] = {
        "head_ver": "0x00010001",
        "loader": {
            "file": "mcuboot.bin",
            "load address": hex(addresses[0] - 256),
            "entry point": hex(addresses[1]),
            "run in dram": "false",
        },
        "resource": resource,
    }

    path.write_text(json.dumps(config, indent=4) + "\n", encoding="ascii")


def verify_image(path: Path, profile: AirBurnProfile) -> None:
    with path.open("rb") as image:
        magic = image.read(8)
        platform = image.read(64).rstrip(b"\0")
        product = image.read(64).rstrip(b"\0")
        version = image.read(64).rstrip(b"\0")
        image.seek(340)
        file_offset = struct.unpack("<I", image.read(4))[0]
        file_size = struct.unpack("<I", image.read(4))[0]

    if magic != b"AIC.FW\0\0":
        raise ValueError(f"invalid AirBurn image magic: {magic!r}")
    if platform != profile.platform.encode():
        raise ValueError(f"unexpected platform: {platform!r}")
    if product != profile.product.encode():
        raise ValueError(f"unexpected product: {product!r}")
    if version != profile.version.encode():
        raise ValueError(f"unexpected version: {version!r}")
    if file_offset + file_size != path.stat().st_size:
        raise ValueError(
            f"image header end {file_offset + file_size} does not match file size "
            f"{path.stat().st_size}"
        )

    print(f"AirBurn image verified: {path}")
    print(f"  platform: {platform.decode()}")
    print(f"  product:  {product.decode()}")
    print(f"  size:     {path.stat().st_size} bytes")


def check_prerequisites(profile: AirBurnProfile) -> None:
    for path in REQUIRED_TOOLS + profile.required_pack:
        if not path.is_file():
            raise FileNotFoundError(f"required file not found: {path}")


def reset_output_dir(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for path in output_dir.iterdir():
        if path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def stage_inputs(zephyr_dir: Path, zephyr_bin: Path, output_dir: Path, profile: AirBurnProfile) -> None:
    copy_required(zephyr_bin, output_dir / "mcuboot.bin")
    for name, source in profile.pack_files.items():
        copy_required(source, output_dir / name)

    for name in ("data.lfs", "rodata.fatfs"):
        source = zephyr_dir / name
        if source.is_file():
            copy_required(source, output_dir / name)
            print(f"Staged filesystem image: {source}")


def prepare_metadata(output_dir: Path, profile: AirBurnProfile, flash_layout: dict,
                     addresses: tuple[int, int]) -> Path:
    config_path = output_dir / "image_cfg.json"
    write_config(config_path, profile, flash_layout, addresses)
    return config_path


def verify_partition_images(config_path: Path, output_dir: Path) -> None:
    config = json.loads(config_path.read_text())
    partitions = config["spi-nor"]["partitions"]
    for target in config["image"]["target"].values():
        source = output_dir / target["file"]
        if not source.is_file():
            if "required" in target["attr"]:
                raise FileNotFoundError(f"missing required image: {source}")
            continue
        size = (source.stat().st_size + 2047) // 2048 * 2048
        for name in target["part"]:
            if size > int(partitions[name]["size"], 0):
                raise ValueError(f"{source.name} ({size} aligned bytes) exceeds partition {name}")


def prepare_private_resource(config_path: Path, output_dir: Path) -> None:
    partition_json = output_dir / "partition.json"
    pbp_cfg_bin = output_dir / "pbp_cfg.bin"
    run_python(
        GEN_PARTITION_TABLE,
        "-c",
        config_path,
        "-j",
        partition_json,
    )
    run_python(
        MK_PRIVATE_RESOURCE,
        "-l",
        f"{output_dir / 'pbp_cfg.json'},{partition_json}",
        "-o",
        pbp_cfg_bin,
    )
    run_python(
        MK_PREBOOTPROG,
        "-m",
        "PDAT",
        "-i",
        pbp_cfg_bin,
        "-o",
        pbp_cfg_bin,
    )

def run_mk_image(config_path: Path, output_dir: Path) -> None:
    """Invoke ArtInChip mk_image.py to pre-process components and build the image."""
    run_python(MK_IMAGE, "-v", "-c", config_path, "-d", output_dir)
