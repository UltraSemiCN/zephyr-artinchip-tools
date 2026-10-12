D213 NAND Shell test image
=========================

Hardware: D213EC-V4, QFN128, 128 MiB DDR3, demo128_nand, XT26G01CW.
The LVDS display is not enabled in this first boot-chain test.

From the west workspace, using its Python environment and configured Zephyr SDK::

   west build --sysbuild -b artinchip_d21x_demo128_nand/d21x \
     zephyr-artinchip-demo-apps/applications/boot-shell \
     -d build/d213_mcuboot_shell
   python zephyr-artinchip-tools/scripts/mk_image_zephyr.py \
     --build-dir build/d213_mcuboot_shell
   python zephyr-artinchip-tools/tests/verify_mcuboot_build.py \
     build/d213_mcuboot_shell

Output::

   build/d213_mcuboot_shell/airburn/artinchip_d21x_demo128_nand_v0.1.0.img

Use the existing ArtInChip USB burning procedure. The updater sends DDR
initialization before the RAM-resident vendor updater. Its USB receive buffer
is 0x41000000, but the binary must be relocated to 0x47b80000 (header included)
and entered at 0x47b80100. These addresses come from the vendor's 128 MiB DDR
layout with a 4 MiB heap and 0x7ff00-byte code region. The packer checks the
resource hashes and link layout recorded in provenance.json. The installed boot
chain is PBP -> MCUboot -> signed Zephyr. MCUboot runs at 0x41000100 and
loads the signed application at 0x40200000 (entry 0x40200200).

UART0 uses PA0/PA1, 115200 baud, 8N1, no hardware flow control. UART clock, reset, pin multiplexing and framing are
initialized by Zephyr. Expected milestones are MCUboot signature verification and RAM-load messages,
followed by the
board name and the aic:~$ prompt. Enter help to check serial input.
Version banners derived from Git tags describe the source revision, not the
hardware model; the application prints CONFIG_BOARD_TARGET.

NAND geometry is 128 MiB, 2048-byte data pages, 128-byte OOB, and 128 KiB
blocks. The expected chip ID is 0b 11. QSPI0 uses PB0-PB5 function 3 and
single-line SPI at no more than 12 MHz. Each partition skips its own bad
blocks, matching the updater's MTD mode. This is not NFTL. Factory flashing
clears the secondary image header; it does not sanitize the entire slot.

Limitations: the mapped flash interface is read-only, runtime firmware
updates are unsupported, and LVDS remains a separate validation step.
Caches are disabled for initial C906 bring-up. The test uses MCUboot's
default development signing key. D213 NAND boot into the shell is hardware verified.
The MCUboot module emits an RV64 integer-to-pointer cast warning at the
RAM-load jump; the generated ELF and signed-header addresses are checked
to remain below 4 GiB. No MCUboot module source was modified for this test.

Software checks: 5 NAND mapping/ECC tests on native_sim, 9 clock tests on
qemu_riscv32, 31 packaging unit tests, and 23 artifact checks including
signature rejection for tampered data/wrong keys, ROM checksums, DDR
resources, updater ordering and NAND geometry. Generated build-info.json
and validation.json record the exact image hash and hardware-verification
status alongside the image.

Burning correction
------------------

The initial v0.1.0 test package incorrectly used the USB receive address as
the vendor updater load address. Hardware testing reported repeated USB PBP
messages without an updater banner. The corrected package keeps the same
filename; replace the downloaded file before retrying. The current image
size is 595968 bytes. Its SHA256 is::

   69f6375d34c2518992755cb5b276cc4c3d987f35bc21cdbf458b8f3f7521ad15

Hardware testing reaches NAND partition writes with the corrected USB
updater. The current MCUboot and application also include the timer and UART
initialization corrections described below.

XTX updater support
-------------------

Hardware testing confirmed that the corrected address reaches the vendor
updater. It then reported unknown raw ID 0b110011: the bundled updater
contains Winbond support but lacks XTX support. The current updater is rebuilt
from the pinned LubanLite commit with CONFIG_SPI_NAND_XTX=y. Its ELF contains
the XT26G01C device table and ECC callback; the packer verifies the binary
hash, chip ID, geometry and ECC pointer against provenance.json.

To reproduce, check out the recorded vendor commit in an isolated directory,
copy scripts/pack/d21x/demo128-nand/updater_defconfig over the vendor file
target/configs/d21x_demo128-nand_baremetal_bootloader_defconfig, then run::

   scons --apply-def=d21x_demo128-nand_baremetal_bootloader_defconfig
   scons -j8

Use the vendor Xuantie V2.6.1 toolchain. The output is
output/d21x_demo128-nand_baremetal_bootloader/images/d21x.bin, with its ELF
and map in the same directory. A replacement resource must have its build
provenance regenerated; do not substitute another board's prebuilt updater.

Hardware testing now reaches all target components and prints ``End of
upgrading`` without the previous unknown-ID error. After reboot, the PBP
reports SPINAND, initializes 128 MiB DDR3, and stops producing output after
``PBP done``. No MCUboot banner or shell has been observed. This does not
establish whether the persistent loader was entered; hardware boot remains
unverified.

