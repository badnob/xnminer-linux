#include "xen_cuda_api.h"

#include "../XenblocksMiner-main/src/hashapi/CudaHashBackend.h"
#include "../XenblocksMiner-main/src/hashapi/HashApiTuning.h"
#include "../XenblocksMiner-main/src/hashapi/HashApiTypes.h"
#include "../XenblocksMiner-main/src/CudaBackend.h"

#include <algorithm>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace {

struct LaneSlot {
    std::unique_ptr<hashapi::CudaHashBackend> backend;
    std::mutex mutex;
};

struct EngineState {
    int device_id = 0;
    uint64_t reserve_bytes = hashapi::kCudaBatchMemoryReserveBytes;
    std::vector<LaneSlot> lanes;
    std::mutex state_mutex;
};

std::unique_ptr<EngineState> g_state;

void copy_str(char* dst, size_t dst_len, const std::string& src)
{
    if (dst == nullptr || dst_len == 0) {
        return;
    }
    std::strncpy(dst, src.c_str(), dst_len - 1);
    dst[dst_len - 1] = '\0';
}

void fill_result(const hashapi::HashApiResult& src, XenCudaBatchResult* out)
{
    std::memset(out, 0, sizeof(*out));
    out->ok = src.ok ? 1 : 0;
    copy_str(out->error, sizeof(out->error), src.error);
    out->attempts = static_cast<uint64_t>(src.attempts);
    out->hashrate = src.hashrate;
    out->elapsed_ms = src.elapsed_ms;
    out->batch_size = static_cast<uint32_t>(src.batch_size);
    out->match_count = static_cast<uint32_t>(
        std::min(src.matches.size(), static_cast<std::size_t>(XEN_CUDA_MAX_MATCHES)));
    for (uint32_t i = 0; i < out->match_count; ++i) {
        const auto& m = src.matches[i];
        copy_str(out->matches[i].key, sizeof(out->matches[i].key), m.key);
        copy_str(out->matches[i].hash, sizeof(out->matches[i].hash), m.hash);
        copy_str(out->matches[i].pattern, sizeof(out->matches[i].pattern), m.matched_pattern);
        out->matches[i].attempt_index = static_cast<uint64_t>(m.attempt_index);
    }
}

bool grow_lanes(EngineState& state, int lane_count)
{
    if (lane_count <= 0) {
        return false;
    }
    const std::size_t needed = static_cast<std::size_t>(lane_count);
    while (state.lanes.size() < needed) {
        auto backend = std::make_unique<CudaBackend>(state.device_id);
        LaneSlot slot;
        slot.backend = std::make_unique<hashapi::CudaHashBackend>(std::move(backend));
        state.lanes.push_back(std::move(slot));
    }
    return true;
}

LaneSlot* lane_slot(int lane_index)
{
    if (!g_state || lane_index < 0
        || static_cast<std::size_t>(lane_index) >= g_state->lanes.size()) {
        return nullptr;
    }
    return &g_state->lanes[static_cast<std::size_t>(lane_index)];
}

int run_lane_batch_locked(
    int lane_index,
    const char* salt_hex,
    const char* key_prefix,
    uint32_t difficulty,
    uint64_t batch_size,
    int allow_xuni,
    XenCudaBatchResult* out)
{
    if (out == nullptr || salt_hex == nullptr) {
        return -1;
    }
    LaneSlot* slot = lane_slot(lane_index);
    if (slot == nullptr || !slot->backend) {
        std::memset(out, 0, sizeof(*out));
        copy_str(out->error, sizeof(out->error), "CUDA lane not initialized");
        return -2;
    }

    hashapi::HashApiRequest request;
    request.backend = "cuda";
    request.salt_hex = salt_hex;
    request.key_prefix = key_prefix ? key_prefix : "";
    request.target_pattern = "XEN11";
    request.difficulty = difficulty;
    request.batch_size = static_cast<std::size_t>(batch_size);
    request.allow_xuni = allow_xuni != 0;
    request.gpu_first_blocks = true;
    request.first_block_dynamic_chunk_auto = true;

    std::lock_guard<std::mutex> lane_lock(slot->mutex);
    try {
        const hashapi::HashApiResult result = slot->backend->runBatch(request);
        fill_result(result, out);
        return result.ok ? 0 : -3;
    } catch (const std::exception& ex) {
        std::memset(out, 0, sizeof(*out));
        copy_str(out->error, sizeof(out->error), ex.what());
        return -4;
    } catch (...) {
        std::memset(out, 0, sizeof(*out));
        copy_str(out->error, sizeof(out->error), "unknown cuda batch error");
        return -5;
    }
}

} // namespace

