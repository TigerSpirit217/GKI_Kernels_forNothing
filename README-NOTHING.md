# Nothing 设备内核构建

本分支在原 GKI 项目上增加 Nothing 适配层。**原有工作流、补丁、配置、网页和 README 均保持原样**；Nothing 构建入口是 [Nothing 设备内核 / AnyKernel3](.github/workflows/nothing-build.yml)。原项目说明见 [README.md](README.md) 和 [进阶功能文档](docs/advanced-features.md)。

## 使用

1. 将本分支推送至自己的 GitHub 仓库并启用 Actions。
2. 打开 **Actions → Nothing 设备内核 / AnyKernel3 → Run workflow**。
3. 从 `model` 下拉框选择机型，从 `version` 下拉框选择官方版本，再勾选需要的功能。自定义版本名和构建时间均可留空。
4. 成功后，从 Artifacts 下载 `Nothing-…-AnyKernel3`，解开 Actions 的外层压缩包，取得里面的 **`*-AnyKernel3.zip`** 和 `.sha256`。

版本菜单包含 11 个机型的官方发布条目，每项显示机型、OS 版本、完整 build 编号和提交短 SHA，后台自动定位分支与完整 SHA，无需填写 JSON、分支或提交编号。默认选项 **“所选机型最新官方发布（运行时查询）”** 会在启动构建时查询该机型的官方发布记录；构建旧系统版本时请选择对应的完整条目。

