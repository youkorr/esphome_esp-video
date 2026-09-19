#pragma once
#include "esphome.h"
#include <vector>
#include "portall_bt.h"

namespace esphome {


class UniversalHID {
 public:
  void handle_report(const std::vector<uint8_t>& bytes) {
    if (bytes.size() == 0) return;

    // NVIDIA SHIELD (33 octets, ID 0x01)
    if (bytes.size() == 33 && bytes[0] == 0x01) {
      handle_nvidia_shield(bytes);
      return;
    }

    // CLAVIER/TELECOMMANDE STANDARD (9 octets, ID 0x01)
    if (bytes.size() == 9 && bytes[0] == 0x01) {
      handle_standard_keyboard(bytes);
      return;
    }

    // TELECOMMANDE MEDIA (ID 0x03 ou 0x04)
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
      if (dpad == 0x00) ESP_LOGI("universal_hid", "NVIDIA Shield : Fleche HAUT");
      else if (dpad == 0x04) ESP_LOGI("universal_hid", "NVIDIA Shield : Fleche BAS");
      else if (dpad == 0x06) ESP_LOGI("universal_hid", "NVIDIA Shield : Fleche GAUCHE");
      else if (dpad == 0x02) ESP_LOGI("universal_hid", "NVIDIA Shield : Fleche DROITE");
      dpad_last_ = dpad;
    }
  }

  void handle_standard_keyboard(const std::vector<uint8_t>& bytes) {
    for (size_t i = 3; i < 9; i++) {
      uint8_t key = bytes[i];
      if (key == 0) continue;

      bool already_pressed = false;
      for (uint8_t last_key : last_keys_) {
        if (key == last_key) already_pressed = true;
      }

      if (!already_pressed) {
        if (key == 0x52) ESP_LOGI("universal_hid", "Telecommande : Fleche HAUT");
        else if (key == 0x51) ESP_LOGI("universal_hid", "Telecommande : Fleche BAS");
        else if (key == 0x50) ESP_LOGI("universal_hid", "Telecommande : Fleche GAUCHE");
        else if (key == 0x4F) ESP_LOGI("universal_hid", "Telecommande : Fleche DROITE");
        else if (key == 0x28) ESP_LOGI("universal_hid", "Telecommande : Bouton OK / ENTER");
        else if (key == 0x29) ESP_LOGI("universal_hid", "Telecommande : Bouton RETOUR");
        else ESP_LOGI("universal_hid", "Telecommande : Touche inconnue (code 0x%02X)", key);
      }
    }

    last_keys_.clear();
    for (size_t i = 3; i < 9; i++) {
      if (bytes[i] != 0) last_keys_.push_back(bytes[i]);
    }
  }

  void handle_media_remote(const std::vector<uint8_t>& bytes) {
     if (bytes.size() >= 3) {
       ESP_LOGI("universal_hid", "Telecommande Media (Volume, Play) envoyee ! Code: 0x%02X", bytes[1]);
     }
  }
};

static UniversalHID* univ_hid = new UniversalHID();

} // namespace esphome
