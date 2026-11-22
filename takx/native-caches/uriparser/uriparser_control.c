/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX uriparser 0.9.8 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "uriparser/Uri.h"

int main(void) {
#if URI_VER_MAJOR != 0 || URI_VER_MINOR != 9 || URI_VER_RELEASE != 8
#error "unexpected uriparser header version"
#endif

    const char *raw = "https://example.invalid/a/b/../c.kml";
    UriUriA uri;
    const char *error_pos = NULL;
    if (uriParseSingleUriA(&uri, raw, &error_pos) != URI_SUCCESS) {
        fprintf(stderr, "uriParseSingleUriA failed\n");
        return 1;
    }

    if (uriNormalizeSyntaxA(&uri) != URI_SUCCESS) {
        fprintf(stderr, "uriNormalizeSyntaxA failed\n");
        uriFreeUriMembersA(&uri);
        return 1;
    }

    int chars_required = 0;
    if (uriToStringCharsRequiredA(&uri, &chars_required) != URI_SUCCESS ||
        chars_required <= 0 || chars_required >= 256) {
        fprintf(stderr, "uriToStringCharsRequiredA failed\n");
        uriFreeUriMembersA(&uri);
        return 1;
    }

    char out[256];
    int written = 0;
    if (uriToStringA(out, &uri, sizeof(out), &written) != URI_SUCCESS ||
        strcmp(out, "https://example.invalid/a/c.kml") != 0) {
        fprintf(stderr, "unexpected normalized URI: %s\n", out);
        uriFreeUriMembersA(&uri);
        return 1;
    }

    uriFreeUriMembersA(&uri);
    printf("uriparser_version=%d.%d.%d\n", URI_VER_MAJOR, URI_VER_MINOR, URI_VER_RELEASE);
    printf("uriparser_static_pic_control=true\n");
    return 0;
}