例如，选择 `Nothing Phone (3a)`，再选择包含 `Asteroids-V3.1-250302-1856` 的版本条目，对应官方 [发布记录](https://github.com/NothingOSS/android_kernel_msm-6.1_nothing_sm7635/blob/sm7635/v/mr/README.md)。

[Actions 原生 `choice` 控件](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs) 使用工作流内的固定选项，不支持机型与版本的联动筛选。因此旧版本条目带机型前缀，选择错配时会在拉取源码前明确提示，不能混用其他机型的版本。

**NothingOSS 同一仓库有多个分支，同一分支内又有多个版本提交。** 本入口会：

- 从机型配置定位 NothingOSS 官方仓库，枚举其分支和 tag，再按机型的分支规则筛选。例如 Tetris、Galaga、Galaxian 共用 mt6878 仓库，但分别对应不同设备，不能混用分支。
- 下拉条目已固定对应分支和 SHA；“最新官方发布”只查询所选机型对应的分支。
- 对 OS/build 版本，逐分支读取 `README.md` 的提交历史，匹配各提交**新增的发布条目**；累计 README 中保留的旧条目不会被当作新版提交的版本。`No update` 发布也按官方记录保留。
- 同一个版本及 SHA 出现在多个分支时去重；同一 build 的不同提交显示各自的短 SHA，选择后固定到该提交。
- 固定并仅检出目标 SHA，检查其所属设备分支和发布记录标识，再读取该提交的 Makefile 和 `build.config.*`，确定内核基线、子版本及 Clang。**手机系统的 Android 版本不能代替源码的 GKI Android 基线。**

查询完整记录时，把 `operation` 改为 `list-versions`，选择机型；产物 `Nothing-official-versions` 内的 JSON 包含分支、版本条目和 SHA，不会编译内核。专家仍可通过 `python -m nothing.source` 使用明确的 ref/SHA、版本号或 `--branch` 参数。

## 机型范围

以下是已配置官方源码映射的机型，**不代表已完成实机启动验证**：

| `model` | 机型 | NothingOSS 仓库 | 内核基线 |
| --- | --- | --- | --- |
| `phone-2` | Phone (2) | [sm8475](https://github.com/NothingOSS/android_kernel_msm-5.10_nothing_sm8475) | 5.10 |
| `phone-2a` | Phone (2a) | [mt6886](https://github.com/NothingOSS/android_kernel_5.15_nothing_mt6886) | 5.15 |
| `phone-3a` / `phone-3a-pro` | Phone (3a) / Pro | [sm7635](https://github.com/NothingOSS/android_kernel_msm-6.1_nothing_sm7635) | 6.1 |
| `phone-3` | Phone (3) | [sm8735](https://github.com/NothingOSS/android_kernel_msm-6.6_nothing_sm8735) | 6.6 |
| `cmf-phone-1` | CMF Phone (1) | [mt6878](https://github.com/NothingOSS/android_kernel_6.1_nothing_mt6878) | 6.1 |
| `cmf-phone-2-pro` | CMF Phone (2) Pro | [mt6878 / Galaga](https://github.com/NothingOSS/android_kernel_6.1_nothing_mt6878/tree/mt6878/Galaga/v) | 6.1 |
| `phone-3a-lite` | Phone (3a) Lite | [mt6878 / Galaxian](https://github.com/NothingOSS/android_kernel_6.1_nothing_mt6878/tree/mt6878/Galaxian/v) | 6.1 |
| `phone-4a` | Phone (4a) | [sm7635](https://github.com/NothingOSS/android_kernel_msm-6.1_nothing_sm7635) | 6.1 |
| `phone-4a-pro` | Phone (4a) Pro | [sm7750](https://github.com/NothingOSS/android_kernel_msm-6.6_nothing_sm7750) | 6.6 |
| `phone-4b` | Phone (4b) | [sm6650](https://github.com/NothingOSS/android_kernel_msm-6.1_nothing_sm6650) | 6.1 |

Actions 显示完整机型名称；命令行也接受代号或型号别名，例如 `Pong`、`A065`。

Phone (1) 的官方 5.4 源码不属于本项目现有 GKI 补丁范围，入口明确拒绝。Phone (2a) Plus 暂未配置独立的官方发布映射。其他未列出的机型需要先在 [nothing/devices.json](nothing/devices.json) 增加经过核对的仓库、分支规则及设备标识映射；不能仅凭相同 SoC 推断为受支持。

Phone (3a) / Pro 的系统可能使用相同 `Asteroids` 设备标识，AnyKernel3 的设备校验也会接受这个共享标识；它不能据此区分这两个型号。共享源码仓库的其他机型仍需选择对应分支与发布提交。

## 补丁与编译

功能均有独立控件，默认不启用 root 或可选补丁：

| 控件 | 用法 |
| --- | --- |
| KernelSU | 下拉选择 None / Official / SukiSU / BakaSU / SukiSU(41013)；普通 SukiSU 跟随上游，原 40726、40548 两个固定版本替换为 41013 |
| SUSFS、KPM | 分别勾选；KPM 需要 SukiSU / BakaSU，SUSFS 需要 KernelSU |
| Droidspaces | 勾选开启；兼容槽位默认 678，另可下拉选择 123 / 345 |
| NTSync | 勾选开启，同时自动启用 Droidspaces |
| ZRAM、BBG、Re-Kernel、NoMount、网络增强、安全修复 | 各自独立勾选 |
| 导出 SUSFS 补丁 | 独立勾选，需要启用 SUSFS 且选择 SukiSU / BakaSU；文件随诊断产物上传 |
| 启用 seccomp 过滤模式 | 默认关闭；适用于 `SukiSU` / `SukiSU(41013)`，保留管理器及授权应用启动时的 seccomp 过滤器；BakaSU 原生支持，无需勾选 |
| 自定义版本名 | 可选，例如 `Tig`；追加到固定的 `内核基础版本-Android-KMI` 末尾，留空则只有固定部分；ZIP 文件名和刷入说明也会显示 |
| 构建时间 | 可选，例如 `2026-10-19 12:30:00 UTC`；留空使用构建时的当前 UTC，也接受原格式 `Sun Dec 01 08:10:00 UTC 2024` |

版本名接受 1–32 位英文字母、数字、点、下划线和连字符，以字母或数字开头。例如本次 5.10.252 / Android 12 / KMI 9 源码，留空生成 `5.10.252-android12-9`，填写 `Tig` 生成 `5.10.252-android12-9-Tig`。其他源码按自身的基础版本和 KMI 生成固定部分。无论是否填写，都不追加 Git 哈希、`dirty` 或 `+`，完整源码 SHA 仍记录在构建清单中。最终版本会在并行编译前由内核生成，检查格式及 64 字节限制。时间会检查真实日期与星期，不正确的输入会在编译前报错。

**仅选择 SukiSU(41013) 时固定版本：** [nothing/sukisu.json](nothing/sukisu.json) 固定内核与管理器提交。启用 SUSFS 时使用 `builtin@7f1c299a` 及固定的配套 SUSFS；未启用时使用破坏性扩展接口改动之前的 `main@ffa00c07`。此选项通过 Make 参数固定 `KSU_VERSION=41013`，避免 SukiSU Makefile 查询在线 main 提交数而将同一份 builtin 源码显示成更高版本。普通 `SukiSU` 完全沿用上游分支选择和 `config/config` 自定义提交配置，不固定内核、SUSFS 或版本号。原 40726 / 40548 配置文件保留供同步上游，Nothing 表单不再提供这两个选项。

选择固定版时，请使用 [41013-6 对应的官方 Manager 构建](https://github.com/SukiSU-Ultra/SukiSU-Ultra/actions/runs/39134589556)，其源码为 `ffa00c0700604216de8a86ad9c74226af1add0d5`。41014 起管理器改用统一扩展接口；单独把内核版本号改成 41013，继续使用 41017-6 管理器，不能消除接口差异。固定 builtin 源码已包含 KPM、Umount 路径和动态管理器的旧接口，但未包含 CPU 身份伪装实现，本适配层不回移新接口或伪报支持。未勾选 seccomp 选项时，保留 SukiSU 原有行为：builtin 会解除管理器及授权应用启动时的过滤器，管理器自身显示“未启用”不等同于内核关闭 `CONFIG_SECCOMP_FILTER`。

**seccomp 可选移植：** [nothing/seccomp.py](nothing/seccomp.py) 在原补丁流程结束后执行，参考 [LingLuo17/AnyKernel3 的过滤模式补丁](https://github.com/LingLuo17/AnyKernel3/commit/841ab62e8af77ee73a1d1538bd3838e33df093cc)，替换 builtin 的六处应用启动路径处理，保留 zygote 安装的过滤器，仅通过缓存放行 `reboot`。辅助函数加入本次下载的 `common/kernel/seccomp.c` 编译单元，直接使用 Nothing 源码自己的私有结构，避免按版本猜测结构偏移；AArch32 的 reboot 编号从该内核的 `unistd32.h` 读取。已经原生保留过滤模式的 SukiSU main 分支不会重复打补丁。选项会要求最终 `CONFIG_SECCOMP=y` / `CONFIG_SECCOMP_FILTER=y`，源码缓存或 hook 结构不兼容时明确失败。开关、处理方式和相关文件 SHA256 记录在 `nothing-build.json` / `nothing-plan.json` 中，编译和打包前复核。SUSFS 导出仍只记录原流程的 SUSFS 差异，复现启用过滤模式的构建还需应用此 Nothing 适配层。

此选项保留的是管理器及授权应用启动时的过滤器，不会给本来没有过滤器的进程凭空安装规则，也不改变 `su` 真正提权时的处理；实机显示仍取决于系统给该进程安装的过滤器。选项不会修复最新 builtin 的其他独立编译问题。

builtin 的编译入口同时兼容旧版 `kernel/ksu.c` 和加入 preinit 后的 `kernel/ksu_main.c`。适配层读取实际生效的 `Kbuild` / `Makefile`，确认入口包含应用启动 hook，并把入口与构建规则的摘要写入清单；源码里残留一个未参与编译的文件不会被误判为集成成功。

构建过程中，[nothing/upstream.py](nothing/upstream.py) 会读取**当前仓库**的 [.github/workflows/build.yml](.github/workflows/build.yml)，生成临时 composite action，继承依赖获取及补丁区间内的步骤。[nothing/form.py](nothing/form.py) 负责把界面控件转换为上游需要的值并保留布尔类型，用户无需编辑 JSON。上游新增的区间内步骤会被继承；新增功能输入需在表单中暴露，测试会检查当前上游功能是否都有控件。步骤定位或输入契约不兼容时，检查直接失败，要求维护者审核适配层。

Nothing 适配层另外负责：

- 拉取选定 NothingOSS 提交，使用该源码声明的官方 Android Clang、Android 基线与 KMI generation，以源码自带的 `gki_defconfig` 编译 arm64 GKI `Image`。新版 `setlocalversion` 使用与源码一致的 KMI 后缀。
- 跳过通用源码下载、通用内核重命名、其他厂商专用修复和通用 boot 镜像生成；版本名按本文的固定规则生成。
- 对齐上游使用 ThinLTO，保留源码启用的 CFI，并在 Kconfig 同步后校验，避免默认 Full LTO 的链接内存开销。构建使用独立进程组、普通日志文件和 120 分钟内部时限；失败或收到终止信号后，先通知进程组退出，3 秒后仍有残留则发送 SIGKILL。工作流通过 `exec` 直接运行监督进程，以便接收 Runner 的停止信号。
- 打补丁后拒绝新增或修改的 `.rej`；在 Kconfig 处理后检查已选择的关键配置是否实际生效。
- 复用上游获取的 AnyKernel3 工具，生成启用设备校验的 Nothing 安装脚本，更新当前槽位的 `boot` 内核。使用实际 boot 镜像保留 ramdisk，不替换 `init_boot`、DTB 或 vendor 模块。
- 产出真正可刷入的 AnyKernel3 ZIP、SHA256，以及源码 SHA、上游工作流摘要、补丁选项、依赖提交、编译器信息和 Image 摘要清单。

这是 **Nothing 官方源码的 GKI 内核替换构建**，不是完整厂商固件或 vendor 模块重编译。正确选取机型和官方版本仍不等于 ABI 与实机启动已验证；首次构建需保留原 boot 以便恢复，并在对应系统版本上验证。补丁在厂商树上发生冲突时会失败，诊断产物会保留 `.rej`、构建日志、实际 `.config` / `auto.conf` 和构建前后内存状态。退出码 143 表示收到 SIGTERM，单凭该退出码不能确认 OOM；可结合 `build-resources.log` 的内存及 cgroup 计数判断。若 Runner 主机已失联或内核陷入不可中断等待，进程组清理无法保证主机立即恢复，仍需 GitHub 终止该运行。

## 同步上游与开发

所有新增内容位于 `nothing/`、`tests/nothing/`、`nothing-*.yml` 和本文档。同步原项目时可直接合并原文件的更新；Nothing 适配层继续引用它们，不需要复制更新补丁实现。每次构建 Nothing 内核前会先运行适配层检查，通过后才开始构建；仅列出官方版本时跳过检查。检查不再由 push 或 PR 自动触发，也可通过 **Actions → Nothing 适配层检查** 手动运行。

维护版本菜单：运行 **Actions → Nothing 更新版本下拉目录**，下载 `Nothing-updated-version-menu`，将其中 `nothing/releases.json` 和 `.github/workflows/nothing-build.yml` 两个文件放回仓库提交。因为 Actions 菜单读取已提交的工作流，下载更新包不会直接改变菜单。默认的“最新官方发布”始终实时查询，不依赖这次更新。也可以本地使用带 GitHub API 读取权限的 `GH_TOKEN` 运行 `python -m nothing.catalog --refresh`。

```bash
python -m pip install -r nothing/requirements.txt
python -m unittest discover -s tests/nothing -v
python -m nothing.catalog --check
bash -n nothing/build.sh
bash -n nothing/anykernel.sh.in

# 查询官方分支与版本（可设置 GH_TOKEN 以提高 GitHub API 配额）
python -m nothing.source --model phone-3a --list-refs
python -m nothing.source --model phone-3a --branch sm7635/v/mr --list-versions
```

内核编译运行在 GitHub Actions Ubuntu 22.04，适配旧版官方 Clang 所需的运行库。版本解析、适配生成和打包测试可在本地运行；本地运行生成器时会创建 `.nothing-actions/` 等临时文件，不应提交这些生成物。

测试使用固定场景覆盖多分支、多版本及 AnyKernel3 结构，不代替完整内核构建或真机验证。适配层需要通过实际 Actions 构建和对应设备启动测试后，才能将该机型版本标记为已验证。
