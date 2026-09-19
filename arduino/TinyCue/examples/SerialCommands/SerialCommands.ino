// TinyCue: read typed commands over the serial port, fully offline.
//
// Open the Serial Monitor at 115200 with a newline ending, type a sentence and press
// send. The board reads it and prints the command, the slot values, the confidence and
// whether it was sure. Nothing leaves the board: no Wi-Fi, no cloud, no allocation.
//
// The model bundled here is the toy coffee machine that `tinycue init` writes, so it
// knows three commands: brew, set_strength and stop_machine. Try:
//
//   make me two lattes
//   teen cup chai bana do
//   i want it milder
//   stop the machine
//   who won the match last night        <- off topic, comes back as none
//
// To use your own device instead, run
//   tinycue init mydevice
//   tinycue train mydevice.yaml --extra mydevice.extra.yaml --dev mydevice.dev.yaml -o out/model
//   tinycue export out/model -o out/device
// and copy out/device/model_data.c and model_data.h over the two in this folder.
//
// No display and no board-specific code, so this builds for any 32 bit board with
// roughly 300 KB of free flash and 12 KB of free RAM.

#include <tinycue.h>

extern "C" {
#include "model_data.h"
}

#define LINE_BYTES 160
#define SCRATCH_BYTES 12288

static tcue_model model;
static tcue_result answer;
static uint8_t scratch[SCRATCH_BYTES] __attribute__((aligned(8)));
static bool ready = false;

static char line[LINE_BYTES];
static int lineLen = 0;

// Print one parsed answer. The result is a file-scope global, so nothing here passes a
// struct to a function: the Arduino builder writes its own prototypes and they break on
// types it has not seen yet.
static void report(unsigned long micros_taken) {
  Serial.print(answer.command);
  Serial.print("  confidence ");
  Serial.print(answer.confidence, 3);
  Serial.print(answer.unsure ? "  UNSURE" : "  sure");
  Serial.print("  ");
  Serial.print(micros_taken);
  Serial.println(" us");

  for (int i = 0; i < answer.slot_count; i++) {
    Serial.print("    ");
    Serial.print(answer.slots[i].name);
    Serial.print(" = ");
    if (answer.slots[i].is_number) {
      Serial.println((long)answer.slots[i].number);
    } else {
      Serial.print(answer.slots[i].text);
      Serial.println(answer.slots[i].known ? "" : "   (not a listed value)");
    }
  }
  for (int i = 0; i < answer.missing_count; i++) {
    Serial.print("    ");
    Serial.print(answer.missing[i]);
    Serial.println(" is missing");
  }
  if (answer.unknown_count > 0) {
    Serial.print("    ");
    Serial.print(answer.unknown_count);
    Serial.println(" word(s) the model has never seen");
  }
  if (answer.unsure) {
    Serial.println("    too unsure to act on. Ask again, or send it somewhere bigger.");
  }
}

static void handle(const char *text) {
  unsigned long started = micros();
  int code = tcue_parse(&model, text, &answer, scratch, sizeof scratch);
  unsigned long taken = micros() - started;
  if (code != TCUE_OK) {
    Serial.print("refused: ");
    Serial.println(tcue_error(code));
    return;
  }
  report(taken);
}

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 3000) {
    // Wait a moment for a USB serial port, but never block a board without one.
  }

  int code = tcue_init(&model, tcue_model_data, tcue_model_data_len);
  if (code != TCUE_OK) {
    Serial.print("the model would not load: ");
    Serial.println(tcue_error(code));
    return;
  }

  size_t needed = tcue_scratch_size(&model);
  if (needed > sizeof scratch) {
    Serial.print("scratch buffer too small: this model needs ");
    Serial.print((unsigned long)needed);
    Serial.println(" bytes. Raise SCRATCH_BYTES.");
    return;
  }

  ready = true;
  Serial.println();
  Serial.print("TinyCue ready. model ");
  Serial.print((unsigned long)tcue_model_data_len);
  Serial.print(" bytes, scratch ");
  Serial.print((unsigned long)needed);
  Serial.print(" bytes, unsure below ");
  Serial.println(tcue_cutoff(&model), 3);
  Serial.println("Type a sentence and press send.");
}

void loop() {
  if (!ready) {
    delay(1000);
    return;
  }

  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      line[lineLen] = '\0';
      if (lineLen > 0) {
        Serial.print("> ");
        Serial.println(line);
        handle(line);
        Serial.println();
      }
      lineLen = 0;
      continue;
    }
    if (lineLen < LINE_BYTES - 1) {
      line[lineLen++] = c;
    }
  }
}
