/* tinycue device runtime. See tinycue.h, docs/model-format.md and features.py. */
#include "tinycue.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

#define TCUE_MAGIC0 'T'
#define TCUE_MAGIC1 'C'
#define TCUE_MAGIC2 'U'
#define TCUE_MAGIC3 'E'
#define TCUE_BLOB_VERSION 2u
#define TCUE_HEADER_SIZE 32u
#define TCUE_DIR_ENTRY 16u

#define SECTION_STRINGS 1u
#define SECTION_INTENT 2u
#define SECTION_CRF 3u
#define SECTION_SPEC 4u
#define SECTION_NUMBERS 5u
#define SECTION_GATE 6u

#define NO_INDEX 0xFFFFFFFFu
#define KIND_NUMBER 1u

/* The gate signals, in the order src/tinycue/gate.py fixes them. A blob names the ones
 * its weights use, so a model may use any subset. */
#define GATE_FEATURES 8
#define GATE_INTENT_LOGIT 0
#define GATE_SLOT_LOGPROB 1
#define GATE_UNKNOWN_SHARE 2
#define GATE_ALL_CARRIER_UNKNOWN 3
#define GATE_MARGIN 4
#define GATE_MISSING_REQUIRED 5
#define GATE_OPEN_VALUE 6
#define GATE_PREDICTED_NONE 7

/* Part of the format: the two log features are divided by this, and the probabilities
 * are pinned away from 0 and 1 first. */
#define GATE_LOG_SCALE 5.0
#define GATE_PROB_FLOOR 1e-6
#define GATE_SLOT_FLOOR 1e-9

#define CMD_WORDS 4
#define CMDSLOT_WORDS 3
#define TYPE_WORDS 10
#define VALUE_WORDS 3

/* Longest feature list one sentence can make: a token each, a pair each, and one per
 * three byte window of the joined text. */
#define TCUE_MAX_FEATURES 384
#define TCUE_RAW_MAX (TCUE_MAX_TEXT + 4)

/* The forward pass calls exp once per label pair per token, which on a chip with no
 * hardware double is most of the parse time. TCUE_FAST_EXP does those two calls in
 * single precision while everything around them stays double. Measured against the
 * double build on both example models, that moves the confidence by under 1e-6. */
#ifdef TCUE_FAST_EXP
#define TCUE_EXP(x) ((double)expf((float)(x)))
#define TCUE_LOG(x) ((double)logf((float)(x)))
#else
#define TCUE_EXP(x) exp(x)
#define TCUE_LOG(x) log(x)
#endif

static const int OFFSETS[5] = {-2, -1, 0, 1, 2};
static const char *const PREFIX[5] = {"-2:", "-1:", "0:", "1:", "2:"};

/* ------------------------------------------------------------------ helpers */

static int is_space(char c)
{
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v' || c == '\f';
}

static int is_punct(char c)
{
    return c == '.' || c == ',' || c == '!' || c == '?';
}

static char lower_byte(char c)
{
    return (c >= 'A' && c <= 'Z') ? (char)(c + 32) : c;
}

static uint32_t fnv32_start(void)
{
    return 2166136261u;
}

static uint32_t fnv32_bytes(uint32_t h, const char *p, size_t n)
{
    while (n--) {
        h ^= (unsigned char)*p++;
        h *= 16777619u;
    }
    return h;
}

static uint64_t fnv64_start(void)
{
    return 0xCBF29CE484222325ULL;
}

static uint64_t fnv64_bytes(uint64_t h, const char *p, size_t n)
{
    while (n--) {
        h ^= (unsigned char)*p++;
        h *= 0x100000001B3ULL;
    }
    return h;
}

static uint64_t fnv64_text(uint64_t h, const char *s)
{
    while (*s) {
        h ^= (unsigned char)*s++;
        h *= 0x100000001B3ULL;
    }
    return h;
}

/* IEEE 754 binary16 to double. The intent weights are stored half sized. */
static double half_to_double(uint16_t half)
{
    uint32_t sign = (uint32_t)(half >> 15) << 31;
    uint32_t exponent = (uint32_t)(half >> 10) & 0x1Fu;
    uint32_t mantissa = (uint32_t)half & 0x3FFu;
    uint32_t bits;
    float value;

    if (exponent == 0u) {
        if (mantissa == 0u) {
            bits = sign;
        } else {
            uint32_t e = 127u - 15u + 1u;
            while ((mantissa & 0x400u) == 0u) {
                mantissa <<= 1;
                e--;
            }
            mantissa &= 0x3FFu;
            bits = sign | (e << 23) | (mantissa << 13);
        }
    } else if (exponent == 31u) {
        bits = sign | 0x7F800000u | (mantissa << 13);
    } else {
        bits = sign | ((exponent + 112u) << 23) | (mantissa << 13);
    }
    memcpy(&value, &bits, sizeof value);
    return (double)value;
}

static uint32_t read_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static float read_f32(const uint8_t *p)
{
    uint32_t bits = read_u32(p);
    float value;
    memcpy(&value, &bits, sizeof value);
    return value;
}

/* Compare a NUL terminated blob string with a slice of the input. */
static int cmp_blob_slice(const char *blob_text, const char *slice, int len)
{
    int i;
    for (i = 0; i < len; i++) {
        unsigned char a = (unsigned char)blob_text[i];
        unsigned char b = (unsigned char)slice[i];
        if (a == 0) {
            return -1;
        }
        if (a != b) {
            return a < b ? -1 : 1;
        }
    }
    return blob_text[i] == 0 ? 0 : 1;
}

