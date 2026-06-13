# USB-UVC — caméra USB externe sur l'ESP32-P4

Le composant `esp_video` peut piloter une **caméra USB (classe UVC)** branchée sur
le port **USB-OTG** de l'ESP32-P4, en plus (ou à la place) d'un capteur MIPI-CSI.
La fonction est **désactivée par défaut** : les configurations MIPI-CSI existantes
ne changent pas et ne paient aucun surcoût.

> ⚠️ **Statut : non testé sur matériel.** Le code compile la pile USB-Host +
> le driver UVC d'Espressif et démarre l'énumération ; validez sur votre carte.

---

## 1. Activation

```yaml
esp_video:
  i2c_id: bsp_bus
  xclk_pin: GPIO36
  enable_jpeg: true
  enable_isp: true
  enable_uvc: true        # <-- active le host USB-UVC
```

Quand `enable_uvc: true` :

1. Le flag `CONFIG_ESP_VIDEO_ENABLE_USB_UVC_VIDEO_DEVICE` est défini et le
   composant managé **`espressif/usb_host_uvc`** (driver natif 2.x, support
   ESP32-P4) est tiré automatiquement — il fournit aussi le glue
   `esp_private/uvc_esp_video.h` dont dépend le driver.
2. La **pile USB-Host** et le **driver UVC** sont installés au démarrage par
   `esp_video_init()` (esp_video possède la USB Host Lib : `init_usb_host_lib = true`).
3. Une caméra UVC branchée est **énumérée comme un device V4L2** : `/dev/videoN`.

| Option | Défaut | Notes |
|--------|--------|-------|
| `enable_uvc` | `false` | `true` = compile et démarre le host USB-UVC. |

Paramètres internes appliqués (dans `esp_video_component.cpp`, modifiables si besoin) :
nombre de caméras UVC = 1, tâches host/UVC à 4096 octets de pile, priorité 5,
sans affinité de cœur.

---

## 2. Prérequis matériel

- **Port USB en mode Host / OTG.** L'ESP32-P4 doit fournir le VBUS (5 V) à la
  caméra. Selon la carte, cela demande un câble/adaptateur OTG et parfois une
  alimentation externe — beaucoup de webcams UVC consomment plusieurs centaines
  de mA.
- **Caméra compatible UVC** (la grande majorité des webcams USB). Les formats
  négociés dépendent de la caméra (souvent MJPEG et/ou YUY2).
- N'entre **pas** en conflit avec le WiFi C6 (SDIO) ni avec le MIPI-CSI : ce sont
  des périphériques distincts.

---

## 3. Coût en ressources

- À l'arrêt (`enable_uvc: false`) : **zéro** — le driver UVC compile en unité vide
  (entièrement gardé par `#if`), aucune pile USB n'est liée.
- Activé : deux tâches FreeRTOS légères (USB Host Lib + driver UVC) et les buffers
  de frames de la caméra (en PSRAM). Aucune incidence sur le pipeline MIPI-CSI/ISP
  ni sur le moteur JPEG.

---

## 4. Consommer le flux UVC

Activer l'UVC rend la caméra **disponible** comme device V4L2 (`/dev/videoN`).

> **Limitation actuelle :** le composant `esp_cam_sensor` (celui pointé par
> `camera_id:` de `face2face`) est **spécifique MIPI-CSI** — il ne pilote pas
> directement le node UVC. Pour consommer un flux UVC il faut un consommateur qui
> ouvre `/dev/videoN` (V4L2 : `VIDIOC_REQBUFS` / `VIDIOC_QBUF` / `VIDIOC_DQBUF`).
> L'intégration « sélection de la caméra par chemin » côté `esp_cam_sensor` /
> `face2face` reste à faire — ouvrez une issue si vous en avez besoin.

---

## 5. Logs attendus au démarrage

```
[esp_video] USB-UVC host enabled: external USB cameras will appear as /dev/videoN
[esp_video_init] Installing USB Host
[esp_video_init] USB Host installed
```
Au branchement de la caméra, le driver UVC l'énumère et crée le device V4L2.

---

## 6. Dépannage

| Symptôme | Piste |
|----------|-------|
| Build : erreurs sur `usb/uvc_host.h` ou `uvc_esp_video.h` | Décalage de version de `usb_host_uvc` — ajustez `ref="2.4.1"` dans `components/esp_video/__init__.py`. |
| La caméra n'est pas détectée | Vérifier le mode Host/OTG et l'alimentation VBUS (5 V) de la caméra. |
| `Failed to install USB Host driver` | Un autre composant a déjà installé la USB Host Lib, ou le port n'est pas en mode host. |
| Image absente côté `face2face` | Attendu pour l'instant : le node UVC n'est pas encore consommé par `esp_cam_sensor` (voir §4). |

---

## 7. Références

- Driver UVC Host : <https://components.espressif.com/components/espressif/usb_host_uvc>
- ESP-IDF USB Host : <https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/api-reference/peripherals/usb_host.html>
- Driver dans ce composant : `src/device/esp_video_usb_uvc_device.c`
