#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#  ifdef XEN_CUDA_EXPORTS
#    define XEN_CUDA_API __declspec(dllexport)
#  else
#    define XEN_CUDA_API __declspec(dllimport)
#  endif
#else
#  define XEN_CUDA_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define XEN_CUDA_MAX_MATCHES 32
#define XEN_CUDA_KEY_LEN 65
#define XEN_CUDA_HASH_LEN 256
#define XEN_CUDA_ERR_LEN 256

typedef struct XenCudaMatch {
    char key[XEN_CUDA_KEY_LEN];
    char hash[XEN_CUDA_HASH_LEN];
    char pattern[32];
    uint64_t attempt_index;
} XenCudaMatch;

typedef struct XenCudaBatchResult {
    int ok;
    char error[XEN_CUDA_ERR_LEN];
    uint64_t attempts;
    double hashrate;
    double elapsed_ms;
    uint32_t batch_size;
    uint32_t match_count;
    XenCudaMatch matches[XEN_CUDA_MAX_MATCHES];
} XenCudaBatchResult;

typedef struct XenCudaDeviceInfo {
    int device_id;
    char name[128];
    uint64_t total_vram_bytes;
    uint64_t free_vram_bytes;
} XenCudaDeviceInfo;

XEN_CUDA_API int xen_cuda_init(int device_id, uint64_t reserve_bytes);
XEN_CUDA_API void xen_cuda_shutdown(void);
XEN_CUDA_API int xen_cuda_device_info(int device_id, XenCudaDeviceInfo* out);
XEN_CUDA_API uint64_t xen_cuda_select_batch_size(
    uint64_t free_vram_bytes,
    uint32_t difficulty,
    uint64_t max_batch_size);
XEN_CUDA_API int xen_cuda_set_lane_count(int lane_count);
XEN_CUDA_API int xen_cuda_run_lane_batch(
    int lane_index,
    const char* salt_hex,
    const char* key_prefix,
    uint32_t difficulty,
    uint64_t batch_size,
    int allow_xuni,
    XenCudaBatchResult* out);
XEN_CUDA_API int xen_cuda_run_batch(
    const char* salt_hex,
    const char* key_prefix,
    uint32_t difficulty,
    uint64_t batch_size,
    int allow_xuni,
    XenCudaBatchResult* out);
XEN_CUDA_API int xen_cuda_verify_known(
    const char* salt_hex,
    const char* key_hex,
    uint32_t difficulty,
    char* hash_out,
    size_t hash_out_len);

#ifdef __cplusplus
}
#endif