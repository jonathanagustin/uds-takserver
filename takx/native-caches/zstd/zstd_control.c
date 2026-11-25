/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX zstd 1.5.6 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "zstd.h"

int main(void) {
#if ZSTD_VERSION_NUMBER != 10506
#error "unexpected zstd header version"
#endif

    const char *ver = ZSTD_versionString();
    if (strcmp(ver, "1.5.6") != 0) {
        fprintf(stderr, "unexpected zstd runtime version: %s\n", ver);
        return 1;
    }

    const char *payload =
        "uds-takserver takx zstd static pic roundtrip control payload "
        "uds-takserver takx zstd static pic roundtrip control payload";
    const size_t payload_len = strlen(payload);

    char compressed[512];
    const size_t bound = ZSTD_compressBound(payload_len);
    if (bound > sizeof(compressed)) {
        fprintf(stderr, "compressBound too large: %zu\n", bound);
        return 1;
    }

    const size_t csize = ZSTD_compress(compressed, sizeof(compressed), payload, payload_len, 3);
    if (ZSTD_isError(csize)) {
        fprintf(stderr, "ZSTD_compress failed: %s\n", ZSTD_getErrorName(csize));
        return 1;
    }

    char restored[256];
    const size_t dsize = ZSTD_decompress(restored, sizeof(restored), compressed, csize);
    if (ZSTD_isError(dsize)) {
        fprintf(stderr, "ZSTD_decompress failed: %s\n", ZSTD_getErrorName(dsize));
        return 1;
    }

    if (dsize != payload_len || memcmp(restored, payload, payload_len) != 0) {
        fprintf(stderr, "roundtrip mismatch (dsize=%zu expected=%zu)\n", dsize, payload_len);
        return 1;
    }

    printf("zstd_version=%s\n", ver);
    printf("zstd_static_pic_control=true\n");
    return 0;
}
