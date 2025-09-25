# Introduction

## Background

I am a developer who likes to play games occasionally (simplifying myself quite a bit here). For the past few years (pretty much since Windows 11 came out) I have been using Windows full time on my primary desktop. For all my self-hosted applications, web server and other homelab stuff I used Ubuntu Server on a separate machine and later moved it to a VM on Hyper-V. Before that and intermittently over the years, I have also used many other Linux distros. Off the top of my head I remember using Linux Mint, PopOS, XFCE, Lubuntu, Ubuntu, Kali and Parrot. All these systems came with their pros and cons, and usually for me the cons outweighed the pros. After quite a bit of distro hopping, I decided that I had enough fun and settled on Windows. Recently (mid-2025), I found Linus' video about SteamOS being better than ever before and sometimes even outperforming Windows for some games. Then there was also PewDiePie's video about him switching to Linux. I also noticed a general increase in popularity/interest in Linux and OpenSource. All of these combined, plus also the fact that I was wasting a lot of resources running applications in a VM made me consider and eventually wipe my Windows and switch to Linux. After some research I installed Fedora KDE Plasma, but after a month or so switched to CachyOS with KDE Plasma.

## Why CachyOS?

From my initial research I found a lot of recommendation for Fedora, and people praising its stability or something. It also helped that I liked how KDE looked. But I faced a number of issues and annoyances on Fedora, some of them being:

- Constant notifications for SELinux and generally having to deal with it (I understand that it keeps my system secure, but it is so annoying when something I need stops working because SELinux blocked it)
- Frame drops and degraded performance in games (which I know was true because I am getting better performance on CachyOS)

CachyOS solved most of the problems I had with Fedora, and on top of that, gave me a lot of features that I found very useful. I had installed Arch with Hyprland on my Laptop and loved Arch, especially with how easy it was to install basically any application with AUR/Paru/Pacman. I decided on CachyOS for my desktop since it seemed to have some optimizations for games and it was based on Arch. A notable feature that CachyOS has is how they let you choose your Desktop Environment during installation. I stuck to KDE Plasma since I was already used to it and it looked the best to me.

## Installation

To install CachyOS, follow the instructions from <https://cachyos.org/> but for reference, this is what I did:

- Setup Ventoy on a USB drive | [Download Ventoy](https://www.ventoy.net/en/download.html)
- Download and copy CachyOS Desktop Edition ISO to the Ventoy drive | [Download CachyOS](https://cachyos.org/download/)
- Boot into CachyOS from the USB drive and follow the instructions to setup CachyOS
  > Before starting the CachyOS installation, I recommend reading through the [CachyOS Getting Started Guide](https://wiki.cachyos.org/cachyos_basic/download/). It explains a lot of specifics, and generally a good reference for setting up a new Linux system.
