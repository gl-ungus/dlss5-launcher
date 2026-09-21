#include "../DLSS5VKLayer/common/shm_protocol.h"
#include <cstdio>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>
int main() {
 int fd = open("/tmp/dlssnr-1000/shm.bin", O_RDONLY);
 if(fd < 0) return 1;
 auto *h = (const ShmHeader*)mmap(nullptr, kHeaderBytes, PROT_READ, MAP_SHARED, fd, 0);
 if(h == MAP_FAILED) return 2;
 if(h->magic.load()!=kShmMagic || h->version.load()!=kShmVersion) return 3;
 printf("requested=%u\nresponded=%u\nsuccessfully_evaluated=%u\nwidth=%u\nheight=%u\nmodel_up=%u\nlast_eval_ms=%.3f\n",h->seq_req.load(),h->seq_resp.load(),h->seq_ok.load(),h->width.load(),h->height.load(),h->modelUp.load(),BitsToFloat(h->helperEvalMsBits.load()));
 return h->seq_req.load() && h->seq_req.load()==h->seq_ok.load() && h->modelUp.load() ? 0 : 4;
}
