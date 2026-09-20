// The round screen, drawn with LVGL. Everything the display does lives behind
// these five calls so the sketch itself stays serial and tinycue only.
#ifndef TCUE_UI_H
#define TCUE_UI_H

#ifdef __cplusplus
extern "C" {
#endif

enum { UI_OK = 0, UI_UNSURE = 1, UI_NONE = 2 };

enum {
  UI_ICON_BULB = 0,
  UI_ICON_FAN,
  UI_ICON_CLOCK,
  UI_ICON_THERMO,
  UI_ICON_DROP,
  UI_ICON_CROSS
};

// Bring up the panel and LVGL. Call once from setup().
void ui_begin(void);

// Run LVGL's timers. Call every loop.
void ui_tick(void);

// The waiting screen: the wordmark and a slow arc going round.
void ui_idle(void);

// One answer. `value` is the big line, `foot` the small grey line under it,
// and `permille` the confidence in thousandths, which is where the rim arc
// stops. Everything sits inside a 200 px circle: the panel's bezel eats the
// rim, so text at the very top or bottom of the 240 px frame never shows.
void ui_answer(const char *heard, const char *value, const char *foot,
               int state, int icon, int permille);

#ifdef __cplusplus
}
#endif
#endif
