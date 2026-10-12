#!/usr/bin/env python3
# Copyright (c) 2026 UltraSemi. SPDX-License-Identifier: Apache-2.0
"""Independent artifact and signature negative tests for an existing sysbuild."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib

import yaml
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from mk_image_mcuboot import read_config, validate_payload, validate_boot_key
from zephyr_image_metadata import read_firmware_addresses, load_build_edt


def verify(root):
    domains = yaml.safe_load((root / 'domains.yaml').read_text())
    builds = {d['name']: Path(d['build_dir']) for d in domains['domains']}
    boot, app = builds['mcuboot'], builds[domains['default']]
    modules = (boot / 'zephyr_modules.txt').read_text().splitlines()
    module = Path(next(line.split('"')[3] for line in modules if line.startswith('"mcuboot":')))
    sys.path.insert(0, str(module / 'scripts'))
    from imgtool.image import Image, VerifyResult
    from imgtool.keys import load as load_key

    config = read_config(boot)
    key_path = Path(config['CONFIG_BOOT_SIGNATURE_KEY_FILE'].strip('"'))
    if not key_path.is_absolute():
        key_path = module / key_path
    validate_boot_key(boot / 'zephyr/zephyr.elf', key_path)
    key = load_key(key_path)
    signed = app / 'zephyr/zephyr.signed.bin'
    binary = app / 'zephyr/zephyr.bin'
    assert Image.verify(str(signed), key)[0] == VerifyResult.OK
    validate_payload(signed.read_bytes(), binary.read_bytes(),
                     *read_firmware_addresses(app / 'zephyr/zephyr.elf', binary))
    with tempfile.TemporaryDirectory() as directory:
        directory = Path(directory)
        bad = bytearray(signed.read_bytes())
        header_size, = struct.unpack_from('<H', bad, 8)
        bad[header_size + 16] ^= 1
        corrupted = directory / 'corrupted.bin'
        corrupted.write_bytes(bad)
        assert Image.verify(str(corrupted), key)[0] != VerifyResult.OK
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        wrong_key_path = directory / 'wrong.pem'
        wrong_key_path.write_bytes(wrong_key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        assert Image.verify(str(signed), load_key(wrong_key_path))[0] != VerifyResult.OK
        assert Image.verify(str(binary), key)[0] != VerifyResult.OK
        try:
            validate_boot_key(boot / 'zephyr/zephyr.elf', wrong_key_path)
        except ValueError as error:
            assert 'differs from configured signing key' in str(error)
        else:
            raise AssertionError('stale bootloader signing key was accepted')

    output = root / 'airburn'
    path = Path((output / 'image-path.txt').read_text().strip())
    firmware = path.read_bytes()
    assert firmware[:8] == b'AIC.FW\0\0'
    meta_offset, meta_size = struct.unpack_from('<II', firmware, 332)
    components = {}
    for offset in range(meta_offset, meta_offset + meta_size, 512):
        meta = firmware[offset:offset + 280]
        assert meta[:8].rstrip(b'\0') == b'META'
        name = meta[8:72].rstrip(b'\0').decode()
        part = meta[72:136].rstrip(b'\0').decode()
        start, size, crc = struct.unpack_from('<III', meta, 136)
        data = firmware[start:start + size]
        assert len(data) == size
        assert zlib.crc32(data) == crc, name
        components[name] = (part, data)
    assert components['image.target.application'] == ('image-0', signed.read_bytes())
    image_cfg = json.loads((output / 'image_cfg.json').read_text())
    nand = image_cfg['image']['info']['media']['type'] == 'spi-nand'
    secondary_size = 2048 if nand else load_build_edt(app, None).label2node['slot1_partition'].regs[0].size
    assert components['image.target.secondary'] == ('image-1', b'\xff' * secondary_size)
    boot_binary = (boot / 'zephyr/zephyr.bin').read_bytes()
    report = json.loads((output / 'build-info.json').read_text())
    assert report['mode'] == 'ram-load'
    assert report['boot_chain'] == 'ROM -> PBP -> MCUboot -> Zephyr'
    assert path.name == f"{report['board']}_v{report['version']}.img"
    part, aic = components['image.target.spl']
    assert part == 'spl' and aic[:4] == b'AIC '
    def verify_aic(data):
        version, length = struct.unpack_from('<II', data, 8)
        assert version == 0x10001 and length <= len(data)
        data = data[:length]
        assert (sum(struct.unpack('<%dI' % (len(data) // 4), data)) & 0xffffffff) == 0xffffffff
        assert hashlib.md5(data[8:-16]).digest() == data[-16:]
        return data

    prefix = verify_aic(aic)
    private_offset, private_size, pbp_offset, pbp_size = struct.unpack_from('<4I', prefix, 64)
    assert private_size > 0 and pbp_size > 0
    assert prefix[private_offset:private_offset + private_size] == (output / 'pbp_cfg.bin').read_bytes()
    image_cfg = json.loads((output / 'image_cfg.json').read_text())
    platform = image_cfg['image']['info']['platform']
    private_data = (output / 'pbp_cfg.bin').read_bytes()
    expected_magic = b'PDAT' if platform == 'd12p' else struct.pack('<I', 0x41490001 if nand else 0x41490006)
    assert private_data[:4] == expected_magic, 'PBP private resource format mismatch'
    psram_init = verify_aic(components['image.updater.ddr' if nand else 'image.updater.psram'][1])
    priv_off, priv_len, pbp_off, pbp_len = struct.unpack_from('<4I', psram_init, 64)
    assert psram_init[priv_off:priv_off + priv_len] == private_data
    assert psram_init[pbp_off:pbp_off + pbp_len] == (output / f'{platform}.pbp').read_bytes()
    assert prefix[pbp_offset:pbp_offset + pbp_size] == (output / f'{platform}.pbp').read_bytes()
    extension, = struct.unpack_from('<I', prefix, 80)
    loader = verify_aic(aic[extension:]) if extension else prefix
    assert len(aic) == (extension + len(loader) if extension else len(prefix))
    size, load, entry = struct.unpack_from('<III', loader, 20)
    assert (load + 256, entry) == read_firmware_addresses(
        boot / 'zephyr/zephyr.elf', boot / 'zephyr/zephyr.bin')
    assert loader[256:256 + size] == boot_binary
    assert 'image.target.os' not in components
    assert components['image.updater.spl'][1] == (output / 'bootloader.aic').read_bytes()
    assert components['image.updater.spl'][1] != aic
    extra_checks = ['image_name_matches_build', 'rom_payload_matches_mcuboot', 'rom_entry_matches_elf',
                    'rom_checksum_and_md5', 'rom_pbp_resources_match',
                    'usb_updater_separate_from_boot_target', 'no_fit_boot_target',
                    'pbp_private_resource_format', 'usb_psram_init_resources']
    if nand:
        updater_order = [name for name in components if name.startswith('image.updater.')]
        assert updater_order == ['image.updater.ddr', 'image.updater.spl']
        assert image_cfg['image']['info']['media']['array_organization'] == [
            {'page': '2k', 'block': '128k', 'oob': '128'}]
        updater_image = components['image.updater.spl'][1]
        updater_prefix = verify_aic(updater_image)
        updater_extension, = struct.unpack_from('<I', updater_prefix, 80)
        updater_loader = verify_aic(updater_image[updater_extension:])
        updater_size, updater_load, updater_entry = struct.unpack_from('<III', updater_loader, 20)
        assert (updater_load, updater_entry) == (0x47b80000, 0x47b80100)
        assert updater_loader[256:256 + updater_size] == (output / 'bootloader.bin').read_bytes()
        assert image_cfg['image']['updater']['spl']['ram'] == '0x41000000'
        extra_checks += ['ddr_before_usb_updater', 'xt26g01c_geometry',
                         'usb_updater_vendor_link_address', 'usb_updater_payload_exact']
    result = {
        'build_dir': str(root), 'image': str(path),
        'sha256': hashlib.sha256(firmware).hexdigest(), 'size': len(firmware),
        'checks': ['embedded_boot_key_matches', 'stale_boot_key_rejected', 'signature_valid', 'tampered_payload_rejected', 'wrong_key_rejected',
                   'unsigned_binary_rejected', 'signed_payload_matches_elf',
                   'airburn_all_component_crc', 'airburn_signed_slot0_exact',
                   'airburn_slot1_header_erased'] + extra_checks,
        'hardware_verified': False,
    }
    (output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_dir', type=Path)
    verify(parser.parse_args().build_dir.resolve())
