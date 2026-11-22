/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX modp-base64 2020-09-26 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "modp_b64.h"

int main(void) {
    const char *src = "uds-takserver-takx-modp-base64";
    const size_t src_len = strlen(src);
    char encoded[128];
    char decoded[128];

    const size_t enc_len = modp_b64_encode(encoded, src, src_len);
    if (enc_len == (size_t)-1 || enc_len >= sizeof(encoded)) {
        fprintf(stderr, "modp_b64_encode failed\n");
        return 1;
    }

    const size_t dec_len = modp_b64_decode(decoded, encoded, enc_len);
    if (dec_len != src_len || memcmp(decoded, src, src_len) != 0) {
        fprintf(stderr, "modp_b64_decode roundtrip mismatch\n");
        return 1;
    }

    printf("modp_base64_encoded_len=%zu\n", enc_len);
    printf("modp_base64_static_pic_control=true\n");
    return 0;
}
