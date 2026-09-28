# Raise one constant in CherryUSB so a Bluetooth dongle can enumerate.
#
# CherryUSB's ESP port fixes how many alternate settings an interface may have
# at two:
#
#     osal/idf/usb_config.h:  #define CONFIG_USBHOST_MAX_INTF_ALTSETTINGS 2
#
# A Bluetooth dongle's second interface carries SCO -- voice -- and has SIX,
# one per audio channel bandwidth; the USB Bluetooth class says so, so every
# dongle is built that way. CherryUSB's parser abandons the WHOLE
# configuration descriptor at the third, and the device never enumerates.
# Measured on an M5Stack Tab5 with a Broadcom BCM20702A1:
#
#     [I/usbh_core] New device found,idVendor:0a5c,idProduct:21e8
#     [E/usbh_core] Interface altsetting num 2 overflow
#     [E/usbh_core] Parse config descriptor fail
#
# It is a bare #define with no #ifndef around it, and not a Kconfig option, so
# neither sdkconfig nor a -D can reach it. The alternative was a fork of
# CherryUSB, which means a second repository to keep in step and a build that
# fails for anybody who has not made it. This edits the copy the component
# manager downloaded INTO THE BUILD DIRECTORY -- never anything in anybody's
# checkout -- and says so out loud when it does.
#
# It is idempotent, it is a no-op when the text is not there, and when
# CherryUSB eventually puts these constants behind #ifndef the replacement
# will simply stop matching, which is the right way for a patch to retire.
#
# Runnable on its own, which is how it was tested:
#     cmake -DPROJECT_DIR=<dir> -P patch.cmake

if(NOT DEFINED PROJECT_DIR)
    message(FATAL_ERROR "cherryusb_patch: PROJECT_DIR was not given")
endif()

set(_old "#define CONFIG_USBHOST_MAX_INTF_ALTSETTINGS 2")
set(_new "#define CONFIG_USBHOST_MAX_INTF_ALTSETTINGS 8")

# Globbed rather than named: the component manager spells a namespaced
# dependency `cherry-embedded__cherryusb` and a git one just `cherryusb`, and
# which of those is on disk depends on how the component asked for it.
# Several roots rather than one, because where `managed_components` sits
# depends on whether ESPHome drove the build through PlatformIO or spoke to
# ESP-IDF itself, and a patch that quietly looked in the wrong place would
# leave exactly the symptom it exists to remove.
file(GLOB _candidates
     "${PROJECT_DIR}/managed_components/*cherryusb*/osal/idf/usb_config.h"
     "${PROJECT_DIR}/components/*cherryusb*/osal/idf/usb_config.h"
     "${PROJECT_DIR}/../managed_components/*cherryusb*/osal/idf/usb_config.h")

if(NOT _candidates)
    message(STATUS "cherryusb_patch: no CherryUSB found under ${PROJECT_DIR}; nothing to do")
endif()

foreach(_config ${_candidates})
    file(READ "${_config}" _content)
    string(REPLACE "${_old}" "${_new}" _patched "${_content}")
    if(_patched STREQUAL _content)
        string(FIND "${_content}" "${_new}" _already)
        if(_already GREATER -1)
            message(STATUS "cherryusb_patch: ${_config} already allows 8 alternate settings")
        else()
            # Loud, because a silent no-op here is a dongle that mysteriously
            # will not enumerate -- which is exactly the hour this cost once.
            message(WARNING
                "cherryusb_patch: could not find the line to change in ${_config}. "
                "A Bluetooth dongle will not enumerate unless "
                "CONFIG_USBHOST_MAX_INTF_ALTSETTINGS is at least 6.")
        endif()
    else()
        file(WRITE "${_config}" "${_patched}")
        message(STATUS "cherryusb_patch: raised CONFIG_USBHOST_MAX_INTF_ALTSETTINGS to 8 in ${_config}")
    endif()
endforeach()
