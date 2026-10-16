// cv1bridge: Rift CV1 Touch controllers for Echo VR on a standalone Quest.
//
// Reads both Touch controllers from the Oculus runtime (the Rift and its sensors stay on this PC)
// and sends them over Wi-Fi to Echo on the Quest (EchoQuestXR's runtime, runtime/cv1_protocol.h),
// about 250 times a second. The Quest is found by itself (it says hello on the network while Echo
// runs); give its IP address as the argument if your network blocks broadcasts. Echo's rumble comes
// back and plays on the Touch.
//
// The Oculus runtime only tracks the controllers while the Rift is "worn": cover the proximity
// sensor inside the Rift (between the lenses, above the nose) with a bit of tape. cv1bridge runs as
// the Rift's VR app, showing it a small black picture every frame: the runtime only gives rumble to
// the app in focus, and keeps the Rift awake while an app draws to it.
//
// EchoFrame (patcher/gui.pyw, the Rift Touch step) runs it with its input on a pipe and shows what
// it prints; it quits when EchoFrame does. Commands, typed or from the pipe:
//   H   test the rumble
//   C   align the Rift's space with the Quest's (or on the Touches: press X, Y, A and B together),
//       then move both hands around, turning the wrists, for 5 seconds (the Quest's hand tracking
//       sees the hands holding the Touches)
//   Q   quit
#define WIN32_LEAN_AND_MEAN
#define COBJMACROS
#define _CRT_SECURE_NO_WARNINGS
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <d3d11.h>
#include <dxgi.h>
#include <conio.h>
#include <mmsystem.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "OVR_CAPI.h"
#include "OVR_CAPI_D3D.h"
#include "../runtime/cv1_protocol.h"

static uint32_t HandBits(const ovrInputState* in, int hand, unsigned connected) {
    const unsigned b = in->Buttons, t = in->Touches;
    uint32_t bits = 0;
    if (hand == 0) {
        if (b & ovrButton_X) bits |= CV1_PRIMARY;
        if (b & ovrButton_Y) bits |= CV1_SECONDARY;
        if (b & ovrButton_LThumb) bits |= CV1_STICK_CLICK;
        if (b & ovrButton_Enter) bits |= CV1_MENU;
        if (t & ovrTouch_X) bits |= CV1_TOUCH_PRIMARY;
        if (t & ovrTouch_Y) bits |= CV1_TOUCH_SECONDARY;
        if (t & ovrTouch_LThumb) bits |= CV1_TOUCH_STICK;
        if (t & ovrTouch_LIndexTrigger) bits |= CV1_TOUCH_TRIGGER;
        if (t & ovrTouch_LThumbRest) bits |= CV1_TOUCH_THUMBREST;
        if (connected & ovrControllerType_LTouch) bits |= CV1_CONNECTED;
    } else {
        if (b & ovrButton_A) bits |= CV1_PRIMARY;
        if (b & ovrButton_B) bits |= CV1_SECONDARY;
        if (b & ovrButton_RThumb) bits |= CV1_STICK_CLICK;
        if (t & ovrTouch_A) bits |= CV1_TOUCH_PRIMARY;
        if (t & ovrTouch_B) bits |= CV1_TOUCH_SECONDARY;
        if (t & ovrTouch_RThumb) bits |= CV1_TOUCH_STICK;
        if (t & ovrTouch_RIndexTrigger) bits |= CV1_TOUCH_TRIGGER;
        if (t & ovrTouch_RThumbRest) bits |= CV1_TOUCH_THUMBREST;
        if (connected & ovrControllerType_RTouch) bits |= CV1_CONNECTED;
    }
    return bits;
}

static char g_log[MAX_PATH];   // cv1bridge-tracking.log beside cv1bridge.exe

// The next command: a key typed in the console, or a byte on the pipe EchoFrame writes to (0 for
// none; 'q' when that pipe closes, so cv1bridge goes when EchoFrame does).
static int NextCommand(void) {
    static int mode = -1;   // 0 console, 1 pipe, 2 neither
    HANDLE in = GetStdHandle(STD_INPUT_HANDLE);
    if (mode < 0) {
        DWORD type = GetFileType(in);
        mode = type == FILE_TYPE_CHAR ? 0 : type == FILE_TYPE_PIPE ? 1 : 2;
    }
    if (mode == 0) return _kbhit() ? _getch() : 0;
    if (mode == 1) {
        DWORD avail = 0, got = 0;
        char c;
        if (!PeekNamedPipe(in, NULL, 0, NULL, &avail, NULL)) return 'q';
        if (!avail) return 0;
        if (!ReadFile(in, &c, 1, &got, NULL) || !got) return 'q';
        return c;
    }
    return 0;
}

