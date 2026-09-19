// tinycue on a classic ESP32 with a 1.28 inch round GC9A01 display.
//
// Type a sentence into the serial port at 115200. The board reads it with the tinycue
// C runtime, prints one JSON line back, and draws the answer on the round screen.
// No Wi-Fi, no cloud, nothing leaves the board.
//
// Wiring: SCL 18, SDA 23, DC 22, CS 5, RST 4, no backlight pin.
// The runtime and the model are copies. Refresh them with `make demo-sync`.
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_GC9A01A.h>

extern "C" {
#include "tinycue.h"
#include "model_data.h"
}

// Nothing here passes a struct to a function on purpose. Arduino writes its own
// prototypes and they break when a function takes a type declared further down.

#define TFT_DC 22
#define TFT_CS 5
#define TFT_RST 4

#define LINE_BYTES 160
#define SCRATCH_BYTES 16384

static Adafruit_GC9A01A tft(TFT_CS, TFT_DC, TFT_RST);

static tcue_model model;
static tcue_result result;
static uint8_t scratch[SCRATCH_BYTES] __attribute__((aligned(8)));
static bool ready = false;

static char line[LINE_BYTES];
static int lineLen = 0;
static char heard[LINE_BYTES];
static unsigned long parseMicros = 0;

static const uint16_t GREEN = 0x2606;
static const uint16_t AMBER = 0xFD00;
static const uint16_t GREY = 0x8410;
static const uint16_t TRACK = 0x2104;

// ------------------------------------------------------------------ drawing

static void centred(const char *text, int y, int size, uint16_t colour) {
  int width = (int)strlen(text) * 6 * size;
  int x = 120 - width / 2;
  if (x < 4) x = 4;
  tft.setTextSize(size);
  tft.setTextColor(colour);
  tft.setCursor(x, y);
  tft.print(text);
}

// A ring around the rim, filled clockwise from the top in proportion to `fraction`.
static void ring(float fraction, uint16_t on, uint16_t off) {
  const int segments = 72;
  if (fraction < 0) fraction = 0;
  if (fraction > 1) fraction = 1;
  int lit = (int)(fraction * segments + 0.5f);
  for (int i = 0; i < segments; i++) {
    float a0 = (i * 5.0f - 90.0f) * 0.01745329f;
    float a1 = (i * 5.0f - 90.0f + 4.2f) * 0.01745329f;
    int x0 = 120 + (int)(112 * cosf(a0)), y0 = 120 + (int)(112 * sinf(a0));
    int x1 = 120 + (int)(112 * cosf(a1)), y1 = 120 + (int)(112 * sinf(a1));
    int u0 = 120 + (int)(119 * cosf(a0)), v0 = 120 + (int)(119 * sinf(a0));
    int u1 = 120 + (int)(119 * cosf(a1)), v1 = 120 + (int)(119 * sinf(a1));
    uint16_t colour = i < lit ? on : off;
    tft.fillTriangle(x0, y0, u0, v0, u1, v1, colour);
    tft.fillTriangle(x0, y0, u1, v1, x1, y1, colour);
  }
}

// The words the board heard, wrapped to the width of the circle, at most three lines.
static int wrapped(const char *text, int top, uint16_t colour) {
  const int perLine = 26;
  char buffer[32];
  int start = 0;
  int drawn = 0;
  int length = (int)strlen(text);

  while (start < length && drawn < 3) {
    int take = length - start;
    if (take > perLine) {
      take = perLine;
      int back = take;
      while (back > 0 && text[start + back] != ' ') back--;
      if (back > 6) take = back;
    }
    memcpy(buffer, text + start, (size_t)take);
    buffer[take] = 0;
    centred(buffer, top + drawn * 11, 1, colour);
    drawn++;
    start += take;
    while (start < length && text[start] == ' ') start++;
  }
  return top + drawn * 11;
}

