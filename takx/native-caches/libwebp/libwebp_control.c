/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libwebp 1.4.0 static PIC archives.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "webp/decode.h"
#include "webp/encode.h"

int main(void) {
    const int enc_ver = WebPGetEncoderVersion();
    const int dec_ver = WebPGetDecoderVersion();
    if (enc_ver != 0x010400 || dec_ver != 0x010400) {
        fprintf(stderr, "unexpected libwebp version: enc=0x%06x dec=0x%06x\n", enc_ver, dec_ver);
        return 1;
    }

    const int width = 8;
    const int height = 8;
    uint8_t src[8 * 8 * 4];
    for (int i = 0; i < width * height; ++i) {
        src[i * 4 + 0] = 32;
        src[i * 4 + 1] = 160;
        src[i * 4 + 2] = 224;
        src[i * 4 + 3] = 255;
    }

    uint8_t *encoded = NULL;
    const size_t encoded_size = WebPEncodeLosslessRGBA(src, width, height, width * 4, &encoded);
    if (encoded_size == 0 || encoded == NULL) {
        fprintf(stderr, "WebPEncodeLosslessRGBA failed\n");
        return 1;
    }

    int out_w = 0;
    int out_h = 0;
    uint8_t *decoded = WebPDecodeRGBA(encoded, encoded_size, &out_w, &out_h);
    WebPFree(encoded);
    if (decoded == NULL || out_w != width || out_h != height) {
        fprintf(stderr, "WebPDecodeRGBA failed (%dx%d)\n", out_w, out_h);
        WebPFree(decoded);
        return 1;
    }

    if (decoded[0] != 32 || decoded[1] != 160 || decoded[2] != 224 || decoded[3] != 255) {
        fprintf(stderr, "unexpected decoded pixel: (%u,%u,%u,%u)\n",
                decoded[0], decoded[1], decoded[2], decoded[3]);
        WebPFree(decoded);
        return 1;
    }

    WebPFree(decoded);
    printf("libwebp_version=0x%06x\n", enc_ver);
    printf("libwebp_bytes=%zu\n", encoded_size);
    printf("libwebp_static_pic_control=true\n");
    return 0;
}
