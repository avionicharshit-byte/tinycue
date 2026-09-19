/* edge-nlu voice demo, board side: stream the microphone and parse sentences.
 *
 * An NXP FRDM-MCXN236 (Cortex-M33 at 150 MHz), bare metal. The on-board PDM
 * microphone is captured at 16 kHz, 16 bit mono, and pushed out of the MCU-Link
 * virtual COM port in small framed packets. The Mac runs the speech-to-text and
 * sends the recognised sentence back on the same port; the board reads it with
 * the edge-nlu runtime, answers with one framed JSON line, and sets the red LED.
 *
 * The link is full duplex, so audio out and text in do not take turns.
 *
 * Frame on the wire, little endian:
 *
 *   0  4  sync   a5 5a e1 1e
 *   4  1  type   1 audio, 2 json, 3 counter test
 *   5  1  spare  0
 *   6  2  seq    per type, wraps at 16 bits
 *   8  2  aux    audio: overruns so far. test: 0. json: 0
 *  10  2  len    payload bytes
 *  12  n  payload
 * 12+n 2  sum    16 bit sum of bytes 4 .. 12+n-1
 *
 * A host that loses bytes hunts for the sync word again and checks the sum, so
 * one bad packet costs 20 ms of audio and nothing else.
 *
 * Text commands from the host are plain lines. A line starting with '!' is
 * control: !audio, !test, !stop, !gain <n>, !ping. Anything else is a sentence
 * to parse.
 *
 * The microphone setup is lifted from the author's own ~/nxp-boards/mic-n236,
 * which is where MICFIL channel 1 and the 16 bit shift were worked out. Nothing
 * is copied from NXP here except board_clock.c, shared with demo/nxp_mcxn236,
 * which keeps its BSD-3 header.
 *
 * edgenlu.c, edgenlu.h, model_data.c and model_data.h are copies. The Makefile
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
#include "fsl_pdm.h"

#include "board_clock.h"
#include "edgenlu.h"
#include "model_data.h"

#define LED_PIN     18U        /* the red LED, active low */
#define UART_CLK_HZ 12000000U  /* LPUART4 stays on the 12 MHz oscillator */

/* 12 MHz over 12 is exactly 1 Mbaud, so the divider is not an approximation.
 * Override at build time: make BAUD=921600 */
#ifndef STREAM_BAUD
#define STREAM_BAUD 1000000U
#endif

#define SAMPLE_RATE  16000U
#define MIC_CHANNEL  1U   /* channels 0 and 1 are the two clock edges of one
                           * data line; on this board the microphone is on 1 */
#define SAMPLE_SHIFT 16   /* DATACH is a left aligned 32 bit result */

#define PACKET_SAMPLES 320U            /* 20 ms of audio a packet */
#define RING_SAMPLES   4096U           /* 256 ms of slack, a power of two */
#define RING_MASK      (RING_SAMPLES - 1U)

#define SYNC0 0xA5U
#define SYNC1 0x5AU
#define SYNC2 0xE1U
#define SYNC3 0x1EU

#define FRAME_AUDIO 1U
#define FRAME_JSON  2U
#define FRAME_TEST  3U
#define FRAME_TYPES 4U

#define MODE_IDLE  0
#define MODE_AUDIO 1
#define MODE_TEST  2

#define LINE_BYTES    192
#define JSON_BYTES    768
#define SCRATCH_BYTES 16384

/* ------------------------------------------------------------------- state */

static enlu_model model;
static enlu_result result;
static uint8_t scratch[SCRATCH_BYTES] __attribute__((aligned(8)));

/* Filled by the MICFIL interrupt, drained by the main loop. One producer and
 * one consumer, so the two counters need no lock on a 32 bit core. */
static volatile int16_t ring[RING_SAMPLES];
static volatile uint32_t ring_head;
static volatile uint32_t ring_tail;
static volatile uint32_t overruns;   /* samples lost to a full ring or FIFO */
static volatile uint32_t captured;   /* samples the interrupt has pushed */
static uint32_t fifo_over;
static uint32_t fifo_under;
static uint32_t cycle_mark;
static uint64_t cycles_total;        /* DWT cycles since boot, wrap extended */

static int32_t dc_accumulator;       /* one pole running mean, Q10 */
static volatile int32_t mic_gain = 2;   /* measured: speech peaks near
                                         * full scale at 4, so 2 leaves headroom */

static int16_t packet[PACKET_SAMPLES];
static uint16_t seq[FRAME_TYPES];
static uint8_t header[12];

