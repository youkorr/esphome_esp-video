# Portall

Home Assistant, and any web page, on an ESP32-P4 panel over Wi-Fi. The page is
rendered on the machine that is already on all the time, sent to the panel as
JPEG rectangles, and the panel's touches are replayed back into it. One
instance serves as many panels as you list; each gets its own browser, its own
process and its own prefix in the log, and is restarted on its own if it fails.

Everything else -- the options one by one, the launcher, the on-screen
keyboard, sound, the gestures and what it all costs -- is on the
**Documentation** tab at the top of this page.

**YouTube works, in television mode.** Point a launcher link at
`https://www.youtube.com/tv`, give that link a smart-television `user_agent`
and `quality: 20`, and sign in with a code typed on your phone -- no password
on the panel. Your phone then acts as the remote: browse there and send the
video to the panel. It is the only arrangement that works, and the
Documentation tab has the exact link to copy under **YouTube: television mode,
and the phone as its remote**.

**Coming from the old ESP32-P4 Panel add-on?** Its slug was
`usb_display_panel` and this one is `portall`, so Home Assistant sees a new
add-on rather than an update: your options do not come across by themselves.
The Documentation tab opens with **Moving from the old add-on** and the four
steps, of which the first is to copy the old add-on's options with **Edit in
YAML** before uninstalling anything.
