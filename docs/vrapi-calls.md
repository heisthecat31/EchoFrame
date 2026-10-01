# What Echo VR (Quest) asks of VrApi

Recorded with the logger on a **Quest 3** (Horizon OS / Android 14), Echo VR build
**4987566**, on 2026-10-01: two sessions, about 2.5 minutes of menus and play. This is the
specification for the OpenXR `libvrapi.so`. Names of enum values marked *(likely)* are
inferred from the logged values; VrApi's headers weren't used.

## Summary

- Only the engine (`libr15.so`) calls VrApi, always from **one thread** (the render
  thread), plus `GetSystemPropertyFloat` from two worker threads.
- **28 different functions** were called. The other 7 of the 35 the engine imports
  (`Shutdown`, `DestroySystemVulkan`, `DestroyTextureSwapChain`, `ShowSystemUI`, ...) are for
  shutdown and system menus, which these sessions didn't reach.
- **One swapchain**, a Vulkan 2D array (one layer per eye), and **one layer** submitted per
  frame.
- Echo asks for **96 Hz** but ran at **72 Hz** (the refresh-rate property stayed 72).
- Two **Touch controllers** drive the game. Hand-tracking devices are enumerated every
  frame but their input is never read.

## Startup, in order

| Time | Call | Arguments → result | OpenXR equivalent |
| --- | --- | --- | --- |
| 0.00 s | `vrapi_Initialize` | `ovrInitParms*` (Java VM/activity, API version) → 0 | `xrCreateInstance` (+ `XR_KHR_android_create_instance`) |
| 0.25 | `vrapi_GetSystemPropertyInt` | prop 0 (device type) → **320** (Quest 3) | `xrGetSystem` / constant |
| 0.36 | `vrapi_GetInstanceExtensionsVulkan` | → `VK_KHR_surface VK_KHR_android_surface VK_KHR_external_memory_capabilities VK_KHR_get_physical_device_properties2` | `xrGetVulkanInstanceExtensionsKHR` |
| 0.37 | `vrapi_GetDeviceExtensionsVulkan` | → `VK_KHR_swapchain VK_KHR_external_memory VK_KHR_get_memory_requirements2 VK_ANDROID_external_memory_android_hardware_buffer VK_EXT_queue_family_foreign` | `xrGetVulkanDeviceExtensionsKHR` |
| 0.37 | `vrapi_GetSystemPropertyInt` | prop 15 → 0; prop 7 → **108**, prop 8 → **110** (suggested eye FOV X/Y in degrees, *likely*) | `xrEnumerateViewConfigurationViews` / `xrLocateViews` |
| 0.54 | `vrapi_CreateSystemVulkan` | `{VkInstance, VkPhysicalDevice, VkDevice}` → 0 | `xrCreateSession` with `XrGraphicsBindingVulkanKHR` |
| 0.59 | `vrapi_CreateTextureSwapChain3` | type 2 (2D array), format 43 (`VK_FORMAT_R8G8B8A8_SRGB`), **3072 × 3380**, 1 mip, 3 buffers → handle | `xrCreateSwapchain` (arraySize 2) |
| 0.62 | `vrapi_GetTextureSwapChainLength` | → 3 | `xrEnumerateSwapchainImages` |
| 0.62 | `vrapi_GetTextureSwapChainBufferVulkan` ×3 | → `VkImage` per buffer | `XrSwapchainImageVulkanKHR.image` |
| 0.62 | `vrapi_GetTextureSwapChainBufferFoveationVulkan` ×3 | → 0 (fixed-foveation map) | stub, or `XR_FB_foveation` |
| 0.62 | `vrapi_SetPropertyInt` / `GetPropertyInt` | prop 15 = 0; prop 32 read → 1, set to 1; prop 30 = 1 | stubs |
| 1.45 | `vrapi_BeginFrame` ×4 | null `ovrMobile` → **-1004** (before VR mode; Echo ignores it) | return the same error |
| 1.48 | `vrapi_EnterVrMode` | `ovrModeParms*` → `ovrMobile*` | `xrBeginSession` once the session is READY |
| 1.64 | `vrapi_SetClockLevels` | CPU 2, GPU 2 → 0 | stub |
| 1.64 | `vrapi_GetSystemPropertyFloatArray` | prop 65 → **[72, 80, 90, 96, 100, 120]** (prop 64 = count 6) | `xrEnumerateDisplayRefreshRatesFB`, or just the current rate |
| 1.64 | `vrapi_SetDisplayRefreshRate` | **96** → 0 | `xrRequestDisplayRefreshRateFB` (Steam Frame ignores it) |
| 1.64 | `vrapi_SetExtraLatencyMode` | 1 → 0 | stub |
| 1.64 | `vrapi_GetSystemPropertyFloat` | prop 4 (display refresh rate) → **72** | `xrGetDisplayRefreshRateFB` |

