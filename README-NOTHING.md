# Nothing 设备内核构建

本分支在原 GKI 项目上增加 Nothing 适配层。**原有工作流、补丁、配置、网页和 README 均保持原样**；Nothing 构建入口是 [Nothing 设备内核 / AnyKernel3](.github/workflows/nothing-build.yml)。原项目说明见 [README.md](README.md) 和 [进阶功能文档](docs/advanced-features.md)。

## 使用

1. 将本分支推送至自己的 GitHub 仓库并启用 Actions。
2. 打开 **Actions → Nothing 设备内核 / AnyKernel3 → Run workflow**。
3. 填写 `model` 和 `version`，按需填写 `source_branch` 和勾选补丁。
4. 成功后，从 Artifacts 下载 `Nothing-…-AnyKernel3`，解开 Actions 的外层压缩包，取得里面的 **`*-AnyKernel3.zip`** 和 `.sha256`。

`version` 支持完整 Nothing OS build 编号、OS 版本号、官方 Git 分支、tag 或 **40 位 commit SHA**。推荐使用完整 build 编号或 SHA。填写分支名表示构建该分支当前最新提交，构建清单会保存它的实际 SHA。

例如，`model=phone-3a`、`source_branch=sm7635/v/mr`、`version=Asteroids-V3.1-250302-1856`，对应官方 [发布记录](https://github.com/NothingOSS/android_kernel_msm-6.1_nothing_sm7635/blob/sm7635/v/mr/README.md)。示例用于说明输入格式；可用分支与提交以运行时查询为准。

**NothingOSS 同一仓库有多个分支，同一分支内又有多个版本提交。** 本入口会：

- 从机型配置定位 NothingOSS 官方仓库，枚举其分支和 tag，再按机型的分支规则筛选。例如 Tetris、Galaga、Galaxian 共用 mt6878 仓库，但分别对应不同设备，不能混用分支。
- 若填写 `source_branch`，仅在该分支内查询。
- 对 OS/build 版本，逐分支读取 `README.md` 的提交历史，匹配各提交**新增的发布条目**；累计 README 中保留的旧条目不会被当作新版提交的版本。`No update` 发布也按官方记录保留。
- 同一个 SHA 出现在多个分支时去重；同一版本匹配到多个 SHA 时中止并列出候选，不会擅自选最新提交。此时填写分支、完整 build 编号或 SHA。
- 固定并仅检出目标 SHA，检查其所属设备分支和发布记录标识，再读取该提交的 Makefile 和 `build.config.*`，确定内核基线、子版本及 Clang。**手机系统的 Android 版本不能代替源码的 GKI Android 基线。**

查询版本时，把 `operation` 改为 `list-versions`，填写机型，可选限定分支；产物 `Nothing-official-versions` 内的 JSON 包含分支、版本条目和 SHA，不会编译内核。尚未出现在官方发布记录中的版本不会自动回退到其他源码；可自行核对官方提交后填写明确的 ref/SHA。

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

也可填写机型全名、代号或配置内的型号别名，例如 `Nothing Phone (2)`、`Pong`、`A065`。

Phone (1) 的官方 5.4 源码不属于本项目现有 GKI 补丁范围，入口明确拒绝。Phone (2a) Plus 暂未配置独立的官方发布映射。其他未列出的机型需要先在 [nothing/devices.json](nothing/devices.json) 增加经过核对的仓库、分支规则及设备标识映射；不能仅凭相同 SoC 推断为受支持。

Phone (3a) / Pro 的系统可能使用相同 `Asteroids` 设备标识，AnyKernel3 的设备校验也会接受这个共享标识；它不能据此区分这两个型号。共享源码仓库的其他机型仍需选择对应分支与发布提交。

## 补丁与编译

常用开关：KernelSU（含 SukiSU / BakaSU 和上游保留的固定版本）、SUSFS、ZRAM、BBG、Re-Kernel、NoMount、网络增强和安全修复。默认不启用 root 或可选补丁，可先验证官方源码基线。

`patch_options` 是进阶上游输入的 JSON 对象，例如：

```json
{"use_kpm":"enabled (开启)","droidspaces":"678","droidspaces_ntsync":true}
```

KPM 需要兼容的 SukiSU/BakaSU；SUSFS 需要 KernelSU。进阶选项可覆盖常用功能开关，但不能覆盖机型、源码元数据或 KernelSU 选项。导出 SUSFS 集成补丁可添加 `"export_susfs_patches":true`，输出随诊断产物上传。原有 [config/config](config/config) 和固定版本 SukiSU 配置继续由上游步骤读取。

构建过程中，[nothing/upstream.py](nothing/upstream.py) 会读取**当前仓库**的 [.github/workflows/build.yml](.github/workflows/build.yml)，生成临时 composite action，继承依赖获取及补丁区间内的步骤，使用 JSON 保留布尔开关类型。无需维护另一份 SUSFS、ZRAM 或网络补丁脚本。上游新增的区间内步骤会被继承；新增输入可通过 `patch_options` 传入。步骤定位或输入契约不兼容时，检查直接失败，要求维护者审核适配层。

Nothing 适配层另外负责：

- 拉取选定 NothingOSS 提交，使用该源码声明的官方 Android Clang、Android 基线与 KMI generation，以源码自带的 `gki_defconfig` 编译 arm64 GKI `Image`。新版 `setlocalversion` 使用与源码一致的 KMI 后缀。
- 跳过通用源码下载、通用内核重命名、其他厂商专用修复和通用 boot 镜像生成；保留 Nothing 的源码配置及 localversion。
- 打补丁后拒绝新增或修改的 `.rej`；在 Kconfig 处理后检查已选择的关键配置是否实际生效。
- 复用上游获取的 AnyKernel3 工具，生成启用设备校验的 Nothing 安装脚本，更新当前槽位的 `boot` 内核。使用实际 boot 镜像保留 ramdisk，不替换 `init_boot`、DTB 或 vendor 模块。
- 产出真正可刷入的 AnyKernel3 ZIP、SHA256，以及源码 SHA、上游工作流摘要、补丁选项、依赖提交、编译器信息和 Image 摘要清单。

这是 **Nothing 官方源码的 GKI 内核替换构建**，不是完整厂商固件或 vendor 模块重编译。正确选取机型和官方版本仍不等于 ABI 与实机启动已验证；首次构建需保留原 boot 以便恢复，并在对应系统版本上验证。补丁在厂商树上发生冲突时会失败，诊断产物会保留 `.rej`、构建日志和实际 `.config`。

## 同步上游与开发

所有新增内容位于 `nothing/`、`tests/nothing/`、`nothing-*.yml` 和本文档。同步原项目时可直接合并原文件的更新；Nothing 适配层继续引用它们，不需要复制更新补丁实现。新增检查工作流也会在原 `build.yml` 或 SUSFS 脚本发生变化时运行。

```bash
python -m pip install -r nothing/requirements.txt
python -m unittest discover -s tests/nothing -v
bash -n nothing/build.sh
bash -n nothing/anykernel.sh.in

# 查询官方分支与版本（可设置 GH_TOKEN 以提高 GitHub API 配额）
python -m nothing.source --model phone-3a --list-refs
python -m nothing.source --model phone-3a --branch sm7635/v/mr --list-versions
```

内核编译运行在 GitHub Actions Ubuntu 22.04，适配旧版官方 Clang 所需的运行库。版本解析、适配生成和打包测试可在本地运行；本地运行生成器时会创建 `.nothing-actions/` 等临时文件，不应提交这些生成物。

测试使用固定场景覆盖多分支、多版本及 AnyKernel3 结构，不代替完整内核构建或真机验证。适配层需要通过实际 Actions 构建和对应设备启动测试后，才能将该机型版本标记为已验证。
