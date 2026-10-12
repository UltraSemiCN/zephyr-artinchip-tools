# Copyright (c) 2026 UltraSemi. SPDX-License-Identifier: Apache-2.0
"""Package standard sysbuild MCUboot + signed RAM-load app for ArtInChip AirBurn."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import struct

from cryptography.hazmat.primitives import serialization
import yaml
from elftools.elf.elffile import ELFFile
from zephyr_image_metadata import load_build_edt, read_flash_layout, read_build_identity, read_firmware_addresses
from airburn import (select_profile, check_prerequisites, reset_output_dir, stage_inputs,
                             prepare_metadata, prepare_private_resource, run_mk_image,
                             verify_partition_images, verify_image, run_python, copy_required)


def read_config(build_dir):
    return dict(re.findall(r'^(CONFIG_\w+)=(.*)$',
                          (build_dir / 'zephyr/.config').read_text(), re.MULTILINE))


def signed_header(data):
    if len(data) < 32:
        raise ValueError('truncated MCUboot header')
    magic, load, header_size, protected_size, size, flags = struct.unpack_from('<IIHHII', data)
    if magic != 0x96F3B83D or not flags & 0x20:
        raise ValueError('expected MCUboot RAM-load image')
    if header_size < 32 or header_size + size + protected_size > len(data):
        raise ValueError('invalid MCUboot image bounds')
    return load, header_size, size


def validate_payload(signed, binary, load, entry):
    address, header_size, image_size = signed_header(signed)
    if address != load or address + header_size != entry:
        raise ValueError('MCUboot RAM-load address/header does not match application ELF entry')
    if len(binary) != header_size + image_size or signed[header_size:header_size + image_size] != binary[header_size:]:
        raise ValueError('signed application does not match compiled zephyr.bin')


def validate_elf_ram(elf_path, ram):
    with elf_path.open('rb') as source:
        elf = ELFFile(source)
        for section in elf.iter_sections():
            if not section['sh_flags'] & 2 or not section['sh_size']:
                continue
            start, size = section['sh_addr'], section['sh_size']
            if start < ram.addr or start + size > ram.addr + ram.size:
                raise ValueError(f'{elf_path.name}: section {section.name} outside assigned RAM')


def validate_boot_key(elf_path, key_path):
    """Compare the configured key with the key in the linked bootloader."""
    pem = key_path.read_bytes()
    try:
        public = serialization.load_pem_public_key(pem)
    except ValueError:
        public = serialization.load_pem_private_key(pem, password=None).public_key()
    expected = public.public_bytes(serialization.Encoding.DER,
                                   serialization.PublicFormat.SubjectPublicKeyInfo)
    with elf_path.open('rb') as source:
        elf = ELFFile(source)
        symbols = elf.get_section_by_name('.symtab')
        matches = symbols.get_symbol_by_name('ecdsa_pub_key') if symbols else None
        if not matches or not isinstance(matches[0]['st_shndx'], int):
            raise ValueError('MCUboot ELF has no embedded ECDSA public key')
        symbol = matches[0]
        section = elf.get_section(symbol['st_shndx'])
        offset = symbol['st_value'] - section['sh_addr']
        actual = section.data()[offset:offset + symbol['st_size']]
    if actual != expected:
        raise ValueError('MCUboot embedded public key differs from configured signing key; rebuild MCUboot')


def build_sysbuild_image(build_dir: Path, board=None, zephyr_base=None):
    domains_path = build_dir / 'domains.yaml'
    if not domains_path.is_file():
        raise ValueError('sysbuild root must contain domains.yaml')
    domains = yaml.safe_load(domains_path.read_text())
    builds = {d['name']: Path(d['build_dir']) for d in domains['domains']}
    if 'mcuboot' not in builds or domains['default'] == 'mcuboot':
        raise ValueError('sysbuild must contain MCUboot and a default application')
    boot_dir, app_dir = builds['mcuboot'], builds[domains['default']]
    board, version = read_build_identity(app_dir, board)
    read_build_identity(boot_dir, board)
    profile = replace(select_profile(board), version=version,
                      description=f'MCUboot for {board}')
    boot_cfg, app_cfg = read_config(boot_dir), read_config(app_dir)
    if boot_cfg.get('CONFIG_BOOT_RAM_LOAD') != 'y' or boot_cfg.get('CONFIG_BOOT_VALIDATE_SLOT0') != 'y':
        raise ValueError('MCUboot must use RAM load and validate slot0 every boot')
    if boot_cfg.get('CONFIG_BOOT_SIGNATURE_TYPE_ECDSA_P256') != 'y':
        raise ValueError('this bring-up profile requires ECDSA-P256 signatures')
    if app_cfg.get('CONFIG_MCUBOOT_BOOTLOADER_MODE_RAM_LOAD') != 'y':
        raise ValueError('application is not built for RAM load')
    boot_edt = load_build_edt(boot_dir, zephyr_base)
    app_edt = load_build_edt(app_dir, zephyr_base)
    layout = read_flash_layout(app_edt)
    if layout != read_flash_layout(boot_edt):
        raise ValueError('MCUboot and application partition layouts differ')
    slot = app_edt.label2node['slot0_partition']
    slot_label = slot.props['label'].val
    secondary = app_edt.label2node['slot1_partition']
    secondary_label = secondary.props['label'].val
    if app_edt.chosen_node('zephyr,code-partition') != slot:
        raise ValueError('application code partition must be slot0')
    boot_ram = boot_edt.chosen_node('zephyr,sram').regs[0]
    image_ram = boot_edt.chosen_node('mcuboot,image-ram').regs[0]
    app_ram = app_edt.chosen_node('zephyr,sram').regs[0]
    if (image_ram.addr, image_ram.size) != (app_ram.addr, app_ram.size):
        raise ValueError('MCUboot image RAM differs from application RAM')
    if max(boot_ram.addr, app_ram.addr) < min(boot_ram.addr + boot_ram.size, app_ram.addr + app_ram.size):
        raise ValueError('MCUboot and application RAM overlap')
    boot_z, app_z = boot_dir / 'zephyr', app_dir / 'zephyr'
    boot_addresses = read_firmware_addresses(boot_z / 'zephyr.elf', boot_z / 'zephyr.bin')
    app_addresses = read_firmware_addresses(app_z / 'zephyr.elf', app_z / 'zephyr.bin')
    if boot_addresses != (profile.mcuboot_entry, profile.mcuboot_entry):
        raise ValueError('MCUboot requires the ROM-header-reserved overlay; rebuild pristine')
    if boot_edt.chosen_node('zephyr,code-partition') != boot_edt.label2node['spl_partition']:
        raise ValueError('MCUboot must occupy the ROM boot partition')
    validate_elf_ram(boot_z / 'zephyr.elf', boot_ram)
    validate_elf_ram(app_z / 'zephyr.elf', app_ram)
    signed = app_z / 'zephyr.signed.bin'
    validate_payload(signed.read_bytes(), (app_z / 'zephyr.bin').read_bytes(), *app_addresses)
    if (signed.stat().st_size + 2047) // 2048 * 2048 > slot.regs[0].size:
        raise ValueError('signed application exceeds slot0')
    # Verify with the same key whose public half was compiled into MCUboot.
    modules = (boot_dir / 'zephyr_modules.txt').read_text()
    module = re.search(r'^"mcuboot":"([^"]+)"', modules, re.MULTILINE)
    if not module:
        raise ValueError('missing MCUboot module path in zephyr_modules.txt')
    key = Path(boot_cfg['CONFIG_BOOT_SIGNATURE_KEY_FILE'].strip('"'))
    if not key.is_absolute():
        key = Path(module[1]) / key
    validate_boot_key(boot_z / 'zephyr.elf', key)
    run_python(Path(module[1]) / 'scripts/imgtool.py', 'verify', '-k', key, signed)
    for filename, partition in (("data.lfs", "data"), ("rodata.fatfs", "rodata")):
        if (app_z / filename).is_file() and partition not in layout['partitions']:
            raise ValueError(f'{filename} exists but partition {partition} is missing')
    if profile.media == 'spi-nand' and signed.stat().st_size > slot.regs[0].size - 2 * 131072:
        raise ValueError('NAND image must leave at least two erase blocks for bad-block reserve')
    check_prerequisites(profile)
    output = build_dir / 'airburn'
    reset_output_dir(output)
    stage_inputs(app_z, boot_z / 'zephyr.bin', output, profile)
    copy_required(signed, output / 'zephyr.signed.bin')
    cfg_path = prepare_metadata(output, profile, layout, boot_addresses)
    cfg = json.loads(cfg_path.read_text())
    cfg['image']['target']['application'] = {
        'file': 'zephyr.signed.bin', 'attr': ['mtd', 'required'], 'part': [slot_label]}
    # Factory flashing must not leave an old, higher-version slot1 behind.
    empty_size = 2048 if profile.media == 'spi-nand' else secondary.regs[0].size
    (output / 'slot1.empty.bin').write_bytes(b'\xff' * empty_size)
    cfg['image']['target']['secondary'] = {
        'file': 'slot1.empty.bin', 'attr': ['mtd', 'required'], 'part': [secondary_label]}
    cfg_path.write_text(json.dumps(cfg, indent=4) + '\n')
    prepare_private_resource(cfg_path, output)
    image = output / profile.image_name
    try:
        run_mk_image(cfg_path, output)
        verify_partition_images(cfg_path, output)
        verify_image(image, profile)
        image = image.replace(output / f'{board}_v{version}.img')
    except Exception:
        image.unlink(missing_ok=True)
        raise
    report = {
        'board': board, 'mode': 'ram-load', 'version': version, 'media': profile.media,
        'boot_chain': 'ROM -> PBP -> MCUboot -> Zephyr',
        'application_sign_version': app_cfg['CONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION'].strip('"'),
        'boot_load': hex(boot_addresses[0]), 'boot_entry': hex(boot_addresses[1]),
        'app_load': hex(app_addresses[0]), 'app_entry': hex(app_addresses[1]),
        'partitions': layout['partitions'], 'hardware_verified': False,
        'files': {f.name: {'size': f.stat().st_size, 'sha256': hashlib.sha256(f.read_bytes()).hexdigest()}
                  for f in (image, output / 'zephyr.signed.bin', output / 'mcuboot.bin')},
    }
    (output / 'build-info.json').write_text(json.dumps(report, indent=2) + '\n')
    (output / 'image-path.txt').write_text(str(image) + '\n')
    print(f'MCUboot AirBurn output: {image}')
    return image
