/* The read-only drive that hands the PC its half of the job.
 *
 * USB defines no display class, so a program has to run on the PC for any of
 * this to work -- that is not a shortcoming of this component, it is why
 * DisplayLink ships a driver and Espressif ship one too. What can be fixed is
 * having to go and find that program: mass storage IS a standard class, so the
 * board can simply carry it. Plug the board in, a drive appears, the sender is
 * on it.
 *
 * No disk image is stored. A FAT12 volume is almost entirely structure and
 * zeroes, so the sectors are answered as the host asks for them and only the
 * script itself takes up flash.
 */

#include "portall.h"

#include "esphome/core/log.h"

#include <cstdio>
#include <cstring>

extern "C" {
#include "tusb.h"
}

#if CFG_TUD_MSC

namespace esphome {
namespace portall {

static const char *const TAG = "portall.drive";

namespace {

constexpr uint16_t SECTOR_SIZE = 512;
// One reserved (the boot sector), one FAT, one root directory. A single 512
// byte FAT holds 341 twelve-bit entries, so the volume can reach 170 KB before
// a second one is needed -- far past anything this carries.
constexpr uint32_t SECTOR_BOOT = 0;
constexpr uint32_t SECTOR_FAT = 1;
constexpr uint32_t SECTOR_ROOT = 2;
constexpr uint32_t SECTOR_DATA = 3;
// The cluster numbering starts at two; zero and one are the two reserved FAT
// entries.
constexpr uint16_t FIRST_CLUSTER = 2;
constexpr uint16_t DIRECTORY_ENTRY_SIZE = 32;
constexpr uint16_t ROOT_ENTRIES = 16;
// Small enough to be honest about, large enough that no host balks at it.
constexpr uint16_t MINIMUM_CLUSTERS = 128;

constexpr uint8_t ATTR_READ_ONLY = 0x01;
constexpr uint8_t ATTR_VOLUME_LABEL = 0x08;
constexpr uint8_t ATTR_ARCHIVE = 0x20;

/// One file in the root directory, laid out over a run of clusters.
struct DriveFile {
  const char name[12];  // 8.3, space padded, no dot -- the on-disk form
  const uint8_t *data;
  size_t length;
  uint16_t first_cluster;
  uint16_t cluster_count;
};

// Two files: the sender, and a note saying what to do with it. Long file names
// need a second directory entry each and a checksum over the short one; 8.3 is
// what a volume this small is for.
constexpr uint8_t FILE_COUNT = 2;
DriveFile g_files[FILE_COUNT] = {
    {"UDISP   PY ", nullptr, 0, 0, 0},
    {"README  TXT", nullptr, 0, 0, 0},
};

// Written once at setup, from the configured geometry, so the instructions
// cannot disagree with the firmware.
char g_readme[512];

uint16_t g_total_clusters = MINIMUM_CLUSTERS;
bool g_ready = false;

uint16_t clusters_for(size_t length) { return (uint16_t) ((length + SECTOR_SIZE - 1) / SECTOR_SIZE); }

void put_u16(uint8_t *at, uint16_t value) {
  at[0] = (uint8_t) (value & 0xFF);
  at[1] = (uint8_t) (value >> 8);
}

void put_u32(uint8_t *at, uint32_t value) {
  at[0] = (uint8_t) (value & 0xFF);
  at[1] = (uint8_t) ((value >> 8) & 0xFF);
  at[2] = (uint8_t) ((value >> 16) & 0xFF);
  at[3] = (uint8_t) ((value >> 24) & 0xFF);
}

/// Twelve-bit FAT entries: two of them share three bytes.
void fat_set(uint8_t *fat, uint16_t cluster, uint16_t value) {
  const uint32_t at = cluster + (cluster / 2);
  if ((cluster & 1) == 0) {
    fat[at] = (uint8_t) (value & 0xFF);
    fat[at + 1] = (uint8_t) ((fat[at + 1] & 0xF0) | ((value >> 8) & 0x0F));
  } else {
    fat[at] = (uint8_t) ((fat[at] & 0x0F) | ((value & 0x0F) << 4));
    fat[at + 1] = (uint8_t) ((value >> 4) & 0xFF);
  }
}

void build_boot_sector(uint8_t *sector) {
  memset(sector, 0, SECTOR_SIZE);
  // A jump nobody executes, but a volume without one is not recognised.
  sector[0] = 0xEB;
  sector[1] = 0x3C;
  sector[2] = 0x90;
  memcpy(sector + 3, "MSDOS5.0", 8);
  put_u16(sector + 11, SECTOR_SIZE);
  sector[13] = 1;  // sectors per cluster
  put_u16(sector + 14, 1);
  sector[16] = 1;  // one FAT; a second would only be a copy of this one
  put_u16(sector + 17, ROOT_ENTRIES);
  put_u16(sector + 19, (uint16_t) (SECTOR_DATA + g_total_clusters));
  sector[21] = 0xF8;  // fixed disk
  put_u16(sector + 22, 1);
  put_u16(sector + 24, 1);
  put_u16(sector + 26, 1);
  put_u32(sector + 28, 0);
  put_u32(sector + 32, 0);
  sector[36] = 0x80;
  sector[38] = 0x29;  // an extended boot record follows
  put_u32(sector + 39, 0x45535000);
  memcpy(sector + 43, "ESPHOME    ", 11);
  memcpy(sector + 54, "FAT12   ", 8);
  sector[510] = 0x55;
  sector[511] = 0xAA;
}

void build_fat_sector(uint8_t *sector) {
  memset(sector, 0, SECTOR_SIZE);
  // Entry 0 repeats the media descriptor, entry 1 is the end-of-chain mark.
  sector[0] = 0xF8;
  sector[1] = 0xFF;
  sector[2] = 0xFF;

  for (uint8_t f = 0; f < FILE_COUNT; f++) {
    const DriveFile &file = g_files[f];
    for (uint16_t i = 0; i < file.cluster_count; i++) {
      const uint16_t cluster = file.first_cluster + i;
      const bool last = i + 1 == file.cluster_count;
      fat_set(sector, cluster, last ? 0xFFF : (uint16_t) (cluster + 1));
    }
  }
}

void build_root_sector(uint8_t *sector) {
  memset(sector, 0, SECTOR_SIZE);

  uint8_t *entry = sector;
  memcpy(entry, "ESPHOME    ", 11);
  entry[11] = ATTR_VOLUME_LABEL;
  entry += DIRECTORY_ENTRY_SIZE;

  for (uint8_t f = 0; f < FILE_COUNT; f++) {
    const DriveFile &file = g_files[f];
    if (file.length == 0)
      continue;
    memcpy(entry, file.name, 11);
    entry[11] = ATTR_READ_ONLY | ATTR_ARCHIVE;
    // 2021-01-01 00:00, so the files do not appear dateless. The board has no
    // idea what time it is when this is built.
    put_u16(entry + 22, 0);
    put_u16(entry + 24, (41 << 9) | (1 << 5) | 1);
    put_u16(entry + 26, file.first_cluster);
    put_u32(entry + 28, (uint32_t) file.length);
    entry += DIRECTORY_ENTRY_SIZE;
  }
}

/// Fills one sector of the data area, or leaves it zeroed if no file lives there.
void build_data_sector(uint8_t *sector, uint32_t lba) {
  memset(sector, 0, SECTOR_SIZE);
  const uint16_t cluster = (uint16_t) (lba - SECTOR_DATA + FIRST_CLUSTER);

  for (uint8_t f = 0; f < FILE_COUNT; f++) {
    const DriveFile &file = g_files[f];
    if (file.length == 0 || cluster < file.first_cluster || cluster >= file.first_cluster + file.cluster_count)
      continue;
    const size_t offset = (size_t) (cluster - file.first_cluster) * SECTOR_SIZE;
    size_t chunk = file.length - offset;
    if (chunk > SECTOR_SIZE)
      chunk = SECTOR_SIZE;
    memcpy(sector, file.data + offset, chunk);
    return;
  }
}

}  // namespace

void Portall::setup_sender_drive_() {
  if (this->sender_script_ == nullptr || this->sender_script_len_ == 0) {
    ESP_LOGW(TAG, "No sender script was compiled in; the drive will not appear");
    return;
  }

  snprintf(g_readme, sizeof(g_readme),
           "This board is a second screen for this computer.\r\n"
           "\r\n"
           "USB has no display class, so nothing can draw on it until a program\r\n"
           "on this computer sends it a picture. That program is UDISP.PY, here\r\n"
           "on this drive. It needs Python, and:\r\n"
           "\r\n"
           "    pip install pyusb mss pillow libusb-package\r\n"
           "\r\n"
           "Then, with this drive open as D: (whatever letter it got):\r\n"
           "\r\n"
           "    python D:\\UDISP.PY --width %u --height %u\r\n"
           "\r\n"
           "It runs straight off this drive; nothing needs copying first. Add\r\n"
           "--install-startup once and it starts at every login instead, from a\r\n"
           "copy it puts in your own directory, waiting for the board rather\r\n"
           "than failing when it is not plugged in.\r\n"
           "\r\n"
           "The rotation is set on the board, not here.\r\n",
           (unsigned) this->width_, (unsigned) this->height_);

  g_files[0].data = this->sender_script_;
  g_files[0].length = this->sender_script_len_;
  g_files[1].data = (const uint8_t *) g_readme;
  g_files[1].length = strlen(g_readme);

  uint16_t next = FIRST_CLUSTER;
  for (auto &file : g_files) {
    file.first_cluster = next;
    file.cluster_count = clusters_for(file.length);
    next = (uint16_t) (next + file.cluster_count);
  }

  const uint16_t used = (uint16_t) (next - FIRST_CLUSTER);
  g_total_clusters = used > MINIMUM_CLUSTERS ? used : MINIMUM_CLUSTERS;
  g_ready = true;

  ESP_LOGCONFIG(TAG, "Sender drive: %u KB, UDISP.PY is %u bytes",
                (unsigned) ((SECTOR_DATA + g_total_clusters) * SECTOR_SIZE / 1024),
                (unsigned) this->sender_script_len_);
}

}  // namespace portall
}  // namespace esphome

