# SPDX-License-Identifier: Apache-2.0
from dataclasses import replace
import json
import shutil
import tempfile
import struct
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from mk_image_mcuboot import signed_header, validate_payload, build_sysbuild_image
from mk_image_zephyr import build_image
from airburn import PROFILE, select_profile, write_config, prepare_private_resource, verify_updater_provenance


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


class SysbuildLayoutTests(unittest.TestCase):
    def test_old_builds_rejected_without_touching_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / 'airburn'
            output.mkdir()
            marker = output / 'keep.img'
            marker.write_bytes(b'known-good')
            with self.assertRaisesRegex(ValueError, 'sysbuild root'):
                build_image(root)
            (root / 'CMakeCache.txt').write_text('')
            with self.assertRaisesRegex(ValueError, 'sysbuild root'):
                build_sysbuild_image(root)
            (root / 'domains.yaml').write_text(
                'default: app\ndomains:\n  - name: app\n    build_dir: app\n')
            with self.assertRaisesRegex(ValueError, 'sysbuild must contain MCUboot'):
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


class DemoBoardProfilesTests(unittest.TestCase):
    def test_explicit_demo_entries(self):
        self.assertEqual(select_profile('artinchip_d12p_demo88_nor').mcuboot_entry, 0x40000100)
        self.assertEqual(select_profile('artinchip_d13x_demo88_nor').mcuboot_entry, 0x40000100)

    def test_d133_pbp_runs_before_loader(self):
        profile = select_profile('artinchip_d13x_demo88_nor')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image_cfg.json'
            write_config(path, profile, {'partitions': {}}, (0x40000100, 0x40000100))
            cfg = json.loads(path.read_text())
            images = cfg['pre-process']['aicimage']
            self.assertEqual(cfg['pre-process']['concatenate']['mcuboot.aic'],
                             ['mcuboot-pbp.aic', 'mcuboot-loader.aic'])
            self.assertEqual(images['mcuboot-pbp.aic']['with_ext'], 'true')
            self.assertNotIn('loader', images['mcuboot-pbp.aic'])
            self.assertEqual(images['mcuboot-loader.aic']['loader']['file'], 'mcuboot.bin')
            self.assertEqual(images['mcuboot-loader.aic']['loader']['load address'], '0x40000000')
            self.assertEqual(images['mcuboot-loader.aic']['loader']['entry point'], '0x40000100')
            self.assertEqual(images['mcuboot-loader.aic']['loader']['run in dram'], 'false')
            self.assertNotIn('pbp', images['mcuboot-loader.aic']['resource'])
            self.assertEqual(cfg['image']['target']['spl']['file'], 'mcuboot.aic')
            self.assertEqual(cfg['image']['updater']['spl']['file'], 'bootloader.aic')


class PrivateResourceFormatTests(unittest.TestCase):
    def generate(self, board):
        profile = select_profile(board)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'image_cfg.json'
            write_config(path, profile, {'size': '0x1000000',
                         'partitions': {'spl': {'offset': '0x0', 'size': '0x80000'}}},
                         (profile.mcuboot_entry, profile.mcuboot_entry))
            shutil.copy2(profile.pack_files['pbp_cfg.json'], root / 'pbp_cfg.json')
            prepare_private_resource(path, root)
            return (root / 'pbp_cfg.bin').read_bytes()

    def test_d133_uses_raw_psram_sections(self):
        data = self.generate('artinchip_d13x_demo88_nor')
        self.assertEqual(struct.unpack_from('<I', data)[0], 0x41490006)

    def test_d125_keeps_pdat_wrapper(self):
        for board in ('artinchip_d12p_demo88_nor',
                      'ultrasemi_d12p_usbdisp_demo88_nor_5_5'):
            with self.subTest(board=board):
                self.assertEqual(self.generate(board)[:4], b'PDAT')

class D213ProfileTests(unittest.TestCase):
    def test_nand_ddr_order_and_geometry(self):
        profile = select_profile('artinchip_d21x_demo128_nand')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image_cfg.json'
            write_config(path, profile, {'partitions': {}}, (0x41000100, 0x41000100))
            cfg = json.loads(path.read_text())
        self.assertEqual(list(cfg['image']['updater']), ['ddr', 'spl'])
        self.assertEqual(cfg['image']['info']['media']['type'], 'spi-nand')
        self.assertEqual(cfg['image']['info']['media']['array_organization'],
                         [{'page': '2k', 'block': '128k', 'oob': '128'}])
        self.assertIn('spi-nand', cfg)
        self.assertEqual(cfg['pre-process']['concatenate']['mcuboot.aic'],
                         ['mcuboot-pbp.aic', 'mcuboot-loader.aic'])

    def test_vendor_updater_link_layout(self):
        profile = select_profile('artinchip_d21x_demo128_nand')
        verify_updater_provenance(profile)
        self.assertEqual(profile.spl_ram, '0x41000000')
        self.assertEqual(profile.bootloader_entry_point, '0x47b80100')
        with self.assertRaisesRegex(ValueError, 'link layout'):
            verify_updater_provenance(replace(profile,
                bootloader_load_address='0x41000000', bootloader_entry_point='0x41000100'))

    def test_vendor_binary_absolute_string_pointer(self):
        profile = select_profile('artinchip_d21x_demo128_nand')
        binary = (profile.pack_dir / 'bootloader.bin').read_bytes()
        manifest = json.loads((profile.pack_dir / 'provenance.json').read_text())
        evidence = manifest['usb_updater']['binary_cross_check']
        pointer, = struct.unpack_from('<Q', binary, int(evidence['pointer_file_offset'], 0))
        offset = pointer - int(profile.bootloader_entry_point, 0)
        self.assertEqual(binary[offset:offset + 17], b'Full disk upgrade')

    def test_vendor_updater_contains_xt26g01c(self):
        profile = select_profile('artinchip_d21x_demo128_nand')
        binary = (profile.pack_dir / 'bootloader.bin').read_bytes()
        manifest = json.loads((profile.pack_dir / 'provenance.json').read_text())
        chip = manifest['usb_updater']['xt26g01c']
        table = int(chip['table_file_offset'], 0)
        self.assertEqual(struct.unpack_from('<4H', binary, table + 16),
                         (2048, 128, 1024, 64))
        self.assertIn('CONFIG_SPI_NAND_XTX=y',
                      (profile.pack_dir / 'updater_defconfig').read_text())
        verify_updater_provenance(profile)
