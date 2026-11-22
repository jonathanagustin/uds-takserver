/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX LASzip 3.4.3 static PIC archive.
 * Verifies exact version 3.4.3 and creates/destroys real LASzip reader/writer handles.
 */

#include <stdio.h>

#include "laszip/laszip_api.h"

int main(void) {
    laszip_U8 major = 0, minor = 0;
    laszip_U16 revision = 0;
    laszip_U32 build = 0;
    if (laszip_get_version(&major, &minor, &revision, &build) != 0) {
        fprintf(stderr, "laszip_get_version failed\n");
        return 1;
    }
    if (major != 3 || minor != 4 || revision != 3) {
        fprintf(stderr, "unexpected LASzip version: %u.%u.%u\n", major, minor, revision);
        return 1;
    }

    laszip_POINTER reader = NULL;
    laszip_POINTER writer = NULL;
    if (laszip_create(&reader) != 0 || reader == NULL) {
        fprintf(stderr, "laszip_create reader failed\n");
        return 1;
    }
    if (laszip_create(&writer) != 0 || writer == NULL) {
        fprintf(stderr, "laszip_create writer failed\n");
        laszip_destroy(reader);
        return 1;
    }
    if (laszip_destroy(writer) != 0 || laszip_destroy(reader) != 0) {
        fprintf(stderr, "laszip_destroy failed\n");
        return 1;
    }

    printf("laszip_version=%u.%u.%u\n", major, minor, revision);
    printf("laszip_static_pic_control=true\n");
    return 0;
}