/* ------------------------------------------------------------------- init */

static const uint8_t *find_section(const uint8_t *blob, size_t len, uint32_t count,
                                   uint32_t want, uint32_t *size)
{
    uint32_t i;
    for (i = 0; i < count; i++) {
        const uint8_t *entry = blob + TCUE_HEADER_SIZE + (size_t)i * TCUE_DIR_ENTRY;
        if (read_u32(entry) != want) {
            continue;
        }
        {
            uint32_t offset = read_u32(entry + 4);
            uint32_t length = read_u32(entry + 8);
            if ((size_t)offset + length > len || (offset & 7u) != 0u) {
                return NULL;
            }
            *size = length;
            return blob + offset;
        }
    }
    return NULL;
}

int tcue_init(tcue_model *model, const uint8_t *blob, size_t len)
{
    const uint8_t *intent;
    const uint8_t *crf;
    const uint8_t *spec;
    const uint8_t *numbers;
    const uint8_t *gate;
    const uint8_t *strings;
    uint32_t size;
    uint32_t count;

    if (model == NULL || blob == NULL) {
        return TCUE_BAD_ARGUMENT;
    }
    if (((uintptr_t)blob & 7u) != 0u) {
        return TCUE_BAD_BLOB;
    }
    if (len < TCUE_HEADER_SIZE + TCUE_DIR_ENTRY) {
        return TCUE_BAD_BLOB;
    }
    if (blob[0] != TCUE_MAGIC0 || blob[1] != TCUE_MAGIC1 || blob[2] != TCUE_MAGIC2 ||
        blob[3] != TCUE_MAGIC3) {
        return TCUE_BAD_BLOB;
    }
    if (read_u32(blob + 4) != TCUE_BLOB_VERSION) {
        return TCUE_BAD_BLOB;
    }
    if (read_u32(blob + 8) > (uint32_t)len) {
        return TCUE_BAD_BLOB;
    }
    count = read_u32(blob + 12);
    if (count == 0u || count > 32u) {
        return TCUE_BAD_BLOB;
    }

    memset(model, 0, sizeof *model);
    model->blob = blob;
    model->blob_len = len;

    strings = find_section(blob, len, count, SECTION_STRINGS, &size);
    intent = find_section(blob, len, count, SECTION_INTENT, &size);
    crf = find_section(blob, len, count, SECTION_CRF, &size);
    spec = find_section(blob, len, count, SECTION_SPEC, &size);
    numbers = find_section(blob, len, count, SECTION_NUMBERS, &size);
    gate = find_section(blob, len, count, SECTION_GATE, &size);
    if (!strings || !intent || !crf || !spec || !numbers || !gate) {
        return TCUE_BAD_BLOB;
    }
    model->strings = (const char *)strings;

    model->table_size = read_u32(intent);
    model->class_count = read_u32(intent + 4);
    model->temperature = read_f32(intent + 8);
    model->class_names = (const uint32_t *)(const void *)(intent + read_u32(intent + 12));
    model->bias = (const float *)(const void *)(intent + read_u32(intent + 16));
    model->weights = (const uint16_t *)(const void *)(intent + read_u32(intent + 20));
    if (model->table_size == 0u || (model->table_size & (model->table_size - 1u)) != 0u) {
        return TCUE_BAD_BLOB;
    }
    if (model->class_count == 0u || read_u32(intent + 24) != 1u) {
        return TCUE_BAD_BLOB;
    }

    model->label_count = read_u32(crf);
    model->attr_count = read_u32(crf + 4);
    model->feature_count = read_u32(crf + 8);
    model->label_names = (const uint32_t *)(const void *)(crf + read_u32(crf + 12));
    model->transitions = (const float *)(const void *)(crf + read_u32(crf + 16));
    model->attr_hashes = (const uint64_t *)(const void *)(crf + read_u32(crf + 20));
    model->attr_starts = (const uint32_t *)(const void *)(crf + read_u32(crf + 24));
    model->feature_labels = (const uint16_t *)(const void *)(crf + read_u32(crf + 28));
    model->feature_weights = (const float *)(const void *)(crf + read_u32(crf + 32));
    if (model->label_count == 0u || model->label_count > TCUE_MAX_LABELS) {
        return TCUE_BAD_BLOB;
    }

    model->command_count = read_u32(spec);
    model->type_count = read_u32(spec + 4);
    model->unsure_below = read_f32(spec + 8);
    model->slot_power = read_f32(spec + 12);
    model->commands = (const uint32_t *)(const void *)(spec + read_u32(spec + 16));
    model->command_slots = (const uint32_t *)(const void *)(spec + read_u32(spec + 20));
    model->slot_types = (const uint32_t *)(const void *)(spec + read_u32(spec + 24));
    model->values = (const uint32_t *)(const void *)(spec + read_u32(spec + 28));
    model->forms = (const uint32_t *)(const void *)(spec + read_u32(spec + 32));
    model->gaz_words = (const uint32_t *)(const void *)(spec + read_u32(spec + 36));
    model->class_command = (const uint32_t *)(const void *)(spec + read_u32(spec + 40));
    model->label_type = (const uint32_t *)(const void *)(spec + read_u32(spec + 44));
    model->label_begin = spec + read_u32(spec + 48);

    {
        uint32_t c;
        for (c = 0; c < model->command_count; c++) {
            if (model->commands[(size_t)c * CMD_WORDS + 1] > (uint32_t)TCUE_MAX_SLOTS) {
                return TCUE_BAD_BLOB;
            }
        }
    }

    model->number_count = read_u32(numbers);
    model->filler_count = read_u32(numbers + 4);
    model->number_words = (const uint32_t *)(const void *)(numbers + read_u32(numbers + 8));
    model->filler_words = (const uint32_t *)(const void *)(numbers + read_u32(numbers + 12));

    model->vocab_count = read_u32(gate);
    model->vocab_hashes = (const uint32_t *)(const void *)(gate + read_u32(gate + 4));
    model->gate_count = read_u32(gate + 8);
    model->gate_ids = (const uint32_t *)(const void *)(gate + read_u32(gate + 12));
    model->gate_weights = (const float *)(const void *)(gate + read_u32(gate + 16));
    model->gate_bias = read_f32(gate + 20);
    if (read_u32(gate + 24) != (uint32_t)GATE_FEATURES) {
        return TCUE_BAD_BLOB;
    }
    {
        uint32_t i;
        for (i = 0; i < model->gate_count; i++) {
            if (model->gate_ids[i] >= (uint32_t)GATE_FEATURES) {
                return TCUE_BAD_BLOB;
            }
        }
    }
    return TCUE_OK;
}

