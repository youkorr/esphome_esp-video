# USB descriptors for the extended-screen device.
#
# TinyUSB reads tusb_config.h and the descriptor callbacks from the application,
# not from a component it depends on, so these files have to be compiled INTO
# the tinyusb library itself. That is the same trick usb_device_uvc uses; there
# is no other way to give TinyUSB a device description from a component.

idf_component_register(INCLUDE_DIRS "." REQUIRES esp_driver_jpeg)

idf_component_get_property(tusb_lib espressif__tinyusb COMPONENT_LIB)
target_include_directories(${tusb_lib} PUBLIC "${COMPONENT_DIR}")
target_sources(${tusb_lib} PUBLIC "${COMPONENT_DIR}/usb_descriptors.c")
