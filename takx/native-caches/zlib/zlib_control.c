/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX zlib 1.3.1 static PIC archive.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "zlib.h"

int main(void) {
    if (strcmp(zlibVersion(), "1.3.1") != 0) {
        fprintf(stderr, "unexpected zlibVersion: %s\n", zlibVersion());
        return 1;
    }

    const unsigned char payload[] = "uds-takserver-takx-zlib-1.3.1-roundtrip-control";
    uLong src_len = (uLong)(sizeof(payload) - 1);
    uLongf comp_len = compressBound(src_len);
    unsigned char *comp = (unsigned char *)malloc(comp_len);
    unsigned char *out = (unsigned char *)malloc(src_len + 1);
    if (!comp || !out) {
        fprintf(stderr, "malloc failed\n");
        free(comp);
        free(out);
        return 1;
    }

    if (compress2(comp, &comp_len, payload, src_len, Z_BEST_COMPRESSION) != Z_OK) {
        fprintf(stderr, "compress2 failed\n");
        free(comp);
        free(out);
        return 1;
    }

    uLongf out_len = src_len;
    if (uncompress(out, &out_len, comp, comp_len) != Z_OK ||
        out_len != src_len ||
        memcmp(out, payload, src_len) != 0) {
        fprintf(stderr, "uncompress roundtrip mismatch\n");
        free(comp);
        free(out);
        return 1;
    }

    free(comp);
    free(out);
    printf("zlib_version=%s\n", zlibVersion());
    printf("zlib_static_pic_control=true\n");
    return 0;
}
