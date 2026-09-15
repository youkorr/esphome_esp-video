"""speaker: - platform: portall_bt -- the panel's sound, over Bluetooth.

The A2DP source underneath this has worked since it paired with a car
receiver on its first run, and the only thing it could play was the test tone
it generates for itself. This is the door a YAML pushes real sound through.

WHAT A HOUSEHOLD HAS TO WRITE, and why it is one extra block rather than two:

    speaker:
      - platform: portall_bt
        id: bt_speaker
        portall_bt_id: dongle

      - platform: resampler
        id: to_bluetooth
        output_speaker: bt_speaker

    portall:
      speaker_id: to_bluetooth

portall sends 48000 Hz, 16-bit, MONO -- 48000 because that is what a browser
produces. A2DP takes 44100 Hz, 16-bit, STEREO, hardcoded in Espressif's
btc_a2dp_source.c. The resampler does the rate; this platform duplicates the
channel, which is each sample written twice and not signal processing at all.

Doing the channel here is what keeps the chain short, and it is also the only
place it CAN be done with what ESPHome ships: AudioResampler::start() returns
ESP_ERR_NOT_SUPPORTED when the input and output channel counts differ, and the
mixer, which does convert channels, wants every source already at the output's
sample rate. Both read in 2026.8.2 rather than remembered.
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

from .. import CONF_AUDIO, PortallBT, portall_bt_ns

AUTO_LOAD = ["audio"]
DEPENDENCIES = ["portall_bt"]

CONF_PORTALL_BT_ID = "portall_bt_id"

PortallBTSpeaker = portall_bt_ns.class_(
    "PortallBTSpeaker", cg.Component, speaker.Speaker
)

# Not a preference and not a default: btc_a2dp_source.c carries the comment
# "for now hardcode 44.1 khz 16 bit stereo PCM format", so these are the only
# numbers this platform can accept.
A2DP_RATE = 44100
A2DP_BITS = 16


def _set_stream_limits(config):
    # Mono is allowed because that is what portall produces and this platform
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
            cv.GenerateID(): cv.declare_id(PortallBTSpeaker),
            cv.GenerateID(CONF_PORTALL_BT_ID): cv.use_id(PortallBT),
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

    parent = await cg.get_variable(config[CONF_PORTALL_BT_ID])
    cg.add(var.set_parent(parent))
