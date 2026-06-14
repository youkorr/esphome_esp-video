# Roadmap d'upstreaming — esp_video / ESP32-P4

Notes de plan pour faire entrer (ou non) les composants dans ESPHome upstream.
Mis à jour le 2026-06-14.

## Périmètre : une PR = un composant

Les mainteneurs ESPHome refusent les grosses PR « tout-en-un ». On découpe.

| Fonctionnalité de mon écosystème | Cible | Statut |
|---|---|---|
| Caméra → Home Assistant (entité, stream JPEG) = **`esp_video_camera`** | **upstream ESPHome** | 🟢 PR ouverte |
| Drivers capteurs Espressif (SC2336, OV5647, OV02C10, SC202CS) | upstream (dépendance managée de `esp_video_camera`) | 🟢 inclus |
| **`esp_cam_sensor`** (mon composant : contrôle direct, RGB565, transforms PPA crop/rotate/mirror) | éventuelle 2e PR séparée | ⚪ plus tard |
| **`lvgl_camera_display`** (canvas LVGL + détection visage/YOLO/piéton) | reste **external** | 🔴 bloqué (voir plus bas) |

## 1) esp_video_camera — EN COURS

- **PR code** : https://github.com/esphome/esphome/pull/16944 (branche fork `esp_video_camera`, base `dev`)
- **PR doc** : https://github.com/esphome/esphome.io/pull/6787 (`src/content/docs/components/esp_video_camera.mdx`, base `next`)
- **CI** : verte (ruff, pylint, ci-custom, clang-tidy, validation de config).
- **Statut du test** : **config-only** (`tests/components/esp_video_camera/validate.esp32-p4-idf.yaml`) — la compilation firmware n'est PAS encore prouvée en CI.

### Ce qui reste à faire (sur vraie carte ESP32-P4)
1. `esphome compile` du test avec les **composants managés** (pas les sources vendorisées).
2. Confirmer / corriger :
   - les versions `ref=` des composants managés (`espressif/esp_video`, `esp_cam_sensor`, `esp_sccb_intf`, `esp_ipa`) — actuellement mis en `"*"` faute de pouvoir vérifier le registre ;
   - que les clés `CONFIG_*` (`add_idf_sdkconfig_option`) correspondent au Kconfig des composants managés (mon setup qui marche utilise des `-D` flags + sources vendorisées, PAS le registre — donc cette voie n'a jamais été validée) ;
   - la récupération du handle I²C (`i2c_master_get_bus_handle`).
3. Une fois la compilation OK : **renommer `validate.esp32-p4-idf.yaml` → `test.esp32-p4-idf.yaml`** pour réactiver la preuve de compile dans la CI.
4. Cocher honnêtement la checklist de la PR.

> ⚠️ Important : mon dépôt qui marche **vendorise** les sources Espressif et les compile via `-D` flags + `esp_video_build.py`. La PR upstream utilise des **composants managés du registre** — c'est une approche différente, jamais testée, à valider sur hardware.

## 2) esp_cam_sensor (composant ESPHome) — PLUS TARD

- Contrôle direct du capteur, sortie RGB565 (zéro-copie LVGL), transforms PPA.
- Chevauche `esp_video_camera` → clarifier le rôle de chacun **avec les mainteneurs** avant d'ouvrir une PR.
- À ne tenter **qu'après** que `esp_video_camera` soit mergé.

## 3) lvgl_camera_display — RESTE EXTERNAL

- Dépend de **mon fork `lvgl_9.5`** (`use_ppa`, `use_ppa_img`) — fonctionnalité **absente** du composant `lvgl` officiel d'ESPHome.
- Dépend aussi des modèles de détection (esp-dl) — peu probable upstream.
- **Bloquant** : tant que le PPA n'est pas dans le `lvgl` upstream, ce composant ne peut pas être upstreamé.
- Action : **le garder dans `youkorr/esphome_esp-video`** (il marche, les utilisateurs l'ont) et le mentionner dans la doc comme « affichage local via external component ».
- Chantier long si un jour : faire accepter le PPA dans LVGL upstream d'abord.

## Règle d'or

- **Rien n'est perdu** : `esp_cam_sensor` et `lvgl_camera_display` restent des external components fonctionnels dans ce dépôt.
- **Je ne merge jamais** les PR upstream — ce sont les mainteneurs qui révisent et mergent.
- Quand un mainteneur commente ou que j'ai un log de compile, je corrige sur la branche fork.