// ===========================================================================
// The mass-storage callbacks TinyUSB calls, which are plain C
// ===========================================================================
// The helpers above have internal linkage but are still named inside their
// enclosing namespace, which is all these need.
using namespace esphome::portall;  // NOLINT(build/namespaces)

extern "C" void tud_msc_inquiry_cb(uint8_t lun, uint8_t vendor_id[8], uint8_t product_id[16], uint8_t product_rev[4]) {
  (void) lun;
  memcpy(vendor_id, "ESPHome ", 8);
  memcpy(product_id, "Sender          ", 16);
  memcpy(product_rev, "1.0 ", 4);
}

extern "C" bool tud_msc_test_unit_ready_cb(uint8_t lun) {
  (void) lun;
  return g_ready;
}

extern "C" void tud_msc_capacity_cb(uint8_t lun, uint32_t *block_count, uint16_t *block_size) {
  (void) lun;
  *block_count = SECTOR_DATA + g_total_clusters;
  *block_size = SECTOR_SIZE;
}

extern "C" bool tud_msc_start_stop_cb(uint8_t lun, uint8_t power_condition, bool start, bool load_eject) {
  (void) lun;
  (void) power_condition;
  (void) start;
  (void) load_eject;
  return true;
}

extern "C" bool tud_msc_is_writable_cb(uint8_t lun) {
  (void) lun;
  return false;
}

