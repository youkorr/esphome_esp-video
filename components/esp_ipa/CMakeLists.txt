set(include_dirs "include")

# CRITICAL FIX: Compile ALL IPA source files for ESPHome/PlatformIO
set(srcs "src/version.c"
         "src/esp_ipa_detect_stubs.c"
         "src/esp_ipa_json_loader.c")

# NOTE: JSON embedding is handled by esp_video_build.py for PlatformIO compatibility
# The Python script generates C wrappers with symbols like ov02c10_ipa_config_json_start

idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS ${include_dirs}
                       REQUIRES json esp_video)

idf_build_get_property(idf_target IDF_TARGET)

# Link prebuilt IPA library
add_prebuilt_library(prebuilt "lib/${idf_target}/libesp_ipa.a"
                     PRIV_REQUIRES esp_timer)
target_link_libraries(${COMPONENT_LIB} PRIVATE prebuilt)

# ESPHome 2026.7 (build ESP-IDF natif) : le prebuilt libesp_ipa.a référence
# __esp_ipa_detect_array_start/end, définis dans esp_ipa_detect_stubs.c (dans le
# COMPONENT_LIB, une archive séparée du prebuilt). Sans référence depuis un objet
# déjà inclus, l'éditeur de liens ne tire pas esp_ipa_detect_stubs.o de l'archive
# et les symboles restent indéfinis. On force leur inclusion via -u (même approche
# que les -u des fonctions detect côté esp_cam_sensor).
target_link_libraries(${COMPONENT_LIB} INTERFACE
    "-u __esp_ipa_detect_array_start"
    "-u __esp_ipa_detect_array_end")






