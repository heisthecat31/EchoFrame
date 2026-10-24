// CV1 bridge wire format: Rift CV1 Touch controllers, read on a PC (cv1bridge/cv1bridge.c), sent to
// Echo on a standalone Quest (vrapi_openxr.cpp, "Rift Touch over Wi-Fi") as UDP. Little-endian, packed.
//
//   Quest -> broadcast :47812   kHello once a second while Echo runs: the PC learns where the Quest is
//   PC    -> Quest     :47811   kState about 250 times a second: both controllers
//   Quest -> PC        :47812   kHaptic (Echo's rumble for a Touch), kStatus (a line for the PC to show)
#pragma once
#include <stdint.h>

#define CV1_QUEST_PORT 47811
#define CV1_PC_PORT 47812
#define CV1_VERSION 4

enum {
    CV1_HELLO = 0x4F4C4851,    // "QHLO"
    CV1_STATE = 0x54535843,    // "CXST"
    CV1_HAPTIC = 0x50414851,   // "QHAP"
    CV1_STATUS = 0x54535351,   // "QSST"
};

// CV1Hand.bits: what the hand is doing, the same for either Touch (A/X, B/Y are primary/secondary)
enum {
    CV1_PRIMARY = 1u << 0, CV1_SECONDARY = 1u << 1, CV1_STICK_CLICK = 1u << 2, CV1_MENU = 1u << 3,
    CV1_TOUCH_PRIMARY = 1u << 8, CV1_TOUCH_SECONDARY = 1u << 9, CV1_TOUCH_STICK = 1u << 10,
    CV1_TOUCH_TRIGGER = 1u << 11, CV1_TOUCH_THUMBREST = 1u << 12,
    CV1_CONNECTED = 1u << 15,
};

// CV1Hand.status: LibOVR's ovrStatusBits
enum { CV1_ORIENTATION_TRACKED = 1, CV1_POSITION_TRACKED = 2, CV1_ORIENTATION_VALID = 4, CV1_POSITION_VALID = 8 };

#pragma pack(push, 1)
typedef struct {
    uint32_t status;
    float orientation[4];       // x y z w, in the Rift's floor-level tracking space (Y up, meters)
    float position[3];
    float angularVelocity[3];   // rad/s, tracking space
    float linearVelocity[3];    // m/s, tracking space
    float angularAcceleration[3];   // rad/s^2, tracking space
    float linearAcceleration[3];    // m/s^2, tracking space
    uint32_t bits;
    float trigger, grip, stickX, stickY;
} CV1Hand;

typedef struct {
    uint32_t magic;             // CV1_STATE
    uint16_t version;           // CV1_VERSION
    uint16_t calibrate;         // changes when the PC asks the Quest to align (a counter)
    uint32_t sequence;
    CV1Hand hands[2];           // left, right
} CV1State;

typedef struct {
    uint32_t magic;             // CV1_HELLO
    uint16_t version;
    uint16_t calibrated;        // 1: the Quest has an alignment saved
} CV1Hello;

typedef struct {
    uint32_t magic;             // CV1_HAPTIC
    uint32_t hand;              // 0 left, 1 right
    float amplitude;            // 0..1; 0 stops
    float seconds;
} CV1Haptic;

typedef struct {
    uint32_t magic;             // CV1_STATUS
    char text[124];             // NUL-terminated
} CV1Status;
#pragma pack(pop)
