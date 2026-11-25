/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libmicrohttpd 1.0.1 static PIC archive.
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>

#include "microhttpd.h"

int main(void) {
    const char *ver = MHD_get_version();
    if (!ver || strcmp(ver, "1.0.1") != 0) {
        fprintf(stderr, "unexpected MHD_get_version: %s\n", ver ? ver : "(null)");
        return 1;
    }

    const char *reason = MHD_get_reason_phrase_for(MHD_HTTP_OK);
    if (!reason || strcmp(reason, "OK") != 0) {
        fprintf(stderr, "unexpected HTTP 200 reason: %s\n", reason ? reason : "(null)");
        return 1;
    }

    const char *body = "uds-takserver-takx-libmicrohttpd-1.0.1-control";
    struct MHD_Response *resp = MHD_create_response_from_buffer(
        strlen(body), (void *)body, MHD_RESPMEM_PERSISTENT);
    if (!resp) {
        fprintf(stderr, "MHD_create_response_from_buffer failed\n");
        return 1;
    }

    if (MHD_add_response_header(resp, "X-TAKX-Control", "ok") != MHD_YES) {
        fprintf(stderr, "MHD_add_response_header failed\n");
        MHD_destroy_response(resp);
        return 1;
    }

    const char *hdr = MHD_get_response_header(resp, "X-TAKX-Control");
    if (!hdr || strcmp(hdr, "ok") != 0) {
        fprintf(stderr, "MHD_get_response_header mismatch\n");
        MHD_destroy_response(resp);
        return 1;
    }

    MHD_destroy_response(resp);
    printf("libmicrohttpd_version=%s\n", ver);
    printf("libmicrohttpd_static_pic_control=true\n");
    return 0;
}
