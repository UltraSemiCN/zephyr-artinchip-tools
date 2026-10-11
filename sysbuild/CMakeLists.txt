# SPDX-License-Identifier: Apache-2.0
# Boot configuration belongs to the Zephyr board sysbuild.cmake.

function(artinchip-tools_post_cmake)
  if(NOT SB_CONFIG_BOOTLOADER_MCUBOOT OR
     NOT BOARD MATCHES "^ultrasemi_d12p_usbdisp_demo88_nor_5_5(/|$)")
    return()
  endif()
  if(NOT ARTINCHIP_MCUBOOT_BOOT_CHAIN STREQUAL "direct")
    message(FATAL_ERROR "Unsupported boot configuration; rebuild using the board sysbuild settings")
  endif()
  set(pack_script "${ZEPHYR_ARTINCHIP_TOOLS_MODULE_DIR}/scripts/mk_image_zephyr.py")
  add_custom_target(artinchip-airburn ALL
    COMMAND ${PYTHON_EXECUTABLE} ${pack_script} --build-dir ${CMAKE_BINARY_DIR}
    DEPENDS mcuboot ${DEFAULT_IMAGE}
    USES_TERMINAL VERBATIM
    COMMENT "Packaging MCUboot and signed application for ArtInChip AirBurn")
endfunction()
