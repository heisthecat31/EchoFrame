// The VrApi structures Echo VR (Quest, build 4987566) passes across libvrapi.so,
// written from what the logger recorded on a Quest 3 (docs/vrapi-calls.md). These are
// our own definitions, not Meta's headers: only the fields Echo uses are named, and
// every offset is checked against the recorded dumps below.
#pragma once
#include <jni.h>
#include <stddef.h>
#include <stdint.h>
#include <vulkan/vulkan.h>

namespace vr {

// results (as Echo saw them returned)
enum : int32_t {
    kSuccess = 0,
    kErrorNotInitialized = -1004,     // BeginFrame before EnterVrMode
    kErrorInvalidParameter = -1005,
    kErrorDeviceUnavailable = -1010,  // EnumerateInputDevices past the last device
    kErrorNotImplemented = -1052,
};

enum : int32_t { kEventNone = 0, kEventVisibilityGained = 2, kEventVisibilityLost = 3, kEventFocusGained = 4, kEventFocusLost = 5 };

// tracking status bits (0x8f = everything valid and tracked, headset connected)
enum : uint32_t { kOrientationTracked = 0x1, kPositionTracked = 0x2, kOrientationValid = 0x4, kPositionValid = 0x8, kHmdConnected = 0x80 };

struct Java { JavaVM* Vm; JNIEnv* Env; jobject ActivityObject; };

struct InitParms {   // vrapi_Initialize: Type 1, version 1.1.40, Vulkan
    int32_t Type, ProductVersion, MajorVersion, MinorVersion, PatchVersion, GraphicsApi;
    Java java;
};
static_assert(offsetof(InitParms, java) == 24, "InitParms");

struct ModeParmsVulkan {   // vrapi_EnterVrMode: Type 5
    int32_t Type;
    uint32_t Flags;
    Java java;
    uint64_t WindowSurface, Display, ShareContext;
    VkQueue SynchronizationQueue;   // the queue Echo submits its rendering to
};
static_assert(offsetof(ModeParmsVulkan, SynchronizationQueue) == 0x38, "ModeParmsVulkan");

struct SystemCreateInfoVulkan { VkInstance Instance; VkPhysicalDevice PhysicalDevice; VkDevice Device; };

struct Quatf { float x, y, z, w; };
struct Vector3f { float x, y, z; };
struct Posef { Quatf Orientation; Vector3f Position; };
struct Matrix4f { float M[4][4]; };   // row-major, column vectors (translation in M[i][3])

struct RigidBodyPosef {
    Posef Pose;
    Vector3f AngularVelocity, LinearVelocity, AngularAcceleration, LinearAcceleration;
    uint32_t Pad;
    double TimeInSeconds;
    double PredictionInSeconds;
};
static_assert(sizeof(RigidBodyPosef) == 96 && offsetof(RigidBodyPosef, TimeInSeconds) == 80, "RigidBodyPosef");

struct Tracking2 {   // returned by value from vrapi_GetPredictedTracking2
    uint32_t Status;
    uint32_t Reserved;
    RigidBodyPosef HeadPose;
    struct { Matrix4f ProjectionMatrix; Matrix4f ViewMatrix; } Eye[2];
};
static_assert(offsetof(Tracking2, HeadPose) == 8 && offsetof(Tracking2, Eye) == 0x68 && sizeof(Tracking2) == 360, "Tracking2");

struct Tracking {    // vrapi_GetInputTrackingState output
    uint32_t Status;
    uint32_t Reserved;
    RigidBodyPosef HeadPose;
};
static_assert(sizeof(Tracking) == 104, "Tracking");

struct LayerHeader2 {
    int32_t Type;     // 1 = projection
    uint32_t Flags;
    float ColorScale[4];
    int32_t SrcBlend, DstBlend;   // 1, 0: opaque
    const void* Reserved;
};
static_assert(sizeof(LayerHeader2) == 40, "LayerHeader2");

struct LayerProjection2 {
    LayerHeader2 Header;
    RigidBodyPosef HeadPose;     // the pose Echo rendered with (TimeInSeconds = the prediction time)
    struct {
        void* ColorSwapChain;    // what CreateTextureSwapChain3 returned
        int32_t SwapChainIndex;  // the image Echo rendered into
        Matrix4f TexCoordsFromTanAngles;
        float TextureRect[4];    // x, y, width, height, 0-1
    } Textures[2];
};
static_assert(offsetof(LayerProjection2, HeadPose) == 0x28 && offsetof(LayerProjection2, Textures) == 0x88 &&
              sizeof(LayerProjection2) == 0x148, "LayerProjection2");

struct SubmitFrameDescription2 {
    uint32_t Flags;
    uint32_t SwapInterval;
    uint64_t FrameIndex;
    double DisplayTime;
    uint64_t Reserved;
    uint32_t LayerCount;
    uint32_t Pad;
    const LayerHeader2* const* Layers;
};
static_assert(offsetof(SubmitFrameDescription2, Layers) == 0x28, "SubmitFrameDescription2");

struct EventHeader { int32_t EventType; };

// input
enum : uint32_t { kControllerTrackedRemote = 4 };
enum : uint32_t { kDeviceLeft = 0x20000002, kDeviceRight = 0x20000003 };

struct InputCapabilityHeader { uint32_t Type; uint32_t DeviceID; };
struct InputTrackedRemoteCapabilities { InputCapabilityHeader Header; uint8_t Rest[40]; };   // replayed from the Quest 3

struct InputStateTrackedRemote {   // 72 bytes, see docs/vrapi-calls.md
    uint32_t ControllerType;
    uint32_t Pad0;
    double TimeInSeconds;
    uint32_t Buttons;
    uint32_t TrackpadStatus;
    float TrackpadPosition[2];     // Echo sees the thumbstick here too
    uint8_t BatteryPercent;
    uint8_t RecenterCount;
    uint16_t Reserved;
    float IndexTrigger;
    float GripTrigger;
    uint32_t Touches;
    uint32_t Reserved2;
    float Joystick[2];
    float JoystickNoDeadZone[2];
    uint32_t Pad1;
};
static_assert(offsetof(InputStateTrackedRemote, Buttons) == 0x10 && offsetof(InputStateTrackedRemote, IndexTrigger) == 0x24 &&
              offsetof(InputStateTrackedRemote, Touches) == 0x2c && offsetof(InputStateTrackedRemote, Joystick) == 0x34 &&
              offsetof(InputStateTrackedRemote, JoystickNoDeadZone) == 0x3c && sizeof(InputStateTrackedRemote) == 72,
              "InputStateTrackedRemote");

enum : uint32_t {
    kButtonA = 0x1, kButtonB = 0x2, kButtonRThumb = 0x4, kButtonX = 0x100, kButtonY = 0x200, kButtonLThumb = 0x400,
    kButtonEnter = 0x00100000,   // the left menu button (from the left controller's capabilities)
    kButtonGrip = 0x04000000, kButtonTrigger = 0x20000000, kButtonJoystick = 0x80000000,
};
enum : uint32_t {
    kTouchA = 0x1, kTouchB = 0x2, kTouchX = 0x4, kTouchY = 0x8, kTouchJoystick = 0x20, kTouchIndexTrigger = 0x40,
    kTouchThumbUp = 0x100, kTouchIndexPointing = 0x200, kTouchLThumb = 0x400, kTouchRThumb = 0x800, kTouchLThumbRest = 0x1000,
};

struct HapticBuffer { double BufferTime; uint32_t NumSamples; bool Terminated; const uint8_t* Samples; };
static_assert(offsetof(HapticBuffer, Samples) == 16, "HapticBuffer");

}  // namespace vr
