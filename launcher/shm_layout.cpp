#include "../DLSS5VKLayer/common/shm_protocol.h"
#include <cstddef>
#include <cstdio>
int main() {
 printf("{\"magic\":%u,\"version\":%u,\"size\":%zu,\"fields\":{", kShmMagic,kShmVersion,kHeaderBytes);
 #define FIELD(n) printf("\"" #n "\":%zu,",offsetof(ShmHeader,n))
 FIELD(magic); FIELD(version); FIELD(seq_req); FIELD(seq_resp); FIELD(seq_ok);
 FIELD(heartbeat); FIELD(quit); FIELD(enabled); FIELD(helperState); FIELD(modelUp);
 FIELD(helperFramesLo); FIELD(helperFramesHi); FIELD(helperEvalMsBits);
 FIELD(helperUploadMsBits); FIELD(helperReadbackMsBits); FIELD(helperVramMB);
 FIELD(layerPid); FIELD(layerFramesLo); FIELD(layerFramesHi); FIELD(layerWidth);
 FIELD(layerHeight); FIELD(layerMsBits); FIELD(layerHeartbeat); FIELD(layerCompositionUp);
 FIELD(width); FIELD(height); FIELD(helperReason); FIELD(layerReason); FIELD(gameName);
 FIELD(workingScaleBits); FIELD(compareMode); FIELD(transferStrengthBits);
 printf("\"compositionBypass\":%zu}}\n",offsetof(ShmHeader,compositionBypass));
}
