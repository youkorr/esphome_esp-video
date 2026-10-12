/* FreeRTOS queues and tasks on a workstation, for tools/checkpipeline.py.
 *
 * Real blocking, on real threads: the point of the check is that the decode
 * task and the draw task run AT THE SAME TIME and hand buffers to each other,
 * so a shim that ran them one after the other could only ever agree with the
 * code. Ticks are milliseconds, as pdMS_TO_TICKS says in the stand-in. */
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

#include <chrono>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

struct QueueStub {
  std::mutex m;
  std::condition_variable items, space;
  std::deque<std::vector<uint8_t>> held;
  unsigned length{0};
  unsigned size{0};
};

QueueHandle_t xQueueCreate(unsigned len, unsigned size) {
  auto *q = new QueueStub;
  q->length = len;
  q->size = size;
  return q;
}

void vQueueDelete(QueueHandle_t q) { delete q; }

template<typename P> static bool wait(std::condition_variable &cv, std::unique_lock<std::mutex> &lock,
                                      TickType_t ticks, P ready) {
  if (ready())
    return true;
  if (ticks == 0)
    return false;
  if (ticks == portMAX_DELAY) {
    cv.wait(lock, ready);
    return true;
  }
  return cv.wait_for(lock, std::chrono::milliseconds(ticks), ready);
}

BaseType_t xQueueSend(QueueHandle_t q, const void *item, TickType_t ticks) {
  std::unique_lock<std::mutex> lock(q->m);
  if (!wait(q->space, lock, ticks, [q] { return q->held.size() < q->length; }))
    return pdFALSE;
  const auto *bytes = static_cast<const uint8_t *>(item);
  q->held.emplace_back(bytes, bytes + q->size);
  q->items.notify_one();
  return pdTRUE;
}

BaseType_t xQueueReceive(QueueHandle_t q, void *item, TickType_t ticks) {
  std::unique_lock<std::mutex> lock(q->m);
  if (!wait(q->items, lock, ticks, [q] { return !q->held.empty(); }))
    return pdFALSE;
  memcpy(item, q->held.front().data(), q->size);
  q->held.pop_front();
  q->space.notify_one();
  return pdTRUE;
}

BaseType_t xTaskCreatePinnedToCore(void (*fn)(void *), const char *, uint32_t, void *arg, unsigned, TaskHandle_t *,
                                   int) {
  std::thread(fn, arg).detach();
  return pdPASS;
}

void vTaskDelay(TickType_t ticks) { std::this_thread::sleep_for(std::chrono::milliseconds(ticks)); }
