# Migration esp_video 1.x → 2.2.0 (WIP — branche `claude/esp-video-2.2.0-migration`)

⚠️ **État : travail en cours, NON compilé/validé.** Cette branche vendoré le cœur
upstream `esp_video` 2.2.0 par-dessus la couche ESPHome du fork. Elle **nécessite
un cycle de build réel** (pas de toolchain ESP32-P4 dans l'environnement où elle a
été préparée) et très probablement plusieurs rounds de correction. Ne pas merger
sans build vert.

## Fait dans cette branche

1. **Cœur `esp_video` → upstream 2.2.0** (overlay complet) :
   - `src/**`, `include/**`, `private_include/**`, `Kconfig`, `CMakeLists.txt`,
     `sdkconfig.rename` remplacés par l'upstream 2.2.0.
   - Nouveaux fichiers ajoutés : `esp_video_jpeg_dec_device.c`,
     `esp_video_jpeg_enc_device.c`, `esp_video_device_common.c`,
     `esp_video_csi_format.c`.
   - Couche ESPHome **préservée** : `__init__.py`, `esp_video_component.cpp/.h`,
     `esp_video_build.py`, `esp_video_download.py`, `i2c_helper.h`, `exemples/`,
     READMEs.

2. **Fix #3 (fuite `frame_info` à la déconnexion UVC) ré-appliqué** au nouveau
   `src/device/esp_video_usb_uvc_device.c` 2.2.0 (libération dans le handler
   `UVC_HOST_DEVICE_DISCONNECTED` + garde avant le `malloc` de `uvc_video_init`).

3. **Dépendances build mises à jour** (`__init__.py`) :
   - `espressif/cmake_utilities` (`0.*`) ajouté — requis par le CMakeLists 2.2.0
     qui utilise `idf_component_optional_requires()`.
   - `espressif/usb_host_uvc` bumpé `2.4.1` → `2.5.0` (requis par esp_video 2.2.0).

4. **Consommateur UVC adapté** (`esp_cam_sensor_camera.cpp`) : en 2.2.0 le MJPEG
   des caméras UVC est exposé en `V4L2_PIX_FMT_JPEG` (et non `V4L2_PIX_FMT_MJPEG`).
   La négociation/conversion accepte désormais les deux fourcc.

