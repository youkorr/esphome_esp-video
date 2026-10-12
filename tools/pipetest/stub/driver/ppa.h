#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include <cstddef>
#include <cstdint>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct ppa_client_t *ppa_client_handle_t;
typedef enum { PPA_OPERATION_SRM } ppa_operation_t;
typedef enum { PPA_DATA_BURST_LENGTH_64, PPA_DATA_BURST_LENGTH_128 } ppa_data_burst_length_t;
typedef struct { ppa_operation_t oper_type; uint32_t max_pending_trans_num; ppa_data_burst_length_t data_burst_length; } ppa_client_config_t;
typedef enum { PPA_SRM_COLOR_MODE_RGB888, PPA_SRM_COLOR_MODE_RGB565 } ppa_srm_color_mode_t;
typedef enum { PPA_SRM_ROTATION_ANGLE_0, PPA_SRM_ROTATION_ANGLE_90, PPA_SRM_ROTATION_ANGLE_180, PPA_SRM_ROTATION_ANGLE_270 } ppa_srm_rotation_angle_t;
typedef enum { PPA_TRANS_MODE_BLOCKING, PPA_TRANS_MODE_NON_BLOCKING } ppa_trans_mode_t;
typedef struct { const void *buffer; uint32_t pic_w, pic_h, block_w, block_h, block_offset_x, block_offset_y; ppa_srm_color_mode_t srm_cm; } ppa_in_pic_blk_config_t;
typedef struct { void *buffer; uint32_t buffer_size, pic_w, pic_h, block_offset_x, block_offset_y; ppa_srm_color_mode_t srm_cm; } ppa_out_pic_blk_config_t;
typedef struct { ppa_in_pic_blk_config_t in; ppa_out_pic_blk_config_t out; ppa_srm_rotation_angle_t rotation_angle; float scale_x, scale_y; ppa_trans_mode_t mode; } ppa_srm_oper_config_t;
int ppa_register_client(const ppa_client_config_t *, ppa_client_handle_t *);
int ppa_do_scale_rotate_mirror(ppa_client_handle_t, const ppa_srm_oper_config_t *);
#ifdef __cplusplus
}
#endif
