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
from zephyr_image_metadata import read_firmware_addresses


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
    assert components['image.target.secondary'] == ('image-1', b'\xff' * 0x280000)
    boot_binary = (boot / 'zephyr/zephyr.bin').read_bytes()
    report = json.loads((output / 'build-info.json').read_text())
    assert report['boot_chain_mode'] == 'direct'
    assert path.name == f"{report['board']}_v{report['version']}.img"
    part, aic = components['image.target.spl']
    assert part == 'spl' and aic[:4] == b'AIC '
    version, length = struct.unpack_from('<II', aic, 8)
    size, load, entry = struct.unpack_from('<III', aic, 20)
    assert version == 0x10001 and length == len(aic)
    assert (load + 256, entry) == read_firmware_addresses(
        boot / 'zephyr/zephyr.elf', boot / 'zephyr/zephyr.bin')
    assert aic[256:256 + size] == boot_binary
    assert (sum(struct.unpack('<%dI' % (len(aic) // 4), aic)) & 0xffffffff) == 0xffffffff
    assert hashlib.md5(aic[8:-16]).digest() == aic[-16:]
    private_offset, private_size, pbp_offset, pbp_size = struct.unpack_from('<4I', aic, 64)
    assert private_size > 0 and pbp_size > 0
    assert aic[private_offset:private_offset + private_size] == (output / 'pbp_cfg.bin').read_bytes()
    assert aic[pbp_offset:pbp_offset + pbp_size] == (output / 'd12p.pbp').read_bytes()
    assert 'image.target.os' not in components
    assert components['image.updater.spl'][1] == (output / 'bootloader.aic').read_bytes()
    assert components['image.updater.spl'][1] != aic
    extra_checks = ['image_name_matches_build', 'rom_payload_matches_mcuboot', 'rom_entry_matches_elf',
                    'rom_checksum_and_md5', 'rom_pbp_resources_match',
                    'usb_updater_separate_from_boot_target', 'no_fit_boot_target']
    result = {
        'build_dir': str(root), 'image': str(path),
        'sha256': hashlib.sha256(firmware).hexdigest(), 'size': len(firmware),
        'checks': ['embedded_boot_key_matches', 'stale_boot_key_rejected', 'signature_valid', 'tampered_payload_rejected', 'wrong_key_rejected',
                   'unsigned_binary_rejected', 'signed_payload_matches_elf',
                   'airburn_all_component_crc', 'airburn_signed_slot0_exact',
                   'airburn_slot1_erased'] + extra_checks,
        'hardware_verified': False,
    }
    (output / 'validation.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build_dir', type=Path)
    verify(parser.parse_args().build_dir.resolve())