## Every frame (≈72 per second)

Counts per frame, from the 5-second summaries (360 frames per 5 s):

| Per frame | Call | What it is | OpenXR equivalent |
| --- | --- | --- | --- |
| 1 | `vrapi_PollEvent` | events (see below) | `xrPollEvent` |
| 1 | `vrapi_GetSystemStatusInt` | status 1 (headset mounted, *likely*) → 1 | session state FOCUSED |
| 1 | `vrapi_SetPropertyInt` | prop 32 = 1 | stub |
| 2 | `vrapi_GetPredictedDisplayTime` | `(ovr, frameIndex)` → seconds | `XrFrameState.predictedDisplayTime` |
| 1 | `vrapi_WaitFrame` | `(ovr, frameIndex)` | `xrWaitFrame` |
| 1 | `vrapi_BeginFrame` | `(ovr, frameIndex)` | `xrBeginFrame` |
| 1 | `vrapi_GetTimeInSeconds` | clock | `XR_KHR_convert_timespec_time` |
| 3 | `vrapi_GetPredictedTracking2` | `(ovr, double time)` → `ovrTracking2` by value (head pose + per-eye matrices) | `xrLocateViews` + `xrLocateSpace` |
| 5 | `vrapi_EnumerateInputDevices` | index 0–3 → devices below, index 4 → -1010 (end) | fixed list |
| 2 | `vrapi_GetInputDeviceCapabilities` | Touch capabilities | constant table |
| 2 | `vrapi_GetCurrentInputState` | Touch buttons, triggers, sticks, touches | `xrSyncActions` + action states |
| 8 | `vrapi_GetInputTrackingState` | `(ovr, deviceID, double time, ovrTracking* out)`: 4 devices × 2 | `xrLocateSpace` on grip/aim spaces |
| 1 | `vrapi_SubmitFrame2` | one layer, swap interval 1 | `xrEndFrame` with one projection layer |
| occasional | `vrapi_SetHapticVibrationBuffer` | `(ovr, deviceID, buffer*)`, both controllers | `xrApplyHapticFeedback` |

`SubmitFrame2`'s description starts `Flags 0, SwapInterval 1, FrameIndex, DisplayTime
(double), LayerCount 1, Layers*`.

## Input devices

| Index | Type | Device ID | Read every frame |
| --- | --- | --- | --- |
| 0 | 4 (tracked remote: Touch) | `0x20000002` (left) | capabilities, input state, tracking, haptics |
| 1 | 4 (tracked remote: Touch) | `0x20000003` (right) | capabilities, input state, tracking, haptics |
| 2 | `0x80` (hand) | `0x20000007` | tracking only |
| 3 | `0x80` (hand) | `0x20000008` | tracking only |

The button layout inside the input state isn't known yet. The logged frames are from
startup, with nothing pressed. Next run: log the state whenever it changes and press
each button once.

## Events

| Type | Seen when | Name *(likely)* |
| --- | --- | --- |
| 2 | start | visibility gained |
| 4 | start | focus gained |
| 5 | opening another app | focus lost |
| 3 | opening another app | visibility lost |

After losing visibility Echo calls `vrapi_LeaveVrMode`, and Horizon OS later kills the
process (`destroyTimeout`). That's normal, not a crash.

## What this means for the OpenXR version

- **Small, one-to-one surface.** Every per-frame call maps directly to OpenXR. The Vulkan
  setup maps to `XR_KHR_vulkan_enable` almost exactly: Echo asks for the extension lists
  first, then hands over its own instance and device.
- **One projection layer.** No overlays or cylinder layers to emulate.
- **Refresh rate.** Steam Frame's runtime ignores refresh-rate requests (see Frame
  Control), so report the current rate back and Echo is satisfied.
- **Stubs.** Clock levels, extra latency, foveation buffers and the two private properties
  (30, 32) can be accepted and ignored.
- **Fingers.** The game poses fingers from the Touch input state, so the OpenXR version can
  fill those fields from any controller's finger sensing, including Steam Frame's.
