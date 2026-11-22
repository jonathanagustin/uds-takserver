/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX nghttp2 1.62.1 static PIC archive.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nghttp2/nghttp2.h"

int main(void) {
    const nghttp2_info *info = nghttp2_version(0);
    if (!info || !info->version_str || strcmp(info->version_str, "1.62.1") != 0) {
        fprintf(stderr, "unexpected nghttp2 version: %s\n",
                info && info->version_str ? info->version_str : "(null)");
        return 1;
    }

    nghttp2_hd_deflater *deflater = NULL;
    if (nghttp2_hd_deflate_new(&deflater, 4096) != 0 || !deflater) {
        fprintf(stderr, "nghttp2_hd_deflate_new failed\n");
        return 1;
    }

    uint8_t name[] = "x-takx-control";
    uint8_t value[] = "uds-takserver-nghttp2-1.62.1";
    nghttp2_nv nva[1];
    nva[0].name = name;
    nva[0].value = value;
    nva[0].namelen = sizeof(name) - 1;
    nva[0].valuelen = sizeof(value) - 1;
    nva[0].flags = NGHTTP2_NV_FLAG_NONE;

    size_t bound = nghttp2_hd_deflate_bound(deflater, nva, 1);
    uint8_t *buf = (uint8_t *)malloc(bound);
    if (!buf) {
        fprintf(stderr, "malloc failed\n");
        nghttp2_hd_deflate_del(deflater);
        return 1;
    }

    ssize_t deflated = nghttp2_hd_deflate_hd(deflater, buf, bound, nva, 1);
    if (deflated <= 0) {
        fprintf(stderr, "nghttp2_hd_deflate_hd failed: %zd\n", deflated);
        free(buf);
        nghttp2_hd_deflate_del(deflater);
        return 1;
    }

    nghttp2_hd_inflater *inflater = NULL;
    if (nghttp2_hd_inflate_new(&inflater) != 0 || !inflater) {
        fprintf(stderr, "nghttp2_hd_inflate_new failed\n");
        free(buf);
        nghttp2_hd_deflate_del(deflater);
        return 1;
    }

    nghttp2_nv nv_out;
    int inflate_flags = 0;
    ssize_t consumed = nghttp2_hd_inflate_hd2(
        inflater, &nv_out, &inflate_flags, buf, (size_t)deflated, 1);
    if (consumed < 0 || !(inflate_flags & NGHTTP2_HD_INFLATE_EMIT) ||
        nv_out.namelen != nva[0].namelen ||
        memcmp(nv_out.name, name, nva[0].namelen) != 0 ||
        nv_out.valuelen != nva[0].valuelen ||
        memcmp(nv_out.value, value, nva[0].valuelen) != 0) {
        fprintf(stderr, "nghttp2 HPACK roundtrip mismatch\n");
        nghttp2_hd_inflate_del(inflater);
        free(buf);
        nghttp2_hd_deflate_del(deflater);
        return 1;
    }

    nghttp2_hd_inflate_del(inflater);
    free(buf);
    nghttp2_hd_deflate_del(deflater);
    printf("nghttp2_version=%s\n", info->version_str);
    printf("nghttp2_static_pic_control=true\n");
    return 0;
}