// Shows the Rift a small black picture every frame, so cv1bridge is its VR app in focus (rumble goes
// to that app only) and the runtime keeps the Rift awake.
static volatile LONG g_running = 1;
static ovrGraphicsLuid g_luid;
static DWORD WINAPI Frames(LPVOID arg) {
    ovrSession session = (ovrSession)arg;
    IDXGIFactory1* factory = NULL;
    IDXGIAdapter1* adapter = NULL;
    if (SUCCEEDED(CreateDXGIFactory1(&IID_IDXGIFactory1, (void**)&factory))) {
        IDXGIAdapter1* a;
        for (UINT i = 0; IDXGIFactory1_EnumAdapters1(factory, i, &a) == S_OK; ++i) {
            DXGI_ADAPTER_DESC1 d;
            IDXGIAdapter1_GetDesc1(a, &d);
            if (!memcmp(&d.AdapterLuid, &g_luid, sizeof(LUID))) { adapter = a; break; }
            IDXGIAdapter1_Release(a);
        }
    }
    ID3D11Device* device = NULL;
    ID3D11DeviceContext* context = NULL;
    if (FAILED(D3D11CreateDevice((IDXGIAdapter*)adapter, adapter ? D3D_DRIVER_TYPE_UNKNOWN : D3D_DRIVER_TYPE_HARDWARE, NULL, 0,
                                 NULL, 0, D3D11_SDK_VERSION, &device, NULL, &context))) {
        printf("\nno Direct3D 11 device: the Rift may sleep and there's no rumble\n");
        return 0;
    }
    ovrTextureSwapChainDesc desc = { 0 };
    desc.Type = ovrTexture_2D;
    desc.Format = OVR_FORMAT_R8G8B8A8_UNORM_SRGB;
    desc.ArraySize = 1;
    desc.Width = desc.Height = 16;
    desc.MipLevels = desc.SampleCount = 1;
    desc.BindFlags = ovrTextureBind_DX_RenderTarget;
    ovrTextureSwapChain chain;
    if (OVR_FAILURE(ovr_CreateTextureSwapChainDX(session, (IUnknown*)device, &desc, &chain))) {
        printf("\ncouldn't make the Rift's picture: it may sleep and there's no rumble\n");
        return 0;
    }
    ovrHmdDesc hmd = ovr_GetHmdDesc(session);
    ovrLayerEyeFov layer = { 0 };
    layer.Header.Type = ovrLayerType_EyeFov;
    for (int e = 0; e < 2; ++e) {
        layer.ColorTexture[e] = chain;
        layer.Viewport[e].Size.w = layer.Viewport[e].Size.h = 16;
        layer.Fov[e] = hmd.DefaultEyeFov[e];
        layer.RenderPose[e].Orientation.w = 1;
    }
    const ovrLayerHeader* layers = &layer.Header;
    long long index = 0;
    while (g_running) {
        if (OVR_FAILURE(ovr_WaitToBeginFrame(session, index)) || OVR_FAILURE(ovr_BeginFrame(session, index))) { Sleep(100); continue; }
        ovr_CommitTextureSwapChain(session, chain);
        if (OVR_FAILURE(ovr_EndFrame(session, index, NULL, &layers, 1))) Sleep(100);
        ++index;
    }
    ovr_DestroyTextureSwapChain(session, chain);
    ID3D11DeviceContext_Release(context);
    ID3D11Device_Release(device);
    return 0;
}

static void Fail(const char* what) {
    ovrErrorInfo e;
    ovr_GetLastErrorInfo(&e);
    fprintf(stderr, "%s failed: %s\nIs the Oculus app installed and running, with the Rift plugged in?\n", what, e.ErrorString);
    exit(1);
}

