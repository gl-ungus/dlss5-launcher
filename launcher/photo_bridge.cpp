// Native atomic boundary between the Python diffusion worker and Vulkan layer.
#include "../DLSS5VKLayer/common/shm_protocol.h"
#include <sys/mman.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <fcntl.h>

struct Bridge { int fd; uint8_t* mapping; ShmHeader* h; };
extern "C" {
void* photo_open(const char* path) {
    int fd = open(path, O_RDWR | O_CREAT | O_NOFOLLOW, 0600);
    if (fd < 0) return nullptr;
    if (flock(fd, LOCK_EX | LOCK_NB) || ftruncate(fd, ShmTotalBytes())) { close(fd); return nullptr; }
    auto* m = (uint8_t*)mmap(nullptr, ShmTotalBytes(), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (m == MAP_FAILED) { close(fd); return nullptr; }
    auto* h = (ShmHeader*)m;
    // Preserve transport counters across stop/resume of an attached game.
    if (h->magic.load() != kShmMagic || h->version.load() != kShmVersion) ShmInitDefaults(h);
    h->quit.store(0); h->enabled.store(1); h->modelUp.store(0);
    h->proxyFormat.store(kProxyRgba8); h->hdrMode.store(kHdrOff);
    h->compositionBypass.store(1); h->helperState.store(kHelperStarting);
    return new Bridge{fd, m, h};
}
void photo_tick(void* ptr) { ((Bridge*)ptr)->h->heartbeat.fetch_add(1); }
int photo_quit(void* ptr) { return ((Bridge*)ptr)->h->quit.load(); }
void photo_ready(void* ptr) {
    auto* h=((Bridge*)ptr)->h; h->modelUp.store(1); h->helperState.store(kHelperRunning);
}
// Returns sequence only after a complete copy. Single producer never overwrites
// input until photo_answer publishes the response, including during resizing.
uint32_t photo_take(void* ptr, uint8_t* dst, size_t capacity, uint32_t* w, uint32_t* h) {
    auto* b=(Bridge*)ptr; auto* s=b->h;
    uint32_t seq=s->seq_req.load(std::memory_order_acquire);
    if (seq == s->seq_resp.load() || !s->enabled.load()) return 0;
    *w=s->width.load(); *h=s->height.load(); size_t bytes=size_t(*w)*(*h)*4;
    if (!*w || !*h || bytes > capacity || s->hdrEncode.load()) {
        s->seq_resp.store(seq, std::memory_order_release); return 0;
    }
    std::memcpy(dst,b->mapping+kHeaderBytes,bytes); return seq;
}
void photo_answer(void* ptr, uint32_t seq, const uint8_t* pixels, uint32_t w, uint32_t h, float ms) {
    auto* b=(Bridge*)ptr; auto* s=b->h;
    if (size_t(w)*h*4 > kMaxFrame || s->seq_req.load()!=seq) return;
    std::memcpy(b->mapping+kHeaderBytes+kMaxFrame,pixels,size_t(w)*h*4);
    s->answeredW.store(w); s->answeredH.store(h);
    s->helperEvalMsBits.store(FloatToBits(ms));
    if (s->helperFramesLo.fetch_add(1)==UINT32_MAX) s->helperFramesHi.fetch_add(1);
    s->seq_ok.store(seq); s->seq_resp.store(seq,std::memory_order_release);
}
void photo_close(void* ptr) {
    if (!ptr) return;
    auto* b=(Bridge*)ptr; b->h->modelUp.store(0); b->h->helperState.store(kHelperStopped);
    b->h->enabled.store(0); b->h->quit.store(1);
    munmap(b->mapping,ShmTotalBytes()); close(b->fd); delete b;
}
}