double tcue_cutoff(const tcue_model *model)
{
    return model == NULL ? 0.0 : (double)model->unsure_below;
}

const char *tcue_error(int code)
{
    switch (code) {
    case TCUE_OK:
        return "ok";
    case TCUE_BAD_BLOB:
        return "the model blob is not one this runtime can read";
    case TCUE_BAD_ARGUMENT:
        return "a required argument was missing";
    case TCUE_SMALL_SCRATCH:
        return "the scratch buffer is too small";
    case TCUE_EMPTY_INPUT:
        return "there were no words to read";
    case TCUE_TOO_LONG:
        return "the sentence is longer than this runtime accepts";
    default:
        return "unknown error";
    }
}

/* ------------------------------------------------------------------- work */

typedef struct {
    char text[TCUE_MAX_TEXT];
    const char *token[TCUE_MAX_TOKENS];
    int token_len[TCUE_MAX_TOKENS];
    int count;
    char raw[TCUE_RAW_MAX];
    int raw_len;
    uint32_t bucket[TCUE_MAX_FEATURES];
    uint32_t repeats[TCUE_MAX_FEATURES];
    int bucket_count;
    char join[TCUE_RAW_MAX];
    char surface[TCUE_MAX_SLOTS][TCUE_MAX_SURFACE];
    uint8_t tag[TCUE_MAX_TOKENS];
    uint8_t known[TCUE_MAX_TOKENS];
    double *state;
    double *delta;
    double *alpha;
    uint8_t *back;
} tcue_work;

static size_t round_up8(size_t n)
{
    return (n + 7u) & ~(size_t)7u;
}

size_t tcue_scratch_size(const tcue_model *model)
{
    size_t labels = model == NULL ? TCUE_MAX_LABELS : model->label_count;
    size_t lattice = (size_t)TCUE_MAX_TOKENS * labels;
    return round_up8(sizeof(tcue_work)) + round_up8(lattice * sizeof(double)) * 2u +
           round_up8(2u * labels * sizeof(double)) + round_up8(lattice);
}

/* -------------------------------------------------------------- tokenising */

static int tokenize(const char *text, tcue_work *work)
{
    size_t used = 0;
    const char *s = text;

    work->count = 0;
    while (*s != '\0') {
        const char *start;
        int len = 0;
        while (*s != '\0' && is_space(*s)) {
            s++;
        }
        if (*s == '\0') {
            break;
        }
        if (work->count >= TCUE_MAX_TOKENS) {
            return TCUE_TOO_LONG;
        }
        start = work->text + used;
        while (*s != '\0' && !is_space(*s)) {
            if (used + 2u > (size_t)TCUE_MAX_TEXT) {
                return TCUE_TOO_LONG;
            }
            work->text[used++] = lower_byte(*s++);
            len++;
        }
        work->text[used++] = '\0';
        work->token[work->count] = start;
        work->token_len[work->count] = len;
        work->count++;
    }
    if (work->count == 0) {
        return TCUE_EMPTY_INPUT;
    }

    work->raw_len = 0;
    work->raw[work->raw_len++] = '^';
    {
        int i;
        for (i = 0; i < work->count; i++) {
            if (i > 0) {
                work->raw[work->raw_len++] = ' ';
            }
            memcpy(work->raw + work->raw_len, work->token[i], (size_t)work->token_len[i]);
            work->raw_len += work->token_len[i];
        }
    }
    work->raw[work->raw_len++] = '$';
    work->raw[work->raw_len] = '\0';
    return TCUE_OK;
}

/* ----------------------------------------------------------------- numbers */

static int compare_u32(const void *a, const void *b)
{
    uint32_t x = *(const uint32_t *)a;
    uint32_t y = *(const uint32_t *)b;
    if (x < y) {
        return -1;
    }
    return x > y ? 1 : 0;
}

