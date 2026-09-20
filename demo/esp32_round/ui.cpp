// The round screen. LVGL 9 draws it; Adafruit_GC9A01A only pushes pixels.
//
// Layout, in the 240 x 240 the panel gives us: the sentence that was heard at
// the top, a 64 px icon under it, the slot values large in the middle, the
// command name small and grey below that, and the confidence at the bottom.
// The rim arc is the confidence and its colour is the answer: green acted on,
// amber asking, grey not a command.
#include <Arduino.h>
#include <SPI.h>
#include <Adafruit_GFX.h>
#include <Adafruit_GC9A01A.h>
#include <lvgl.h>

#include "ui.h"

#define TFT_DC 22
#define TFT_CS 5
#define TFT_RST 4

#define W 240
#define H 240
#define FLUSH_LINES 30

static Adafruit_GC9A01A tft(TFT_CS, TFT_DC, TFT_RST);

static uint8_t buf1[W * FLUSH_LINES * 2];
static uint8_t buf2[W * FLUSH_LINES * 2];

static const uint32_t GREEN = 0x20C030;
static const uint32_t AMBER = 0xF8A000;
static const uint32_t GREY = 0x8A8A8A;
static const uint32_t DIM = 0x1C1C1C;
static const uint32_t SLATE = 0x6E6E6E;

// The halo behind the icon, largest first. LVGL has no blur, so it is six
// circles at rising opacity, which on a 1.28 inch panel reads as one glow.
#define GLOW_RINGS 6
static const int GLOW_SIZE[GLOW_RINGS] = {210, 182, 154, 126, 98, 70};
static const uint8_t GLOW_OPA[GLOW_RINGS] = {6, 10, 16, 24, 38, 60};

static lv_obj_t *glow[GLOW_RINGS];
static lv_obj_t *arc;
static lv_obj_t *iconBox;
static lv_obj_t *labHeard;
static lv_obj_t *labValue;
static lv_obj_t *labFoot;

// ------------------------------------------------------------------ plumbing

static uint32_t tickMs(void) { return millis(); }

static void flush(lv_display_t *disp, const lv_area_t *area, uint8_t *px) {
  uint32_t w = area->x2 - area->x1 + 1;
  uint32_t h = area->y2 - area->y1 + 1;
  lv_draw_rgb565_swap(px, w * h);
  tft.startWrite();
  tft.setAddrWindow(area->x1, area->y1, w, h);
  tft.writePixels((uint16_t *)px, w * h, true, true);
  tft.endWrite();
  lv_display_flush_ready(disp);
}

// ------------------------------------------------------------------ helpers

// A bare rectangle: no theme, no scrolling, no border unless asked.
static lv_obj_t *box(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_set_pos(o, x, y);
  lv_obj_set_size(o, w, h);
  return o;
}

static lv_obj_t *fill(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h,
                      int32_t radius, uint32_t colour, lv_opa_t opa) {
  lv_obj_t *o = box(parent, x, y, w, h);
  lv_obj_set_style_radius(o, radius, 0);
  lv_obj_set_style_bg_color(o, lv_color_hex(colour), 0);
  lv_obj_set_style_bg_opa(o, opa, 0);
  return o;
}

static lv_obj_t *outline(lv_obj_t *parent, int32_t x, int32_t y, int32_t w, int32_t h,
                         int32_t radius, uint32_t colour, int32_t width) {
  lv_obj_t *o = box(parent, x, y, w, h);
  lv_obj_set_style_radius(o, radius, 0);
  lv_obj_set_style_bg_opa(o, LV_OPA_TRANSP, 0);
  lv_obj_set_style_border_color(o, lv_color_hex(colour), 0);
  lv_obj_set_style_border_width(o, width, 0);
  return o;
}

static void spin(lv_obj_t *o, int32_t tenthsOfADegree, int32_t pivotX, int32_t pivotY) {
  lv_obj_set_style_transform_pivot_x(o, pivotX, 0);
  lv_obj_set_style_transform_pivot_y(o, pivotY, 0);
  lv_obj_set_style_transform_rotation(o, tenthsOfADegree, 0);
}

static lv_obj_t *label(const lv_font_t *font, uint32_t colour) {
  lv_obj_t *l = lv_label_create(lv_screen_active());
  lv_obj_set_style_text_font(l, font, 0);
  lv_obj_set_style_text_color(l, lv_color_hex(colour), 0);
  lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
  lv_label_set_text(l, "");
  return l;
}

