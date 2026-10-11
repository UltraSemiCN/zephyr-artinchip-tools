# SPDX-License-Identifier: Apache-2.0
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as Node

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from zephyr_image_metadata import read_flash_layout
from airburn import verify_partition_images


def prop(value):
    return Node(val=value)


def fixture():
    children = {}
    for name, offset, size in (("spl", 0, 0x80000), ("env", 0x80000, 0x20000),
                               ("env_r", 0xa0000, 0x20000), ("os", 0x100000, 0x300000)):
        children[name] = Node(path=f"/flash/partitions/{name}", status="okay",
                              props={"label": prop(name)},
                              regs=[Node(addr=offset, size=size)])
    table = Node(compats=["fixed-partitions"], children=children)
    flash = Node(compats=["jedec,spi-nor"], aliases=["airburn-flash"],
                 props={"size": prop(128 * 1024 * 1024)}, children={"partitions": table})
    return Node(nodes=[flash]), children


class PartitionTests(unittest.TestCase):
    def test_bits_to_bytes_and_explicit_gap(self):
        edt, _ = fixture()
        layout = read_flash_layout(edt)
        self.assertEqual(int(layout["size"], 0), 16 * 1024 * 1024)
        self.assertEqual(layout["partitions"]["os"], {"offset": "0x100000", "size": "0x300000"})

    def test_reserved_os_partition_is_optional(self):
        edt, parts = fixture()
        del parts["os"]
        self.assertNotIn("os", read_flash_layout(edt)["partitions"])

    def test_overlap(self):
        edt, parts = fixture()
        parts["os"].regs[0].addr = 0x80000
        with self.assertRaisesRegex(ValueError, "overlaps"):
            read_flash_layout(edt)

    def test_out_of_flash(self):
        edt, parts = fixture()
        parts["os"].regs[0].size = 16 * 1024 * 1024
        with self.assertRaisesRegex(ValueError, "exceeds"):
            read_flash_layout(edt)

    def test_missing_required_partition(self):
        edt, parts = fixture()
        del parts["spl"]
        with self.assertRaisesRegex(ValueError, "missing required.*spl"):
            read_flash_layout(edt)

    def test_duplicate_label(self):
        edt, parts = fixture()
        parts["env"].props["label"].val = "spl"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            read_flash_layout(edt)

    def test_unaligned_partition(self):
        edt, parts = fixture()
        parts["os"].regs[0].size += 1
        with self.assertRaisesRegex(ValueError, "aligned"):
            read_flash_layout(edt)

    def test_ambiguous_flash_requires_alias(self):
        edt, _ = fixture()
        edt.nodes[0].aliases = []
        edt.nodes.append(edt.nodes[0])
        with self.assertRaisesRegex(ValueError, "alias"):
            read_flash_layout(edt)

    def test_image_limit_includes_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cfg = {"spi-nor": {"partitions": {"os": {"size": "0x800"}}},
                   "image": {"target": {"os": {"file": "mcuboot.aic", "part": ["os"],
                                                "attr": ["required"]}}}}
            config = root / "image_cfg.json"
            config.write_text(json.dumps(cfg))
            (root / "mcuboot.aic").write_bytes(bytes(2048))
            verify_partition_images(config, root)
            (root / "mcuboot.aic").write_bytes(bytes(2049))
            with self.assertRaisesRegex(ValueError, "4096 aligned bytes"):
                verify_partition_images(config, root)
            (root / "mcuboot.aic").unlink()
            with self.assertRaises(FileNotFoundError):
                verify_partition_images(config, root)


if __name__ == "__main__":
    unittest.main()