static int is_filler_word(const tcue_model *model, const char *word, int len)
{
    uint32_t i;
    for (i = 0; i < model->filler_count; i++) {
        if (cmp_blob_slice(model->strings + model->filler_words[i], word, len) == 0) {
            return 1;
        }
    }
    return 0;
}

static int lookup_number_word(const tcue_model *model, const char *word, int len,
                              int32_t *value)
{
    uint32_t low = 0;
    uint32_t high = model->number_count;
    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        const uint32_t *row = model->number_words + (size_t)mid * 2u;
        int order = cmp_blob_slice(model->strings + row[0], word, len);
        if (order < 0) {
            low = mid + 1u;
        } else if (order > 0) {
            high = mid;
        } else {
            *value = (int32_t)row[1];
            return 1;
        }
    }
    return 0;
}

static int trim_punctuation(const char *word, int len, int *start)
{
    int a = 0;
    int b = len;
    while (a < b && is_punct(word[a])) {
        a++;
    }
    while (b > a && is_punct(word[b - 1])) {
        b--;
    }
    *start = a;
    return b - a;
}

static int all_digits(const char *word, int len)
{
    int i;
    if (len == 0) {
        return 0;
    }
    for (i = 0; i < len; i++) {
        if (word[i] < '0' || word[i] > '9') {
            return 0;
        }
    }
    return 1;
}

/* Digits, English words or Hindi words as one whole number, exactly as numbers.py. */
static int read_number(const tcue_model *model, const tcue_work *work, int start, int end,
                       int32_t *out)
{
    long total = 0;
    int seen = 0;
    int i;

    for (i = start; i < end; i++) {
        int offset;
        int len = trim_punctuation(work->token[i], work->token_len[i], &offset);
        const char *word = work->token[i] + offset;
        if (len == 0 || is_filler_word(model, word, len)) {
            continue;
        }
        if (all_digits(word, len)) {
            long value = 0;
            int k;
            for (k = 0; k < len; k++) {
                if (value < 100000000L) {
                    value = value * 10 + (word[k] - '0');
                }
            }
            total += value;
            seen = 1;
            continue;
        }
        {
            int32_t value;
            if (!lookup_number_word(model, word, len, &value)) {
                return 0;
            }
            if (value == 100) {
                total = (total != 0 ? total : 1) * 100;
            } else {
                total += value;
            }
            seen = 1;
        }
    }
    if (!seen) {
        return 0;
    }
    if (total > 2147483647L) {
        total = 2147483647L;
    }
    *out = (int32_t)total;
    return 1;
}

static int is_number_token(const tcue_model *model, const tcue_work *work, int index)
{
    int32_t value;
    return read_number(model, work, index, index + 1, &value);
}

/* ------------------------------------------------------------------ intent */

static void intent_scores(const tcue_model *model, tcue_work *work, double *scores)
{
    uint32_t mask = model->table_size - 1u;
    int i;
    int unique;
    double norm = 0.0;
    uint32_t k;

    work->bucket_count = 0;
    for (i = 0; i < work->count; i++) {
        uint32_t h = fnv32_bytes(fnv32_start(), "u:", 2);
        h = fnv32_bytes(h, work->token[i], (size_t)work->token_len[i]);
        work->bucket[work->bucket_count++] = h & mask;
    }
    for (i = 1; i < work->count; i++) {
        uint32_t h = fnv32_bytes(fnv32_start(), "b:", 2);
        h = fnv32_bytes(h, work->token[i - 1], (size_t)work->token_len[i - 1]);
        h = fnv32_bytes(h, "\x1f", 1);
        h = fnv32_bytes(h, work->token[i], (size_t)work->token_len[i]);
        work->bucket[work->bucket_count++] = h & mask;
    }
    for (i = 0; i + 3 <= work->raw_len; i++) {
        uint32_t h = fnv32_bytes(fnv32_start(), "c:", 2);
        h = fnv32_bytes(h, work->raw + i, 3);
        if (work->bucket_count >= TCUE_MAX_FEATURES) {
            break;
        }
        work->bucket[work->bucket_count++] = h & mask;
    }

    qsort(work->bucket, (size_t)work->bucket_count, sizeof work->bucket[0], compare_u32);

    /* Collapse repeats into counts. Writing never runs ahead of reading. */
    unique = 0;
    {
        int read = 0;
        while (read < work->bucket_count) {
            uint32_t value = work->bucket[read];
            uint32_t run = 0;
            while (read < work->bucket_count && work->bucket[read] == value) {
                read++;
                run++;
            }
            work->bucket[unique] = value;
            work->repeats[unique] = run;
            unique++;
        }
    }
    for (i = 0; i < unique; i++) {
        double count = (double)work->repeats[i];
        norm += count * count;
    }
    norm = sqrt(norm);

    for (k = 0; k < model->class_count; k++) {
        const uint16_t *row = model->weights + (size_t)k * model->table_size;
        double total = 0.0;
        for (i = 0; i < unique; i++) {
            double value = norm > 0.0 ? (double)work->repeats[i] / norm : 0.0;
            total += half_to_double(row[work->bucket[i]]) * value;
        }
        scores[k] = total + (double)model->bias[k];
    }
}