// ------------------------------------------------------------------ animation

static void setOpa(void *o, int32_t v) {
  lv_obj_set_style_opa((lv_obj_t *)o, (lv_opa_t)v, 0);
}

static void setLift(void *o, int32_t v) {
  lv_obj_set_style_translate_y((lv_obj_t *)o, v, 0);
}

static void setArc(void *o, int32_t v) { lv_arc_set_value((lv_obj_t *)o, v); }

static void setSpinArc(void *o, int32_t v) { lv_arc_set_rotation((lv_obj_t *)o, v); }

static void setScale(void *o, int32_t v) {
  lv_obj_set_style_transform_scale_x((lv_obj_t *)o, v, 0);
  lv_obj_set_style_transform_scale_y((lv_obj_t *)o, v, 0);
}

static void setTilt(void *o, int32_t v) {
  lv_obj_set_style_transform_rotation((lv_obj_t *)o, v, 0);
}

static void setGlow(void *o, int32_t v) {
  (void)o;
  for (int i = 0; i < GLOW_RINGS; i++) {
    lv_obj_set_style_bg_opa(glow[i], (lv_opa_t)((GLOW_OPA[i] * v) / 255), 0);
  }
}

// Fade up, and rise the last few pixels while doing it.
static void riseIn(lv_obj_t *o, uint32_t delay) {
  lv_anim_t a;

  lv_obj_set_style_opa(o, LV_OPA_TRANSP, 0);
  lv_obj_set_style_translate_y(o, 6, 0);

  lv_anim_init(&a);
  lv_anim_set_var(&a, o);
  lv_anim_set_exec_cb(&a, setOpa);
  lv_anim_set_values(&a, 0, 255);
  lv_anim_set_duration(&a, 220);
  lv_anim_set_delay(&a, delay);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
  lv_anim_start(&a);

  lv_anim_init(&a);
  lv_anim_set_var(&a, o);
  lv_anim_set_exec_cb(&a, setLift);
  lv_anim_set_values(&a, 6, 0);
  lv_anim_set_duration(&a, 260);
  lv_anim_set_delay(&a, delay);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
  lv_anim_start(&a);
}

static void hide(lv_obj_t *o) { lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN); }
static void show(lv_obj_t *o) { lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN); }

// ------------------------------------------------------------------ the icons

// Each icon is built from plain rounded boxes inside a 64 x 64 container, so
// there is no icon font and no bitmap to keep in step with the model.
static void drawIcon(int which, uint32_t colour) {
  lv_obj_clean(iconBox);

  switch (which) {
    case UI_ICON_BULB: {
      lv_obj_t *glass = outline(iconBox, 13, 2, 38, 38, LV_RADIUS_CIRCLE, colour, 3);
      lv_obj_set_style_bg_color(glass, lv_color_hex(colour), 0);
      lv_obj_set_style_bg_opa(glass, 60, 0);
      fill(iconBox, 23, 42, 18, 6, 3, colour, LV_OPA_COVER);
      fill(iconBox, 25, 51, 14, 5, 2, colour, LV_OPA_COVER);
      break;
    }
    case UI_ICON_FAN: {
      for (int i = 0; i < 3; i++) {
        lv_obj_t *blade = fill(iconBox, 26, 1, 12, 27, 6, colour, 150);
        spin(blade, i * 1200, 6, 31);
      }
      fill(iconBox, 25, 25, 14, 14, LV_RADIUS_CIRCLE, colour, LV_OPA_COVER);
      break;
    }
    case UI_ICON_CLOCK: {
      outline(iconBox, 4, 4, 56, 56, LV_RADIUS_CIRCLE, colour, 3);
      fill(iconBox, 30, 17, 4, 17, 2, colour, LV_OPA_COVER);
      fill(iconBox, 32, 30, 14, 4, 2, colour, LV_OPA_COVER);
      break;
    }
    case UI_ICON_THERMO: {
      outline(iconBox, 24, 2, 16, 42, 8, colour, 3);
      fill(iconBox, 20, 38, 24, 24, LV_RADIUS_CIRCLE, colour, LV_OPA_COVER);
      fill(iconBox, 29, 18, 6, 22, 3, colour, LV_OPA_COVER);
      break;
    }
    case UI_ICON_DROP: {
      lv_obj_t *tip = fill(iconBox, 24, 8, 18, 18, 4, colour, 110);
      spin(tip, 450, 9, 9);
      lv_obj_t *body = outline(iconBox, 15, 24, 34, 34, LV_RADIUS_CIRCLE, colour, 3);
      lv_obj_set_style_bg_color(body, lv_color_hex(colour), 0);
      lv_obj_set_style_bg_opa(body, 60, 0);
      break;
    }
    default: {
      outline(iconBox, 4, 4, 56, 56, LV_RADIUS_CIRCLE, colour, 3);
      lv_obj_t *bar = fill(iconBox, 12, 30, 40, 4, 2, colour, LV_OPA_COVER);
      spin(bar, 450, 20, 2);
      break;
    }
  }
}

