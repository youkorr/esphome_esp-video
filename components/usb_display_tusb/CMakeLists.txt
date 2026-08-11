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
idf_component_register(INCLUDE_DIRS "."
                       REQUIRES esp_driver_jpeg ${usb_requires})

idf_component_get_property(tusb_lib espressif__tinyusb COMPONENT_LIB)
target_include_directories(${tusb_lib} PUBLIC "${COMPONENT_DIR}")
target_sources(${tusb_lib} PUBLIC "${COMPONENT_DIR}/usb_descriptors.c")
