// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX spdlog 1.14.1 + fmt 10.2.1 static PIC archives.

#define SPDLOG_COMPILED_LIB 1
#define SPDLOG_FMT_EXTERNAL 1

#include <iostream>
#include <memory>
#include <sstream>
#include <string>

#include "spdlog/sinks/ostream_sink.h"
#include "spdlog/spdlog.h"

int main() {
    static_assert(SPDLOG_VER_MAJOR == 1 && SPDLOG_VER_MINOR == 14 && SPDLOG_VER_PATCH == 1,
                  "unexpected spdlog version");

    std::ostringstream oss;
    auto sink = std::make_shared<spdlog::sinks::ostream_sink_mt>(oss);
    spdlog::logger logger("takx-control", sink);
    logger.set_pattern("%v");
    logger.info("spdlog-{}.{}.{}-ok", SPDLOG_VER_MAJOR, SPDLOG_VER_MINOR, SPDLOG_VER_PATCH);
    logger.flush();

    if (oss.str().find("spdlog-1.14.1-ok") == std::string::npos) {
        std::cerr << "unexpected spdlog output: " << oss.str() << '\n';
        return 1;
    }

    std::cout << "spdlog_version=" << SPDLOG_VER_MAJOR << '.' << SPDLOG_VER_MINOR << '.'
              << SPDLOG_VER_PATCH << '\n';
    std::cout << "spdlog_static_pic_control=true\n";
    return 0;
}
