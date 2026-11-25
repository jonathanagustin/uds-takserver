/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libiconv 1.15 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "iconv.h"

int main(void) {
#if _LIBICONV_VERSION != 0x010F
#error "unexpected libiconv header version"
#endif

    iconv_t cd = iconv_open("UTF-16LE", "UTF-8");
    if (cd == (iconv_t)-1) {
        fprintf(stderr, "iconv_open failed\n");
        return 1;
    }

    char input[] = "TAK";
    char *inbuf = input;
    size_t inbytes = 3;
    unsigned char output[16];
    char *outbuf = (char *)output;
    size_t outbytes = sizeof(output);

    if (iconv(cd, &inbuf, &inbytes, &outbuf, &outbytes) == (size_t)-1) {
        fprintf(stderr, "iconv conversion failed\n");
        iconv_close(cd);
        return 1;
    }

    iconv_close(cd);

    const size_t written = sizeof(output) - outbytes;
    const unsigned char expected[6] = {'T', 0, 'A', 0, 'K', 0};
    if (written != sizeof(expected) || memcmp(output, expected, sizeof(expected)) != 0) {
        fprintf(stderr, "unexpected UTF-16LE output (written=%zu)\n", written);
        return 1;
    }

    printf("libiconv_version=0x%04x\n", _LIBICONV_VERSION);
    printf("libiconv_static_pic_control=true\n");
    return 0;
}
