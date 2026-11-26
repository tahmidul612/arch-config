# VRR blanking on NVIDIA

With VRR (FreeSync / adaptive sync) turned on, the main monitor keeps going black for a moment. This page records the cause, what was tried, and the setup that fixed it.

## Setup where it happened

| Part | Value |
| --- | --- |
| GPU | RTX 3080, NVIDIA driver 620.70.06 |
| Desktop | KDE Plasma 6.8 (KWin 6.8.3), Wayland |
| Monitor | LG HDR 4K, 3840x2160 at 60 Hz, over DisplayPort (`DP-1`) |
| Monitor VRR range | 40–61 Hz with FreeSync **Extended**, about 50–60 Hz with FreeSync **Basic** |
| Goal | Smoother Minecraft, whose 1% lows sit at 40–45 fps and can't be raised further |

## Symptoms

- Adaptive sync set to **Automatic** in KDE, FreeSync Extended on the monitor: the screen blanks whenever something goes fullscreen, such as Minecraft in fullscreen or Spectacle's fullscreen screenshot overlay. Windowed apps are fine.
- Adaptive sync set to **Always**: the screen also blanks at random on an idle desktop, with Minecraft windowed.
- Nothing appears in the kernel log or the KWin log when it blanks. The monitor drops the picture by itself, without a GPU or driver error.

## Cause

NVIDIA's Linux driver has no low framerate compensation (LFC). LFC is what keeps the monitor in range when the fps falls below the VRR minimum. Without it, the refresh rate swings between the minimum and maximum, and the monitor flickers or loses the picture.

- In the [NVIDIA forum thread on LFC](https://forums.developer.nvidia.com/t/lfc-low-framerate-compensation-fails-below-minimum-vrr-range-adaptive-sync-oscillates-between-min-and-max/379210), NVIDIA support called it a known limitation of LFC in NVKMS, and said engineering is evaluating the work to add it. A reporter on the same 620.70.06 driver still has the problem (November 2026). Their only workaround is a fixed refresh rate.
- [KDE bug 505812](https://bugs.kde.org/show_bug.cgi?id=505812) covers Spectacle blanking the screen on NVIDIA. A KWin developer replied that it is "most likely a driver bug around adaptive sync handling with low fps content".
- The [NVIDIA forum thread on black frames with VRR](https://forums.developer.nvidia.com/t/linux-wayland-intermittent-black-frames-on-display-with-vrr-enabled-rtx-4090-driver-595-71-05/369187) has a similar report from GNOME. The workaround there is also to turn VRR off.

Under **Automatic**, KWin turns VRR on only while a fullscreen window is in front. That explains why only fullscreen blanked at first. **Always** keeps VRR on all the time, so it blanks everywhere.

## What doesn't work

- **FreeSync Basic:** the range shrinks to about 50–60 Hz, so the 40–45 fps lows fall below it anyway.
- **Raising the lows:** the performance mods and settings tried didn't get the lows above 45 fps.
- **Editing the EDID to lower the VRR minimum:** per a reading of NVIDIA's open driver code in the LFC thread, the setting for the VRR minimum can only move about 10 Hz from the monitor's own value, and the driver never sets it. Neither Wayland nor KDE can reach it.
- **Switching VRR from Automatic to Always:** this stops VRR turning on and off at each fullscreen change, but the blanking still happens on an idle desktop.

## Fix

Turn VRR off on both ends and let the game sync to the fixed 60 Hz.

1. **KDE: set adaptive sync to Never.** This stops KWin sending a variable refresh rate, which is what triggers the driver bug.
    ```bash
    kscreen-doctor output.DP-1.vrrpolicy.never
    ```
    The same setting is under System Settings → Display & Monitor → Adaptive sync.
2. **Monitor OSD: set FreeSync to Off.** With adaptive sync on Never it does nothing, and Off is the simplest setup.
3. **Monitor OSD: set response time to Normal.** This has nothing to do with the blanking: Fastest overdrive causes inverse ghosting (bright trails behind moving things).
4. **Minecraft: turn VSync on, or set Max Framerate to 60.** With VRR off this keeps frames paced to the 60 Hz panel and stops tearing.

Check it:

```bash
kscreen-doctor -o | grep -A20 DP-1 | grep Vrr
```

It should print `Vrr: Never`. Then go fullscreen in Minecraft, take a fullscreen screenshot, and leave the desktop idle for a few minutes. The screen should not blank.

To read the monitor's current VRR range (it changes with the FreeSync mode):

```bash
edid-decode /sys/class/drm/card1-DP-1/edid | grep -i range
```

!!! note "Option not taken: a fixed 45 Hz mode"
    With FreeSync Extended on, the monitor accepts 40–61 Hz, so KDE can add a custom 3840x2160 mode at 45 Hz. Capping Minecraft at 45 fps then gives evenly paced frames without VRR. It was not tried, and the monitor may refuse 45 Hz when FreeSync is Off, because the range it reports gets narrower.

## When to try VRR again

Check the LFC thread or the NVIDIA driver release notes for LFC support in NVKMS. Once a driver adds it, set FreeSync back to Extended and adaptive sync to Automatic, then repeat the checks above.

If the screen blanks even with VRR off, the cause is something else. Look at the DisplayPort cable and link next.
