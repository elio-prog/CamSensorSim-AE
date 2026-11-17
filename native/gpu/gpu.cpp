#include "gpu.h"

#ifdef CAMSIM_GPU
#define NOMINMAX
#include <windows.h>
#include <cuda.h>          // types et constantes seulement : le pilote est charge a l'execution

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <map>
#include <mutex>

#include "gpu_kernels_data.h"
#include "../core/log.h"
#endif

namespace camsim {
namespace gpu {

#ifdef CAMSIM_GPU
namespace {

// Fonctions du pilote (noms exportes par nvcuda.dll).
struct Driver {
    CUresult(CUDAAPI* Init)(unsigned int);
    CUresult(CUDAAPI* DriverGetVersion)(int*);
    CUresult(CUDAAPI* DeviceGetCount)(int*);
    CUresult(CUDAAPI* DeviceGet)(CUdevice*, int);
    CUresult(CUDAAPI* DeviceGetAttribute)(int*, CUdevice_attribute, CUdevice);
    CUresult(CUDAAPI* DeviceGetName)(char*, int, CUdevice);
    CUresult(CUDAAPI* DevicePrimaryCtxRetain)(CUcontext*, CUdevice);
    CUresult(CUDAAPI* CtxPushCurrent)(CUcontext);
    CUresult(CUDAAPI* CtxPopCurrent)(CUcontext*);
    CUresult(CUDAAPI* ModuleLoadData)(CUmodule*, const void*);
    CUresult(CUDAAPI* ModuleGetFunction)(CUfunction*, CUmodule, const char*);
    CUresult(CUDAAPI* MemAlloc)(CUdeviceptr*, size_t);
    CUresult(CUDAAPI* MemFree)(CUdeviceptr);
    CUresult(CUDAAPI* MemcpyHtoDAsync)(CUdeviceptr, const void*, size_t, CUstream);
    CUresult(CUDAAPI* MemcpyDtoHAsync)(void*, CUdeviceptr, size_t, CUstream);
    CUresult(CUDAAPI* StreamCreate)(CUstream*, unsigned int);
    CUresult(CUDAAPI* StreamDestroy)(CUstream);
    CUresult(CUDAAPI* StreamSynchronize)(CUstream);
    CUresult(CUDAAPI* MemsetD8Async)(CUdeviceptr, unsigned char, size_t, CUstream);
    CUresult(CUDAAPI* LaunchKernel)(CUfunction, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned,
                                    CUstream, void**, void**);
};

struct State {
    bool tried = false, ok = false;
    std::string info = "carte graphique non essayee";
    Driver d{};
    CUcontext ctx = nullptr;
    CUmodule module = nullptr;
    std::map<std::string, CUfunction> fn;
    std::mutex mu;
    // Reserve de memoire : les blocs liberes par une image servent a la suivante (pas de reservation a chaque image).
    std::multimap<size_t, CUdeviceptr> pool;
    size_t pool_bytes = 0;
    std::mutex pool_mu;
};

State& state() {
    static State s;
    return s;
}

const size_t POOL_LIMIT = (size_t)1536 << 20;          // au plus 1,5 Go gardes en reserve

CUdeviceptr pool_take(size_t bytes) {
    State& S = state();
    std::lock_guard<std::mutex> lk(S.pool_mu);
    auto it = S.pool.lower_bound(bytes);
    if (it == S.pool.end() || it->first > 2 * bytes + (1 << 20)) return 0;
    const CUdeviceptr p = it->second;
    S.pool_bytes -= it->first;
    S.pool.erase(it);
    return p;
}

void pool_give(size_t bytes, CUdeviceptr p) {
    State& S = state();
    std::lock_guard<std::mutex> lk(S.pool_mu);
    if (S.pool_bytes + bytes > POOL_LIMIT) { S.d.MemFree(p); return; }
    S.pool.emplace(bytes, p);
    S.pool_bytes += bytes;
}


template <typename T>
bool load(HMODULE lib, const char* name, T& f) {
    f = reinterpret_cast<T>(GetProcAddress(lib, name));
    return f != nullptr;
}

void init(State& S) {
    S.tried = true;
    HMODULE lib = LoadLibraryA("nvcuda.dll");
    if (!lib) { S.info = "pas de pilote NVIDIA (nvcuda.dll absent)"; return; }
    Driver& d = S.d;
    bool ok = load(lib, "cuInit", d.Init) && load(lib, "cuDriverGetVersion", d.DriverGetVersion) &&
              load(lib, "cuDeviceGetCount", d.DeviceGetCount) && load(lib, "cuDeviceGet", d.DeviceGet) &&
              load(lib, "cuDeviceGetAttribute", d.DeviceGetAttribute) && load(lib, "cuDeviceGetName", d.DeviceGetName) &&
              load(lib, "cuDevicePrimaryCtxRetain", d.DevicePrimaryCtxRetain) &&
              load(lib, "cuCtxPushCurrent_v2", d.CtxPushCurrent) && load(lib, "cuCtxPopCurrent_v2", d.CtxPopCurrent) &&
              load(lib, "cuModuleLoadData", d.ModuleLoadData) && load(lib, "cuModuleGetFunction", d.ModuleGetFunction) &&
              load(lib, "cuMemAlloc_v2", d.MemAlloc) && load(lib, "cuMemFree_v2", d.MemFree) &&
              load(lib, "cuMemcpyHtoDAsync_v2", d.MemcpyHtoDAsync) && load(lib, "cuMemcpyDtoHAsync_v2", d.MemcpyDtoHAsync) &&
              load(lib, "cuStreamCreate", d.StreamCreate) && load(lib, "cuStreamDestroy_v2", d.StreamDestroy) &&
              load(lib, "cuStreamSynchronize", d.StreamSynchronize) && load(lib, "cuLaunchKernel", d.LaunchKernel) &&
              load(lib, "cuMemsetD8Async", d.MemsetD8Async);
    if (!ok) { S.info = "pilote NVIDIA trop ancien (fonctions absentes)"; return; }
    if (d.Init(0) != CUDA_SUCCESS) { S.info = "le pilote NVIDIA ne demarre pas"; return; }
    int n = 0, ver = 0;
    d.DriverGetVersion(&ver);
    if (d.DeviceGetCount(&n) != CUDA_SUCCESS || n < 1) { S.info = "aucune carte NVIDIA"; return; }
    CUdevice dev;
    d.DeviceGet(&dev, 0);
    int major = 0, minor = 0;
    d.DeviceGetAttribute(&major, CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MAJOR, dev);
    d.DeviceGetAttribute(&minor, CU_DEVICE_ATTRIBUTE_COMPUTE_CAPABILITY_MINOR, dev);
    char name[256] = {0};
    d.DeviceGetName(name, 255, dev);
    if (d.DevicePrimaryCtxRetain(&S.ctx, dev) != CUDA_SUCCESS) { S.info = std::string(name) + " : contexte impossible"; return; }
    d.CtxPushCurrent(S.ctx);
    // Binaire de la meme generation (meme chiffre principal, le plus proche en dessous), sinon PTX.
    const KernelImage* best = nullptr;
    for (int i = 0; i < KERNEL_IMAGE_COUNT; ++i) {
        const KernelImage& k = KERNEL_IMAGES[i];
        if (k.major == major && k.minor <= minor && (!best || k.minor > best->minor)) best = &k;
    }
    CUresult r = CUDA_ERROR_UNKNOWN;
    if (best) r = d.ModuleLoadData(&S.module, best->data);
    std::string how = best ? "binaire sm_" + std::to_string(best->major * 10 + best->minor) : "";
    if (r != CUDA_SUCCESS) {
        r = d.ModuleLoadData(&S.module, KERNEL_PTX);
        how = "PTX recompile par le pilote";
    }
    CUcontext dummy;
    d.CtxPopCurrent(&dummy);
    if (r != CUDA_SUCCESS) {
        S.info = std::string(name) + " : noyaux non charges (pilote trop ancien ?), version du pilote " + std::to_string(ver);
        return;
    }
    S.ok = true;
    S.info = std::string(name) + " (sm_" + std::to_string(major * 10 + minor) + ", " + how + ")";
}

CUfunction function(const char* name) {
    State& S = state();
    std::lock_guard<std::mutex> lk(S.mu);
    auto it = S.fn.find(name);
    if (it != S.fn.end()) return it->second;
    CUfunction f = nullptr;
    S.d.CtxPushCurrent(S.ctx);
    if (S.d.ModuleGetFunction(&f, S.module, name) != CUDA_SUCCESS) f = nullptr;
    CUcontext dummy;
    S.d.CtxPopCurrent(&dummy);
    S.fn[name] = f;
    return f;
}

}  // namespace

bool available() {
    State& S = state();
    std::lock_guard<std::mutex> lk(S.mu);
    if (!S.tried) init(S);
    return S.ok;
}

std::string describe() {
    available();
    return state().info;
}

Session::Session() {
    if (!available()) return;
    State& S = state();
    if (S.d.CtxPushCurrent(S.ctx) != CUDA_SUCCESS) return;
    CUstream st = nullptr;
    if (S.d.StreamCreate(&st, CU_STREAM_NON_BLOCKING) != CUDA_SUCCESS) {
        CUcontext dummy;
        S.d.CtxPopCurrent(&dummy);
        return;
    }
    stream_ = st;
    ok_ = true;
}

Session::~Session() {
    State& S = state();
    if (stream_) {
        S.d.StreamSynchronize((CUstream)stream_);
        for (size_t i = 0; i < owned_.size(); ++i) pool_give(sizes_[i], (CUdeviceptr)owned_[i]);
        S.d.StreamDestroy((CUstream)stream_);
        CUcontext dummy;
        S.d.CtxPopCurrent(&dummy);
    }
}

DevPtr Session::alloc(size_t bytes) {
    if (!ok_) return 0;
    bytes = bytes > 0 ? bytes : 1;
    // Un bloc de la reserve peut etre plus grand que demande ; il y retourne sous la taille demandee
    // (toujours inferieure ou egale a sa taille reelle, donc sans risque).
    CUdeviceptr p = pool_take(bytes);
    if (!p) {
        const CUresult r = state().d.MemAlloc(&p, bytes);
        if (r != CUDA_SUCCESS) {
            camsim::log("carte graphique : memoire refusee (%.0f Mo demandes, erreur %d)", bytes / 1048576.0, (int)r);
            ok_ = false;
            return 0;
        }
    }
    owned_.push_back((DevPtr)p);
    sizes_.push_back(bytes);
    return (DevPtr)p;
}

bool Session::upload(DevPtr dst, const void* src, size_t bytes) {
    if (!ok_) return false;
    if (bytes && state().d.MemcpyHtoDAsync((CUdeviceptr)dst, src, bytes, (CUstream)stream_) != CUDA_SUCCESS) ok_ = false;
    return ok_;
}

bool Session::download(void* dst, DevPtr src, size_t bytes) {
    if (!ok_) return false;
    if (bytes && state().d.MemcpyDtoHAsync(dst, (CUdeviceptr)src, bytes, (CUstream)stream_) != CUDA_SUCCESS) ok_ = false;
    return ok_ && sync();
}

bool Session::launch(const char* kernel, size_t n, void** args) {
    if (!ok_) return false;
    CUfunction f = function(kernel);
    if (!f) { camsim::log("carte graphique : noyau %s introuvable", kernel); ok_ = false; return false; }
    const unsigned block = 256, grid = (unsigned)((n + block - 1) / block);
    if (grid == 0) return true;
    static const bool timing = std::getenv("CAMSIM_TIMING") != nullptr;
    auto t0 = std::chrono::steady_clock::now();
    {
        const CUresult r = state().d.LaunchKernel(f, grid, 1, 1, block, 1, 1, 0, (CUstream)stream_, args, nullptr);
        if (r != CUDA_SUCCESS) {
            camsim::log("carte graphique : lancement de %s refuse (%u x %u, erreur %d)", kernel, grid, block, (int)r);
            ok_ = false;
        }
    }
    if (timing && ok_) {
        sync();
        std::fprintf(stderr, "    %s %.2f ms\n", kernel, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
    }
    return ok_;
}

bool Session::launch_grid(const char* kernel, unsigned grid, unsigned block, void** args, unsigned shared) {
    if (!ok_) return false;
    CUfunction f = function(kernel);
    if (!f) { camsim::log("carte graphique : noyau %s introuvable", kernel); ok_ = false; return false; }
    if (grid == 0) return true;
    static const bool timing = std::getenv("CAMSIM_TIMING") != nullptr;
    auto t0 = std::chrono::steady_clock::now();
    {
        const CUresult r = state().d.LaunchKernel(f, grid, 1, 1, block, 1, 1, shared, (CUstream)stream_, args, nullptr);
        if (r != CUDA_SUCCESS) {
            camsim::log("carte graphique : lancement de %s refuse (%u x %u, erreur %d)", kernel, grid, block, (int)r);
            ok_ = false;
        }
    }
    if (timing && ok_) {
        sync();
        std::fprintf(stderr, "    %s %.2f ms (%u x %u)\n", kernel, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count(), grid, block);
    }
    return ok_;
}

bool Session::zero(DevPtr p, size_t bytes) {
    if (!ok_) return false;
    if (bytes && state().d.MemsetD8Async((CUdeviceptr)p, 0, bytes, (CUstream)stream_) != CUDA_SUCCESS) ok_ = false;
    return ok_;
}

bool Session::sync() {
    if (!ok_) return false;
    const CUresult r = state().d.StreamSynchronize((CUstream)stream_);
    if (r != CUDA_SUCCESS) {
        camsim::log("carte graphique : erreur pendant le calcul (erreur %d)", (int)r);
        ok_ = false;
    }
    return ok_;
}

#else   // construit sans CUDA : toujours sur le processeur

bool available() { return false; }
std::string describe() { return "module construit sans carte graphique"; }
Session::Session() {}
Session::~Session() {}
DevPtr Session::alloc(size_t) { return 0; }
bool Session::upload(DevPtr, const void*, size_t) { return false; }
bool Session::download(void*, DevPtr, size_t) { return false; }
bool Session::launch(const char*, size_t, void**) { return false; }
bool Session::launch_grid(const char*, unsigned, unsigned, void**, unsigned) { return false; }
bool Session::zero(DevPtr, size_t) { return false; }
bool Session::sync() { return false; }

#endif

}  // namespace gpu
}  // namespace camsim
