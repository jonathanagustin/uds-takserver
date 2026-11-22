/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libjpeg-turbo 3.0.3 static PIC archive.
 */

#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>

#include "jpeglib.h"

int main(void) {
    const unsigned int width = 8;
    const unsigned int height = 8;
    unsigned char src[8 * 8 * 3];
    for (unsigned int i = 0; i < width * height; ++i) {
        src[i * 3 + 0] = 64;
        src[i * 3 + 1] = 128;
        src[i * 3 + 2] = 192;
    }

    struct jpeg_compress_struct cinfo;
    struct jpeg_error_mgr jerr;
    cinfo.err = jpeg_std_error(&jerr);
    jpeg_create_compress(&cinfo);

    unsigned char *outbuf = NULL;
    unsigned long outsize = 0;
    jpeg_mem_dest(&cinfo, &outbuf, &outsize);

    cinfo.image_width = width;
    cinfo.image_height = height;
    cinfo.input_components = 3;
    cinfo.in_color_space = JCS_RGB;
    jpeg_set_defaults(&cinfo);
    jpeg_set_quality(&cinfo, 95, TRUE);
    jpeg_start_compress(&cinfo, TRUE);

    while (cinfo.next_scanline < cinfo.image_height) {
        JSAMPROW row = &src[cinfo.next_scanline * width * 3];
        jpeg_write_scanlines(&cinfo, &row, 1);
    }

    jpeg_finish_compress(&cinfo);
    jpeg_destroy_compress(&cinfo);

    if (outbuf == NULL || outsize == 0) {
        fprintf(stderr, "jpeg compression produced empty output\n");
        free(outbuf);
        return 1;
    }

    struct jpeg_decompress_struct dinfo;
    dinfo.err = jpeg_std_error(&jerr);
    jpeg_create_decompress(&dinfo);
    jpeg_mem_src(&dinfo, outbuf, outsize);

    if (jpeg_read_header(&dinfo, TRUE) != JPEG_HEADER_OK) {
        fprintf(stderr, "jpeg_read_header failed\n");
        jpeg_destroy_decompress(&dinfo);
        free(outbuf);
        return 1;
    }

    jpeg_start_decompress(&dinfo);
    if (dinfo.output_width != width || dinfo.output_height != height ||
        dinfo.output_components != 3) {
        fprintf(stderr, "unexpected decoded dimensions: %ux%ux%d\n",
                dinfo.output_width, dinfo.output_height, dinfo.output_components);
        jpeg_destroy_decompress(&dinfo);
        free(outbuf);
        return 1;
    }

    unsigned char dst[8 * 8 * 3];
    while (dinfo.output_scanline < dinfo.output_height) {
        JSAMPROW row = &dst[dinfo.output_scanline * width * 3];
        jpeg_read_scanlines(&dinfo, &row, 1);
    }

    jpeg_finish_decompress(&dinfo);
    jpeg_destroy_decompress(&dinfo);
    free(outbuf);

    if (abs((int)dst[0] - 64) > 8 || abs((int)dst[1] - 128) > 8 ||
        abs((int)dst[2] - 192) > 8) {
        fprintf(stderr, "unexpected decoded pixel: (%u,%u,%u)\n", dst[0], dst[1], dst[2]);
        return 1;
    }

    printf("libjpeg_turbo_bytes=%lu\n", outsize);
    printf("libjpeg_turbo_static_pic_control=true\n");
    return 0;
}
