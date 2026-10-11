# SPDX-License-Identifier: Apache-2.0
import json
import tempfile
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from mk_image_mcuboot import signed_header, validate_payload, build_sysbuild_image
from mk_image_zephyr import build_image
from airburn import PROFILE, select_profile, write_config


class MCUbootPayloadTests(unittest.TestCase):
    def setUp(self):
        self.binary = bytes(512) + b'compiled application'
        self.signed = bytearray(self.binary + b'TLV')
        struct.pack_into('<IIHHII', self.signed, 0, 0x96f3b83d, 0x40200000,
                         512, 0, len(self.binary) - 512, 0x20)

    def test_matches_compiled_image(self):
        validate_payload(self.signed, self.binary, 0x40200000, 0x40200200)

    def test_wrong_entry_point(self):
        with self.assertRaisesRegex(ValueError, 'ELF entry'):
            validate_payload(self.signed, self.binary, 0x40200000, 0x40200000)

    def test_stale_signed_image(self):
        self.signed[515] ^= 1
        with self.assertRaisesRegex(ValueError, 'does not match'):
            validate_payload(self.signed, self.binary, 0x40200000, 0x40200200)

    def test_not_ram_load(self):
        struct.pack_into('<I', self.signed, 16, 0)
        with self.assertRaisesRegex(ValueError, 'RAM-load'):
            signed_header(self.signed)

    def test_truncated_header(self):
        with self.assertRaisesRegex(ValueError, 'truncated'):
            signed_header(bytes(20))

    def test_oversized_payload(self):
        struct.pack_into('<I', self.signed, 12, 0xffffffff)
        with self.assertRaisesRegex(ValueError, 'bounds'):
            signed_header(self.signed)


class DirectOnlyTests(unittest.TestCase):
    def test_old_builds_rejected_without_touching_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'airburn'
            output.mkdir()
            marker = output / 'keep.img'
            marker.write_bytes(b'known-good')
            with self.assertRaisesRegex(ValueError, 'sysbuild root'):
                build_image(root)
            for entry in ('ARTINCHIP_MCUBOOT_BOOT_CHAIN:STRING=spl', ''):
                (root / 'CMakeCache.txt').write_text(entry)
                with self.assertRaisesRegex(ValueError, 'unsupported boot configuration'):
                    build_sysbuild_image(root)
                self.assertEqual(marker.read_bytes(), b'known-good')

    def test_no_implicit_or_legacy_board_profile(self):
        for board in (None, 'artinchip_kunlunpi_d133cbs', 'artinchip_d125ccv_demo88_nor'):
            with self.assertRaisesRegex(ValueError, 'unsupported board'):
                select_profile(board)

    def test_boot_target_is_not_usb_updater(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image_cfg.json'
            write_config(path, PROFILE, {'partitions': {}}, (0x40000100, 0x40000100))
            cfg = json.loads(path.read_text())
            self.assertEqual(cfg['image']['target']['spl']['file'], 'mcuboot.aic')
            self.assertEqual(cfg['image']['updater']['spl']['file'], 'bootloader.aic')
            self.assertNotIn('os', cfg['image']['target'])
            self.assertNotIn('itb', cfg['pre-process'])
            loaders = cfg['pre-process']['aicimage']
            self.assertEqual(loaders['mcuboot.aic']['loader']['file'], 'mcuboot.bin')
            self.assertEqual(loaders['bootloader.aic']['loader']['file'], 'bootloader.bin')
