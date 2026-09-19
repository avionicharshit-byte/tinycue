/* tinycue_cli: read sentences on standard input, print one JSON line each.
 *
 *   tinycue_cli model.bin [-r repeats]
 *
 * File reading lives here, not in the runtime. The runtime only ever sees the bytes.
 */
#define _POSIX_C_SOURCE 199309L

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "tinycue.h"

#define LINE_MAX_BYTES 1024

/* malloc plus a hand rolled 8 byte alignment, so this stays plain C99. */
static void *aligned_block(size_t bytes, void **owner)
{
    unsigned char *raw = (unsigned char *)malloc(bytes + 8u);
    size_t slack;
    *owner = raw;
    if (raw == NULL) {
        return NULL;
    }
    slack = (size_t)(8u - ((uintptr_t)raw & 7u)) & 7u;
    return raw + slack;
}

static uint8_t *read_file(const char *path, size_t *length, void **owner)
{
    FILE *file = fopen(path, "rb");
    uint8_t *data;
    long size;

    if (file == NULL) {
        return NULL;
    }
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    size = ftell(file);
    if (size < 0) {
        fclose(file);
        return NULL;
    }
    rewind(file);
    data = (uint8_t *)aligned_block((size_t)size, owner);
    if (data == NULL) {
        fclose(file);
        return NULL;
    }
    if (fread(data, 1, (size_t)size, file) != (size_t)size) {
        free(*owner);
        fclose(file);
        return NULL;
    }
    fclose(file);
    *length = (size_t)size;
    return data;
}

static void print_json_string(const char *text)
{
    putchar('"');
    while (*text != '\0') {
        unsigned char c = (unsigned char)*text++;
        if (c == '"' || c == '\\') {
            printf("\\%c", c);
        } else if (c < 0x20) {
            printf("\\u%04x", c);
        } else {
            putchar((int)c);
        }
    }
    putchar('"');
}

static double now_micros(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec * 1e6 + (double)ts.tv_nsec / 1e3;
}

int main(int argc, char **argv)
{
    tcue_model model;
    tcue_result result;
    uint8_t *blob;
    size_t blob_len = 0;
    void *blob_owner = NULL;
    void *scratch_owner = NULL;
    void *scratch;
    size_t scratch_len;
    char line[LINE_MAX_BYTES];
    int repeats = 1;
    int status;
    int i;

    if (argc < 2) {
        fprintf(stderr, "usage: %s model.bin [-r repeats]\n", argv[0]);
        return 2;
    }
    for (i = 2; i < argc; i++) {
        if (strcmp(argv[i], "-r") == 0 && i + 1 < argc) {
            repeats = atoi(argv[++i]);
            if (repeats < 1) {
                repeats = 1;
            }
        } else {
            fprintf(stderr, "unknown option: %s\n", argv[i]);
            return 2;
        }
    }

    blob = read_file(argv[1], &blob_len, &blob_owner);
    if (blob == NULL) {
        fprintf(stderr, "cannot read %s\n", argv[1]);
        return 2;
    }
    status = tcue_init(&model, blob, blob_len);
    if (status != TCUE_OK) {
        fprintf(stderr, "%s: %s\n", argv[1], tcue_error(status));
        free(blob_owner);
        return 2;
    }

    scratch_len = tcue_scratch_size(&model);
    scratch = aligned_block(scratch_len, &scratch_owner);
    if (scratch == NULL) {
        fprintf(stderr, "out of memory\n");
        free(blob_owner);
        return 2;
    }

    while (fgets(line, (int)sizeof line, stdin) != NULL) {
        size_t len = strlen(line);
        double started;
        double micros;
        int slot;

        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r')) {
            line[--len] = '\0';
        }

        started = now_micros();
        for (i = 0; i < repeats; i++) {
            status = tcue_parse(&model, line, &result, scratch, scratch_len);
        }
        micros = (now_micros() - started) / (double)repeats;

        if (status != TCUE_OK) {
            printf("{\"text\":");
            print_json_string(line);
            printf(",\"error\":");
            print_json_string(tcue_error(status));
            printf("}\n");
            fflush(stdout);
            continue;
        }

        printf("{\"text\":");
        print_json_string(line);
        printf(",\"command\":");
        print_json_string(result.command);
        printf(",\"slots\":{");
        for (slot = 0; slot < result.slot_count; slot++) {
            if (slot > 0) {
                putchar(',');
            }
            print_json_string(result.slots[slot].name);
            putchar(':');
            if (result.slots[slot].is_number) {
                printf("%ld", (long)result.slots[slot].number);
            } else {
                print_json_string(result.slots[slot].text);
            }
        }
        printf("},\"missing\":[");
        for (slot = 0; slot < result.missing_count; slot++) {
            if (slot > 0) {
                putchar(',');
            }
            print_json_string(result.missing[slot]);
        }
        printf("],\"confidence\":%.6f,\"intent\":%.6f,\"slot\":%.6f,\"margin\":%.6f,"
               "\"unknown\":%d,\"unknown_share\":%.6f,\"all_carrier_unknown\":%s,"
               "\"unsure\":%s,\"micros\":%.1f}\n",
               result.confidence, result.intent_probability, result.slot_probability,
               result.intent_margin, result.unknown_count, result.unknown_share,
               result.all_carrier_unknown ? "true" : "false",
               result.unsure ? "true" : "false", micros);
        fflush(stdout);
    }

    free(scratch_owner);
    free(blob_owner);
    return 0;
}
