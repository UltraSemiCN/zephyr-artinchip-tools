# SPDX-License-Identifier: Apache-2.0
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from zephyr_image_metadata import read_build_identity


class BuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'CMakeCache.txt').write_text('BOARD:STRING=example@1.0/soc\n')
        self.headers = self.root / 'zephyr/include/generated/zephyr'
        self.headers.mkdir(parents=True)
        (self.headers / 'version.h').write_text(
            '#define KERNEL_VERSION_STRING "4.4.99"\n#define KERNEL_TWEAK 0\n')

    def test_qualified_board_and_kernel_fallback(self):
        self.assertEqual(read_build_identity(self.root), ('example', '4.4.99'))
        self.assertEqual(read_build_identity(self.root, 'example@1.0/soc'),
                         ('example', '4.4.99'))

    def test_application_version_takes_precedence(self):
        (self.headers / 'app_version.h').write_text(
            '#define APP_VERSION_STRING "1.2.3-rc1"\n#define APP_TWEAK 4\n')
        self.assertEqual(read_build_identity(self.root), ('example', '1.2.3-rc1+4'))

    def test_wrong_board_rejected(self):
        for board in ('another', 'example/other_soc'):
            with self.assertRaisesRegex(ValueError, 'does not match'):
                read_build_identity(self.root, board)

    def test_missing_board_rejected(self):
        (self.root / 'CMakeCache.txt').write_text('')
        with self.assertRaisesRegex(ValueError, 'missing BOARD'):
            read_build_identity(self.root)

    def test_invalid_application_version_does_not_fall_back(self):
        (self.headers / 'app_version.h').write_text('#define APP_VERSION_STRING "../bad"\n')
        with self.assertRaisesRegex(ValueError, 'invalid AirBurn version'):
            read_build_identity(self.root)
