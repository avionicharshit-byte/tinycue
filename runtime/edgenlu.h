/* edge-nlu device runtime: read a sentence, get a command back.
 *
 * Portable C99. No malloc, no file IO, nothing beyond libc and libm. The model blob is
 * read where it lies, so on a microcontroller it stays in flash and never reaches RAM.
 * The caller owns one scratch buffer, sized by enlu_scratch_size().
 *
 * The blob format is written out in docs/model-format.md, and everything this file
 * computes is defined in src/edgenlu/features.py. The two must stay in step.
 */
#ifndef EDGENLU_H
#define EDGENLU_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Fixed ceilings. Longer input is refused rather than silently cut short. */
#define ENLU_MAX_TOKENS 32
#define ENLU_MAX_TEXT 256
#define ENLU_MAX_LABELS 64
#define ENLU_MAX_SLOTS 8
#define ENLU_MAX_SURFACE 48

/* Return codes. Everything below zero is a refusal. */
#define ENLU_OK 0
#define ENLU_BAD_BLOB (-1)
#define ENLU_BAD_ARGUMENT (-2)
#define ENLU_SMALL_SCRATCH (-3)
#define ENLU_EMPTY_INPUT (-4)
#define ENLU_TOO_LONG (-5)

/* One filled slot. `text` points into the blob for a listed value, or into the scratch
 * buffer for words nobody listed, so both stay valid until the next parse. */
typedef struct {
    const char *name;
    const char *text;   /* NULL on a number slot */
    int32_t number;     /* meaningful when is_number is 1 */
    int is_number;
    int known;          /* 0 when the words matched no listed value */
} enlu_slot;

typedef struct {
    const char *command;      /* the command name, or "none" */
    int is_none;
    enlu_slot slots[ENLU_MAX_SLOTS];
    int slot_count;
    const char *missing[ENLU_MAX_SLOTS];
    int missing_count;
    double confidence;
    double intent_probability;
    double slot_probability;
    int unsure;               /* 1 when the confidence is under the model's cut-off */
    int token_count;

    /* The evidence behind the confidence. A word is known when the model trained on it,
     * when it reads as a number, or when it appears in some slot's value list. */
    int unknown_count;        /* words of this sentence the model has never seen */
    double unknown_share;     /* unknown_count divided by token_count */
    int carrier_unknown;      /* 1 when a word outside every slot span is unknown */
    int all_carrier_unknown;  /* 1 when every word outside a slot span is unknown */
    double intent_margin;     /* the top command's probability minus the second one's */
} enlu_result;

/* Pointers into the blob, worked out once by enlu_init. Treat it as opaque. */
typedef struct {
    const uint8_t *blob;
    size_t blob_len;
    const char *strings;

    uint32_t table_size;
    uint32_t class_count;
    float temperature;
    const uint32_t *class_names;
    const float *bias;
    const uint16_t *weights;

    uint32_t label_count;
    uint32_t attr_count;
    uint32_t feature_count;
    const uint32_t *label_names;
    const float *transitions;
    const uint64_t *attr_hashes;
    const uint32_t *attr_starts;
    const uint16_t *feature_labels;
    const float *feature_weights;

    uint32_t command_count;
    uint32_t type_count;
    float unsure_below;
    float slot_power;
    const uint32_t *commands;
    const uint32_t *command_slots;
    const uint32_t *slot_types;
    const uint32_t *values;
    const uint32_t *forms;
    const uint32_t *gaz_words;
    const uint32_t *class_command;
    const uint32_t *label_type;
    const uint8_t *label_begin;

    uint32_t number_count;
    uint32_t filler_count;
    const uint32_t *number_words;  /* pairs of (string offset, value) */
    const uint32_t *filler_words;

    uint32_t vocab_count;
    const uint32_t *vocab_hashes;  /* FNV-1a 32 of every training word, sorted */
    uint32_t gate_count;
    const uint32_t *gate_ids;      /* which signal each gate weight multiplies */
    const float *gate_weights;
    float gate_bias;
} enlu_model;

/* Point a model at a blob. The blob must stay put and stay 8 byte aligned. */
int enlu_init(enlu_model *model, const uint8_t *blob, size_t len);

/* How many bytes of scratch enlu_parse needs for this model. */
size_t enlu_scratch_size(const enlu_model *model);

/* Read one sentence. `scratch` must be at least enlu_scratch_size() bytes and stay
 * untouched while the result is read, because slot text can point into it. */
int enlu_parse(const enlu_model *model, const char *text, enlu_result *out,
               void *scratch, size_t scratch_len);

/* A one line description of a return code. */
const char *enlu_error(int code);

/* The model's unsure cut-off, for a caller that wants to show it. */
double enlu_cutoff(const enlu_model *model);

#ifdef __cplusplus
}
#endif

#endif /* EDGENLU_H */