// v rotated by q (controller axes -> room axes)
static ovrVector3f Rotate(ovrQuatf q, ovrVector3f v) {
    // t = 2 (q.xyz x v); v' = v + w t + q.xyz x t
    float tx = 2 * (q.y * v.z - q.z * v.y), ty = 2 * (q.z * v.x - q.x * v.z), tz = 2 * (q.x * v.y - q.y * v.x);
    ovrVector3f r = { v.x + q.w * tx + (q.y * tz - q.z * ty), v.y + q.w * ty + (q.z * tx - q.x * tz), v.z + q.w * tz + (q.x * ty - q.y * tx) };
    return r;
}

static void Rumble(ovrSession session, int hand, float amplitude) {
    ovr_SetControllerVibration(session, hand ? ovrControllerType_RTouch : ovrControllerType_LTouch, amplitude > 0 ? 1.0f : 0, amplitude);
}

int main(int argc, char** argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
    SOCKET s = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    struct sockaddr_in me = { 0 };
    me.sin_family = AF_INET;
    me.sin_port = htons(CV1_PC_PORT);
    me.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(s, (struct sockaddr*)&me, sizeof me) != 0) {
        fprintf(stderr, "can't listen on UDP %d (another cv1bridge running?)\n", CV1_PC_PORT);
        return 1;
    }
    u_long nonblocking = 1;
    ioctlsocket(s, FIONBIO, &nonblocking);

    struct sockaddr_in quest = { 0 };
    int haveQuest = 0;
    if (argc > 1) {
        quest.sin_family = AF_INET;
        quest.sin_port = htons(CV1_QUEST_PORT);
        if (inet_pton(AF_INET, argv[1], &quest.sin_addr) != 1) { fprintf(stderr, "not an IP address: %s\n", argv[1]); return 1; }
        haveQuest = 1;
    }

    ovrInitParams ip = { 0 };
    ip.Flags = ovrInit_RequestVersion;
    ip.RequestedMinorVersion = OVR_MINOR_VERSION;
    if (OVR_FAILURE(ovr_Initialize(&ip))) Fail("ovr_Initialize");
    ovrSession session;
    ovrGraphicsLuid luid;
    if (OVR_FAILURE(ovr_Create(&session, &luid))) Fail("ovr_Create");
    g_luid = luid;
    ovr_SetTrackingOriginType(session, ovrTrackingOrigin_FloorLevel);
    HANDLE frames = CreateThread(NULL, 0, Frames, session, 0, NULL);
    GetModuleFileNameA(NULL, g_log, MAX_PATH);
    char* dot = strrchr(g_log, '.');
    if (dot) strcpy(dot, "-tracking.log");

    printf("cv1bridge: Rift Touch -> Echo on the Quest.   H  test rumble    C  align    Q  quit\n");
    printf(haveQuest ? "sending to %s\n" : "waiting for Echo on the Quest (start it there)...\n", argc > 1 ? argv[1] : "");

    timeBeginPeriod(1);
    CV1State st = { 0 };
    st.magic = CV1_STATE;
    st.version = CV1_VERSION;
    double hapticEnd[2] = { 0, 0 };
    float lastVelocity[2][3] = { { 0 } };   // each Touch's last velocity with its position valid, and when
    double lastValid[2] = { -1, -1 };
    double nextLine = 0, lastHello = -1;
    int sent = 0, questCalibrated = -1, warnedMount = 0, rumbles = 0;
    double lostSince[2] = { -1, -1 }, longest[2] = { 0, 0 };
    float lostAt[2][3] = { { 0 } };
    int drops[2] = { 0, 0 };
    FILE* trackLog = fopen(g_log, "a");
    for (;;) {
        double now = ovr_GetTimeInSeconds();
        // from the Quest: hello, rumble, status lines
        char buf[256];
        struct sockaddr_in from;
        int fl = sizeof from, n;
        while ((n = recvfrom(s, buf, sizeof buf, 0, (struct sockaddr*)&from, &fl)) > 0) {
            uint32_t magic;
            memcpy(&magic, buf, 4);
            if (magic == CV1_HELLO && n == sizeof(CV1Hello)) {
                CV1Hello h;
                memcpy(&h, buf, sizeof h);
                if (h.version != CV1_VERSION) {
                    static int warned;
                    if (!warned++) printf("\nThe Quest's Echo speaks bridge version %u, this cv1bridge %u: update both.\n", h.version, CV1_VERSION);
                }
                if (!haveQuest || quest.sin_addr.s_addr != from.sin_addr.s_addr) {
                    char ipText[32];
                    inet_ntop(AF_INET, &from.sin_addr, ipText, sizeof ipText);
                    printf("\nEcho found on the Quest at %s\n", ipText);
                    quest = from;
                    quest.sin_port = htons(CV1_QUEST_PORT);
                    haveQuest = 1;
                }
                if (h.calibrated != questCalibrated) {
                    questCalibrated = h.calibrated;
                    if (!h.calibrated)
                        printf("\nNot aligned yet: with a Touch in each hand, press X, Y, A and B together (or C here),\n"
                               "then move both hands around, turning your wrists, for 5 s.\n");
                }
                lastHello = now;
            } else if (magic == CV1_HAPTIC && n == sizeof(CV1Haptic)) {
                CV1Haptic h;
                memcpy(&h, buf, sizeof h);
                if (h.hand < 2) {
                    float amp = h.amplitude < 0 ? 0 : h.amplitude > 1 ? 1 : h.amplitude;
                    Rumble(session, h.hand, amp);
                    hapticEnd[h.hand] = amp > 0 ? now + (h.seconds < 0.02f ? 0.02f : h.seconds) : 0;
                    ++rumbles;
                }
            } else if (magic == CV1_STATUS && n == sizeof(CV1Status)) {
                CV1Status m;
                memcpy(&m, buf, sizeof m);
                m.text[sizeof m.text - 1] = 0;
                printf("\nQuest: %s\n", m.text);
            }
            fl = sizeof from;
        }
        for (int h = 0; h < 2; ++h)
            if (hapticEnd[h] > 0 && now >= hapticEnd[h]) {
                Rumble(session, h, 0);
                hapticEnd[h] = 0;
            }
        // commands
        for (int k; (k = NextCommand()) != 0;) {
            if (k == 'q' || k == 'Q') goto done;
            if (k == 'h' || k == 'H')
                for (int h = 0; h < 2; ++h) {
                    Rumble(session, h, 0.8f);
                    hapticEnd[h] = now + 0.3;
                }
            if (k == 'c' || k == 'C') {
                st.calibrate++;
                printf("\naligning: move both hands around, turning your wrists, for 5 s\n");
            }
        }
        // the controllers, a little ahead (the Wi-Fi's and Echo's delay)
        ovrTrackingState ts = ovr_GetTrackingState(session, now + 0.010, ovrFalse);
        ovrInputState in;
        memset(&in, 0, sizeof in);
        ovr_GetInputState(session, ovrControllerType_Touch, &in);
        unsigned connected = ovr_GetConnectedControllerTypes(session);
        for (int h = 0; h < 2; ++h) {
            const ovrPoseStatef* p = &ts.HandPoses[h];
            CV1Hand* o = &st.hands[h];
            o->status = ts.HandStatusFlags[h];
            o->orientation[0] = p->ThePose.Orientation.x; o->orientation[1] = p->ThePose.Orientation.y;
            o->orientation[2] = p->ThePose.Orientation.z; o->orientation[3] = p->ThePose.Orientation.w;
            o->position[0] = p->ThePose.Position.x; o->position[1] = p->ThePose.Position.y; o->position[2] = p->ThePose.Position.z;
            // The Oculus runtime gives a Touch's turn rate (and its change) in the controller's own
            // axes (measured: 0.8 rad/s off the true rotation that way, 5.8 the other); Echo wants
            // them in the room's, like the velocity
            ovrVector3f w = Rotate(p->ThePose.Orientation, p->AngularVelocity);
            ovrVector3f wa = Rotate(p->ThePose.Orientation, p->AngularAcceleration);
            o->angularVelocity[0] = w.x; o->angularVelocity[1] = w.y; o->angularVelocity[2] = w.z;
            o->linearVelocity[0] = p->LinearVelocity.x; o->linearVelocity[1] = p->LinearVelocity.y; o->linearVelocity[2] = p->LinearVelocity.z;
            o->angularAcceleration[0] = wa.x; o->angularAcceleration[1] = wa.y; o->angularAcceleration[2] = wa.z;
            o->linearAcceleration[0] = p->LinearAcceleration.x; o->linearAcceleration[1] = p->LinearAcceleration.y;
            o->linearAcceleration[2] = p->LinearAcceleration.z;
            // A flick can take a Touch out of the sensors' sight long enough that the runtime gives
            // up on its position, and its velocity drops to nothing: a disc let go then would just
            // fall. For up to 150 ms of that, keep the last real velocity (and call the position
            // valid), so the throw goes as fast as the hand did.
            // dropouts: when the sensors lose a Touch, for how long, and where it was (logged to
            // cv1bridge-tracking.log, counted on the status line)
            int seen = (ts.HandStatusFlags[h] & ovrStatus_PositionTracked) != 0;
            if (!seen && lostSince[h] < 0 && (o->bits & CV1_CONNECTED)) {
                lostSince[h] = now;
                memcpy(lostAt[h], o->position, sizeof lostAt[h]);
            } else if (seen && lostSince[h] >= 0) {
                double ms = (now - lostSince[h]) * 1000;
                if (ms > 30) {
                    drops[h]++;
                    if (ms > longest[h]) longest[h] = ms;
                    if (trackLog) {
                        fprintf(trackLog, "%.3f %s lost %.0f ms at %.2f %.2f %.2f (m; y is height above the floor)\n", now,
                                h ? "right" : "left", ms, lostAt[h][0], lostAt[h][1], lostAt[h][2]);
                        fflush(trackLog);
                    }
                }
                lostSince[h] = -1;
            }
            if (o->status & ovrStatus_PositionValid) {
                memcpy(lastVelocity[h], o->linearVelocity, sizeof lastVelocity[h]);
                lastValid[h] = now;
            } else if (now - lastValid[h] < 0.15) {
                memcpy(o->linearVelocity, lastVelocity[h], sizeof lastVelocity[h]);
                o->status |= ovrStatus_PositionValid;
            }
            o->bits = HandBits(&in, h, connected);
            o->trigger = in.IndexTrigger[h];
            o->grip = in.HandTrigger[h];
            o->stickX = in.Thumbstick[h].x;
            o->stickY = in.Thumbstick[h].y;
        }
        st.sequence++;
        if (haveQuest) {
            sendto(s, (const char*)&st, sizeof st, 0, (struct sockaddr*)&quest, sizeof quest);
            sent++;
        }
        if (now >= nextLine) {
            nextLine = now + 1;
            ovrSessionStatus ss;
            ovr_GetSessionStatus(session, &ss);
            if (!ss.HmdMounted && !warnedMount) {
                warnedMount = 1;
                printf("\nThe Rift isn't tracking the controllers: cover its proximity sensor (inside, above the nose) with tape.\n");
            }
            if (ss.HmdMounted) warnedMount = 0;
            const char* link = !haveQuest ? "no Quest yet" : (lastHello >= 0 && now - lastHello < 3) ? "Echo running" : "Echo not answering";
            if (drops[0] || drops[1])
                printf("\ntracking lost: left %d time(s) (longest %.0f ms), right %d (longest %.0f ms)\n", drops[0], longest[0], drops[1], longest[1]);
            drops[0] = drops[1] = 0;
            longest[0] = longest[1] = 0;
            printf("\r%-18s %3d/s  left %s%s  right %s%s  rumble %d%s    ", link, sent,
                   (st.hands[0].bits & CV1_CONNECTED) ? "on" : "off", (st.hands[0].status & CV1_POSITION_TRACKED) ? " tracked" : "        ",
                   (st.hands[1].bits & CV1_CONNECTED) ? "on" : "off", (st.hands[1].status & CV1_POSITION_TRACKED) ? " tracked" : "        ",
                   rumbles, ss.HasInputFocus ? "" : "  (no focus: close other Rift apps)");
            sent = 0;
            rumbles = 0;
        }
        Sleep(4);
    }
done:
    g_running = 0;
    WaitForSingleObject(frames, 1000);
    Rumble(session, 0, 0);
    Rumble(session, 1, 0);
    timeEndPeriod(1);
    ovr_Destroy(session);
    ovr_Shutdown();
    return 0;
}
