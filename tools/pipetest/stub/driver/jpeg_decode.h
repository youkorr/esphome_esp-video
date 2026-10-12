#pragma once
/* Stand-in for a workstation build of portall (tools/checkpipeline.py). */
#include <cstddef>
#include <cstdint>
#ifdef __cplusplus
extern "C" {
#endif
typedef int esp_err_t;
#define ESP_OK 0
const char *esp_err_to_name(esp_err_t);
typedef struct jpeg_dec_t *jpeg_decoder_handle_t;
typedef struct { int intr_priority; int timeout_ms; } jpeg_decode_engine_cfg_t;
typedef enum { JPEG_DECODE_OUT_FORMAT_RGB888, JPEG_DECODE_OUT_FORMAT_RGB565 } jpeg_dec_output_format_t;
typedef enum { JPEG_DEC_RGB_ELEMENT_ORDER_BGR, JPEG_DEC_RGB_ELEMENT_ORDER_RGB } jpeg_dec_rgb_element_order_t;
typedef struct { jpeg_dec_output_format_t output_format; jpeg_dec_rgb_element_order_t rgb_order; int conv_std; } jpeg_decode_cfg_t;
typedef enum { JPEG_DEC_ALLOC_INPUT_BUFFER, JPEG_DEC_ALLOC_OUTPUT_BUFFER } jpeg_dec_buffer_alloc_direction_t;
typedef struct { jpeg_dec_buffer_alloc_direction_t buffer_direction; } jpeg_decode_memory_alloc_cfg_t;
esp_err_t jpeg_new_decoder_engine(const jpeg_decode_engine_cfg_t *, jpeg_decoder_handle_t *);
void *jpeg_alloc_decoder_mem(size_t size, const jpeg_decode_memory_alloc_cfg_t *cfg, size_t *allocated);
esp_err_t jpeg_decoder_process(jpeg_decoder_handle_t, const jpeg_decode_cfg_t *, const uint8_t *, uint32_t, uint8_t *, uint32_t, uint32_t *);
#ifdef __cplusplus
}
#endif
