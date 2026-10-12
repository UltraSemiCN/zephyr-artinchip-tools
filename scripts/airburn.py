#!/usr/bin/env python3

# Copyright (c) 2026 UltraSemi Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""AirBurn container helpers for PBP -> MCUboot -> signed Zephyr.

Vendor bootloader.bin is used only by the RAM-resident USB updater.
"""

from __future__ import annotations

import json
import hashlib
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, replace
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
    mcuboot_entry: int = 0x40000100
    extended_loader: bool = False
    media: str = "spi-nor"
    private_file: str = "pbp_cfg.json"

    @property
    def image_name(self) -> str:
        """Intermediate filename imposed by the vendor packer."""
        suffix = "_page_2k_block_128k" if self.media == "spi-nand" else ""
        return f"{self.platform}_{self.product}{suffix}_v{self.version}.img"

    @property
    def pack_files(self) -> dict[str, Path]:
        """Staged filename to its source asset; also drives prerequisites."""
        return {
            "bootloader.bin": self.pack_dir / "bootloader.bin",
            "env.txt": self.env_file,
            "pbp_cfg.json": self.pack_dir / self.private_file,
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


D213_PROFILE = AirBurnProfile(
    board="artinchip_d21x_demo128_nand", platform="d21x", product="demo128_nand",
    description="MCUboot for ArtInChip D213EC-V4 XT26G01CW NAND",
    pack_dir=PACK_DIR / "d21x" / "demo128-nand",
    env_file=PACK_DIR / "common" / "env.txt", pbp_name="d21x.pbp",
    pbp_file=PACK_DIR / "d21x" / "common" / "d21x.pbp",
    psram_ram="0x00103000", spl_ram="0x41000000",
    resource_private_key="private", resource_pbp_key="pbp",
    # Vendor updater links below its 4 MiB heap at the top of 128 MiB DDR.
    # spl_ram is the USB receive buffer, not this binary's linked address.
    bootloader_load_address="0x47b80000", bootloader_entry_point="0x47b80100",
    mcuboot_entry=0x41000100, extended_loader=True, media="spi-nand",
    private_file="ddr_init.json")

PROFILES = {
    D213_PROFILE.board: D213_PROFILE,
    PROFILE.board: PROFILE,
    "artinchip_d12p_demo88_nor": replace(
        PROFILE, board="artinchip_d12p_demo88_nor",
        description="MCUboot for ArtInChip D125 Demo88 NOR"),
    "artinchip_d13x_demo88_nor": AirBurnProfile(
        board="artinchip_d13x_demo88_nor", platform="d13x", product="demo88-nor",
        description="MCUboot for ArtInChip D133 Demo88 NOR",
        pack_dir=PACK_DIR / "d13x" / "demo88-nor",
        env_file=PACK_DIR / "common" / "env.txt", pbp_name="d13x.pbp",
        psram_ram="0x30044000", spl_ram="0x40100000",
        resource_private_key="private", resource_pbp_key="pbp",
        bootloader_load_address="0x406c0000", bootloader_entry_point="0x406c0100",
        bootloader_run_in_dram="true", mcuboot_entry=0x40000100,
        extended_loader=True),
}


def select_profile(board: str) -> AirBurnProfile:
    try:
        return PROFILES[board]
    except KeyError as error:
        raise ValueError(f"unsupported board '{board}'; supported: {', '.join(PROFILES)}") from error


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
    init_file = "usbupg-ddr-init.aic" if profile.media == "spi-nand" else "usbupg-psram-init.aic"
    config = {
        profile.media: flash_layout,
        "image": {
            "info": {
                "platform": profile.platform,
                "product": profile.product,
                "version": profile.version,
                "media": {"type": profile.media, "device_id": 0},
            },
            "updater": {
                "psram": {
                    "file": init_file,
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
    if profile.media == "spi-nand":
        config["image"]["info"]["media"]["array_organization"] = [
            {"page": "2k", "block": "128k", "oob": "128"}]
        updater = config["image"]["updater"]
        config["image"]["updater"] = {"ddr": updater["psram"], "spl": updater["spl"]}
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
    config["pre-process"]["aicimage"][init_file] = {
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

    if profile.extended_loader:
        # D13x vendor format: execute the PBP prefix before loading the CPU image.
        images = config["pre-process"]["aicimage"]
        concatenate = {}
        for name in ("bootloader", "mcuboot"):
            loader = images.pop(f"{name}.aic")
            loader["resource"] = {profile.resource_private_key: pbp_cfg_bin}
            images[f"{name}-loader.aic"] = loader
            images[f"{name}-pbp.aic"] = {
                "head_ver": "0x00010001", "resource": resource, "with_ext": "true"}
            concatenate[f"{name}.aic"] = [f"{name}-pbp.aic", f"{name}-loader.aic"]
        config["pre-process"]["concatenate"] = concatenate

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
    expected_product = profile.product + ("_page_2k_block_128k" if profile.media == "spi-nand" else "")
    if product != expected_product.encode():
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
    if profile.media == "spi-nand":
        verify_updater_provenance(profile)
    for path in REQUIRED_TOOLS + profile.required_pack:
        if not path.is_file():
            raise FileNotFoundError(f"required file not found: {path}")


def verify_updater_provenance(profile: AirBurnProfile) -> None:
    """Check the fixed vendor updater against its recorded link layout."""
    manifest = json.loads((profile.pack_dir / "provenance.json").read_text())
    updater = manifest["usb_updater"]
    expected_entry = (int(updater["dram_base"], 0) + int(updater["dram_size"], 0)
                      - int(updater["heap_size"], 0) - int(updater["text_size"], 0))
    if (int(profile.bootloader_entry_point, 0) != expected_entry or
            int(profile.bootloader_load_address, 0) != expected_entry - 256):
        raise ValueError("vendor updater load/entry differs from its recorded link layout")
    for name, item in manifest["files"].items():
        actual = hashlib.sha256((profile.pack_dir / name).read_bytes()).hexdigest()
        if actual != item["sha256"]:
            raise ValueError(f"vendor resource {name} differs from its recorded SHA256")


    binary = (profile.pack_dir / "bootloader.bin").read_bytes()
    chip = updater["xt26g01c"]
    table = int(chip["table_file_offset"], 0)
    maker = int(chip["manufacturer_file_offset"], 0)
    id_pointer, = struct.unpack_from("<Q", binary, table)
    geometry = struct.unpack_from("<4H", binary, table + 16)
    ecc_pointer, = struct.unpack_from("<Q", binary, table + 48)
    if (binary[maker] != 0x0b or binary[table + 8] != 1 or
            binary[id_pointer - expected_entry] != 0x11 or
            geometry != (2048, 128, 1024, 64) or
            ecc_pointer != int(chip["ecc_function_address"], 0)):
        raise ValueError("vendor updater is missing the validated XT26G01C table")


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
    partitions = config[config["image"]["info"]["media"]["type"]]["partitions"]
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
    # Match the vendor build: only private2 resources carry a PDAT header.
    # Legacy private resources (D13x) start directly with configuration sections.
    images = json.loads(config_path.read_text())["pre-process"]["aicimage"]
    if any(image.get("resource", {}).get("private2") == pbp_cfg_bin.name
           for image in images.values()):
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
