/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_NOTHING_KSU_SECCOMP_H
#define _LINUX_NOTHING_KSU_SECCOMP_H

/* Resolved from the selected kernel's AArch32 syscall table. */
#define NOTHING_KSU_COMPAT_NR_REBOOT @COMPAT_REBOOT@

void nothing_ksu_seccomp_allow_reboot(void);

#endif