static void softmax(double *values, uint32_t count)
{
    uint32_t i;
    double top = values[0];
    double total = 0.0;
    for (i = 1; i < count; i++) {
        if (values[i] > top) {
            top = values[i];
        }
    }
    for (i = 0; i < count; i++) {
        values[i] = exp(values[i] - top);
        total += values[i];
    }
    for (i = 0; i < count; i++) {
        values[i] /= total;
    }
}

/* --------------------------------------------------------------------- crf */

static void add_attribute(const tcue_model *model, uint64_t hash, double *row)
{
    uint32_t low = 0;
    uint32_t high = model->attr_count;
    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        uint64_t value = model->attr_hashes[mid];
        if (value < hash) {
            low = mid + 1u;
        } else if (value > hash) {
            high = mid;
        } else {
            uint32_t at = model->attr_starts[mid];
            uint32_t stop = model->attr_starts[mid + 1u];
            for (; at < stop; at++) {
                row[model->feature_labels[at]] += (double)model->feature_weights[at];
            }
            return;
        }
    }
}

static int shape_of(const char *word, int len, char *out, int cap)
{
    int n = 0;
    char last = 0;
    int i;
    for (i = 0; i < len; i++) {
        char c = word[i];
        char code;
        if (c >= '0' && c <= '9') {
            code = 'd';
        } else if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z')) {
            code = 'a';
        } else {
            code = 'x';
        }
        if (code != last) {
            if (n < cap - 1) {
                out[n++] = code;
            }
            last = code;
        }
    }
    out[n] = '\0';
    return n;
}

static int in_gazetteer(const tcue_model *model, uint32_t type, const char *word, int len)
{
    const uint32_t *record = model->slot_types + (size_t)type * TYPE_WORDS;
    uint32_t low = record[8];
    uint32_t high = low + record[7];
    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        int order = cmp_blob_slice(model->strings + model->gaz_words[mid], word, len);
        if (order < 0) {
            low = mid + 1u;
        } else if (order > 0) {
            high = mid;
        } else {
            return 1;
        }
    }
    return 0;
}

/* The CRF features for one token, exactly as features.token_features builds them. */
static void token_state(const tcue_model *model, const tcue_work *work, int index,
                        double *row)
{
    uint32_t j;
    int o;

    for (j = 0; j < model->label_count; j++) {
        row[j] = 0.0;
    }
    add_attribute(model, fnv64_text(fnv64_start(), "bias"), row);
    if (index == 0) {
        add_attribute(model, fnv64_text(fnv64_start(), "BOS"), row);
    }
    if (index == work->count - 1) {
        add_attribute(model, fnv64_text(fnv64_start(), "EOS"), row);
    }

    for (o = 0; o < 5; o++) {
        int position = index + OFFSETS[o];
        uint64_t base = fnv64_text(fnv64_start(), PREFIX[o]);
        const char *word;
        int len;

        if (position < 0 || position >= work->count) {
            add_attribute(model, fnv64_text(base, "pad"), row);
            continue;
        }
        word = work->token[position];
        len = work->token_len[position];

        add_attribute(model, fnv64_bytes(fnv64_text(base, "w="), word, (size_t)len), row);
        if (len >= 2) {
            add_attribute(model, fnv64_bytes(fnv64_text(base, "p2="), word, 2), row);
            add_attribute(model, fnv64_bytes(fnv64_text(base, "s2="), word + len - 2, 2), row);
        }
        if (len >= 3) {
            add_attribute(model, fnv64_bytes(fnv64_text(base, "p3="), word, 3), row);
            add_attribute(model, fnv64_bytes(fnv64_text(base, "s3="), word + len - 3, 3), row);
        }
        {
            char shape[TCUE_MAX_SURFACE];
            shape_of(word, len, shape, (int)sizeof shape);
            add_attribute(model, fnv64_text(fnv64_text(base, "sh="), shape), row);
        }
        if (is_number_token(model, work, position)) {
            add_attribute(model, fnv64_text(base, "num=1"), row);
        }
        for (j = 0; j < model->type_count; j++) {
            const uint32_t *record = model->slot_types + (size_t)j * TYPE_WORDS;
            if (record[7] == 0u) {
                continue;
            }
            if (in_gazetteer(model, j, word, len)) {
                uint64_t h = fnv64_text(base, "g=");
                add_attribute(model, fnv64_text(h, model->strings + record[0]), row);
            }
        }
    }
}