static char line[LINE_BYTES];
static int line_len;
static volatile int line_ready;
static char heard[LINE_BYTES];

static char json[JSON_BYTES];
static int json_len;

static int mode = MODE_AUDIO;
static uint32_t parse_cycles;
static uint32_t cycles_per_us;
static int use_systick;

static void poll_rx(void);

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
    cfg.baudRate_Bps = STREAM_BAUD;
    cfg.enableTx     = true;
    cfg.enableRx     = true;
    LPUART_Init(LPUART4, &cfg, UART_CLK_HZ);
}

/* Send one byte, and read the host while the transmitter is busy. Without the
 * poll a 654 byte packet would deafen the board for the length of the packet
 * and short text lines would be lost in the receive FIFO. */
static void tx_byte(uint8_t b)
{
    while ((LPUART_GetStatusFlags(LPUART4) & (uint32_t)kLPUART_TxDataRegEmptyFlag) == 0U) {
        poll_rx();
    }
    LPUART4->DATA = b;
}

static void tx_bytes(const uint8_t *data, uint32_t len)
{
    uint32_t i;

    for (i = 0U; i < len; i++) {
        tx_byte(data[i]);
    }
}

static void poll_rx(void)
{
    while ((LPUART_GetStatusFlags(LPUART4) & (uint32_t)kLPUART_RxDataRegFullFlag) != 0U) {
        char c = (char)(uint8_t)LPUART4->DATA;

        if (c == '\n') {
            line[line_len] = '\0';
            if (line_len > 0) {
                line_ready = 1;
            }
            line_len = 0;
        } else if (c != '\r' && line_len < LINE_BYTES - 1) {
            line[line_len++] = c;
        }
    }
}

/* ------------------------------------------------------------------ frames */

static void send_frame(uint8_t type, const uint8_t *payload, uint16_t len, uint16_t aux)
{
    uint16_t sum = 0U;
    uint16_t i;

    header[0] = SYNC0;
    header[1] = SYNC1;
    header[2] = SYNC2;
    header[3] = SYNC3;
    header[4] = type;
    header[5] = 0U;
    header[6] = (uint8_t)(seq[type] & 0xFFU);
    header[7] = (uint8_t)(seq[type] >> 8);
    header[8] = (uint8_t)(aux & 0xFFU);
    header[9] = (uint8_t)(aux >> 8);
    header[10] = (uint8_t)(len & 0xFFU);
    header[11] = (uint8_t)(len >> 8);
    seq[type]++;

    for (i = 4U; i < 12U; i++) {
        sum = (uint16_t)(sum + header[i]);
    }
    for (i = 0U; i < len; i++) {
        sum = (uint16_t)(sum + payload[i]);
    }

    tx_bytes(header, 12U);
    tx_bytes(payload, len);
    tx_byte((uint8_t)(sum & 0xFFU));
    tx_byte((uint8_t)(sum >> 8));
}

/* -------------------------------------------------------------- microphone */

static const pdm_config_t pdm_config = {
    .enableDoze        = false,
    .fifoWatermark     = 4U,
    .qualityMode       = kPDM_QualityModeHigh,
    .cicOverSampleRate = 0U,
};

static const pdm_channel_config_t channel_config = {
    .outputCutOffFreq = kPDM_DcRemoverCutOff40Hz,
    .gain             = kPDM_DfOutputGain4,   /* a signed range adjust, not a gain */
};

/* PDM microphone on P0_16 (clock) and P0_17 (data), alt 9.
 * Root clock: the 24 MHz crystal, PLL1 at 122.88 MHz, divided by 10. */