// ------------------------------------------------------------------ the screen

void ui_begin(void) {
  lv_display_t *disp;
  int i;

  tft.begin(40000000);
  tft.setRotation(0);
  tft.fillScreen(0x0000);

  lv_init();
  lv_tick_set_cb(tickMs);

  disp = lv_display_create(W, H);
  lv_display_set_flush_cb(disp, flush);
  lv_display_set_buffers(disp, buf1, buf2, sizeof buf1, LV_DISPLAY_RENDER_MODE_PARTIAL);

  lv_obj_t *scr = lv_screen_active();
  lv_obj_remove_flag(scr, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_bg_color(scr, lv_color_black(), 0);
  lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);

  for (i = 0; i < GLOW_RINGS; i++) {
    glow[i] = fill(scr, 0, 0, GLOW_SIZE[i], GLOW_SIZE[i], LV_RADIUS_CIRCLE, GREEN, 0);
    lv_obj_center(glow[i]);
  }

  arc = lv_arc_create(scr);
  lv_obj_set_size(arc, 236, 236);
  lv_obj_center(arc);
  lv_obj_remove_style(arc, NULL, LV_PART_KNOB);
  lv_obj_remove_flag(arc, LV_OBJ_FLAG_CLICKABLE);
  lv_arc_set_bg_angles(arc, 0, 360);
  lv_arc_set_rotation(arc, 270);
  lv_arc_set_range(arc, 0, 1000);
  lv_arc_set_value(arc, 0);
  lv_obj_set_style_arc_width(arc, 7, LV_PART_MAIN);
  lv_obj_set_style_arc_width(arc, 7, LV_PART_INDICATOR);
  lv_obj_set_style_arc_color(arc, lv_color_hex(DIM), LV_PART_MAIN);
  lv_obj_set_style_arc_color(arc, lv_color_hex(GREEN), LV_PART_INDICATOR);
  lv_obj_set_style_arc_rounded(arc, true, LV_PART_INDICATOR);

  iconBox = box(scr, 0, 0, 64, 64);
  lv_obj_align(iconBox, LV_ALIGN_TOP_MID, 0, 76);
  lv_obj_set_style_transform_pivot_x(iconBox, 32, 0);
  lv_obj_set_style_transform_pivot_y(iconBox, 32, 0);

  labHeard = label(&lv_font_montserrat_12, GREY);
  lv_obj_set_size(labHeard, 148, 32);
  lv_label_set_long_mode(labHeard, LV_LABEL_LONG_MODE_DOTS);
  lv_obj_align(labHeard, LV_ALIGN_TOP_MID, 0, 42);

  labValue = label(&lv_font_montserrat_28, GREEN);
  labFoot = label(&lv_font_montserrat_12, GREY);

  ui_idle();
}

void ui_tick(void) { lv_timer_handler(); }