5. **Fichiers youkorr restaurés** (l'overlay les avait écrasés) — conservés mais
   **dormants** (PAS recâblés dans le CMakeLists 2.2.0 pour éviter des symboles
   dupliqués) : `src/embedded_ov02c10_ipa_config_json.c`,
   `src/embedded_ov5647_ipa_config_json.c` (ton tuning couleur IPA),
   `src/esp_video_isp_stubs.c`, `private_include/esp_video_version.h`. Le tuning
   n'est donc **pas perdu** mais **pas actif** tant que le sous-système IPA n'est
   pas réintégré. (`src/device/esp_video_jpeg_device.c` reste supprimé : remplacé
   par le split `jpeg_dec`/`jpeg_enc` upstream.)

6. **Shim de compatibilité** `src/esp_video_youkorr_compat.c` + prototype ajouté à
   `include/esp_video_init.h` : fournit `esp_video_reconfigure_isp_pipeline()` en
   **no-op** (le consommateur l'appelle après un format custom OV5647). Le seul
   autre symbole « manquant » détecté, `esp_video_init_task_core0`, est une
   fonction statique locale au wrapper (faux positif). → le wrapper + le
   consommateur **compilent/linkent** au niveau symboles esp_video.
   ⚠️ Conséquence : le re-init ISP par capteur (formats custom OV5647) est
   **neutralisé** tant que le contrôleur IPA n'est pas porté.

## Compatibilité vérifiée (lecture de code)

- `esp_video_init_config_t` : les champs `.csi` et `.usb_uvc` utilisés par le
  wrapper ESPHome **existent toujours** en 2.2.0 → `esp_video_component.cpp`
  reste compatible au niveau API esp_video (nouveau champ optionnel
  `usb.peripheral_map`, zéro-initialisé, sans impact).
- `esp_video_init_usb_uvc_config_t.{uvc,usb}` : sous-champs inchangés.

## RESTE À FAIRE / risques (à traiter avec les logs de build)

1. **Cascade NON faite volontairement** : `esp_cam_sensor` (2.2.0) et `esp_ipa`
   (2.1.0) sont **restés sur le fork** pour préserver ton tuning (couleurs, formats
   natifs SC202CS/OV5647/OV02C10, IPA JSON). esp_video 2.2.0 appelle des API
   `esp_cam_sensor`/`esp_ipa` qui peuvent avoir changé → **erreurs de compilation
   probables** à résoudre (soit migrer aussi ces composants, soit patcher les
   points d'appel). C'est le principal point d'itération.
2. **Breaking changes formats (2.0.0)** :
   - RGB565 scindé en `V4L2_PIX_FMT_RGB565` (LE) / `V4L2_PIX_FMT_RGB565X` (BE) —
     vérifier l'ordre d'octets à l'affichage (couleurs).
   - `V4L2_PIX_FMT_YUV422P` supprimé → utiliser YUYV/UYVY (le consommateur UVC
     utilise déjà YUYV).
3. **Nouvelles dépendances cœur** : `lwip` et `cmake_utilities` (gérées), à
   confirmer dans le build.
4. **Formats custom** (`sc202cs_custom_formats.h`, `ov5647_custom_formats.h`,
   `ov02c10_custom_formats.h`) : référencent des structs `esp_cam_sensor` — à
   revérifier si esp_cam_sensor est migré ensuite.

## Conclusions empiriques de compilation (ESP32-P4, ESP-IDF 5.5.4)

La branche a été **réellement compilée** (toolchain GCC 14.2) en itérant sur les erreurs.
Corrections appliquées au cœur esp_video 2.2.0 :

1. `esp_video_component.cpp` : `esp_video_init_csi_config_t` n'a plus `xclk_pin`/`xclk_freq`
   en 2.2.0 (lignes retirées — ignorées en MIPI-CSI de toute façon).
2. `esp_video_init.c` : itération du registre de détection capteurs adaptée au mécanisme
   **tableau** du fork (`__esp_cam_sensor_detect_fn_array_start[]`) au lieu du `&start`
   linker-section de l'upstream.
3. `CMakeLists.txt` + `__init__.py` : suppression de la dépendance **cmake_utilities**
   (retirée de github, indisponible hors registre) — liaison directe d'esp_ipa/
   esp_driver_isp/esp_driver_jpeg + `ESP_VIDEO_VER_{MAJOR,MINOR,PATCH}` définis en flags.

**BLOCAGE DE FOND (prouvé au build) — sous-système ISP/IPA non portable tel quel :**

- `esp_video_isp_pipeline.c` **2.2.0** appelle l'**API esp_ipa 2.1.0** (`esp_ipa_stats_af_t`,
  `esp_ipa_gamma_t.{flags,red,green,blue}`, `IPA_STATS_FLAGS_AF`, `IPA_METADATA_FLAGS_*`,
  `esp_ipa_awb_range_t`, `esp_ipa_pipeline_create`…) — **absente du fork esp_ipa** (~40 erreurs).
- À l'inverse, le `esp_video_isp_pipeline.c` **youkorr** exige des champs/types **internes
  youkorr** absents du cœur 2.2.0 : `esp_video_isp_config_t.sensor_name`,
  `esp_video_csi_state_t`, `bypass_isp`, et tout `esp_video_isp_stubs.c`.

Autrement dit : la couche de tuning youkorr (pipeline ISP + esp_ipa + formats capteurs,
dont **ov02c10 qui n'existe PAS dans l'upstream 2.2.0**) est **tissée dans les internes
d'esp_video**. Une migration 2.2.0 **complète** = **réécrire** le contrôleur de pipeline
ISP sur l'API esp_ipa 2.1.0 + ré-ajouter ov02c10 et les formats custom sur l'esp_cam_sensor
2.2.0 restructuré. C'est une **réécriture** de la couche fork, pas un portage.

**Recommandation** : la branche `claude/usb-uvc-consumer-integration-gnbflf` (cœur
esp_video **1.x** + fork) **compile** (build ESP32-P4 vert vérifié) et reste la base
fonctionnelle. La 2.2.0 n'apporte surtout que le support IDF 6.0 ; or le fork compile déjà
sur IDF 5.5.4. À réécrire seulement si IDF 6.0 devient indispensable.

## Réversibilité

Tout est isolé sur `claude/esp-video-2.2.0-migration`. La branche
`claude/usb-uvc-consumer-integration-gnbflf` (UVC + fix includes, **build vérifié**)
n'est pas touchée et reste la base stable.