static void mic_clocks(void)
{
    const port_pin_config_t pdm_pin = {
        .pullSelect          = kPORT_PullDisable,
        .pullValueSelect     = kPORT_LowPullResistor,
        .slewRate            = kPORT_FastSlewRate,
        .passiveFilterEnable = kPORT_PassiveFilterDisable,
        .openDrainEnable     = kPORT_OpenDrainDisable,
        .driveStrength       = kPORT_LowDriveStrength,
        .mux                 = kPORT_MuxAlt9,
        .inputBuffer         = kPORT_InputBufferEnable,
        .invertInput         = kPORT_InputNormal,
        .lockRegister        = kPORT_UnlockRegister,
    };

    const pll_setup_t pll1_setup = {
        .pllctrl = SCG_SPLLCTRL_SOURCE(0U) | SCG_SPLLCTRL_SELI(15U) | SCG_SPLLCTRL_SELP(31U),
        .pllndiv = SCG_SPLLNDIV_NDIV(25U),
        .pllpdiv = SCG_SPLLPDIV_PDIV(2U),
        .pllmdiv = SCG_SPLLMDIV_MDIV(512U),
        .pllRate = 122880000U,
    };

    CLOCK_EnableClock(kCLOCK_Port0);
    RESET_ReleasePeripheralReset(kPORT0_RST_SHIFT_RSTn);
    PORT_SetPinConfig(PORT0, 16U, &pdm_pin);
    PORT_SetPinConfig(PORT0, 17U, &pdm_pin);

    CLOCK_EnableClock(kCLOCK_Scg);
    CLOCK_SetupExtClocking(24000000U);
    CLOCK_SetPLL1Freq(&pll1_setup);
    CLOCK_SetClkDiv(kCLOCK_DivPLL1Clk0, 10U);
    CLOCK_AttachClk(kPLL1_CLK0_to_MICFILF);
    CLOCK_SetClkDiv(kCLOCK_DivMicfilFClk, 1U);
}

static int mic_start(void)
{
    int ok;

    PDM_Init(PDM, &pdm_config);
    PDM_SetChannelConfig(PDM, MIC_CHANNEL, &channel_config);
    ok = (PDM_SetSampleRateConfig(PDM, CLOCK_GetMicfilClkFreq(), SAMPLE_RATE) == kStatus_Success);

    PDM_Reset(PDM);
    PDM_EnableInterrupts(PDM, (uint32_t)kPDM_FIFOInterruptEnable);
    NVIC_SetPriority(PDM_EVENT_IRQn, 1U);
    EnableIRQ(PDM_EVENT_IRQn);
    PDM_Enable(PDM, true);
    return ok;
}

/* One microphone sample: 16 bits out of the 32 bit result, the running mean
 * taken off, then a fixed gain. The hardware DC remover is already on at 40 Hz;
 * this second pass catches the slow drift it leaves behind. */
static int16_t mic_sample(void)
{
    int32_t v = (int32_t)PDM_ReadData(PDM, MIC_CHANNEL) >> SAMPLE_SHIFT;

    dc_accumulator += v - (dc_accumulator >> 10);
    v = (v - (dc_accumulator >> 10)) * mic_gain;

    if (v > 32767) {
        v = 32767;
    }
    if (v < -32768) {
        v = -32768;
    }
    return (int16_t)v;
}

void PDM_EVENT_IRQHandler(void);
void PDM_EVENT_IRQHandler(void)
{
    uint32_t guard = 8U;

    while (guard-- != 0U) {
        uint32_t status = PDM_GetStatus(PDM);
        uint32_t i;

        if ((status & (1UL << MIC_CHANNEL)) == 0U) {
            if (status != 0U) {
                PDM_ClearStatus(PDM, status);
            }
            break;
        }

        /* The watermark is 4, so four samples are known to be there. */
        for (i = 0U; i < 4U; i++) {
            int16_t s = mic_sample();

            if ((uint32_t)(ring_head - ring_tail) >= RING_SAMPLES) {
                if (overruns < 0xFFFFFFFFU) {
                    overruns++;
                }
            } else {
                ring[ring_head & RING_MASK] = s;
                ring_head++;
                captured++;
            }
        }
        PDM_ClearStatus(PDM, status);
    }

    SDK_ISR_EXIT_BARRIER;
}

/* ------------------------------------------------------------------ timing */

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

    return use_systick ? ((started - now) & 0x00FFFFFFU) : (now - started);
}

/* -------------------------------------------------------------------- json */

static void jput(const char *s)
{
    while (*s != '\0' && json_len < JSON_BYTES - 1) {
        json[json_len++] = *s++;
    }
}

static void jput_u32(uint32_t v)
{
    char buf[11];
    int i = 10;

    buf[i] = '\0';
    do {
        buf[--i] = (char)('0' + (v % 10U));
        v /= 10U;
    } while (v != 0U);
    jput(&buf[i]);
}

static void jput_i32(int32_t v)
{
    if (v < 0) {
        jput("-");
        jput_u32((uint32_t)(-(v + 1)) + 1U);
    } else {
        jput_u32((uint32_t)v);
    }
}

/* Six decimals, the same as the desktop tool prints. No printf in this build,
 * and newlib nano has no float printf anyway. */
