/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#define KHRONOS_STATIC
#include <ktx.h>

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    ktxTextureCreateInfo create_info;
    memset(&create_info, 0, sizeof(create_info));
    create_info.vkFormat = 37; /* VK_FORMAT_R8G8B8A8_UNORM */
    create_info.baseWidth = 4;
    create_info.baseHeight = 4;
    create_info.baseDepth = 1;
    create_info.numDimensions = 2;
    create_info.numLevels = 1;
    create_info.numLayers = 1;
    create_info.numFaces = 1;
    create_info.isArray = KTX_FALSE;
    create_info.generateMipmaps = KTX_FALSE;

    ktxTexture2 *tex = NULL;
    KTX_error_code rc = ktxTexture2_Create(
        &create_info, KTX_TEXTURE_CREATE_ALLOC_STORAGE, &tex);
    if (rc != KTX_SUCCESS || tex == NULL) {
        fprintf(stderr, "ktxTexture2_Create failed: %s\n", ktxErrorString(rc));
        return 1;
    }

    uint8_t pixels[4 * 4 * 4];
    for (size_t i = 0; i < sizeof(pixels); ++i) {
        pixels[i] = (uint8_t)((i * 17u + 3u) & 0xffu);
    }

    rc = ktxTexture_SetImageFromMemory(
        ktxTexture(tex), 0, 0, 0, pixels, sizeof(pixels));
    if (rc != KTX_SUCCESS) {
        fprintf(stderr, "ktxTexture_SetImageFromMemory failed: %s\n", ktxErrorString(rc));
        ktxTexture_Destroy(ktxTexture(tex));
        return 1;
    }

    rc = ktxTexture2_DeflateZstd(tex, 3);
    if (rc != KTX_SUCCESS) {
        fprintf(stderr, "ktxTexture2_DeflateZstd failed: %s\n", ktxErrorString(rc));
        ktxTexture_Destroy(ktxTexture(tex));
        return 1;
    }

    ktx_uint8_t *encoded = NULL;
    ktx_size_t encoded_size = 0;
    rc = ktxTexture_WriteToMemory(ktxTexture(tex), &encoded, &encoded_size);
    ktxTexture_Destroy(ktxTexture(tex));
    if (rc != KTX_SUCCESS || encoded == NULL || encoded_size == 0) {
        fprintf(stderr, "ktxTexture_WriteToMemory failed: %s\n", ktxErrorString(rc));
        free(encoded);
        return 1;
    }

    ktxTexture2 *decoded = NULL;
    rc = ktxTexture2_CreateFromMemory(
        encoded, encoded_size, KTX_TEXTURE_CREATE_LOAD_IMAGE_DATA_BIT, &decoded);
    free(encoded);
    if (rc != KTX_SUCCESS || decoded == NULL) {
        fprintf(stderr, "ktxTexture2_CreateFromMemory failed: %s\n", ktxErrorString(rc));
        return 1;
    }

    if (decoded->baseWidth != 4 || decoded->baseHeight != 4 ||
        ktxTexture_GetDataSize(ktxTexture(decoded)) != sizeof(pixels) ||
        memcmp(ktxTexture_GetData(ktxTexture(decoded)), pixels, sizeof(pixels)) != 0) {
        fprintf(stderr, "ktx2 zstd roundtrip mismatch\n");
        ktxTexture_Destroy(ktxTexture(decoded));
        return 1;
    }

    ktxTexture_Destroy(ktxTexture(decoded));
    printf("ktx_static_pic_control=true encoded=%zu\n", (size_t)encoded_size);
    return 0;
}