void ui_idle(void) {
  lv_anim_t a;

  lv_anim_delete(arc, NULL);
  lv_anim_delete(iconBox, NULL);
  lv_anim_delete(glow[0], NULL);

  setGlow(NULL, 0);
  hide(iconBox);
  hide(labHeard);

  lv_obj_set_style_arc_color(arc, lv_color_hex(DIM), LV_PART_MAIN);
  lv_obj_set_style_arc_color(arc, lv_color_hex(SLATE), LV_PART_INDICATOR);
  lv_arc_set_value(arc, 110);

  show(labValue);
  lv_obj_set_style_text_font(labValue, &lv_font_montserrat_28, 0);
  lv_obj_set_style_text_color(labValue, lv_color_white(), 0);
  lv_label_set_text(labValue, "tinycue");
  lv_obj_align(labValue, LV_ALIGN_TOP_MID, 0, 96);

  show(labFoot);
  lv_obj_set_style_text_color(labFoot, lv_color_hex(GREY), 0);
  lv_label_set_text(labFoot, "say something");
  lv_obj_align(labFoot, LV_ALIGN_TOP_MID, 0, 136);

  riseIn(labValue, 0);
  riseIn(labFoot, 90);

  // The rim keeps turning while nothing has been said.
  lv_anim_init(&a);
  lv_anim_set_var(&a, arc);
  lv_anim_set_exec_cb(&a, setSpinArc);
  lv_anim_set_values(&a, 0, 3600);
  lv_anim_set_duration(&a, 4200);
  lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
  lv_anim_set_path_cb(&a, lv_anim_path_linear);
  lv_anim_start(&a);
}

void ui_answer(const char *heard, const char *value, const char *foot,
               int state, int icon, int permille) {
  uint32_t colour = state == UI_OK ? GREEN : (state == UI_UNSURE ? AMBER : SLATE);
  lv_anim_t a;

  lv_anim_delete(arc, NULL);
  lv_anim_delete(iconBox, NULL);
  lv_anim_delete(glow[0], NULL);
  lv_arc_set_rotation(arc, 270);

  // The rim sweeps to the confidence it came back with.
  lv_obj_set_style_arc_color(arc, lv_color_hex(colour), LV_PART_INDICATOR);
  lv_arc_set_value(arc, 0);
  lv_anim_init(&a);
  lv_anim_set_var(&a, arc);
  lv_anim_set_exec_cb(&a, setArc);
  lv_anim_set_values(&a, 0, permille);
  lv_anim_set_duration(&a, 460);
  lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
  lv_anim_start(&a);

  // The glow comes up with it, except when there was no command to light.
  for (int i = 0; i < GLOW_RINGS; i++) {
    lv_obj_set_style_bg_color(glow[i], lv_color_hex(colour), 0);
  }
  setGlow(NULL, 0);
  if (state != UI_NONE) {
    lv_anim_init(&a);
    lv_anim_set_var(&a, glow[0]);
    lv_anim_set_exec_cb(&a, setGlow);
    lv_anim_set_values(&a, 0, 255);
    lv_anim_set_duration(&a, 420);
    lv_anim_set_delay(&a, 60);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
  }

  // The icon scales up past its size and settles back.
  drawIcon(icon, colour);
  show(iconBox);
  setTilt(iconBox, 0);
  lv_anim_init(&a);
  lv_anim_set_var(&a, iconBox);
  lv_anim_set_exec_cb(&a, setScale);
  lv_anim_set_values(&a, 170, 256);
  lv_anim_set_duration(&a, 420);
  lv_anim_set_path_cb(&a, lv_anim_path_overshoot);
  lv_anim_start(&a);

  if (state == UI_UNSURE) {
    // Asking, not acting: the icon tips twice.
    lv_anim_init(&a);
    lv_anim_set_var(&a, iconBox);
    lv_anim_set_exec_cb(&a, setTilt);
    lv_anim_set_values(&a, 0, 70);
    lv_anim_set_duration(&a, 150);
    lv_anim_set_playback_duration(&a, 150);
    lv_anim_set_repeat_count(&a, 2);
    lv_anim_set_delay(&a, 380);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
  }

  show(labHeard);
  lv_label_set_text(labHeard, heard);
  riseIn(labHeard, 0);

  show(labValue);
  lv_obj_set_style_text_color(labValue, lv_color_hex(state == UI_NONE ? GREY : colour), 0);
  lv_obj_set_style_text_font(labValue,
                             state == UI_NONE ? &lv_font_montserrat_20 : &lv_font_montserrat_28, 0);
  lv_label_set_text(labValue, value);
  lv_obj_align(labValue, LV_ALIGN_TOP_MID, 0, state == UI_NONE ? 146 : 142);
  riseIn(labValue, 140);

  show(labFoot);
  lv_obj_set_style_text_color(labFoot, lv_color_hex(state == UI_UNSURE ? AMBER : GREY), 0);
  lv_label_set_text(labFoot, foot);
  lv_obj_align(labFoot, LV_ALIGN_TOP_MID, 0, 180);
  riseIn(labFoot, 200);
}
