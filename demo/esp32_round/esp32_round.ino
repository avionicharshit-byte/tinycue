// tinycue on a classic ESP32 with a 1.28 inch round GC9A01 display.
//
// Type a sentence into the serial port at 115200. The board reads it with the
// tinycue C runtime, prints one JSON line back, and draws the answer on the
// round screen. No Wi-Fi, no cloud, nothing leaves the board.
//
// Wiring: SCL 18, SDA 23, DC 22, CS 5, RST 4, no backlight pin. The panel and
// every pixel of the layout live in ui.cpp; this file is serial and tinycue.
// The runtime and the model are copies. Refresh them with `make demo-sync`.
#include "ui.h"

extern "C" {
#include "tinycue.h"
#include "model_data.h"
}

// Nothing here passes a struct to a function on purpose. Arduino writes its own
// prototypes and they break when a function takes a type declared further down.

#define LINE_BYTES 160
#define SCRATCH_BYTES 16384

static tcue_model model;
static tcue_result result;
static uint8_t scratch[SCRATCH_BYTES] __attribute__((aligned(8)));
static bool ready = false;

static char line[LINE_BYTES];
static int lineLen = 0;
static char heard[LINE_BYTES];
static unsigned long parseMicros = 0;

static char valueText[96];
static char footText[64];

// ------------------------------------------------------------------- drawing

// Which of the six drawings fits this command. `show` splits on what it was
// asked for, so the temperature gets a thermometer and the time a clock.
static int iconFor() {
  if (strcmp(result.command, "set_light") == 0) return UI_ICON_BULB;
  if (strcmp(result.command, "set_fan") == 0) return UI_ICON_FAN;
  if (strcmp(result.command, "set_timer") == 0) return UI_ICON_CLOCK;
  if (strcmp(result.command, "show") == 0) {
    for (int i = 0; i < result.slot_count; i++) {
      const char *text = result.slots[i].text;
      if (!text) continue;
      if (strcmp(text, "temperature") == 0) return UI_ICON_THERMO;
      if (strcmp(text, "humidity") == 0) return UI_ICON_DROP;
    }
    return UI_ICON_CLOCK;
  }
  return UI_ICON_CROSS;
}

// The big line: the slot values, "10 minutes" for a number and "bedroom on"
// for two words. Slots come back in the order the sentence used them, which
// reads backwards as often as not ("on bedroom"), so the screen puts them in
// slot name order instead, which is stable whatever the sentence looked like.
static void buildValue() {
  int order[TCUE_MAX_SLOTS];
  int count = result.slot_count < 3 ? result.slot_count : 3;

  for (int i = 0; i < count; i++) order[i] = i;
  for (int i = 1; i < count; i++) {
    int pick = order[i];
    int j = i - 1;
    while (j >= 0 && strcmp(result.slots[order[j]].name, result.slots[pick].name) > 0) {
      order[j + 1] = order[j];
      j--;
    }
    order[j + 1] = pick;
  }

  valueText[0] = 0;
  for (int i = 0; i < count; i++) {
    const tcue_slot *slot = &result.slots[order[i]];
    char one[40];
    if (slot->is_number) {
      snprintf(one, sizeof one, "%d %s", (int)slot->number, slot->name);
    } else {
      snprintf(one, sizeof one, "%s", slot->text ? slot->text : "?");
    }
    if (valueText[0] != 0) strncat(valueText, " ", sizeof valueText - strlen(valueText) - 1);
    strncat(valueText, one, sizeof valueText - strlen(valueText) - 1);
  }
}

static void drawAnswer() {
  int percent = (int)(result.confidence * 100.0f + 0.5f);
  int permille = (int)(result.confidence * 1000.0f + 0.5f);

  if (result.is_none) {
    snprintf(valueText, sizeof valueText, "not a command");
    snprintf(footText, sizeof footText, "nothing to do");
    ui_answer(heard, valueText, footText, UI_NONE, UI_ICON_CROSS, permille);
    return;
  }

  if (result.unsure) {
    snprintf(valueText, sizeof valueText, "%s", result.command);
    snprintf(footText, sizeof footText, "did you mean, %d%% sure", percent);
    ui_answer(heard, valueText, footText, UI_UNSURE, iconFor(), permille);
    return;
  }

  buildValue();
  if (valueText[0] == 0) {
    snprintf(valueText, sizeof valueText, "%s", result.command);
  }
  if (result.missing_count > 0) {
    snprintf(footText, sizeof footText, "%s, needs %s", result.command, result.missing[0]);
  } else {
    snprintf(footText, sizeof footText, "%s  %d%% sure", result.command, percent);
  }
  ui_answer(heard, valueText, footText, UI_OK, iconFor(), permille);
}

