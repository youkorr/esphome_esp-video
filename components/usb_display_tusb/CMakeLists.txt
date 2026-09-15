# USB descriptors for the extended-screen device.
#
# TinyUSB reads tusb_config.h and the descriptor callbacks from the application,
# not from a component it depends on, so these files have to be compiled INTO
# the tinyusb library itself. That is the same trick usb_device_uvc uses; there
# is no other way to give TinyUSB a device description from a component.

# esp_private/usb_phy.h moved between components in IDF 6, the same split
# usb_device_uvc handles.
if(${IDF_VERSION_MAJOR} LESS 6)
    set(usb_requires usb)
else()
    set(usb_requires esp_hw_support)
endif()

# These are public REQUIRES on purpose: ESPHome's "src" component requires this
# one, so it inherits the include directories from here. Without that,
# usb_display.cpp cannot find driver/jpeg_decode.h or esp_private/usb_phy.h --
# neither is on the default include path for src.
if(CONFIG_USB_DISPLAY_AUDIO)
    list(APPEND usb_requires espressif__usb_device_uac)
endif()

idf_component_register(INCLUDE_DIRS "."
                       REQUIRES esp_driver_jpeg ${usb_requires})

# Is TinyUSB in this build? ASK THE BUILD, not the option.
#
# This used to test CONFIG_USB_DISPLAY_DEVICE, which is what portall's `usb:`
# writes -- our own intention rather than the build's state, and the two can
# disagree. A board reported the disagreement as a compile error naming two
# components it had never configured:
#
#   tusb_option.h (in "espressif__tinyusb") includes tusb_config.h, provided
#   by usb_display_tusb component(s). However, usb_display_tusb is not in the
#   requirements list of "espressif__tinyusb".
#
# TinyUSB was in the build and the wiring below had been skipped, so nothing
# handed it the tusb_config.h it cannot start without. A firmware with no
# `portall:` at all asks for neither component, so what put them there was a
# build directory carrying both from an earlier run -- `managed_components/`
# is discovered from DISK, and a dependency that stops being requested does
# not always stop being on disk. The Kconfig default of `y` cannot rescue
# that either: a stale sdkconfig answers from its own file.
#
# BUILD_COMPONENTS is the build's own list and it is complete before any
# component's CMakeLists runs -- __build_expand_requirements fills it during
# requirement expansion, which is before add_subdirectory reaches components
# (esp-idf tools/cmake/build.cmake, read rather than assumed). So this is the
# same question with a true answer: wire up exactly when there is something to
# wire into, and return cleanly when there is not.
idf_build_get_property(build_components BUILD_COMPONENTS)
if(NOT "espressif__tinyusb" IN_LIST build_components)
    # No TinyUSB, so nothing to compile the descriptors into and nothing that
    # needs tusb_config.h. This component still registered above, because the
    # udisp WIRE FORMAT in usb_descriptors.h and these include directories are
    # what the NETWORK path is built on -- that half has never been TinyUSB's.
    return()
endif()

# TinyUSB is here and the configuration says the device is off. That pair
# cannot come out of this repository's codegen -- portall adds the library and
# writes the option together -- so it is a build directory carrying TinyUSB
# from an earlier run. Wiring it up below is what makes the build SUCCEED; it
# is still a megabyte of device stack in a firmware that asked for none, and
# on a Tab5 the peripheral it claims is the one the USB-A host socket needs.
# Said here because a build directory is not something a reader can see.
if(NOT CONFIG_USB_DISPLAY_DEVICE)
    message(STATUS "portall: TinyUSB is in this build and `usb:` is off. That is a leftover from an "
                   "earlier build in the same directory -- it compiles, but delete the build folder "
                   "(ESPHome: three dots > Clean build files) to stop carrying it.")
endif()

idf_component_get_property(tusb_lib espressif__tinyusb COMPONENT_LIB)
target_include_directories(${tusb_lib} PUBLIC "${COMPONENT_DIR}")
target_sources(${tusb_lib} PUBLIC "${COMPONENT_DIR}/usb_descriptors.c")

if(CONFIG_USB_DISPLAY_AUDIO)
    # usb_device_uac keeps its TinyUSB half out of its own public headers, and
    # skips wiring it up when it is built as part of somebody else's device --
    # which is what we are doing. tusb_config.h and the descriptors here reach
    # into it, so put it on the path ourselves.
    idf_component_get_property(uac_dir espressif__usb_device_uac COMPONENT_DIR)
    target_include_directories(${tusb_lib} PUBLIC "${uac_dir}/tusb_uac")
endif()
