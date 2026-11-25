// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX fmt 10.2.1 static PIC archive.

#include <iostream>
#include <string>

#include "fmt/core.h"
#include "fmt/format.h"

int main() {
    static_assert(FMT_VERSION == 100201, "unexpected fmt version");

    const std::string rendered = fmt::format("takx-fmt-{}.{}.{}-ok", 10, 2, 1);
    if (rendered != "takx-fmt-10.2.1-ok") {
        std::cerr << "unexpected fmt output: " << rendered << '\n';
        return 1;
    }

    std::cout << "fmt_version=" << FMT_VERSION << '\n';
    std::cout << "fmt_static_pic_control=true\n";
    return 0;
}
