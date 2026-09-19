/* tinycue on an NXP FRDM-MCXN236: a Cortex-M33 at 150 MHz, bare metal.
 *
 * Type a sentence into the debug serial port at 115200. The board reads it with
 * the tinycue C runtime and prints one JSON line back. No network, no RTOS, no
 * heap: the model sits in flash and the runtime gets one static scratch buffer.
 *
 * The red LED is the visible signal: it lights while a sentence is being read
 * and stays lit when the answer is unsure or is not a command at all.
 *
 * tinycue.c, tinycue.h, model_data.c and model_data.h are copies. The Makefile
 * refreshes them from runtime/ and out/device/ before every build.
 */

#include <string.h>

#include "fsl_device_registers.h"
#include "fsl_clock.h"
#include "fsl_reset.h"
#include "fsl_gpio.h"
#include "fsl_port.h"
#include "fsl_lpflexcomm.h"
#include "fsl_lpuart.h"

#include "board_clock.h"
#include "tinycue.h"
#include "model_data.h"

#define LED_PIN      18U        /* the red LED, active low */
#define UART_CLK_HZ  12000000U  /* LPUART4 stays on the 12 MHz oscillator */

#define LINE_BYTES    192
#define SCRATCH_BYTES 16384

static tcue_model model;
static tcue_result result;
static uint8_t scratch[SCRATCH_BYTES] __attribute__((aligned(8)));

static char line[LINE_BYTES];
static int line_len;
static char heard[LINE_BYTES];

static uint32_t parse_cycles;
static uint32_t cycles_per_us;
static int use_systick; /* set when the cycle counter is not running */

/* ------------------------------------------------------------------- board */

static void led_init(void)
{
    gpio_pin_config_t led = {kGPIO_DigitalOutput, 1U};

    CLOCK_EnableClock(kCLOCK_Port4);
    CLOCK_EnableClock(kCLOCK_Gpio4);
    RESET_ReleasePeripheralReset(kPORT4_RST_SHIFT_RSTn);
    RESET_ReleasePeripheralReset(kGPIO4_RST_SHIFT_RSTn);
    PORT_SetPinMux(PORT4, LED_PIN, kPORT_MuxAsGpio);
    GPIO_PinInit(GPIO4, LED_PIN, &led);
}

static void led_set(int on)
{
    GPIO_PinWrite(GPIO4, LED_PIN, on ? 0U : 1U);
}

/* LPUART4 on P1_8 (RX) and P1_9 (TX), alt 2: the MCU-Link virtual COM port. */
static void uart_init(void)
{
    const port_pin_config_t pin = {
        .pullSelect          = kPORT_PullDisable,
        .pullValueSelect     = kPORT_LowPullResistor,
        .slewRate            = kPORT_FastSlewRate,
        .passiveFilterEnable = kPORT_PassiveFilterDisable,
        .openDrainEnable     = kPORT_OpenDrainDisable,
        .driveStrength       = kPORT_LowDriveStrength,
        .mux                 = kPORT_MuxAlt2,
        .inputBuffer         = kPORT_InputBufferEnable,
        .invertInput         = kPORT_InputNormal,
        .lockRegister        = kPORT_UnlockRegister,
    };
    lpuart_config_t cfg;

    CLOCK_EnableClock(kCLOCK_Port1);
    RESET_ReleasePeripheralReset(kPORT1_RST_SHIFT_RSTn);
    PORT_SetPinConfig(PORT1, 8U, &pin);
    PORT_SetPinConfig(PORT1, 9U, &pin);

    CLOCK_SetClkDiv(kCLOCK_DivFlexcom4Clk, 1U);
    CLOCK_AttachClk(kFRO12M_to_FLEXCOMM4);
    LP_FLEXCOMM_Init(4U, LP_FLEXCOMM_PERIPH_LPUART);

    LPUART_GetDefaultConfig(&cfg);
    cfg.baudRate_Bps = 115200U;
    cfg.enableTx     = true;
    cfg.enableRx     = true;
    LPUART_Init(LPUART4, &cfg, UART_CLK_HZ);
}

