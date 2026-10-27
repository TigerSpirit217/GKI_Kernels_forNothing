# Kernel+TTL Patch

在 Actions 中运行 **Kernel+TTL Patch**。默认目标为 **5.10.233-android12-9**
（`5.10` / `2025-02`，上游 `build.config.common` 的 `KMI_GENERATION=9`）。
默认启用 SukiSU builtin、SUSFS、ZRAM、BBG 和 KPM；其余菜单与原来的
“Android 内核构建-自定义”一致。`ksu_mode=关闭` 表示不禁用 KSU / SUSFS，
因此 SukiSU 会沿用原流程的 `builtin` 分支选择。

改选其他内核时，也需要选择该内核有效的安全补丁月份；`lts` 表示跟随该系列
的 LTS。版本解析、源码下载、SUSFS 等补丁、编译、管理器下载以及 AnyKernel3
打包均复用 `kernel-custom.yml → build.yml`，没有单独复制构建流程。
自定义版本名留空即可保留上游 KMI 标识。产物名称增加 `-TTL` 以区分普通构建。

**强制 LTO=thin** 默认关闭。开启后，经典 `build/build.sh` 构建必传
`LTO=thin`，Bazel / Kleaf 构建必传 `--lto=thin`，并检查最终生成的
`.config` 包含 `CONFIG_LTO_CLANG_THIN=y`；没有生效或找不到配置时会直接失败。
关闭时不传 LTO 覆盖参数，保留上游的 LTO 选择和校验。开启时允许用户指定的
ThinLTO，不再被原流程的禁止 ThinLTO 校验拒绝。

## 补丁行为

`scripts/ttl_patch.py` 支持 GKI 5.10、5.15、6.1、6.6、6.12，在以下路径添加
`CONFIG_IP_FORWARD_PRESERVE_TTL` 条件，并在 `gki_defconfig` 中启用：

- IPv4 单播转发 `ip_forward()`：不递减 TTL，也不执行相应的校验和调整。
- IPv6 单播转发 `ip6_forward()`：不递减 Hop Limit。
- Netfilter 软件 flowtable：同样保持 IPv4 TTL / IPv6 Hop Limit。

保留原来的 TTL / Hop Limit ≤ 1 过期检查。该功能影响经过上述路径的所有
单播转发（两个方向，包括 USB 网络共享），不局限于某个热点接口；不修改
本机发包的初始 TTL、组播或隧道专用转发路径。转发时不消耗跳数会降低路由环路
的抑制能力，因此只应在需要这一行为的设备上启用。

补丁会在修改文件前检查所有目标函数，重复应用保持不变；不认识的源码结构会
直接让构建失败。编译后还会检查实际 `.config`，确认配置没有被构建系统丢弃。
共享工作流的 `use_ttl_patch` 默认 `false`，原有入口不启用此功能。

## 刷入与热点加速

按原流程下载 AnyKernel3 刷入包并刷入。完整下载的 GitHub artifact ZIP 中，
“仅上传 AnyKernel3.zip”模式直接包含 `Image`、`anykernel.sh` 等刷入文件；
“上传全部”模式则包含单独的 `*-TTL-AnyKernel3.zip` 及 boot 镜像。

**内核补丁不能控制 Android 用户空间的 tethering BPF 程序和芯片硬件卸载。**
这些加速路径可能仍然递减 TTL / Hop Limit，并绕过内核普通转发路径。
以 root 执行以下命令，然后重启手机，再开启热点：

```sh
su
settings put global tether_offload_disabled 1
device_config put connectivity override_tether_enable_bpf_offload false
reboot
```

原生 AOSP 使用 `connectivity / override_tether_enable_bpf_offload` 控制 BPF
卸载，硬件卸载使用 `tether_offload_disabled`。OEM ROM 可能有额外开关或忽略
上述设置；构建成功不代表已完成真机热点验证。可检查：

```sh
settings get global tether_offload_disabled
device_config get connectivity override_tether_enable_bpf_offload
dumpsys tethering
```

返回值应分别是 `1`、`false`，并结合 `dumpsys` 确认加速实际关闭。关闭卸载可能
增加 CPU 使用率、降低共享带宽。最终应在受控测试网络的手机下游及上游抓取同一
数据包，确认 TTL / Hop Limit 相同；仅观察远端收到的 TTL 无法排除其他路由器的影响。

恢复设置时，若这两项原先不存在，可执行下面的命令并重启；如原先有自定义值，
应恢复记录的原值：

```sh
settings delete global tether_offload_disabled
device_config delete connectivity override_tether_enable_bpf_offload
```

上游依据：[GKI 5.10.233 配置](https://android.googlesource.com/kernel/common/+/refs/tags/android12-5.10-2025-02_r1/build.config.common)、
[AOSP Tethering 配置](https://android.googlesource.com/platform/packages/modules/Connectivity/+/refs/heads/android12-release/Tethering/src/com/android/networkstack/tethering/TetheringConfiguration.java)、
[AOSP BPF 转发](https://android.googlesource.com/platform/packages/modules/Connectivity/+/8f105009afb58545c0bca6bf512c5b2f03f32a5c/bpf_progs/offload.c)。
