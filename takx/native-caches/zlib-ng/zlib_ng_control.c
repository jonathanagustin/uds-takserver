/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib-ng.h>

int main(void) {
    const char *ver = zng_zlibng_version();
    if (ver == NULL || strstr(ver, "2.1.5") == NULL) {
        fprintf(stderr, "unexpected zlib-ng version: %s\n", ver ? ver : "(null)");
        return 1;
    }

    const char payload[] = "uds-takserver-zlib-ng-static-pic-control-roundtrip-payload-2.1.5";
    const size_t payload_len = sizeof(payload) - 1;

    size_t bound = zng_compressBound(payload_len);
    unsigned char *compressed = (unsigned char *)malloc(bound);
    unsigned char *restored = (unsigned char *)malloc(payload_len + 1);
    if (compressed == NULL || restored == NULL) {
        free(compressed);
        free(restored);
        fprintf(stderr, "allocation failed\n");
        return 1;
    }

    size_t comp_len = bound;
    int rc = zng_compress2(compressed, &comp_len, (const unsigned char *)payload, payload_len, 6);
    if (rc != Z_OK || comp_len == 0) {
        free(compressed);
        free(restored);
        fprintf(stderr, "zng_compress2 failed: %d\n", rc);
        return 1;
    }

    size_t out_len = payload_len;
    rc = zng_uncompress(restored, &out_len, compressed, comp_len);
    if (rc != Z_OK || out_len != payload_len || memcmp(restored, payload, payload_len) != 0) {
        free(compressed);
        free(restored);
        fprintf(stderr, "zng_uncompress roundtrip mismatch: rc=%d out_len=%zu\n", rc, out_len);
        return 1;
    }

    uint32_t crc = zng_crc32(0, (const unsigned char *)payload, (uint32_t)payload_len);
    free(compressed);
    free(restored);
    if (crc == 0) {
        fprintf(stderr, "unexpected zero crc32\n");
        return 1;
    }

    printf("zlib_ng_version=%s\n", ver);
    printf("zlib_ng_static_pic_control=true\n");
    return 0;
}
