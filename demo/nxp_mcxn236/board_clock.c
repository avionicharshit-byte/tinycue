/*
 * Copyright 2023 NXP
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 */

/* Cut down from the MCUXpresso Config Tools clock configuration for the
 * FRDM-MCXN236, keeping only the 150 MHz PLL0 setting. The core voltage, the
 * flash wait states and the SRAM timing all have to move with the frequency,
 * which is why this is not two lines. No external crystal is needed: PLL0 runs
 * off the internal 48 MHz oscillator. */

#include "fsl_clock.h"
#include "fsl_spc.h"

#include "board_clock.h"

extern uint32_t SystemCoreClock;

void BOARD_BootClockPLL150M(void)
{
    CLOCK_EnableClock(kCLOCK_Scg);

    /* Park on the 12 MHz oscillator while the rest is reconfigured. */
    CLOCK_AttachClk(kFRO12M_to_MAIN_CLK);

    /* 1.2 V from the DC-DC converter and from the core LDO. */
    spc_active_mode_dcdc_option_t dcdcOpt = {
        .DCDCVoltage       = kSPC_DCDC_OverdriveVoltage,
        .DCDCDriveStrength = kSPC_DCDC_NormalDriveStrength,
    };
    SPC_SetActiveModeDCDCRegulatorConfig(SPC0, &dcdcOpt);

    spc_active_mode_core_ldo_option_t ldoOpt = {
        .CoreLDOVoltage       = kSPC_CoreLDO_OverDriveVoltage,
        .CoreLDODriveStrength = kSPC_CoreLDO_NormalDriveStrength,
    };
    SPC_SetActiveModeCoreLDORegulatorConfig(SPC0, &ldoOpt);

    /* Flash read wait states for 1.2 V at 150 MHz. */
    FMU0->FCTRL = (FMU0->FCTRL & ~((uint32_t)FMU_FCTRL_RWSC_MASK)) | (FMU_FCTRL_RWSC(0x3U));

    spc_sram_voltage_config_t sramCfg = {
        .operateVoltage       = kSPC_sramOperateAt1P2V,
        .requestVoltageUpdate = true,
    };
    SPC_SetSRAMOperateVoltage(SPC0, &sramCfg);

    CLOCK_SetupFROHFClocking(48000000U);

    const pll_setup_t pll0Setup = {
        .pllctrl = SCG_APLLCTRL_SOURCE(1U) | SCG_APLLCTRL_SELI(27U) | SCG_APLLCTRL_SELP(13U),
        .pllndiv = SCG_APLLNDIV_NDIV(8U),
        .pllpdiv = SCG_APLLPDIV_PDIV(1U),
        .pllmdiv = SCG_APLLMDIV_MDIV(50U),
        .pllRate = 150000000U,
    };
    CLOCK_SetPLL0Freq(&pll0Setup);
    CLOCK_SetPll0MonitorMode(kSCG_Pll0MonitorDisable);

    CLOCK_AttachClk(kPLL0_to_MAIN_CLK);
    CLOCK_SetClkDiv(kCLOCK_DivAhbClk, 1U);

    SystemCoreClock = BOARD_BOOTCLOCKPLL150M_CORE_CLOCK;
}
