// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX ktx 4.3.1 static PIC archive.

#include <cstdint>
#include <cstdlib>
#include <iostream>

#include "ktx.h"

int main() {
    ktxTextureCreateInfo create_info{};
    create_info.vkFormat = 37;  // VK_FORMAT_R8G8B8A8_UNORM
    create_info.baseWidth = 4;
    create_info.baseHeight = 4;
    create_info.baseDepth = 1;
    create_info.numDimensions = 2;
    create_info.numLevels = 1;
    create_info.numLayers = 1;
    create_info.numFaces = 1;
    create_info.isArray = KTX_FALSE;
    create_info.generateMipmaps = KTX_FALSE;

    ktxTexture2* tex = nullptr;
    KTX_error_code err = ktxTexture2_Create(&create_info, KTX_TEXTURE_CREATE_ALLOC_STORAGE, &tex);
    if (err != KTX_SUCCESS || tex == nullptr) {
        std::cerr << "ktxTexture2_Create failed: " << ktxErrorString(err) << '\n';
        return 1;
    }

    ktx_uint8_t pixels[4 * 4 * 4];
    for (int i = 0; i < 4 * 4; ++i) {
        pixels[i * 4 + 0] = 0x11;
        pixels[i * 4 + 1] = 0x22;
        pixels[i * 4 + 2] = 0x33;
        pixels[i * 4 + 3] = 0xFF;
    }

    err = ktxTexture_SetImageFromMemory(ktxTexture(tex), 0, 0, 0, pixels, sizeof(pixels));
    if (err != KTX_SUCCESS) {
        std::cerr << "ktxTexture_SetImageFromMemory failed: " << ktxErrorString(err) << '\n';
        ktxTexture_Destroy(ktxTexture(tex));
        return 1;
    }

    err = ktxTexture2_DeflateZstd(tex, 3);
    if (err != KTX_SUCCESS) {
        std::cerr << "ktxTexture2_DeflateZstd failed: " << ktxErrorString(err) << '\n';
        ktxTexture_Destroy(ktxTexture(tex));
        return 1;
    }

    ktx_uint8_t* bytes = nullptr;
    ktx_size_t size = 0;
    err = ktxTexture_WriteToMemory(ktxTexture(tex), &bytes, &size);
    ktxTexture_Destroy(ktxTexture(tex));
    if (err != KTX_SUCCESS || bytes == nullptr || size == 0) {
        std::cerr << "ktxTexture_WriteToMemory failed: " << ktxErrorString(err) << '\n';
        std::free(bytes);
        return 1;
    }

    ktxTexture2* restored = nullptr;
    err = ktxTexture2_CreateFromMemory(
        bytes, size, KTX_TEXTURE_CREATE_LOAD_IMAGE_DATA_BIT, &restored);
    std::free(bytes);
    if (err != KTX_SUCCESS || restored == nullptr) {
        std::cerr << "ktxTexture2_CreateFromMemory failed: " << ktxErrorString(err) << '\n';
        return 1;
    }

    const bool dims_ok = restored->baseWidth == 4 && restored->baseHeight == 4 &&
                         restored->numLevels == 1;
    ktxTexture_Destroy(ktxTexture(restored));
    if (!dims_ok) {
        std::cerr << "unexpected restored ktx2 dimensions\n";
        return 1;
    }

    std::cout << "ktx_zstd_bytes=" << size << '\n';
    std::cout << "ktx_static_pic_control=true\n";
    return 0;
}
