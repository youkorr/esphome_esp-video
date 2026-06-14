# Roadmap d'upstreaming — esp_video / ESP32-P4

Notes de plan pour faire entrer (ou non) les composants dans ESPHome upstream.

## ⚠️ Mise à jour 2026-06-14 — vérification + réécriture contre esp_video 2.2.0 (sans hardware)

Vérifié en lisant la source officielle `espressif/esp-video-components` (master).

### Corrigé dans la PR (commits poussés)
- **Versions réelles** des composants managés : `esp_video` **2.2.0** (tire tout le
  reste : esp_cam_sensor 2.2.*, esp_ipa 2.1.*, esp_sccb_intf >=0.0.5, esp_h264 sur
  P4). `usb_host_uvc` **2.5.*** si UVC. (Mes valeurs 1.4.0/1.0.0/2.4.1 étaient fausses.)
  esp_video 2.2.0 exige **IDF >= 5.4** ; ESPHome embarque **5.5.4** → OK.
- **Erreur de compile corrigée** : `csi_config.xclk_pin/xclk_freq` n'existent plus
  dans `esp_video_init_csi_config_t` en 2.x (XCLK via LEDC). Lignes retirées.
- **OV02C10 retiré** : pas dans `esp_cam_sensor` 2.2.0 (28 capteurs, pas lui — c'est
  un driver custom de ce dépôt). Upstream = SC202CS / OV5647 / SC2336.
- **Clés Kconfig corrigées** : `CONFIG_CAMERA_<s>_AUTO_DETECT` n'existe pas ;
  c'est `..._AUTO_DETECT_MIPI_INTERFACE_SENSOR`. Les `CONFIG_ESP_VIDEO_ENABLE_*`
  sont confirmées présentes.

### 🟡 Pipeline JPEG → Home Assistant — RÉÉCRIT (M2M), à valider sur P4
- `/dev/video10` (JPEG) est un device **M2M** (memory-to-memory), pas une capture simple.
  L'ancien code (capture simple sur `/dev/video10`) ne pouvait pas marcher.
- **Réécrit** d'après l'exemple officiel `esp_video/examples/image_storage/sd_card` :
  - `device: jpeg` → 2 devices : capture **RGB565** sur `/dev/video0` (capteur/ISP),
    puis on **donne** chaque frame à la file OUTPUT de l'encodeur (USERPTR) et on **lit**
    le JPEG sur sa file CAPTURE (MMAP). fd JPEG ouvert **bloquant** (DQBUF attend l'encode).
  - `device: uvc` / `/dev/videoN` → capture simple inchangée (sort déjà du MJPEG).
  - **Résolution** réglée sur `/dev/video0` (le bon device), taille négociée relue et
    passée au format OUTPUT de l'encodeur. → le choix `resolution:` agit vraiment.
- ✅ Passe tout le statique (clang-format, ci-custom, ruff, import).
- ⚠️ **Non compilé / non exécuté ici** : le handshake M2M (ordre QBUF/DQBUF entre les
  2 files) doit être validé sur une **vraie ESP32-P4**. Code architecturalement juste,
  mais pas prouvé fonctionnel.

---

## Périmètre : une PR = un composant

| Fonctionnalité | Cible | Statut |
|---|---|---|
| Caméra → Home Assistant (`esp_video_camera`, JPEG) | upstream | 🟡 PR ouverte, pipeline M2M réécrit, test config-only |
| Drivers capteurs (SC202CS/OV5647/SC2336) | upstream (dépendance de esp_video) | 🟢 |
| OV02C10 | external (driver custom) | 🔴 pas upstream — à contribuer à esp_cam_sensor d'abord |
| `esp_cam_sensor` (contrôle direct + PPA) | 2e PR séparée, plus tard | ⚪ |
| `lvgl_camera_display` (canvas LVGL) | reste external | 🔴 bloqué (PPA pas dans lvgl upstream) |

## esp_video_camera — liens
- PR code : https://github.com/esphome/esphome/pull/16944 (branche fork `esp_video_camera`, base `dev`)
- PR doc : https://github.com/esphome/esphome.io/pull/6787 (`components/esp_video_camera.mdx`, base `next`)
- CI : verte (lint/clang-tidy/config). Test = **config-only** (`validate.esp32-p4-idf.yaml`).

## Étapes restantes (nécessitent une vraie ESP32-P4)
1. `esphome compile` du test → valider la compilation et **le handshake M2M JPEG**.
2. Corriger les détails runtime du pipeline M2M si besoin (ordre des files, buffers).
3. Rebasculer `validate.` → `test.` une fois la compilation OK.
4. Corriger doc + description de PR (enlever OV02C10).

## Règle d'or
- **Rien n'est perdu** : esp_cam_sensor / lvgl_camera_display / OV02C10 restent
  external et fonctionnels.
- **Je ne merge jamais** les PR upstream — ce sont les mainteneurs.