extern "C" int32_t tud_msc_read10_cb(uint8_t lun, uint32_t lba, uint32_t offset, void *buffer, uint32_t bufsize) {
  (void) lun;
  if (!g_ready || lba >= (uint32_t) (SECTOR_DATA + g_total_clusters))
    return -1;

  // Synthesise the whole sector, then hand back the slice that was asked for.
  // Hosts normally ask for all of it at once, but they are allowed not to.
  static uint8_t sector[SECTOR_SIZE];
  switch (lba) {
    case SECTOR_BOOT:
      build_boot_sector(sector);
      break;
    case SECTOR_FAT:
      build_fat_sector(sector);
      break;
    case SECTOR_ROOT:
      build_root_sector(sector);
      break;
    default:
      build_data_sector(sector, lba);
      break;
  }

  if (offset >= SECTOR_SIZE)
    return 0;
  uint32_t length = SECTOR_SIZE - offset;
  if (length > bufsize)
    length = bufsize;
  memcpy(buffer, sector + offset, length);
  return (int32_t) length;
}

extern "C" int32_t tud_msc_write10_cb(uint8_t lun, uint32_t lba, uint32_t offset, uint8_t *buffer, uint32_t bufsize) {
  (void) lun;
  (void) lba;
  (void) offset;
  (void) buffer;
  (void) bufsize;
  // Read-only, and said so in tud_msc_is_writable_cb. A host that tries anyway
  // gets a failure rather than silent success followed by a corrupt volume.
  return -1;
}

extern "C" int32_t tud_msc_scsi_cb(uint8_t lun, uint8_t const scsi_cmd[16], void *buffer, uint16_t bufsize) {
  (void) lun;
  (void) buffer;
  (void) bufsize;
  // Nothing beyond the commands TinyUSB answers itself.
  switch (scsi_cmd[0]) {
    case SCSI_CMD_PREVENT_ALLOW_MEDIUM_REMOVAL:
      return 0;
    default:
      tud_msc_set_sense(lun, SCSI_SENSE_ILLEGAL_REQUEST, 0x20, 0x00);
      return -1;
  }
}

#endif  // CFG_TUD_MSC
