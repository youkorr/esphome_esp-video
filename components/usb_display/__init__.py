"""Renamed to portall.

This is not the component any more, and it is here on purpose: without it a
board whose YAML still says `usb_display:` fails with "Component not found",
which says nothing about what to do. Five panels were running on that name.

The two actions are registered here as well, and that is not decoration.
ESPHome resolves an action before it validates the component block, so a YAML
with `on_touch: - usb_display.wake:` failed with "Unable to find action with
the name 'usb_display.wake'" and the explanation below was never reached.
Whichever the file trips over first, the message is now the same one.

Deliberately not an alias. Re-exporting portall's schema from here would work
only when portall happened to be loaded as well, and a compatibility path that
works by accident is worse than a rename that says so plainly.
"""
import esphome.codegen as cg
import esphome.config_validation as cv
from esphome import automation

MESSAGE = (
    "usb_display has been renamed to portall, because it has not been about "
    "USB alone for a long time. Four changes to your YAML, and nothing else: "
    "external_components components: [usb_display] -> [portall]; the block "
    "usb_display: -> portall:; the actions usb_display.sleep / "
    "usb_display.wake -> portall.sleep / portall.wake; and, if you use it, "
    "number platform: usb_display -> portall. Every option keeps its name and "
    "its meaning."
)

CONFIG_SCHEMA = cv.invalid(MESSAGE)

# Never reached: the schema refuses every use before codegen runs.
_renamed_ns = cg.esphome_ns.namespace("usb_display_renamed")
_RenamedAction = _renamed_ns.class_("RenamedAction", automation.Action)


@automation.register_action("usb_display.sleep", _RenamedAction, cv.invalid(MESSAGE))
@automation.register_action("usb_display.wake", _RenamedAction, cv.invalid(MESSAGE))
async def _renamed_to_code(config, action_id, template_arg, args):
    raise cv.Invalid(MESSAGE)