// ------------------------------------------------------------------- serial

static void printJsonString(const char *text) {
  Serial.print('"');
  while (*text) {
    unsigned char c = (unsigned char)*text++;
    if (c == '"' || c == '\\') {
      Serial.print('\\');
      Serial.print((char)c);
    } else if (c < 0x20) {
      Serial.print(' ');
    } else {
      Serial.print((char)c);
    }
  }
  Serial.print('"');
}

static void reply() {
  Serial.print("{\"text\":");
  printJsonString(heard);
  Serial.print(",\"command\":");
  printJsonString(result.command);
  Serial.print(",\"slots\":{");
  for (int i = 0; i < result.slot_count; i++) {
    if (i > 0) Serial.print(',');
    printJsonString(result.slots[i].name);
    Serial.print(':');
    if (result.slots[i].is_number) {
      Serial.print((long)result.slots[i].number);
    } else {
      printJsonString(result.slots[i].text ? result.slots[i].text : "");
    }
  }
  Serial.print("},\"missing\":[");
  for (int i = 0; i < result.missing_count; i++) {
    if (i > 0) Serial.print(',');
    printJsonString(result.missing[i]);
  }
  Serial.print("],\"confidence\":");
  Serial.print(result.confidence, 6);
  Serial.print(",\"intent\":");
  Serial.print(result.intent_probability, 6);
  Serial.print(",\"slot\":");
  Serial.print(result.slot_probability, 6);
  Serial.print(",\"margin\":");
  Serial.print(result.intent_margin, 6);
  Serial.print(",\"unknown\":");
  Serial.print((long)result.unknown_count);
  Serial.print(",\"unknown_share\":");
  Serial.print(result.unknown_share, 6);
  Serial.print(",\"all_carrier_unknown\":");
  Serial.print(result.all_carrier_unknown ? "true" : "false");
  Serial.print(",\"unsure\":");
  Serial.print(result.unsure ? "true" : "false");
  Serial.print(",\"micros\":");
  Serial.print((unsigned long)parseMicros);
  Serial.print(",\"heap\":");
  Serial.print((unsigned long)ESP.getFreeHeap());
  Serial.println('}');
}

static void handle(const char *text) {
  unsigned long started;
  int status;

  strncpy(heard, text, sizeof heard - 1);
  heard[sizeof heard - 1] = 0;

  started = micros();
  status = tcue_parse(&model, heard, &result, scratch, sizeof scratch);
  parseMicros = micros() - started;

  if (status != TCUE_OK) {
    Serial.print("{\"text\":");
    printJsonString(heard);
    Serial.print(",\"error\":");
    printJsonString(tcue_error(status));
    Serial.print(",\"heap\":");
    Serial.print((unsigned long)ESP.getFreeHeap());
    Serial.println('}');
    ui_answer(heard, "cannot read that", tcue_error(status), UI_NONE, UI_ICON_CROSS, 1000);
    return;
  }

  reply();
  drawAnswer();
}

// --------------------------------------------------------------------- life

void setup() {
  int status;

  Serial.begin(115200);
  delay(50);
  ui_begin();

  status = tcue_init(&model, tcue_model_data, tcue_model_data_len);
  if (status != TCUE_OK) {
    ui_answer("", "bad model", tcue_error(status), UI_NONE, UI_ICON_CROSS, 1000);
    Serial.print("{\"error\":\"");
    Serial.print(tcue_error(status));
    Serial.println("\"}");
    return;
  }
  if (tcue_scratch_size(&model) > sizeof scratch) {
    ui_answer("", "scratch too small", "rebuild", UI_NONE, UI_ICON_CROSS, 1000);
    Serial.println("{\"error\":\"scratch too small\"}");
    return;
  }
  ready = true;

  Serial.print("{\"ready\":true,\"model_bytes\":");
  Serial.print((unsigned long)tcue_model_data_len);
  Serial.print(",\"scratch\":");
  Serial.print((unsigned long)tcue_scratch_size(&model));
  Serial.print(",\"heap\":");
  Serial.print((unsigned long)ESP.getFreeHeap());
  Serial.println('}');
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      line[lineLen] = 0;
      if (ready && strcmp(line, "!idle") == 0) {
        // Back to the waiting screen, so a recording can start from it.
        ui_idle();
        Serial.println("{\"idle\":true}");
      } else if (ready && lineLen > 0) {
        handle(line);
      }
      lineLen = 0;
    } else if (c != '\r' && lineLen < LINE_BYTES - 1) {
      line[lineLen++] = c;
    }
  }
  ui_tick();
}
