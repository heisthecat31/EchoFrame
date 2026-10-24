// libcustgd.so: the customization guard (customization_guard.h) on its own, for an Echo build
// whose libvrapi.so isn't this runtime (a mod's own). It's loaded as one of libvrapi.so's
// DT_NEEDED libraries, so its constructor runs while libr15.so is still being loaded: a thread
// waits for that load to finish (dl_iterate_phdr only answers then) and puts the guard in,
// long before any player's cosmetics load.
#include "customization_guard.h"

#include <pthread.h>

namespace {

void* WaitAndInstall(void*) {
    for (int i = 0; i < 6000; ++i) {   // 60 s
        if (customization_guard::Install()) return nullptr;
        usleep(10000);
    }
    CG_LOGE("customization guard: libr15.so never loaded");
    return nullptr;
}

__attribute__((constructor)) void Start() {
    pthread_t t;
    if (pthread_create(&t, nullptr, WaitAndInstall, nullptr) == 0) pthread_detach(t);
}

}  // namespace
