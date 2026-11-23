// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

#include <cmath>
#include <cstddef>
#include <cstdio>
#include <string>
#include <vector>

#include <CesiumGeospatial/Cartographic.h>
#include <CesiumGeospatial/Ellipsoid.h>
#include <CesiumGltfReader/GltfReader.h>

int main() {
    const CesiumGeospatial::Cartographic input =
        CesiumGeospatial::Cartographic::fromDegrees(-77.0, 38.9, 100.0);
    const glm::dvec3 cartesian =
        CesiumGeospatial::Ellipsoid::WGS84.cartographicToCartesian(input);
    const auto roundtrip =
        CesiumGeospatial::Ellipsoid::WGS84.cartesianToCartographic(cartesian);
    if (!roundtrip.has_value()) {
        std::fprintf(stderr, "cartesianToCartographic returned nullopt\n");
        return 1;
    }
    if (std::abs(roundtrip->longitude - input.longitude) > 1e-9 ||
        std::abs(roundtrip->latitude - input.latitude) > 1e-9 ||
        std::abs(roundtrip->height - input.height) > 1e-6) {
        std::fprintf(stderr, "WGS84 cartographic round-trip mismatch\n");
        return 1;
    }

    const std::string gltf_json = R"({"asset":{"version":"2.0"}})";
    std::vector<std::byte> bytes;
    bytes.reserve(gltf_json.size());
    for (unsigned char ch : gltf_json) {
        bytes.push_back(static_cast<std::byte>(ch));
    }

    const CesiumGltfReader::GltfReader reader;
    const CesiumGltfReader::GltfReaderResult result = reader.readGltf(bytes);
    if (!result.model.has_value() || !result.errors.empty()) {
        std::fprintf(stderr, "GltfReader::readGltf failed (%zu errors)\n", result.errors.size());
        return 1;
    }
    if (result.model->asset.version != "2.0") {
        std::fprintf(
            stderr,
            "unexpected glTF version: %s\n",
            result.model->asset.version.c_str()
        );
        return 1;
    }

    std::printf("cesium_static_pic_control=true\n");
    return 0;
}