/* Viterbi for the best path, then the forward pass for its probability. */
static double tag_sentence(const tcue_model *model, tcue_work *work)
{
    uint32_t labels = model->label_count;
    int n = work->count;
    int t;
    uint32_t j;
    uint32_t i;
    double logz;
    double path = 0.0;
    double best;
    uint32_t argbest;

    for (t = 0; t < n; t++) {
        token_state(model, work, t, work->state + (size_t)t * labels);
    }

    for (j = 0; j < labels; j++) {
        work->delta[j] = work->state[j];
        work->back[j] = 0;
    }
    for (t = 1; t < n; t++) {
        const double *previous = work->delta + (size_t)(t - 1) * labels;
        double *current = work->delta + (size_t)t * labels;
        const double *state = work->state + (size_t)t * labels;
        for (j = 0; j < labels; j++) {
            double top = previous[0] + (double)model->transitions[j];
            uint32_t from = 0;
            for (i = 1; i < labels; i++) {
                double value = previous[i] + (double)model->transitions[(size_t)i * labels + j];
                if (value > top) {
                    top = value;
                    from = i;
                }
            }
            current[j] = top + state[j];
            work->back[(size_t)t * labels + j] = (uint8_t)from;
        }
    }

    best = work->delta[(size_t)(n - 1) * labels];
    argbest = 0;
    for (j = 1; j < labels; j++) {
        double value = work->delta[(size_t)(n - 1) * labels + j];
        if (value > best) {
            best = value;
            argbest = j;
        }
    }
    work->tag[n - 1] = (uint8_t)argbest;
    for (t = n - 1; t > 0; t--) {
        work->tag[t - 1] = work->back[(size_t)t * labels + work->tag[t]];
    }

    for (t = 0; t < n; t++) {
        path += work->state[(size_t)t * labels + work->tag[t]];
        if (t > 0) {
            path += (double)model->transitions[(size_t)work->tag[t - 1] * labels + work->tag[t]];
        }
    }

    /* Forward pass in log space, so the partition function never overflows. */
    for (j = 0; j < labels; j++) {
        work->alpha[j] = work->state[j];
    }
    for (t = 1; t < n; t++) {
        const double *previous = work->alpha + (size_t)((t - 1) & 1) * labels;
        double *current = work->alpha + (size_t)(t & 1) * labels;
        const double *state = work->state + (size_t)t * labels;
        for (j = 0; j < labels; j++) {
            double top = previous[0] + (double)model->transitions[j];
            double total = 0.0;
            for (i = 1; i < labels; i++) {
                double value = previous[i] + (double)model->transitions[(size_t)i * labels + j];
                if (value > top) {
                    top = value;
                }
            }
            for (i = 0; i < labels; i++) {
                total += TCUE_EXP(previous[i] +
                                  (double)model->transitions[(size_t)i * labels + j] - top);
            }
            current[j] = top + TCUE_LOG(total) + state[j];
        }
    }
    {
        const double *last = work->alpha + (size_t)((n - 1) & 1) * labels;
        double top = last[0];
        double total = 0.0;
        for (j = 1; j < labels; j++) {
            if (last[j] > top) {
                top = last[j];
            }
        }
        for (j = 0; j < labels; j++) {
            total += TCUE_EXP(last[j] - top);
        }
        logz = top + TCUE_LOG(total);
    }

    return exp(path - logz);
}

/* ------------------------------------------------------------------ decode */

static int join_tokens(const tcue_work *work, int start, int end, char *out, int cap)
{
    int n = 0;
    int i;
    for (i = start; i < end; i++) {
        int len = work->token_len[i];
        if (i > start) {
            if (n + 1 >= cap) {
                break;
            }
            out[n++] = ' ';
        }
        if (n + len >= cap) {
            len = cap - n - 1;
        }
        if (len <= 0) {
            break;
        }
        memcpy(out + n, work->token[i], (size_t)len);
        n += len;
    }
    out[n] = '\0';
    return n;
}

/* The canonical value a surface form stands for, or NULL when it is not listed. */
static const char *find_canonical(const tcue_model *model, uint32_t type, const char *text)
{
    const uint32_t *record = model->slot_types + (size_t)type * TYPE_WORDS;
    uint32_t v;
    for (v = 0; v < record[5]; v++) {
        const uint32_t *value = model->values + (size_t)(record[6] + v) * VALUE_WORDS;
        uint32_t f;
        for (f = 0; f < value[1]; f++) {
            const char *form = model->strings + model->forms[value[2] + f];
            if (strcmp(form, text) == 0) {
                return model->strings + value[0];
            }
        }
    }
    return NULL;
}

typedef struct {
    int matched;
    int is_number;
    int32_t number;
    const char *text;
} tcue_match;

static tcue_match span_value(const tcue_model *model, tcue_work *work, uint32_t type,
                             int start, int end)
{
    const uint32_t *record = model->slot_types + (size_t)type * TYPE_WORDS;
    tcue_match out;
    out.matched = 0;
    out.is_number = 0;
    out.number = 0;
    out.text = NULL;

    if (record[1] == KIND_NUMBER) {
        int32_t value;
        if (!read_number(model, work, start, end, &value)) {
            return out;
        }
        if ((record[4] & 1u) != 0u && value < (int32_t)record[2]) {
            return out;
        }
        if ((record[4] & 2u) != 0u && value > (int32_t)record[3]) {
            return out;
        }
        out.matched = 1;
        out.is_number = 1;
        out.number = value;
        return out;
    }

    join_tokens(work, start, end, work->join, (int)sizeof work->join);
    out.text = find_canonical(model, type, work->join);
    out.matched = out.text != NULL;
    return out;
}

/* Trailing words are dropped first, then leading ones. decode.py does the same. */
static tcue_match trim_span(const tcue_model *model, tcue_work *work, uint32_t type,
                            int *start, int *end)
{
    tcue_match out;
    int stop;
    int begin;

    for (stop = *end; stop > *start; stop--) {
        out = span_value(model, work, type, *start, stop);
        if (out.matched) {
            *end = stop;
            return out;
        }
    }
    for (begin = *start + 1; begin < *end; begin++) {
        out = span_value(model, work, type, begin, *end);
        if (out.matched) {
            *start = begin;
            return out;
        }
    }
    out.matched = 0;
    out.is_number = 0;
    out.number = 0;
    out.text = NULL;
    return out;
}

