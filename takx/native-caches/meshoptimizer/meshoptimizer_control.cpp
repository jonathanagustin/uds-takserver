// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX meshoptimizer 0.20 static PIC archive.

#include <array>
#include <cstddef>
#include <iostream>

#include "meshoptimizer.h"

struct Vertex {
    float x;
    float y;
    float z;
};

int main() {
    static_assert(MESHOPTIMIZER_VERSION == 200, "unexpected meshoptimizer version");

    const std::array<Vertex, 6> vertices = {{
        {0.0f, 0.0f, 0.0f},
        {1.0f, 0.0f, 0.0f},
        {1.0f, 1.0f, 0.0f},
        {0.0f, 0.0f, 0.0f},
        {1.0f, 1.0f, 0.0f},
        {0.0f, 1.0f, 0.0f},
    }};

    std::array<unsigned int, 6> remap{};
    const std::size_t unique_vertices = meshopt_generateVertexRemap(
        remap.data(), nullptr, vertices.size(), vertices.data(), vertices.size(), sizeof(Vertex));
    if (unique_vertices != 4) {
        std::cerr << "unexpected unique vertex count: " << unique_vertices << '\n';
        return 1;
    }

    std::array<unsigned int, 6> indices{};
    meshopt_remapIndexBuffer(indices.data(), nullptr, vertices.size(), remap.data());

    std::array<unsigned int, 6> optimized{};
    meshopt_optimizeVertexCache(
        optimized.data(), indices.data(), indices.size(), unique_vertices);

    const meshopt_VertexCacheStatistics stats = meshopt_analyzeVertexCache(
        optimized.data(), optimized.size(), unique_vertices, 16, 0, 0);
    if (stats.vertices_transformed != 4) {
        std::cerr << "unexpected transformed vertex count: " << stats.vertices_transformed << '\n';
        return 1;
    }

    std::cout << "meshoptimizer_version=" << MESHOPTIMIZER_VERSION << '\n';
    std::cout << "meshoptimizer_static_pic_control=true\n";
    return 0;
}
