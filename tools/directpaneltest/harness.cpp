// Real Portall parser/decoder/pipeline, real scan-out ownership state, fake DMA.
#define main pipeline_main
#include "../pipetest/harness.cpp"
#undef main

using esphome::mipi_dsi::ScanoutBuffers;

struct DsiSimulation {
  ScanoutBuffers pool;
  void *buffers[3];
  std::atomic<uint8_t *> target;
  std::atomic<bool> running{true};
  std::atomic<unsigned> torn{0}, direct{0}, cancelled{0};
  std::mutex mutex;
  std::vector<Drawn> events;
  std::thread scanner;

  DsiSimulation() {
    for (void *&p : this->buffers) {
      p = aligned_alloc(128, W * H * 2);
      memset(p, 0, W * H * 2);
    }
    this->pool.init(this->buffers);
    this->target = static_cast<uint8_t *>(this->buffers[0]);
    this->scanner = std::thread([this] {
      while (this->running) {
        // ESP-IDF selects a link list BEFORE invoking frame-buf-complete.
        // Delay that callback to exercise a swap during an in-flight ISR.
        auto *selected = this->target.load();
        sleep_us(2000);
        this->pool.frame_complete();
        for (int i = 0; i < 40; i++) {
          if (read_seq(selected) == BEING_WRITTEN)
            this->torn++;
          sleep_us(250);
        }
      }
    });
  }
  uint8_t *acquire(uint32_t timeout) {
    const auto start = now_us();
    do {
      if (void *p = this->pool.acquire())
        return static_cast<uint8_t *>(p);
      sleep_us(1000);
    } while (now_us() - start < timeout * 1000ull);
    return nullptr;
  }
  bool present(uint8_t *buffer) {
    std::lock_guard<std::mutex> lock(this->mutex);
    if (!this->pool.leased(buffer))
      return false;
    this->target.store(buffer);
    if (!this->pool.presented(buffer))
      return false;
    this->direct++;
    this->events.push_back({read_seq(buffer), 0, 0, W, H});
    return true;
  }
  void blit(int x, int y, int w, int h, const uint8_t *source) {
    std::lock_guard<std::mutex> lock(this->mutex);
    auto *dest = this->target.load();
    for (int row = 0; row < h; row++)
      memcpy(dest + ((y + row) * W + x) * 2, source + row * w * 2, w * 2);
    this->events.push_back({read_seq(source), x, y, w, h});
  }
};

static void check_ownership() {
  int a, b, c;
  void *buffers[]{&a, &b, &c};
  ScanoutBuffers pool;
  pool.init(buffers);
  void *first = pool.acquire(), *second = pool.acquire();
  ok(first == &b && second == &c && pool.acquire() == nullptr,
     "the initial scan-out buffer is never offered for decoding");
  pool.presented(first);
  pool.frame_complete();
  ok(pool.acquire() == nullptr, "one completion cannot retire a buffer: its ISR may have selected the old one");
  pool.frame_complete();
  ok(pool.acquire() == &a, "two later completions release the old scan-out buffer");
  pool.cancel(second);
  ok(pool.acquire() == second, "a cancelled decode returns its unpresented buffer");
}

int main(int argc, char **argv) {
  const bool disabled = argc > 1 && strcmp(argv[1], "--disabled") == 0;
  check_ownership();
  auto *sim = new DsiSimulation;
  auto *display = new FakeDisplay(W, H);
  display->on_blit_ = [sim](int x, int y, int w, int h, const uint8_t *p) { sim->blit(x, y, w, h, p); };
  mipi_dsi::g_direct_panel = {
      display, sim, W, H, 2, W * H * 2,
      [](void *p, uint32_t timeout) { return static_cast<DsiSimulation *>(p)->acquire(timeout); },
      [](void *p, uint8_t *b) { return static_cast<DsiSimulation *>(p)->present(b); },
      [](void *p, uint8_t *b) {
        auto *s = static_cast<DsiSimulation *>(p);
        s->pool.cancel(b);
        s->cancelled++;
      }};
  auto *panel = new portall::Portall;
  panel->set_display(display);
  panel->set_resolution(W, H);
  panel->set_frame_buffers(4);
  panel->set_max_frame_bytes(1024);
  panel->set_decode_buffers(2);
  panel->set_panel_buffers(!disabled);
  panel->setup();
  g_decode_us = 4000;
  g_draw_us = 12000;
  std::vector<Picture> sent;
  uint32_t seq = 10;
  unsigned failed = 0, full = 0;
  // Decode faster than scan-out so that buffer retirement, rather than the
  // JPEG engine, must hold the producer back.
  for (int i = 0; i < 40; i++) {
    sent.push_back({seq++, 0, 0, W, H, static_cast<uint16_t>(seq), false});
    full++;
  }
  for (int i = 0; i < 18; i++) {
    sent.push_back({seq++, 0, 0, W, H, static_cast<uint16_t>(seq), false});
    full++;
    for (int j = 0; j < 3; j++)
      sent.push_back({seq++, static_cast<uint16_t>((j % 2) * 32), static_cast<uint16_t>((j / 2) * 16),
                      32, 16, static_cast<uint16_t>(seq), false});
    if (i % 3 == 0)
      for (int j = 0; j < 4; j++) {
        sent.push_back({seq++, 0, 0, W, H, static_cast<uint16_t>(seq), true});
        failed++;
      }
  }
  // Finish with partial updates, to check that their unchanged surroundings
  // still hold the newest full frame after many buffer swaps.
  for (const auto &pic : sent)
    send(panel, pic);
  const size_t want = sent.size() - failed;
  for (int i = 0; i < 10000; i++) {
    {
      std::lock_guard<std::mutex> lock(sim->mutex);
      if (sim->events.size() == want)
        break;
    }
    sleep_us(1000);
  }
  std::vector<uint8_t> expected(W * H * 2, 0);
  bool order = true;
  {
    std::lock_guard<std::mutex> lock(sim->mutex);
    size_t at = 0;
    for (const auto &pic : sent) {
      if (pic.bad)
        continue;
      if (at >= sim->events.size() || sim->events[at].seq != pic.seq)
        order = false;
      at++;
      for (int y = pic.y; y < pic.y + pic.h; y++)
        for (int x = pic.x; x < pic.x + pic.w; x += 2)
          memcpy(expected.data() + (y * W + x) * 2, &pic.seq, 4);
    }
    order = order && sim->events.size() == want;
    ok(order, "whole frames and rectangles reach the display in order across both tasks");
    ok(memcmp(expected.data(), sim->target.load(), expected.size()) == 0,
       "rectangles preserve every unchanged pixel of the most recent full frame");
  }
  ok(sim->direct == (disabled ? 0 : full), "direct decode count matches the opt-in setting (" + std::to_string(sim->direct) + ")");
  ok(display->drawn().size() == want - (disabled ? 0 : full), "every other rectangle takes the normal blit path");
  ok(sim->cancelled == (disabled ? 0 : failed), "failed decodes release all their leases (" + std::to_string(failed) + ")");
  ok(sim->torn == 0 && g_torn == 0, "the decoder never writes a buffer still scanned or copied");
  sim->running = false;
  sim->scanner.join();
  fflush(stdout);
  std::_Exit(g_faults ? 1 : 0);
}
