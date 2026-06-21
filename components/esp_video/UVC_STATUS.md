# USB-UVC — État et reste à faire

Récapitulatif de l'intégration USB-UVC dans `esp_video` sur cette branche
(`claude/esp-video-params-dxca9c`). Sert de base aux correctifs restants.

> TL;DR : la **base build + énumération** est en place (à valider sur matériel),
> mais l'**image de bout en bout n'est pas encore câblée** : aucun consommateur
> ne lit le nœud V4L2 de la caméra UVC.

---

## 1. Les trois niveaux

| Niveau | Description | État |
|--------|-------------|------|
| 1. **Compilation** (`enable_uvc: true`) | le code UVC + host compile | ✅ corrigé — ⚠️ non testé sur matériel |
| 2. **Énumération** | la caméra USB apparaît comme `/dev/videoN` à la connexion | ✅ devrait marcher — ⚠️ non testé sur matériel |
| 3. **Image réelle** (affichage / stream) | un consommateur lit le flux et l'affiche/l'encode | ❌ **pas câblé** |

---

## 2. Ce qui a été corrigé sur cette branche

| # | Commit | Corrige |
|---|--------|---------|
| 1 | `project_include.cmake` | erreurs d'include `esp_video_init.h` / `sys/mman.h` (la colle ESPHome est compilée dans `__idf_src`, qui ne recevait pas les include dirs) |
| 2 | CMakeLists `optional_requires "usb" "usb_host_uvc"` | `usb/usb_host.h` introuvable (chemin CMake) quand `enable_uvc: true` |
| 3 | `__init__.py` : `-I.../managed_components/espressif__usb_host_uvc/include` + `CONFIG_USB_HOST_CONTROL_TRANSFER_MAX_SIZE=2048` | `usb/uvc_host.h` / `esp_private/uvc_esp_video.h` introuvables (chemin PlatformIO) + énumération des caméras à gros descripteurs |
| 4 | pin `usb_host_uvc` → `2.5.*` | alignement sur la paire validée par Espressif (esp-video pinne `2.5.*`) + fixes fiabilité (URB size, MJPEG SOI, bulk) |
| 5 | option `manage_usb_host` | intégration event-driven : possibilité de confier le bus au composant ESPHome `usb_host` (coexistence, mémoire libérée, hot-swap) |

---

## 3. Ce qui MANQUE (les correctifs à faire)

### 3.1 — BLOQUANT : consommateur du nœud UVC (niveau 3)

`components/esp_cam_sensor/esp_cam_sensor_camera.cpp` ouvre des nœuds **codés en
dur**, tous MIPI-CSI :

```cpp
ESP_VIDEO_MIPI_CSI_DEVICE_NAME   // /dev/video0
ESP_VIDEO_JPEG_DEVICE_NAME       // /dev/video10
ESP_VIDEO_H264_DEVICE_NAME       // /dev/video11
```

Il **n'ouvre jamais** le `/dev/videoN` créé par la caméra UVC → même énumérée,
la caméra ne produit aucune image dans le pipeline actuel.

**À faire :**
- Ajouter une sélection de source dans `esp_cam_sensor` (ex. `source: uvc` ou
  `device_path: /dev/videoN`).
- Ouvrir ce nœud, négocier le format **UVC** (souvent MJPEG/YUY2, ≠ MIPI-CSI),
  puis faire passer ses buffers dans le même chemin d'affichage/encodage.
- Gérer l'absence de caméra (nœud pas encore présent) et la (dé)connexion.

> C'est le morceau qui transforme « la caméra est détectée » en « tu vois
> l'image ». Tant qu'il n'est pas fait, `enable_uvc: true` ne sert qu'à monter
> le périphérique V4L2.

### 3.2 — Validation matérielle des niveaux 1-2

Aucun test sur carte réelle. À faire : flasher avec `enable_uvc: true` + une
caméra USB, vérifier dans les logs que `/dev/videoN` est créé à la connexion.

### 3.3 — Teardown mémoire sur déconnexion (amélioration)

Le callback de déconnexion UVC (`uvc_event_callback`, `UVC_HOST_DEVICE_DISCONNECTED`)
remet les index à zéro mais ne libère pas explicitement `frame_info`. À revoir
pour libérer les buffers UVC quand la caméra est débranchée (mémoire réellement
rendue, surtout en mode `manage_usb_host: false`).

### 3.4 — Garde-fou config `manage_usb_host: false` sans `usb_host:`

Aujourd'hui, `manage_usb_host: false` sans bloc `usb_host:` ne plante pas à la
compilation : `uvc_host_install` échoue silencieusement au runtime. À faire :
lever une erreur claire à la validation Python si `manage_usb_host: false` et
qu'aucun `usb_host:` n'est présent.

### 3.5 — Décision sur le défaut de `manage_usb_host`

Reste à `true` (esp_video possède le bus) pour ne pas casser l'existant. À
rebasculer vers `false` (modèle recommandé : coexistence/hot-swap) **une fois la
coexistence validée sur matériel**.

---

## 4. Ordre conseillé

1. Valider niveaux 1-2 sur carte (3.2).
2. Câbler le consommateur UVC (3.1) — le vrai déblocage.
3. Ajouter le garde-fou config (3.4) et le teardown mémoire (3.3).
4. Basculer le défaut de `manage_usb_host` (3.5) si la coexistence est OK.
