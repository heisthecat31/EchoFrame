// Customization guard for Echo VR's Quest engine (libr15.so build 4987566), shared by the
// EchoQuestXR runtime (vrapi_openxr.cpp) and the standalone libcustgd.so (custguard.cpp).
//
// Each player's cosmetics live in a context whose draw data is a
// CTableXT<SR15NetCustomDrawData> (38680-byte elements). Its Grow() asks CMemBlock::Resize for
// room (32 elements, 1.24 MB, the first time) and never checks the answer: when the allocator
// is out of memory Resize leaves the data pointer NULL (and its size set to the new one),
// Grow() raises the capacity anyway and writes the new element at address 0 (SIGSEGV in
// memset, seen on Quest in full lobbies). The guard takes Grow()'s slot in libr15.so's GOT
// (LoadMeshData calls it through its PLT): it does the resize itself, and when that fails it
// makes the table a consistent empty one and hands LoadMeshData a spare element instead, so
// that player's customization just isn't drawn. Off with debug.echoquestxr.customguard 0.
#pragma once

#include <android/log.h>
#include <dlfcn.h>
#include <errno.h>
#include <link.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/system_properties.h>
#include <unistd.h>

#include <atomic>
#include <cstddef>

namespace customization_guard {

#define CG_LOG(...) __android_log_print(ANDROID_LOG_INFO, "EchoQuestXR", __VA_ARGS__)
#define CG_LOGE(...) __android_log_print(ANDROID_LOG_ERROR, "EchoQuestXR", __VA_ARGS__)

struct DrawTable {          // CTableXT<SR15NetCustomDrawData>, as Grow() reads it
    uint8_t* data;          // CMemBlock: data, size, allocator, alignment
    uint64_t size;
    void* allocator;
    uint32_t align;
    uint32_t flags;
    uint64_t step;          // +0x20: elements added when full (at least the capacity)
    uint64_t capacity;      // +0x28
    uint64_t count;         // +0x30
};
static_assert(offsetof(DrawTable, step) == 0x20 && offsetof(DrawTable, capacity) == 0x28 &&
              offsetof(DrawTable, count) == 0x30, "CTableXT layout");

constexpr uint64_t kDrawDataSize = 0x9718;
constexpr const char* kGrowName = "_ZN10NRadEngine8CTableXTINS_8NRadGame21SR15NetCustomDrawDataEE4GrowEv";
constexpr const char* kResizeName = "_ZN10NRadEngine9CMemBlock6ResizeEyj";

using GrowFn = void* (*)(DrawTable*);
using ResizeFn = void (*)(void* block, uint64_t bytes, uint32_t keep);
inline GrowFn g_realGrow = nullptr;
inline ResizeFn g_realResize = nullptr;
inline std::atomic<int> g_failures{0};

inline void* GuardedGrow(DrawTable* t) {
    if (t->count >= t->capacity) {
        uint64_t add = t->step < t->capacity ? t->capacity : t->step;   // as Grow() picks it
        uint64_t inUse = t->count, wanted = (t->capacity + add) * kDrawDataSize;
        if (add) g_realResize(t, wanted, 1);
        if (!add || !t->data) {
            // Resize has dropped the block (an aligned one is freed; whether the allocator's
            // realloc kept the old one isn't known, so it isn't used again): make the table a
            // consistent empty one, size 0, so a later Resize doesn't copy from NULL. Elements
            // already in it (only after 32 of them) are leaked, not destroyed.
            t->data = nullptr;
            t->size = 0;
            t->count = 0;
            int n = ++g_failures;
            if (n <= 20 || n % 100 == 0)
                CG_LOGE("customization guard: no memory for %llu more cosmetic draw entries (%llu bytes, %llu in use); "
                        "this player's customization is skipped (failure %d)", (unsigned long long)add,
                        (unsigned long long)wanted, (unsigned long long)inUse, n);
            // LoadMeshData fills in what it gets back, so give it a fresh element that isn't in
            // any table: built by Grow() itself in a one-slot table of our own. It's never
            // freed (a resource load may still point into it), only made when memory ran out.
            DrawTable* spare = static_cast<DrawTable*>(calloc(1, sizeof(DrawTable)));
            void* slot = nullptr;
            if (posix_memalign(&slot, 64, kDrawDataSize) != 0) slot = nullptr;
            if (!spare || !slot) { CG_LOGE("customization guard: no memory for a spare element either"); abort(); }
            spare->data = static_cast<uint8_t*>(slot);
            spare->size = kDrawDataSize;
            spare->allocator = t->allocator;
            spare->step = 1;
            spare->capacity = 1;
            return g_realGrow(spare);
        }
        t->capacity += add;
    }
    return g_realGrow(t);   // count < capacity now: Grow() only adds the element
}

inline int FindLibr15(dl_phdr_info* info, size_t, void* data) {
    const char* name = info->dlpi_name ? strrchr(info->dlpi_name, '/') : nullptr;
    if (!name || strcmp(name, "/libr15.so") != 0) return 0;
    *static_cast<dl_phdr_info*>(data) = *info;
    return 1;
}

// Returns false only when libr15.so isn't loaded yet (worth trying again later).
inline bool Install() {
    static std::atomic<bool> done{false};
    if (done.load()) return true;
    char opt[PROP_VALUE_MAX] = "";
    __system_property_get("debug.echoquestxr.customguard", opt);
    if (!strcmp(opt, "0")) { CG_LOG("customization guard: off"); done = true; return true; }

    dl_phdr_info lib{};
    if (!dl_iterate_phdr(FindLibr15, &lib)) return false;   // after Echo's load has finished, when found
    done = true;

    void* echo = dlopen("libr15.so", RTLD_NOW | RTLD_NOLOAD);
    if (!echo) { CG_LOGE("customization guard: libr15.so can't be opened"); return true; }
    auto grow = reinterpret_cast<const uint32_t*>(dlsym(echo, kGrowName));
    auto resize = reinterpret_cast<ResizeFn>(dlsym(echo, kResizeName));
    dlclose(echo);
    if (!grow || !resize) { CG_LOG("customization guard: not this Echo build (no Grow/Resize)"); return true; }

    // Only where Grow() is the one this guard was written for: the first 15 instructions, up to
    // its call to Resize, read the table at +0x20/+0x28/+0x30 and use 38680-byte elements.
    static const uint32_t expected[] = {
        0xf81d0ff6, 0xa90153f5, 0xa9027bf3, 0xa942a009, 0xaa0003f3, 0xeb09011f, 0x540001a3, 0xf9401268,
        0x52800022, 0xaa1303e0, 0xeb09011f, 0x9a883134, 0x8b090288, 0x5292e309, 0x9b097d01,
    };
    if (memcmp(grow, expected, sizeof expected) != 0) { CG_LOG("customization guard: Grow() differs, not installed"); return true; }

    const ElfW(Dyn)* dyn = nullptr;
    for (int i = 0; i < lib.dlpi_phnum; ++i)
        if (lib.dlpi_phdr[i].p_type == PT_DYNAMIC) dyn = reinterpret_cast<const ElfW(Dyn)*>(lib.dlpi_addr + lib.dlpi_phdr[i].p_vaddr);
    if (!dyn) return true;
    const ElfW(Rela)* jmprel = nullptr;
    size_t jmprelSize = 0;
    const ElfW(Sym)* symtab = nullptr;
    const char* strtab = nullptr;
    // An address in the dynamic section: some linkers leave these as offsets in the library
    // (Pico's did, and reading one as an address crashed Echo), others make them addresses.
    auto at = [&](ElfW(Addr) v) { return v < lib.dlpi_addr ? lib.dlpi_addr + v : v; };
    for (; dyn->d_tag != DT_NULL; ++dyn) {
        switch (dyn->d_tag) {
            case DT_JMPREL: jmprel = reinterpret_cast<const ElfW(Rela)*>(at(dyn->d_un.d_ptr)); break;
            case DT_PLTRELSZ: jmprelSize = dyn->d_un.d_val; break;
            case DT_SYMTAB: symtab = reinterpret_cast<const ElfW(Sym)*>(at(dyn->d_un.d_ptr)); break;
            case DT_STRTAB: strtab = reinterpret_cast<const char*>(at(dyn->d_un.d_ptr)); break;
        }
    }
    if (!jmprel || !symtab || !strtab) { CG_LOGE("customization guard: libr15.so has no PLT relocations"); return true; }

    for (size_t i = 0; i < jmprelSize / sizeof(ElfW(Rela)); ++i) {
        const ElfW(Rela)& r = jmprel[i];
        if (ELF64_R_TYPE(r.r_info) != R_AARCH64_JUMP_SLOT) continue;
        if (strcmp(strtab + symtab[ELF64_R_SYM(r.r_info)].st_name, kGrowName) != 0) continue;
        void** slot = reinterpret_cast<void**>(lib.dlpi_addr + r.r_offset);
        // Someone else (a mod) already holds the slot: leave it to them, as their hooks do.
        if (*slot != reinterpret_cast<const void*>(grow)) {
            CG_LOG("customization guard: Grow()'s GOT slot holds %p, not Grow() at %p; not installed", *slot, grow);
            return true;
        }
        g_realGrow = reinterpret_cast<GrowFn>(*slot);
        g_realResize = resize;
        // BIND_NOW: the GOT is in RELRO, read-only once Echo is loaded.
        long page = sysconf(_SC_PAGESIZE);
        void* start = reinterpret_cast<void*>(reinterpret_cast<uintptr_t>(slot) & ~(uintptr_t)(page - 1));
        if (mprotect(start, page, PROT_READ | PROT_WRITE) != 0) { CG_LOGE("customization guard: mprotect: %s", strerror(errno)); return true; }
        __atomic_store_n(slot, reinterpret_cast<void*>(&GuardedGrow), __ATOMIC_RELEASE);
        mprotect(start, page, PROT_READ);
        CG_LOG("customization guard: in (Grow() at libr15.so+0x%zx)", (size_t)(reinterpret_cast<uintptr_t>(grow) - lib.dlpi_addr));
        return true;
    }
    CG_LOGE("customization guard: Grow() isn't called through libr15.so's PLT");
    return true;
}

}  // namespace customization_guard
