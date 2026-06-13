# Prompt à coller dans la nouvelle session (scopée sur le fork)

> Prérequis : la nouvelle session doit avoir DANS SON PÉRIMÈTRE les deux dépôts
> **youkorr/esphome** (ton fork d'ESPHome) et **youkorr/esphome_esp-video**.
> Copie le texte ci-dessous tel quel.

---

Contexte : je veux contribuer un nouveau composant caméra à ESPHome. Tout le
code est déjà préparé dans le dépôt **youkorr/esphome_esp-video**, sur la branche
**`upstream/esphome-esp_video_camera`**, dans le dossier **`upstream/`**. Lis
d'abord `upstream/README.md`, puis `UPSTREAMING.md` et `GAP_ANALYSIS.md` (sur la
branche `claude/esp-video-home-assistant-7g8a1n`) pour le contexte complet.

Ta tâche, dans mon fork **youkorr/esphome** :

1. Crée une branche **`esp_video_camera`** à partir de **`dev`** (la branche de
   développement d'ESPHome ; ajoute le remote `upstream = esphome/esphome` et
   `git fetch upstream` si besoin pour partir de `upstream/dev`). Ne touche JAMAIS
   à `main`.

2. Copie les fichiers depuis youkorr/esphome_esp-video
   (branche `upstream/esphome-esp_video_camera`, dossier `upstream/`) vers le fork :
   - `upstream/esphome/components/esp_video_camera/`  →  `esphome/components/esp_video_camera/`
     (`__init__.py`, `esp_video_camera.h`, `esp_video_camera.cpp`, `i2c_helper.h`)
   - `upstream/tests/components/esp_video_camera/test.esp32-p4-idf.yaml`
     →  `tests/components/esp_video_camera/test.esp32-p4-idf.yaml`
   - Ajoute la ligne de `upstream/CODEOWNERS.snippet`
     (`esphome/components/esp_video_camera/* @youkorr`) dans le `CODEOWNERS` du
     fork, à l'ordre alphabétique.

3. Commit avec un message clair et **push** la branche `esp_video_camera` sur le
   fork (`origin`).

4. Je n'ai pas accès en écriture à `esphome/esphome` : NE tente PAS d'ouvrir la PR
   là-bas. À la place, donne-moi le **lien « Compare & pull request »** vers
   `esphome:dev` (https://github.com/esphome/esphome/compare/dev...youkorr:esp_video_camera?expand=1),
   et la **description de PR** prête à coller (tu la trouveras dans
   `upstream/PR_DESCRIPTION.md` du dépôt source).

5. Liste-moi les **`TODO(upstream)`** restant dans le code (cherche ce mot) — ce
   sont les points à valider sur une vraie carte ESP32-P4 avant que ça compile :
   versions des composants managés (`ref=`), clés `sdkconfig` (CONFIG_*), et la
   récupération du handle I²C.

Important : c'est un nouveau composant caméra. ESPHome a déjà un framework caméra
(PR #7639) ; ce composant est un **backend `esp_video` complémentaire** (ISP/IPA,
H.264, USB-UVC, capteurs SC2336/OV02C10). Garde ce cadrage.
