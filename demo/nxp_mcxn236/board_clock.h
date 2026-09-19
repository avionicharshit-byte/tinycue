/*
 * Copyright 2023 NXP
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

/* Cut down from the MCUXpresso Config Tools clock configuration for the
 * FRDM-MCXN236, keeping only the 150 MHz PLL0 setting. */

#ifndef EDGENLU_BOARD_CLOCK_H
#define EDGENLU_BOARD_CLOCK_H

#include "fsl_common.h"

#define BOARD_BOOTCLOCKPLL150M_CORE_CLOCK 150000000U

#if defined(__cplusplus)
extern "C" {
#endif

/* Run the core, and the AHB bus with it, at 150 MHz off PLL0. */
void BOARD_BootClockPLL150M(void);

#if defined(__cplusplus)
}
#endif

#endif /* EDGENLU_BOARD_CLOCK_H */
