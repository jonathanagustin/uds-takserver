// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX abseil 20240116.2 static PIC archives.

#include <iostream>
#include <string>
#include <vector>

#include "absl/base/config.h"
#include "absl/strings/str_cat.h"
#include "absl/strings/str_join.h"
#include "absl/strings/str_split.h"

int main() {
    static_assert(ABSL_LTS_RELEASE_VERSION == 20240116, "unexpected abseil LTS release");

    const std::string joined = absl::StrCat("takx-", absl::StrJoin({"abseil", "20240116.2"}, "-"));
    if (joined != "takx-abseil-20240116.2") {
        std::cerr << "unexpected absl::StrCat output: " << joined << '\n';
        return 1;
    }

    const std::vector<std::string> parts = absl::StrSplit(joined, '-');
    if (parts.size() != 3 || parts[1] != "abseil" || parts[2] != "20240116.2") {
        std::cerr << "unexpected absl::StrSplit result\n";
        return 1;
    }

    std::cout << "abseil_lts=" << ABSL_LTS_RELEASE_VERSION << '\n';
    std::cout << "abseil_static_pic_control=true\n";
    return 0;
}
