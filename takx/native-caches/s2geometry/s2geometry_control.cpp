/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <s2/s2cell_id.h>
#include <s2/s2latlng.h>

#include <cmath>
#include <cstdio>

int main() {
    const S2LatLng origin = S2LatLng::FromDegrees(37.7749, -122.4194);
    if (!origin.is_valid()) {
        std::fprintf(stderr, "invalid S2LatLng\n");
        return 1;
    }

    const S2CellId cell(origin);
    if (!cell.is_valid() || !cell.is_leaf()) {
        std::fprintf(stderr, "invalid S2CellId\n");
        return 1;
    }

    const S2LatLng roundtrip = cell.ToLatLng();
    if (std::fabs(roundtrip.lat().degrees() - origin.lat().degrees()) > 1e-6 ||
        std::fabs(roundtrip.lng().degrees() - origin.lng().degrees()) > 1e-6) {
        std::fprintf(stderr, "s2geometry roundtrip mismatch\n");
        return 1;
    }

    std::printf("s2geometry_static_pic_control=true token=%s\n", cell.ToToken().c_str());
    return 0;
}