static int command_slot_for_type(const tcue_model *model, const uint32_t *command,
                                 uint32_t type)
{
    uint32_t i;
    for (i = 0; i < command[1]; i++) {
        const uint32_t *slot = model->command_slots + (size_t)(command[2] + i) * CMDSLOT_WORDS;
        if (slot[1] == type) {
            return (int)i;
        }
    }
    return -1;
}

static void decode_tags(const tcue_model *model, tcue_work *work, uint32_t command_index,
                        tcue_result *out)
{
    const uint32_t *command = model->commands + (size_t)command_index * CMD_WORDS;
    int filled[TCUE_MAX_SLOTS];
    uint32_t i;
    int t;
    int current = -1;
    int span_start = 0;

    for (i = 0; i < TCUE_MAX_SLOTS; i++) {
        filled[i] = 0;
    }

    /* Walk the tags, closing a span whenever the type changes or an O tag arrives. */
    for (t = 0; t <= work->count; t++) {
        int type = -1;
        int begins = 0;
        if (t < work->count) {
            uint32_t label = work->tag[t];
            uint32_t mapped = model->label_type[label];
            if (mapped != NO_INDEX) {
                type = (int)mapped;
                begins = model->label_begin[label] != 0;
            }
        }

        if (current >= 0 && (t == work->count || type < 0 || begins || type != current)) {
            int slot_index = command_slot_for_type(model, command, (uint32_t)current);
            if (slot_index >= 0 && !filled[slot_index] && out->slot_count < TCUE_MAX_SLOTS) {
                const uint32_t *slot =
                    model->command_slots + (size_t)(command[2] + (uint32_t)slot_index) *
                                               CMDSLOT_WORDS;
                int from = span_start;
                int to = t;
                tcue_match match = trim_span(model, work, (uint32_t)current, &from, &to);
                const uint32_t *record =
                    model->slot_types + (size_t)current * TYPE_WORDS;
                int keep = 1;
                tcue_slot *filled_slot = &out->slots[out->slot_count];

                if (!match.matched && record[1] == KIND_NUMBER) {
                    keep = 0;  /* a number slot has no open vocabulary */
                }
                if (keep) {
                    filled_slot->name = model->strings + slot[0];
                    filled_slot->is_number = match.is_number;
                    filled_slot->number = match.number;
                    filled_slot->known = match.matched;
                    if (match.is_number) {
                        filled_slot->text = NULL;
                    } else if (match.matched) {
                        filled_slot->text = match.text;
                    } else {
                        char *store = work->surface[out->slot_count];
                        join_tokens(work, span_start, t, store, TCUE_MAX_SURFACE);
                        filled_slot->text = store;
                    }
                    filled[slot_index] = 1;
                    out->slot_count++;
                }
            }
            current = -1;
        }

        if (t == work->count) {
            break;
        }
        if (type >= 0 && (current < 0 || begins || type != current)) {
            current = type;
            span_start = t;
        }
    }

    for (i = 0; i < command[1]; i++) {
        const uint32_t *slot = model->command_slots + (size_t)(command[2] + i) * CMDSLOT_WORDS;
        if (slot[2] != 0u && !filled[i] && out->missing_count < TCUE_MAX_SLOTS) {
            out->missing[out->missing_count++] = model->strings + slot[0];
        }
    }
}

/* -------------------------------------------------------------------- gate */

/* Is this word one the model trained on, one that reads as a number, or one that some
 * slot's value list holds? Anything else is a word it has never seen, and the features
 * are hashed n-grams, so such a word contributes nothing and the rest decide alone.
 * gate.known_flags in src/tinycue/gate.py asks the same three questions in this order. */
static int token_is_known(const tcue_model *model, const tcue_work *work, int index)
{
    const char *word = work->token[index];
    int len = work->token_len[index];
    uint32_t hash = fnv32_bytes(fnv32_start(), word, (size_t)len);
    uint32_t low = 0;
    uint32_t high = model->vocab_count;
    uint32_t j;

    while (low < high) {
        uint32_t mid = low + (high - low) / 2u;
        uint32_t value = model->vocab_hashes[mid];
        if (value < hash) {
            low = mid + 1u;
        } else if (value > hash) {
            high = mid;
        } else {
            return 1;
        }
    }
    if (is_number_token(model, work, index)) {
        return 1;
    }
    for (j = 0; j < model->type_count; j++) {
        const uint32_t *record = model->slot_types + (size_t)j * TYPE_WORDS;
        if (record[7] != 0u && in_gazetteer(model, j, word, len)) {
            return 1;
        }
    }
    return 0;
}

/* The unknown word counts, and whether the words outside every slot span are all new. */
static void gate_evidence(const tcue_model *model, tcue_work *work, tcue_result *out)
{
    int i;
    int carriers = 0;
    int carriers_unknown = 0;

    out->unknown_count = 0;
    for (i = 0; i < work->count; i++) {
        int known = token_is_known(model, work, i);
        work->known[i] = (uint8_t)known;
        if (!known) {
            out->unknown_count++;
        }
        if (model->label_type[work->tag[i]] == NO_INDEX) {
            carriers++;
            if (!known) {
                carriers_unknown++;
            }
        }
    }
    out->unknown_share = work->count > 0 ? (double)out->unknown_count / (double)work->count : 0.0;
    out->carrier_unknown = carriers_unknown > 0;
    out->all_carrier_unknown = carriers > 0 && carriers_unknown == carriers;
}

