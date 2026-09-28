"""speaker: - platform: usb_bluetooth -- ESPHome's sound, over Bluetooth.

An ordinary ESPHome speaker, so anything that plays into a speaker plays into
this one: a speaker media_player, a voice assistant's answer, a mixer.

    speaker:
      - platform: usb_bluetooth
        id: bt_speaker
        num_channels: 2        # or 1: each sample is then sent to both sides

A2DP carries 44100 Hz, 16-bit, STEREO -- hardcoded in Espressif's
btc_a2dp_source.c -- so `sample_rate:` can only be 44100. A source at another
rate goes through ESPHome's `resampler` speaker first, or through a mixer whose
output is this speaker. Put a resampler BEFORE a mixer, not after it: a
resampler holds back half its filter, and a mixer only finishes an
announcement once every frame it mixed has been reported played.

This platform turns mono into stereo itself, because AudioResampler::start()
returns ESP_ERR_NOT_SUPPORTED when the channel counts differ. The volume is
applied here in software: there is no codec on this path.
"""

import esphome.codegen as cg
from esphome.components import audio, speaker
import esphome.config_validation as cv
from esphome.const import (
    CONF_BITS_PER_SAMPLE,
    CONF_ID,
    CONF_NUM_CHANNELS,
    CONF_SAMPLE_RATE,
    PLATFORM_ESP32,
)

from .. import CONF_AUDIO, UsbBluetooth, usb_bluetooth_ns

AUTO_LOAD = ["audio"]
DEPENDENCIES = ["usb_bluetooth"]

CONF_USB_BLUETOOTH_ID = "usb_bluetooth_id"

UsbBluetoothSpeaker = usb_bluetooth_ns.class_(
    "UsbBluetoothSpeaker", cg.Component, speaker.Speaker
)

# Not a preference and not a default: btc_a2dp_source.c carries the comment
# "for now hardcode 44.1 khz 16 bit stereo PCM format", so these are the only
# numbers this platform can accept.
A2DP_RATE = 44100
A2DP_BITS = 16


def _set_stream_limits(config):
    # Mono is allowed because a voice assistant produces it and this platform
    # duplicates it; stereo is allowed because it is what the wire carries and
    # a source that already has two channels should not be made to lose one.
    audio.set_stream_limits(
        min_bits_per_sample=A2DP_BITS,
        max_bits_per_sample=A2DP_BITS,
        min_channels=1,
        max_channels=2,
        min_sample_rate=A2DP_RATE,
        max_sample_rate=A2DP_RATE,
    )(config)
    return config


CONFIG_SCHEMA = cv.All(
    speaker.SPEAKER_SCHEMA.extend(
        {
            cv.GenerateID(): cv.declare_id(UsbBluetoothSpeaker),
            cv.GenerateID(CONF_USB_BLUETOOTH_ID): cv.use_id(UsbBluetooth),
            # These three are given defaults rather than left open because a
            # resampler pointed at this speaker INHERITS them from here --
            # esphome.core.entity_helpers.inherit_property_from, which reads
            # the output speaker's own config. Leaving them unset does not
            # mean "anything"; it means the resampler's to_code raises a
            # KeyError on a line nobody can connect to this file.
            cv.Optional(CONF_BITS_PER_SAMPLE, default=A2DP_BITS): cv.int_range(
                A2DP_BITS, A2DP_BITS
            ),
            cv.Optional(CONF_NUM_CHANNELS, default=1): cv.int_range(1, 2),
            cv.Optional(CONF_SAMPLE_RATE, default=A2DP_RATE): cv.int_range(
                A2DP_RATE, A2DP_RATE
            ),
        }
    ).extend(cv.COMPONENT_SCHEMA),
    cv.only_on([PLATFORM_ESP32]),
    _set_stream_limits,
)


async def to_code(config):
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    await speaker.register_speaker(var, config)

    parent = await cg.get_variable(config[CONF_USB_BLUETOOTH_ID])
    cg.add(var.set_parent(parent))