For the next inspection, attach a D213/C906-compatible JTAG debugger without
resetting the stopped board. Capture PC, SP, RA, mstatus, mcause, mepc, mtval,
mtvec, mie and mip before changing execution state. Compare memory beginning
at 0x41000100 with mcuboot/zephyr/zephyr.bin to check whether the boot payload
was loaded correctly. Use the ELF from the same build to resolve addresses.
The current image places the AIC loader header at 0x41000000 and MCUboot's
entry at 0x41000100; its application RAM-load base is 0x40200000. Do not
resume, reset or write memory before saving the initial register snapshot.

JTAG boot investigation
-----------------------

The initial stop was inside MCUboot's Zephyr kernel, with the main thread
sleeping during NAND reset. RV64 64-bit stores did not update MTIMECMP's
high word correctly. The D21x timer now declares reg-io-width = 4, selecting
the existing ordered 32-bit register access sequence in the generic driver.
Other platforms retain their previous access width. Both the default RV64
path and 32-bit MMIO path pass the timer API suite (208 test cases total).

After fixing the timer, the console blocked while writing the first banner
character. UART0 clock/reset and PA0/PA1 settings were not preserved by the
boot firmware. UART0 now uses native clock, reset and pinctrl initialization
through the existing ArtInChip UART driver instead of the boot-configured
UART driver.

JTAG execution of the corrected MCUboot in RAM reached the signed application.
Hardware feedback confirms MCUboot RAM-load messages, the XT26G01C partition
map with zero bad blocks skipped, and a responsive aic:~$ shell. NAND was not
rewritten during that test; the application in NAND was still the earlier
build. The packaged image includes both corrected stages and passes 23
artifact checks. Hardware feedback after reflashing confirms NAND boot into the shell.
Display is not enabled in this validated Shell image. A separate explicit
power-cycle test was not reported.

LVDS test variant
-----------------

The same output path can contain the optional Boot Shell LVDS test variant;
see the application README for its configuration command. The shell-only
image validated after flashing has SHA256
69f6375d34c2518992755cb5b276cc4c3d987f35bc21cdbf458b8f3f7521ad15.
Do not apply that validation result to the later display variant.

The display configuration follows LubanLite origin/master commit
f2ee295b1a098b4f6e7d9e78133ed972b58d4ce7, including the default LVDS v1.0
link 1 selection. JTAG confirms LVDS_CTL=0x13, link 1 PHY=0xfa, PC7 high,
and DE active size 1024x600 while the application updates its color bars.
Hardware feedback confirms visible color bars and a moving white marker
during JTAG RAM execution. Hardware feedback after flashing also confirms color bars and a working shell.

Dashboard performance
---------------------

The separate ``dashboard_1024x600_d213_lvds`` application uses the same boot
chain and panel timing. The user confirmed its first uncached JTAG build
showed the Dashboard and a working Shell, but animation was slow. Enabling
C906 I/D caches and cleaning only the modified framebuffer span made animation
visibly smooth; display and Shell were confirmed working in JTAG RAM execution.
The user subsequently confirmed board testing of all four packaged Dashboard
versions, including this D213 target.

C906 cache control follows LubanLite origin/master
``f2ee295b1a098b4f6e7d9e78133ed972b58d4ce7``
(``bsp/artinchip/sys/d21x/include/core_rv64.h``). MCUboot also enables caches while
validating the RAM-loaded image, then writes back data and disables caches
before chainload.
See the demo-apps README for the Dashboard build command and VALIDATION.md
for artifact hashes. The public filename remains
``artinchip_d21x_demo128_nand_v0.1.0.img`` in the Dashboard build's airburn directory.

MCUboot validation performance
-----------------------------

Hardware feedback reported a 10-30 second pause between RAM-load success and
image-validation success with the uncached MCUboot build. The current image
enables C906 instruction and data caches in MCUboot without changing signature
policy. This bootloader update passes 23 offline image checks. After reconnecting
the board, a JTAG RAM run validated the NAND application successfully and reached
its entry at 0x40200200. The user confirmed normal Dashboard display, smooth
animation and a responsive Shell after handoff. The authentication function
took about 0.573 seconds
between debugger timestamps, including communication overhead. This is not a
measurement of total cold-boot time. The user subsequently confirmed board
testing of all four Dashboard versions, including the updated D213 bootloader;
an exhaustive power-cycle campaign was not reported. The output filename and path are unchanged. D125 and D133
configurations are unaffected.

Requesting another USB upgrade
------------------------------

The D213 Dashboard includes the ``aicupg`` shell command, also available as
``uart aicupg``. With the upgrade USB cable connected, run the command to store
the ROM-upgrade reason and reset via watchdog. AirBurn then handles firmware
installation. The implementation follows LubanLite ``origin/master``
``f2ee295b1a098b4f6e7d9e78133ed972b58d4ce7`` and passes register-sequence tests;
the user subsequently reported testing OK for the D213 update. No additional
USB trace or reset timing measurements were supplied.
