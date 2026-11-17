/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Retain zygote's seccomp filter for the SukiSU manager/authorized apps.
 * Adapted from LingLuo17/AnyKernel3's builtin seccomp filter-mode approach:
 * https://github.com/LingLuo17/AnyKernel3/commit/841ab62e8af77ee73a1d1538bd3838e33df093cc
 *
 * Include in kernel/seccomp.c: use this kernel's real private filter/cache
 * layout instead of duplicating struct seccomp_filter in KernelSU.
 */
#include <linux/nothing_ksu_seccomp.h>
#include <linux/sched/signal.h>
#include <asm/unistd.h>

#if !defined(CONFIG_SECCOMP_FILTER) || !defined(SECCOMP_ARCH_NATIVE)
#error "Nothing SukiSU filter mode requires the native seccomp action cache"
#endif

void nothing_ksu_seccomp_allow_reboot(void)
{
	struct seccomp_filter *filter;
	unsigned long flags;

	spin_lock_irqsave(&current->sighand->siglock, flags);
	filter = current->seccomp.filter;
	if (current->seccomp.mode == SECCOMP_MODE_FILTER && filter) {
		set_bit(__NR_reboot, filter->cache.allow_native);
#ifdef SECCOMP_ARCH_COMPAT
		/* AArch32 and AArch64 have different reboot syscall numbers. */
		set_bit(NOTHING_KSU_COMPAT_NR_REBOOT, filter->cache.allow_compat);
#endif
	}
	spin_unlock_irqrestore(&current->sighand->siglock, flags);
}
