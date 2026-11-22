/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX ngtcp2 1.6.0 + quictls static PIC archives.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ngtcp2/ngtcp2.h"
#include "ngtcp2/ngtcp2_crypto_quictls.h"

int main(void) {
    const ngtcp2_info *info = ngtcp2_version(0);
    if (!info || !info->version_str || strcmp(info->version_str, "1.6.0") != 0) {
        fprintf(stderr, "unexpected ngtcp2 version: %s\n",
                info && info->version_str ? info->version_str : "(null)");
        return 1;
    }

    const uint8_t raw_cid[8] = {0x54, 0x41, 0x4b, 0x58, 0x01, 0x06, 0x00, 0xaa};
    ngtcp2_cid cid;
    ngtcp2_cid_init(&cid, raw_cid, sizeof(raw_cid));
    if (cid.datalen != sizeof(raw_cid) || memcmp(cid.data, raw_cid, sizeof(raw_cid)) != 0) {
        fprintf(stderr, "ngtcp2_cid_init mismatch\n");
        return 1;
    }

    ngtcp2_settings settings;
    ngtcp2_settings_default(&settings);
    ngtcp2_transport_params params;
    ngtcp2_transport_params_default(&params);

    if (ngtcp2_crypto_quictls_init() != 0) {
        fprintf(stderr, "ngtcp2_crypto_quictls_init failed\n");
        return 1;
    }

    printf("ngtcp2_version=%s\n", info->version_str);
    printf("ngtcp2_static_pic_control=true\n");
    return 0;
}