static void jput_fixed6(double v)
{
    uint64_t scaled;
    char buf[7];
    int i;

    if (v < 0.0) {
        jput("-");
        v = -v;
    }
    if (v > 1000000.0) {
        v = 1000000.0;
    }
    scaled = (uint64_t)(v * 1000000.0 + 0.5);

    jput_u32((uint32_t)(scaled / 1000000U));
    jput(".");
    {
        uint32_t frac = (uint32_t)(scaled % 1000000U);

        for (i = 5; i >= 0; i--) {
            buf[i] = (char)('0' + (frac % 10U));
            frac /= 10U;
        }
    }
    buf[6] = '\0';
    jput(buf);
}

static void jput_string(const char *text)
{
    char one[2] = {0, 0};

    jput("\"");
    while (*text != '\0') {
        unsigned char c = (unsigned char)*text++;

        if (c == '"' || c == '\\') {
            jput("\\");
            one[0] = (char)c;
            jput(one);
        } else if (c < 0x20) {
            jput(" ");
        } else {
            one[0] = (char)c;
            jput(one);
        }
    }
    jput("\"");
}

static void json_send(void)
{
    send_frame(FRAME_JSON, (const uint8_t *)json, (uint16_t)json_len, 0U);
    json_len = 0;
}

/* -------------------------------------------------------------------- life */

static void reply(void)
{
    int i;

    json_len = 0;
    jput("{\"text\":");
    jput_string(heard);
    jput(",\"command\":");
    jput_string(result.command);
    jput(",\"slots\":{");
    for (i = 0; i < result.slot_count; i++) {
        if (i > 0) {
            jput(",");
        }
        jput_string(result.slots[i].name);
        jput(":");
        if (result.slots[i].is_number) {
            jput_i32(result.slots[i].number);
        } else {
            jput_string(result.slots[i].text ? result.slots[i].text : "");
        }
    }
    jput("},\"missing\":[");
    for (i = 0; i < result.missing_count; i++) {
        if (i > 0) {
            jput(",");
        }
        jput_string(result.missing[i]);
    }
    jput("],\"confidence\":");
    jput_fixed6(result.confidence);
    jput(",\"intent\":");
    jput_fixed6(result.intent_probability);
    jput(",\"slot\":");
    jput_fixed6(result.slot_probability);
    jput(",\"margin\":");
    jput_fixed6(result.intent_margin);
    jput(",\"unknown\":");
    jput_u32((uint32_t)result.unknown_count);
    jput(",\"unknown_share\":");
    jput_fixed6(result.unknown_share);
    jput(",\"all_carrier_unknown\":");
    jput(result.all_carrier_unknown ? "true" : "false");
    jput(",\"unsure\":");
    jput(result.unsure ? "true" : "false");
    jput(",\"micros\":");
    jput_u32(parse_cycles / cycles_per_us);
    jput(",\"cycles\":");
    jput_u32(parse_cycles);
    jput("}");
    json_send();
}

static void parse_sentence(const char *text)
{
    uint32_t started;
    int status;

    strncpy(heard, text, sizeof heard - 1);
    heard[sizeof heard - 1] = '\0';

    started = timer_now();
    status = enlu_parse(&model, heard, &result, scratch, sizeof scratch);
    parse_cycles = timer_since(started);

    if (status != ENLU_OK) {
        json_len = 0;
        jput("{\"text\":");
        jput_string(heard);
        jput(",\"error\":");
        jput_string(enlu_error(status));
        jput("}");
        json_send();
        led_set(1);
        return;
    }

    reply();
    led_set(result.unsure || result.is_none);
}

static void say_status(const char *what)
{
    json_len = 0;
    jput("{\"ok\":");
    jput_string(what);
    jput(",\"mode\":");
    jput_string(mode == MODE_AUDIO ? "audio" : (mode == MODE_TEST ? "test" : "idle"));
    jput(",\"gain\":");
    jput_i32(mic_gain);
    jput(",\"overruns\":");
    jput_u32(overruns);
    jput(",\"captured\":");
    jput_u32(captured);
    jput(",\"seconds\":");
    jput_fixed6((double)cycles_total / (double)SystemCoreClock);
    jput(",\"fifo_over\":");
    jput_u32(fifo_over);
    jput(",\"fifo_under\":");
    jput_u32(fifo_under);
    jput("}");
    json_send();
}

