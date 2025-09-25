# CachyOS

## Bootloader

I tried using ReFind but it wasn't working, so I selected Grub

## Partitioning and File Systems

Your choices for partitioning and file systems may (and probably should) be different. This is just how I set up my system.

| Drive        | File System | Mount Point     | Note                            |
| ------------ | ----------- | --------------- | ------------------------------- |
| HDD1         | XFS         | /mnt/data1      | These are my data drives/hdds   |
| HDD{2..4}    | XFS         | /mnt/data{2..4} | Using mergerfs to mount them and snapraid for parity |
| HDD5         | XFS         | /mnt/snap1      | The snapraid parity drive       |
| SSD1 Part 1  | FAT32       | /boot/efi       | Boot efi partition, use boot and esp flags |
| SSD1 Part 2  | EXT4        | /home           | Home partition                  |
| SSD1 Part 3  | BTRFS       | /, /root, ...   | Root partition, created by CachyOS |
| SSD2         | XFS         | /mnt/ssd2       | Other SSD for games             |

## Recommended Steps

Read these pages from the CachyOS guide and apply the modifications or install the apps you need.

<div class="annotate" markdown>
- [Configure WiFi Regulatory Domain](https://wiki.cachyos.org/configuration/post_install_setup/#configure-wi-fi-regulatory-domain) (1)
- [Update and use tldr](https://wiki.cachyos.org/configuration/post_install_setup/#updatingusing-tldr)
- [Setup AppImages](https://wiki.cachyos.org/configuration/post_install_setup/#managing-appimages) (2)
- Follow the guide for optimizing/setting up some gaming stuff | [Gaming with CachyOS Guide](https://wiki.cachyos.org/configuration/gaming/)
  - Install CachyOS gaming packages (google it if you want to select what to install)
    ```shell
    sudo pacman -S cachyos-gaming-meta cachyos-gaming-applications
    ```
  - [Setup umu-launcher in Lutris](https://wiki.cachyos.org/configuration/gaming/#umu-launcher-setup)
  - [Add game-performance to Lutris](https://wiki.cachyos.org/configuration/gaming/#tab-panel-22)
  - [Increate maximum shader cache size](https://wiki.cachyos.org/configuration/gaming/#increase-maximum-shader-cache-size)
  - Install OBS Studio from CachyOS and VKCapture
    ```shell
    sudo pacman -S obs-studio-browser # (3)!
    paru obs-vkcapture-git # (4)!
    ```
</div>

1. Might unlock some extra wifi bands
2. Install appimagelauncher to have a GUI for installing AppImages
3. <https://wiki.cachyos.org/configuration/general_system_tweaks/#obs-studio>
4. <https://github.com/nowrep/obs-vkcapture>
