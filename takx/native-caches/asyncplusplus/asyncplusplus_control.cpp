// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX asyncplusplus 1.1 static PIC archive.

#define LIBASYNC_STATIC
#include <iostream>

#include "async++.h"

int main() {
    auto task = async::spawn([]() -> int { return 21; })
                    .then([](int value) -> int { return value * 2; });
    const int result = task.get();
    if (result != 42) {
        std::cerr << "unexpected asyncplusplus result: " << result << '\n';
        return 1;
    }

    std::cout << "asyncplusplus_result=" << result << '\n';
    std::cout << "asyncplusplus_static_pic_control=true\n";
    return 0;
}