static void control(const char *cmd)
{
    if (strcmp(cmd, "audio") == 0) {
        mode = MODE_AUDIO;
        ring_tail = ring_head;
        say_status("audio");
    } else if (strcmp(cmd, "test") == 0) {
        mode = MODE_TEST;
        seq[FRAME_TEST] = 0U;
        say_status("test");
    } else if (strcmp(cmd, "stop") == 0) {
        mode = MODE_IDLE;
        say_status("stop");
    } else if (strcmp(cmd, "ping") == 0) {
        say_status("ping");
    } else if (strncmp(cmd, "gain ", 5) == 0) {
        int32_t g = 0;
        const char *p = cmd + 5;

        while (*p >= '0' && *p <= '9') {
            g = g * 10 + (*p++ - '0');
        }
        if (g < 1) {
            g = 1;
        }
        if (g > 64) {
            g = 64;
        }
        mic_gain = g;
        say_status("gain");
    } else if (strcmp(cmd, "reset") == 0) {
        overruns = 0U;
        say_status("reset");
    } else {
        say_status("unknown");
    }
}

static void handle_line(void)
{
    char copy[LINE_BYTES];

    strncpy(copy, line, sizeof copy - 1);
    copy[sizeof copy - 1] = '\0';
    line_ready = 0;

    if (copy[0] == '!') {
        control(copy + 1);
    } else {
        parse_sentence(copy);
    }
}

static void banner(void)
{
    json_len = 0;
    jput("{\"ready\":true,\"board\":\"FRDM-MCXN236\",\"core_hz\":");
    jput_u32(SystemCoreClock);
    jput(",\"baud\":");
    jput_u32(STREAM_BAUD);
    jput(",\"rate\":");
    jput_u32(SAMPLE_RATE);
    jput(",\"micfil_hz\":");
    jput_u32(CLOCK_GetMicfilClkFreq());
    jput(",\"pll1_hz\":");
    jput_u32(CLOCK_GetPll1OutFreq());
    jput(",\"pdm_ctrl2\":");
    jput_u32(PDM->CTRL_2);
    jput(",\"packet_samples\":");
    jput_u32(PACKET_SAMPLES);
    jput(",\"model_bytes\":");
    jput_u32((uint32_t)enlu_model_data_len);
    jput(",\"cutoff\":");
    jput_fixed6(enlu_cutoff(&model));
    jput(",\"timer\":");
    jput(use_systick ? "\"systick\"" : "\"dwt\"");
#ifdef ENLU_FAST_EXP
    jput(",\"fast_exp\":true}");
#else
    jput(",\"fast_exp\":false}");
#endif
    json_send();
}

int main(void)
{
    uint32_t test_counter = 0U;
    int status;

    BOARD_BootClockPLL150M();
    led_init();
    uart_init();
    timer_init();

    status = enlu_init(&model, enlu_model_data, enlu_model_data_len);
    if (status != ENLU_OK) {
        for (;;) {
            led_set(1);
        }
    }
    if (enlu_scratch_size(&model) > sizeof scratch) {
        for (;;) {
            led_set(1);
        }
    }

    mic_clocks();
    if (!mic_start()) {
        for (;;) {
            led_set(1);
        }
    }

    banner();
    led_set(0);

    cycle_mark = DWT->CYCCNT;

    for (;;) {
        uint32_t now = DWT->CYCCNT;

        cycles_total += (uint32_t)(now - cycle_mark);
        cycle_mark = now;

        poll_rx();
        if (line_ready) {
            handle_line();
        }

        if (mode == MODE_AUDIO) {
            if ((uint32_t)(ring_head - ring_tail) >= PACKET_SAMPLES) {
                uint32_t fifo = PDM_GetFifoStatus(PDM);
                uint32_t i;

                if (fifo != 0U) {
                    PDM_ClearFIFOStatus(PDM, fifo);
                    if ((fifo & (uint32_t)kPDM_FifoStatusOverflowCh1) != 0U) {
                        fifo_over++;
                        overruns++;
                    }
                    if ((fifo & (uint32_t)kPDM_FifoStatusUnderflowCh1) != 0U) {
                        fifo_under++;
                    }
                }
                for (i = 0U; i < PACKET_SAMPLES; i++) {
                    packet[i] = ring[(ring_tail + i) & RING_MASK];
                }
                ring_tail += PACKET_SAMPLES;
                send_frame(FRAME_AUDIO, (const uint8_t *)packet, PACKET_SAMPLES * 2U,
                           (uint16_t)(overruns > 0xFFFFU ? 0xFFFFU : overruns));
            }
        } else if (mode == MODE_TEST) {
            uint32_t i;

            for (i = 0U; i < PACKET_SAMPLES * 2U; i++) {
                ((uint8_t *)packet)[i] = (uint8_t)(test_counter++ & 0xFFU);
            }
            send_frame(FRAME_TEST, (const uint8_t *)packet, PACKET_SAMPLES * 2U, 0U);
        }
    }
}
