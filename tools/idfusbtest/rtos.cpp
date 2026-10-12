// SPDX-License-Identifier: MIT
// Copyright (c) 2024-2026 youkorr
// Part of Portall: https://github.com/youkorr/esphome_esp-video
//
// FreeRTOS on real threads, for tools/checkidfusb.py.
//
// The declarations are tools/btstub's. These are definitions that BLOCK, on
// purpose: portall_bt's own USB layer is a client task handing completions to
// callers that wait on semaphores, and a stand-in that ran them one after the
// other could only ever agree with the code. Ticks are milliseconds, as
// pdMS_TO_TICKS says in the stand-in.
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include <chrono>
#include <condition_variable>
#include <mutex>
#include <thread>

static const auto g_start = std::chrono::steady_clock::now();

int xTaskCreate(void (*fn)(void *), const char *, unsigned, void *arg, unsigned, TaskHandle_t *out) {
  std::thread(fn, arg).detach();
  if (out != nullptr)
    *out = nullptr;
  return pdPASS;
}

// Every caller in portall_bt returns right after it, which is all a thread
// needs to end.
void vTaskDelete(TaskHandle_t) {}

unsigned xTaskGetTickCount(void) {
  return (unsigned) std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() -
                                                                         g_start)
      .count();
}

void vTaskDelay(unsigned ticks) { std::this_thread::sleep_for(std::chrono::milliseconds(ticks)); }

struct SemaphoreStandIn {
  std::mutex m;
  std::condition_variable cv;
  unsigned count = 0;
};

SemaphoreHandle_t xSemaphoreCreateBinary(void) { return new SemaphoreStandIn; }

SemaphoreHandle_t xSemaphoreCreateMutex(void) {
  auto *sem = new SemaphoreStandIn;
  sem->count = 1;  // a mutex starts free
  return sem;
}

BaseType_t xSemaphoreGive(SemaphoreHandle_t sem) {
  std::lock_guard<std::mutex> lock(sem->m);
  if (sem->count >= 1)
    return pdFALSE;  // binary: already given
  sem->count = 1;
  sem->cv.notify_one();
  return pdTRUE;
}

BaseType_t xSemaphoreTake(SemaphoreHandle_t sem, TickType_t ticks) {
  std::unique_lock<std::mutex> lock(sem->m);
  auto ready = [sem] { return sem->count > 0; };
  if (ticks == portMAX_DELAY) {
    sem->cv.wait(lock, ready);
  } else if (!sem->cv.wait_for(lock, std::chrono::milliseconds(ticks), ready)) {
    return pdFALSE;
  }
  sem->count = 0;
  return pdTRUE;
}