/* The eight signals, in the order the format fixes. gate.feature_vector does the same. */
static void gate_vector(const tcue_result *out, double *feature)
{
    double intent = out->intent_probability;
    double slot = out->slot_probability;
    int i;
    int open_value = 0;

    if (intent < GATE_PROB_FLOOR) {
        intent = GATE_PROB_FLOOR;
    }
    if (intent > 1.0 - GATE_PROB_FLOOR) {
        intent = 1.0 - GATE_PROB_FLOOR;
    }
    if (slot < GATE_SLOT_FLOOR) {
        slot = GATE_SLOT_FLOOR;
    }
    if (slot > 1.0) {
        slot = 1.0;
    }
    for (i = 0; i < out->slot_count; i++) {
        if (!out->slots[i].known) {
            open_value = 1;
        }
    }

    feature[GATE_INTENT_LOGIT] = log(intent / (1.0 - intent)) / GATE_LOG_SCALE;
    feature[GATE_SLOT_LOGPROB] = log(slot) / GATE_LOG_SCALE;
    feature[GATE_UNKNOWN_SHARE] = out->unknown_share;
    feature[GATE_ALL_CARRIER_UNKNOWN] = out->all_carrier_unknown ? 1.0 : 0.0;
    feature[GATE_MARGIN] = out->intent_margin;
    feature[GATE_MISSING_REQUIRED] = out->missing_count > 0 ? 1.0 : 0.0;
    feature[GATE_OPEN_VALUE] = open_value ? 1.0 : 0.0;
    feature[GATE_PREDICTED_NONE] = out->is_none ? 1.0 : 0.0;
}

/* The chance the whole answer is right. With no weights in the blob this falls back to
 * the old product of the two probabilities. */
static double gate_confidence(const tcue_model *model, const tcue_result *out)
{
    double feature[GATE_FEATURES];
    double total;
    uint32_t i;

    if (model->gate_count == 0u) {
        double slot = out->slot_probability < 0.0 ? 0.0 : out->slot_probability;
        return out->intent_probability * pow(slot, (double)model->slot_power);
    }

    gate_vector(out, feature);
    total = (double)model->gate_bias;
    for (i = 0; i < model->gate_count; i++) {
        total += (double)model->gate_weights[i] * feature[model->gate_ids[i]];
    }
    if (total >= 0.0) {
        return 1.0 / (1.0 + exp(-total));
    }
    {
        double value = exp(total);
        return value / (1.0 + value);
    }
}

/* ------------------------------------------------------------------- parse */

int tcue_parse(const tcue_model *model, const char *text, tcue_result *out, void *scratch,
               size_t scratch_len)
{
    tcue_work *work;
    uint8_t *cursor;
    double scores[TCUE_MAX_LABELS > 64 ? TCUE_MAX_LABELS : 64];
    uint32_t labels;
    size_t lattice;
    uint32_t best = 0;
    uint32_t k;
    int status;

    if (model == NULL || text == NULL || out == NULL || scratch == NULL) {
        return TCUE_BAD_ARGUMENT;
    }
    if (model->class_count > (uint32_t)(sizeof scores / sizeof scores[0])) {
        return TCUE_BAD_BLOB;
    }
    if (scratch_len < tcue_scratch_size(model)) {
        return TCUE_SMALL_SCRATCH;
    }
    if (((uintptr_t)scratch & 7u) != 0u) {
        return TCUE_BAD_ARGUMENT;
    }

    labels = model->label_count;
    lattice = (size_t)TCUE_MAX_TOKENS * labels;
    work = (tcue_work *)scratch;
    cursor = (uint8_t *)scratch + round_up8(sizeof(tcue_work));
    work->state = (double *)(void *)cursor;
    cursor += round_up8(lattice * sizeof(double));
    work->delta = (double *)(void *)cursor;
    cursor += round_up8(lattice * sizeof(double));
    work->alpha = (double *)(void *)cursor;
    cursor += round_up8(2u * labels * sizeof(double));
    work->back = cursor;

    memset(out, 0, sizeof *out);
    status = tokenize(text, work);
    if (status != TCUE_OK) {
        return status;
    }
    out->token_count = work->count;

    intent_scores(model, work, scores);
    for (k = 0; k < model->class_count; k++) {
        scores[k] /= (double)model->temperature;
    }
    softmax(scores, model->class_count);
    for (k = 1; k < model->class_count; k++) {
        if (scores[k] > scores[best]) {
            best = k;
        }
    }

    /* How far ahead the winner is. One class means nothing to compare it with. */
    if (model->class_count < 2u) {
        out->intent_margin = 1.0;
    } else {
        double second = -1.0;
        for (k = 0; k < model->class_count; k++) {
            if (k != best && scores[k] > second) {
                second = scores[k];
            }
        }
        out->intent_margin = scores[best] - second;
    }

    out->command = model->strings + model->class_names[best];
    out->intent_probability = scores[best];
    out->slot_probability = tag_sentence(model, work);
    out->is_none = model->class_command[best] == NO_INDEX;
    if (!out->is_none) {
        decode_tags(model, work, model->class_command[best], out);
    }

    gate_evidence(model, work, out);
    out->confidence = gate_confidence(model, out);
    out->unsure = out->confidence < (double)model->unsure_below;
    return TCUE_OK;
}