static void uart_put(const char *s)
{
    LPUART_WriteBlocking(LPUART4, (const uint8_t *)s, strlen(s));
}

static int uart_pending(void)
{
    return (LPUART_GetStatusFlags(LPUART4) & (uint32_t)kLPUART_RxDataRegFullFlag) != 0U;
}

static char uart_getc(void)
{
    uint8_t c = 0U;
    LPUART_ReadBlocking(LPUART4, &c, 1U);
    return (char)c;
}

/* ------------------------------------------------------------------ timing */

/* The DWT cycle counter is the honest clock here. It needs the trace unit
 * powered, which some parts leave off until a debugger asks; SysTick free
 * running is the fallback, and one parse is far shorter than its 24 bit wrap. */
static void timer_init(void)
{
    uint32_t before;

    cycles_per_us = SystemCoreClock / 1000000U;

    CoreDebug->DEMCR |= CoreDebug_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0U;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    before = DWT->CYCCNT;
    __asm volatile("nop; nop; nop; nop; nop; nop; nop; nop");
    if (DWT->CYCCNT == before) {
        use_systick = 1;
        SysTick->LOAD = 0x00FFFFFFU;
        SysTick->VAL  = 0U;
        SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    }
}

static uint32_t timer_now(void)
{
    return use_systick ? SysTick->VAL : DWT->CYCCNT;
}

static uint32_t timer_since(uint32_t started)
{
    uint32_t now = timer_now();

    /* SysTick counts down and wraps at 24 bits; DWT counts up over 32. */
    return use_systick ? ((started - now) & 0x00FFFFFFU) : (now - started);
}

/* ------------------------------------------------------------------- print */

static void put_u32(uint32_t v)
{
    char buf[11];
    int i = 10;

    buf[i] = '\0';
    do {
        buf[--i] = (char)('0' + (v % 10U));
        v /= 10U;
    } while (v != 0U);
    uart_put(&buf[i]);
}

static void put_i32(int32_t v)
{
    if (v < 0) {
        uart_put("-");
        put_u32((uint32_t)(-(v + 1)) + 1U);
    } else {
        put_u32((uint32_t)v);
    }
}

/* Six decimals, the same as the desktop tool prints. There is no printf in this
 * build, and no float printf in newlib nano anyway. */
static void put_fixed6(double v)
{
    uint64_t scaled;
    uint32_t whole;
    uint32_t frac;
    char buf[7];
    int i;

    if (v < 0.0) {
        uart_put("-");
        v = -v;
    }
    if (v > 1000000.0) {
        v = 1000000.0;
    }
    scaled = (uint64_t)(v * 1000000.0 + 0.5);
    whole  = (uint32_t)(scaled / 1000000U);
    frac   = (uint32_t)(scaled % 1000000U);

    put_u32(whole);
    uart_put(".");
    for (i = 5; i >= 0; i--) {
        buf[i] = (char)('0' + (frac % 10U));
        frac /= 10U;
    }
    buf[6] = '\0';
    uart_put(buf);
}

static void put_json_string(const char *text)
{
    char one[2] = {0, 0};

    uart_put("\"");
    while (*text != '\0') {
        unsigned char c = (unsigned char)*text++;
        if (c == '"' || c == '\\') {
            uart_put("\\");
            one[0] = (char)c;
            uart_put(one);
        } else if (c < 0x20) {
            uart_put(" ");
        } else {
            one[0] = (char)c;
            uart_put(one);
        }
    }
    uart_put("\"");
}

