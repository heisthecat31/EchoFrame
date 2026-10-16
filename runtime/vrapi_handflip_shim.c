// libvrapi.so shim: Quest 2 hand orientation fix for a runtime build we have no source for.
//
// On a Quest 2 (Meta's OpenXR runtime 207.x), the s3/afk build's controller poses come out
// rolled 180 degrees about the pointing axis: the backs of the hands face where the palms
// should. This library takes libvrapi.so's place; the real runtime is renamed libvrafk.so
// (its SONAME patched to match) and is this library's only dependency, so every other VrApi
// symbol Echo imports resolves there. Only vrapi_GetInputTrackingState is wrapped: on success
// the controller orientation is rolled 180 degrees about its forward (-Z) axis,
// q * (0, 0, 1, 0) = (y, -x, w, -z).
//
// When: ro.product.model "Quest 2" (any other headset: off). Override with an empty file
// <media dir>/files/afk/handflip.on or handflip.off.
#include <android/log.h>
#include <dlfcn.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/system_properties.h>
#include <unistd.h>

#define TAG "EchoQuestXR"
#define LOG(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

typedef int32_t (*TrackingFn)(void*, uint32_t, double, void*);
static TrackingFn g_real;
static int g_flip;
static pthread_once_t g_once = PTHREAD_ONCE_INIT;

static void Init(void) {
    void* h = dlopen("libvrafk.so", RTLD_NOW);
    g_real = h ? (TrackingFn)dlsym(h, "vrapi_GetInputTrackingState") : NULL;
    if (!g_real) LOG("handflip: libvrafk.so's vrapi_GetInputTrackingState not found: %s", dlerror());

    char model[PROP_VALUE_MAX] = "";
    __system_property_get("ro.product.model", model);
    g_flip = strcmp(model, "Quest 2") == 0;
    const char* why = "headset model";

    char pkg[128] = "";
    FILE* c = fopen("/proc/self/cmdline", "rb");
    if (c) {
        size_t n = fread(pkg, 1, sizeof pkg - 1, c);
        pkg[n] = 0;
        fclose(c);
    }
    char path[256];
    snprintf(path, sizeof path, "/sdcard/Android/media/%s/files/afk/handflip.on", pkg);
    if (access(path, F_OK) == 0) { g_flip = 1; why = "handflip.on"; }
    snprintf(path, sizeof path, "/sdcard/Android/media/%s/files/afk/handflip.off", pkg);
    if (access(path, F_OK) == 0) { g_flip = 0; why = "handflip.off"; }
    LOG("handflip: %s (model '%s', by %s)", g_flip ? "on: controller poses rolled 180 degrees" : "off", model, why);
}

__attribute__((visibility("default"))) int32_t vrapi_GetInputTrackingState(void* ovr, uint32_t device, double seconds, void* out) {
    pthread_once(&g_once, Init);
    if (!g_real) return -1000;   // ovrError_Unknown-ish: no runtime
    int32_t r = g_real(ovr, device, seconds, out);
    if (r == 0 && g_flip && out) {
        // vr::Tracking: uint32 Status, uint32 Reserved, then HeadPose.Pose.Orientation (x, y, z, w)
        float* q = (float*)((char*)out + 8);
        float x = q[0], y = q[1], z = q[2], w = q[3];
        q[0] = y;
        q[1] = -x;
        q[2] = w;
        q[3] = -z;
    }
    return r;
}