// A small drawn state for the command, so the screen says something without reading.
static void icon(const char *command, const char *value, int number, bool isNumber) {
  bool on = value != NULL && strcmp(value, "on") == 0;
  bool up = value != NULL && strcmp(value, "up") == 0;

  if (strcmp(command, "set_light") == 0) {
    uint16_t colour = on ? GC9A01A_YELLOW : GREY;
    tft.fillCircle(120, 176, 13, colour);
    tft.fillRect(114, 188, 12, 6, colour);
    if (on) {
      for (int i = 0; i < 8; i++) {
        float a = i * 0.7853982f;
        tft.drawLine(120 + (int)(18 * cosf(a)), 176 + (int)(18 * sinf(a)),
                     120 + (int)(24 * cosf(a)), 176 + (int)(24 * sinf(a)), colour);
      }
    }
    return;
  }
  if (strcmp(command, "set_fan") == 0) {
    uint16_t colour = up ? GC9A01A_CYAN : GREY;
    int tip = up ? 162 : 192;
    int base = up ? 192 : 162;
    tft.drawLine(120, base, 120, tip, colour);
    tft.drawLine(120, tip, 110, up ? 172 : 182, colour);
    tft.drawLine(120, tip, 130, up ? 172 : 182, colour);
    return;
  }
  if (isNumber) {
    char buffer[12];
    snprintf(buffer, sizeof buffer, "%d", number);
    centred(buffer, 168, 3, GC9A01A_WHITE);
    tft.drawCircle(120, 178, 26, GREY);
    return;
  }
  if (value != NULL) {
    centred(value, 176, 2, GC9A01A_CYAN);
  }
}

static void drawIdle() {
  tft.fillScreen(GC9A01A_BLACK);
  ring(1.0f, TRACK, TRACK);
  centred("tinycue", 104, 3, GC9A01A_WHITE);
  centred("type a command", 140, 1, GREY);
  centred("offline, on this chip", 156, 1, GREY);
}

static void drawAnswer() {
  const char *value = NULL;
  int number = 0;
  bool isNumber = false;
  char buffer[48];
  uint16_t rim;
  int y;

  if (result.slot_count > 0) {
    value = result.slots[0].text;
    number = (int)result.slots[0].number;
    isNumber = result.slots[0].is_number != 0;
  }

  if (result.is_none) {
    rim = GREY;
  } else if (result.unsure) {
    rim = AMBER;
  } else {
    rim = GREEN;
  }

  tft.fillScreen(GC9A01A_BLACK);
  ring((float)result.confidence, rim, TRACK);
  y = wrapped(heard, 44, GREY);

  if (result.is_none) {
    centred("Not a command", 108, 2, GREY);
    centred("nothing to do", 136, 1, GREY);
    return;
  }

  if (result.unsure) {
    centred("Did you mean", y + 8, 1, AMBER);
    centred(result.command, y + 22, 2, AMBER);
    snprintf(buffer, sizeof buffer, "%d%% sure", (int)(result.confidence * 100 + 0.5));
    centred(buffer, y + 44, 1, AMBER);
    if (result.unknown_count > 0) {
      snprintf(buffer, sizeof buffer, "%d new word%s", result.unknown_count,
               result.unknown_count == 1 ? "" : "s");
      centred(buffer, y + 58, 1, GREY);
    }
    return;
  }

  centred(result.command, y + 10, 2, GC9A01A_WHITE);

  buffer[0] = 0;
  for (int i = 0; i < result.slot_count && i < 3; i++) {
    char one[24];
    if (result.slots[i].is_number) {
      snprintf(one, sizeof one, "%s=%d", result.slots[i].name, (int)result.slots[i].number);
    } else {
      snprintf(one, sizeof one, "%s=%s", result.slots[i].name,
               result.slots[i].text ? result.slots[i].text : "?");
    }
    if (buffer[0] != 0) strncat(buffer, " ", sizeof buffer - strlen(buffer) - 1);
    strncat(buffer, one, sizeof buffer - strlen(buffer) - 1);
  }
  if (buffer[0] != 0) centred(buffer, y + 32, 1, GC9A01A_CYAN);
  if (result.missing_count > 0) {
    snprintf(buffer, sizeof buffer, "need %s", result.missing[0]);
    centred(buffer, y + 44, 1, AMBER);
  }
  icon(result.command, value, number, isNumber);
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
    tft.fillScreen(GC9A01A_BLACK);
    ring(1.0f, GREY, GREY);
    centred("cannot read that", 112, 1, GREY);
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
  tft.begin(27000000);
  tft.fillScreen(GC9A01A_BLACK);

  status = tcue_init(&model, tcue_model_data, tcue_model_data_len);
  if (status != TCUE_OK) {
    centred("bad model", 112, 2, GC9A01A_RED);
    Serial.print("{\"error\":\"");
    Serial.print(tcue_error(status));
    Serial.println("\"}");
    return;
  }
  if (tcue_scratch_size(&model) > sizeof scratch) {
    centred("scratch too small", 112, 1, GC9A01A_RED);
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
  drawIdle();
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      line[lineLen] = 0;
      if (ready && lineLen > 0) {
        handle(line);
      }
      lineLen = 0;
    } else if (c != '\r' && lineLen < LINE_BYTES - 1) {
      line[lineLen++] = c;
    }
  }
  delay(2);
}