extern "C" {

int xen_cuda_init(int device_id, uint64_t reserve_bytes)
{
    try {
        auto state = std::make_unique<EngineState>();
        state->device_id = device_id;
        if (reserve_bytes > 0) {
            state->reserve_bytes = reserve_bytes;
        }
        if (!grow_lanes(*state, 1)) {
            return -1;
        }
        g_state = std::move(state);
        return 0;
    } catch (...) {
        g_state.reset();
        return -1;
    }
}

void xen_cuda_shutdown()
{
    if (!g_state) {
        return;
    }
    std::lock_guard<std::mutex> lock(g_state->state_mutex);
    g_state->lanes.clear();
    g_state.reset();
}

int xen_cuda_set_lane_count(int lane_count)
{
    if (!g_state || lane_count <= 0) {
        return -1;
    }
    std::lock_guard<std::mutex> lock(g_state->state_mutex);
    try {
        if (!grow_lanes(*g_state, lane_count)) {
            return -1;
        }
        if (static_cast<std::size_t>(lane_count) < g_state->lanes.size()) {
            g_state->lanes.resize(static_cast<std::size_t>(lane_count));
        }
        return 0;
    } catch (...) {
        return -1;
    }
}

int xen_cuda_device_info(int device_id, XenCudaDeviceInfo* out)
{
    if (out == nullptr) {
        return -1;
    }
    std::memset(out, 0, sizeof(*out));
    try {
        CudaBackend backend(device_id);
        backend.activate();
        const DeviceInfo info = backend.getDeviceInfo();
        out->device_id = info.index;
        copy_str(out->name, sizeof(out->name), info.name);
        out->total_vram_bytes = static_cast<uint64_t>(info.totalMemoryBytes);
        out->free_vram_bytes = static_cast<uint64_t>(backend.getFreeMemory());
        return 0;
    } catch (...) {
        return -1;
    }
}

uint64_t xen_cuda_select_batch_size(
    uint64_t free_vram_bytes,
    uint32_t difficulty,
    uint64_t max_batch_size)
{
    const auto decision = hashapi::selectCudaBatchSize(
        static_cast<std::size_t>(free_vram_bytes),
        difficulty,
        static_cast<std::size_t>(max_batch_size));
    return static_cast<uint64_t>(decision.selected_batch_size);
}

int xen_cuda_run_lane_batch(
    int lane_index,
    const char* salt_hex,
    const char* key_prefix,
    uint32_t difficulty,
    uint64_t batch_size,
    int allow_xuni,
    XenCudaBatchResult* out)
{
    if (!g_state) {
        if (out != nullptr) {
            std::memset(out, 0, sizeof(*out));
            copy_str(out->error, sizeof(out->error), "CUDA engine not initialized");
        }
        return -2;
    }
    return run_lane_batch_locked(
        lane_index, salt_hex, key_prefix, difficulty, batch_size, allow_xuni, out);
}

int xen_cuda_run_batch(
    const char* salt_hex,
    const char* key_prefix,
    uint32_t difficulty,
    uint64_t batch_size,
    int allow_xuni,
    XenCudaBatchResult* out)
{
    return xen_cuda_run_lane_batch(
        0, salt_hex, key_prefix, difficulty, batch_size, allow_xuni, out);
}

int xen_cuda_verify_known(
    const char* salt_hex,
    const char* key_hex,
    uint32_t difficulty,
    char* hash_out,
    size_t hash_out_len)
{
    if (salt_hex == nullptr || key_hex == nullptr || hash_out == nullptr) {
        return -1;
    }
    LaneSlot* slot = lane_slot(0);
    if (!g_state || slot == nullptr || !slot->backend) {
        return -2;
    }

    hashapi::HashApiRequest request;
    request.backend = "cuda";
    request.salt_hex = salt_hex;
    request.key = key_hex;
    request.target_pattern = "XEN11";
    request.difficulty = difficulty;
    request.batch_size = 1;
    request.allow_xuni = 1;
    request.gpu_first_blocks = true;

    std::lock_guard<std::mutex> lane_lock(slot->mutex);
    try {
        const hashapi::HashApiResult result = slot->backend->runBatch(request);
        if (!result.ok) {
            return -3;
        }
        copy_str(hash_out, hash_out_len, result.hash);
        return 0;
    } catch (...) {
        return -4;
    }
}

} // extern "C"