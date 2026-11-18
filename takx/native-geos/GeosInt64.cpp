/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only */
#include <geos/platform.h>
#include <climits>
#include <cstdio>
#include <limits>

#ifdef INT64_IS_REALLY32
#error Unsupported GEOS integer fallback
#endif
#if defined(HAVE_INT64_T_64)
#define GEOS_INT64_FEATURE "HAVE_INT64_T_64"
#elif defined(HAVE_LONG_LONG_INT_64)
#define GEOS_INT64_FEATURE "HAVE_LONG_LONG_INT_64"
#elif defined(HAVE_LONG_INT_64)
#define GEOS_INT64_FEATURE "HAVE_LONG_INT_64"
#else
#error Missing GEOS 64-bit integer feature
#endif

static_assert(CHAR_BIT == 8 && sizeof(int64) == 8, "GEOS int64 must be eight octets");
static_assert(std::numeric_limits<int64>::is_integer && std::numeric_limits<int64>::is_signed
              && std::numeric_limits<int64>::digits == 63, "GEOS int64 must be signed 64-bit");

int main() {
    volatile int64 high = static_cast<int64>(1) << 40;
    volatile int64 small = 12345;
    const int64 positive = high + small;
    const int64 negative = -positive;
    if (positive <= high || negative >= 0 || positive + negative != 0
            || positive - high != small || (high * 1024) / high != 1024
            || negative / 3 != -(positive / 3) || -(negative % 3) != positive % 3) {
        return 2;
    }
    std::printf("int64_feature=%s\nint64_bytes=8\nint64_signed=true\nint64_arithmetic=true\n", GEOS_INT64_FEATURE);
    return 0;
}
