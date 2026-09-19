#pragma once
#include "esphome.h"
#include <vector>

namespace esphome {

class UniversalHID {
 public:
  void handle_report(const std::vector<uint8_t>& bytes) {
    if (bytes.size() == 0) return;

    // --- MANETTE NVIDIA SHIELD ---
    // Les rapports font 33 octets et commencent par 0x01
    if (bytes.size() == 33 && bytes[0] == 0x01) {
      handle_nvidia_shield(bytes);
      return;
    }

    // --- TELECOMMANDE STANDARD (Mode Clavier / Flèches) ---
    // La plupart des télécommandes se font passer pour un clavier USB (ID 1 + 8 octets)
    if (bytes.size() == 9 && bytes[0] == 0x01) {
      handle_standard_keyboard(bytes);
      return;
    }

    // --- TELECOMMANDE MEDIA (Volume, Lecture/Pause) ---
    // Les touches multimédias utilisent souvent l'ID 0x03 ou 0x04 avec moins d'octets
    if (bytes.size() >= 3 && (bytes[0] == 0x03 || bytes[0] == 0x04)) {
      handle_media_remote(bytes);
      return;
    }
  }

 private:
  bool a_was_pressed_{false}, b_was_pressed_{false}, x_was_pressed_{false}, y_was_pressed_{false};
  uint8_t dpad_last_{0x08};
  std::vector<uint8_t> last_keys_;

  void handle_nvidia_shield(const std::vector<uint8_t>& bytes) {
    bool a_pressed = (bytes[3] & 0x01) != 0;
    bool b_pressed = (bytes[3] & 0x02) != 0;
    bool x_pressed = (bytes[3] & 0x04) != 0;
    bool y_pressed = (bytes[3] & 0x08) != 0;

    if (a_pressed && !a_was_pressed_) ESP_LOGI("universal_hid", "NVIDIA Shield : Bouton A / OK");
    if (b_pressed && !b_was_pressed_) ESP_LOGI("universal_hid", "NVIDIA Shield : Bouton B / Retour");
    if (x_pressed && !x_was_pressed_) ESP_LOGI("universal_hid", "NVIDIA Shield : Bouton X");
    if (y_pressed && !y_was_pressed_) ESP_LOGI("universal_hid", "NVIDIA Shield : Bouton Y");

    a_was_pressed_ = a_pressed; b_was_pressed_ = b_pressed;
    x_was_pressed_ = x_pressed; y_was_pressed_ = y_pressed;

    uint8_t dpad = (bytes[2] >> 4) & 0x0F;
    if (dpad != dpad_last_) {
      if (dpad == 0x00) ESP_LOGI("universal_hid", "NVIDIA Shield : Flèche HAUT");
      else if (dpad == 0x04) ESP_LOGI("universal_hid", "NVIDIA Shield : Flèche BAS");
      else if (dpad == 0x06) ESP_LOGI("universal_hid", "NVIDIA Shield : Flèche GAUCHE");
      else if (dpad == 0x02) ESP_LOGI("universal_hid", "NVIDIA Shield : Flèche DROITE");
      dpad_last_ = dpad;
    }
  }

  void handle_standard_keyboard(const std::vector<uint8_t>& bytes) {
    // Les octets 3 à 8 contiennent les touches actives. On cherche si de nouvelles touches sont pressées.
    for (size_t i = 3; i < 9; i++) {
      uint8_t key = bytes[i];
      if (key == 0) continue; // Pas de touche à cet emplacement

      // On vérifie si la touche vient juste d'être pressée
      bool already_pressed = false;
      for (uint8_t last_key : last_keys_) {
        if (key == last_key) already_pressed = true;
      }

      if (!already_pressed) {
        if (key == 0x52) ESP_LOGI("universal_hid", "Télécommande : Flèche HAUT");
        else if (key == 0x51) ESP_LOGI("universal_hid", "Télécommande : Flèche BAS");
        else if (key == 0x50) ESP_LOGI("universal_hid", "Télécommande : Flèche GAUCHE");
        else if (key == 0x4F) ESP_LOGI("universal_hid", "Télécommande : Flèche DROITE");
        else if (key == 0x28) ESP_LOGI("universal_hid", "Télécommande : Bouton OK / ENTER");
        else if (key == 0x29) ESP_LOGI("universal_hid", "Télécommande : Bouton RETOUR");
        else ESP_LOGI("universal_hid", "Télécommande : Touche inconnue (code 0x%02X)", key);
      }
    }

    // On sauvegarde l'état actuel pour le prochain rapport
    last_keys_.clear();
    for (size_t i = 3; i < 9; i++) {
      if (bytes[i] != 0) last_keys_.push_back(bytes[i]);
    }
  }

  void handle_media_remote(const std::vector<uint8_t>& bytes) {
     // Les rapports media varient énormément selon la marque de la télécommande.
     // On affiche temporairement l'octet brut pour pouvoir facilement le mapper ensuite !
     if (bytes.size() >= 3) {
       ESP_LOGI("universal_hid", "Télécommande Media (Volume, Play) envoyée ! Code: 0x%02X", bytes[1]);
     }
  }
};

UniversalHID* univ_hid = new UniversalHID();

} // namespace esphome