static void reply(void)
{
    int i;

    uart_put("{\"text\":");
    put_json_string(heard);
    uart_put(",\"command\":");
    put_json_string(result.command);
    uart_put(",\"slots\":{");
    for (i = 0; i < result.slot_count; i++) {
        if (i > 0) {
            uart_put(",");
        }
        put_json_string(result.slots[i].name);
        uart_put(":");
        if (result.slots[i].is_number) {
            put_i32(result.slots[i].number);
        } else {
            put_json_string(result.slots[i].text ? result.slots[i].text : "");
        }
    }
    uart_put("},\"missing\":[");
    for (i = 0; i < result.missing_count; i++) {
        if (i > 0) {
            uart_put(",");
        }
        put_json_string(result.missing[i]);
    }
    uart_put("],\"confidence\":");
    put_fixed6(result.confidence);
    uart_put(",\"intent\":");
    put_fixed6(result.intent_probability);
    uart_put(",\"slot\":");
    put_fixed6(result.slot_probability);
    uart_put(",\"margin\":");
    put_fixed6(result.intent_margin);
    uart_put(",\"unknown\":");
    put_u32((uint32_t)result.unknown_count);
    uart_put(",\"unknown_share\":");
    put_fixed6(result.unknown_share);
    uart_put(",\"all_carrier_unknown\":");
    uart_put(result.all_carrier_unknown ? "true" : "false");
    uart_put(",\"unsure\":");
    uart_put(result.unsure ? "true" : "false");
    uart_put(",\"micros\":");
    put_u32(parse_cycles / cycles_per_us);
    uart_put(",\"cycles\":");
    put_u32(parse_cycles);
    uart_put("}\r\n");
}

/* -------------------------------------------------------------------- life */

static void handle(const char *text)
{
    uint32_t started;
    int status;

    strncpy(heard, text, sizeof heard - 1);
    heard[sizeof heard - 1] = '\0';

    led_set(1);
    started = timer_now();
    status = tcue_parse(&model, heard, &result, scratch, sizeof scratch);
    parse_cycles = timer_since(started);

    if (status != TCUE_OK) {
        uart_put("{\"text\":");
        put_json_string(heard);
        uart_put(",\"error\":");
        put_json_string(tcue_error(status));
        uart_put("}\r\n");
        led_set(1);
        return;
    }

    reply();
    led_set(result.unsure || result.is_none);
}

int main(void)
{
    int status;

    BOARD_BootClockPLL150M();
    led_init();
    uart_init();
    timer_init();

    status = tcue_init(&model, tcue_model_data, tcue_model_data_len);
    if (status != TCUE_OK) {
        uart_put("{\"error\":");
        put_json_string(tcue_error(status));
        uart_put("}\r\n");
        for (;;) {
        }
    }
    if (tcue_scratch_size(&model) > sizeof scratch) {
        uart_put("{\"error\":\"scratch too small\"}\r\n");
        for (;;) {
        }
    }

    uart_put("\r\n{\"ready\":true,\"board\":\"FRDM-MCXN236\",\"core_hz\":");
    put_u32(SystemCoreClock);
    uart_put(",\"model_bytes\":");
    put_u32((uint32_t)tcue_model_data_len);
    uart_put(",\"scratch\":");
    put_u32((uint32_t)tcue_scratch_size(&model));
    uart_put(",\"cutoff\":");
    put_fixed6(tcue_cutoff(&model));
    uart_put(",\"timer\":");
    uart_put(use_systick ? "\"systick\"" : "\"dwt\"");
#ifdef TCUE_FAST_EXP
    uart_put(",\"fast_exp\":true}\r\n");
#else
    uart_put(",\"fast_exp\":false}\r\n");
#endif
    led_set(0);

    for (;;) {
        while (uart_pending()) {
            char c = uart_getc();
            if (c == '\n') {
                line[line_len] = '\0';
                if (line_len > 0) {
                    handle(line);
                }
                line_len = 0;
            } else if (c != '\r' && line_len < LINE_BYTES - 1) {
                line[line_len++] = c;
            }
        }
    }
}
