# C6 diagnostic host
This experimental branch starts from the TCP window experiment; keep 96 KiB
for the baseline. Add `c6_diagnostics: true` under portall only after flashing
the matching diagnostic co-processor image built from
youkorr/esp-hosted-mcu:experimental-c6-portall-diag. The C6 version string
remains 3.1.0, so a normal same-version update will not necessarily install it.
Do not attempt to flash the factory image or bypass an updater without verifying
its recovery path. The current artifact is not a published OTA URL.
A separate P4 task polls CustomRpc every 10 seconds and logs the reply with
tag portall.c6. First interval includes boot. No changes to display, audio,
SDIO clock, C6 tuning or add-on. Remove the diagnostic option to stop polling.
Compare same clip and network settings, with/without diagnostics to quantify
observer overhead. See the C6 branch's tools/portall-c6-diag/README.md for units.
