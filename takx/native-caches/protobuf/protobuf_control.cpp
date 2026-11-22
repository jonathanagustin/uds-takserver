// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX protobuf 3.21.0 libprotobuf-lite.a archive.

#include <cstdint>
#include <iostream>
#include <string>

#include "google/protobuf/io/coded_stream.h"
#include "google/protobuf/io/zero_copy_stream_impl_lite.h"
#include "google/protobuf/stubs/common.h"

int main() {
    GOOGLE_PROTOBUF_VERIFY_VERSION;

    const std::string version =
        google::protobuf::internal::VersionString(GOOGLE_PROTOBUF_VERSION);
    if (version != "3.21.0") {
        std::cerr << "unexpected protobuf version: " << version << '\n';
        return 1;
    }

    const uint32_t expected_tag = 4242U;
    const std::string expected_payload = "uds-takserver-takx-protobuf-3.21.0-control";

    std::string wire;
    {
        google::protobuf::io::StringOutputStream sos(&wire);
        google::protobuf::io::CodedOutputStream cos(&sos);
        cos.WriteVarint32(expected_tag);
        cos.WriteVarint32(static_cast<uint32_t>(expected_payload.size()));
        cos.WriteString(expected_payload);
        if (cos.HadError()) {
            std::cerr << "CodedOutputStream encode failed\n";
            return 1;
        }
    }

    uint32_t decoded_tag = 0;
    uint32_t decoded_len = 0;
    std::string decoded_payload;
    {
        google::protobuf::io::ArrayInputStream ais(wire.data(), static_cast<int>(wire.size()));
        google::protobuf::io::CodedInputStream cis(&ais);
        if (!cis.ReadVarint32(&decoded_tag) ||
            !cis.ReadVarint32(&decoded_len) ||
            !cis.ReadString(&decoded_payload, static_cast<int>(decoded_len))) {
            std::cerr << "CodedInputStream decode failed\n";
            return 1;
        }
    }

    if (decoded_tag != expected_tag || decoded_payload != expected_payload) {
        std::cerr << "protobuf wire roundtrip mismatch\n";
        return 1;
    }

    google::protobuf::ShutdownProtobufLibrary();
    std::cout << "protobuf_version=" << version << '\n';
    std::cout << "protobuf_static_pic_control=true\n";
    return 0;
}
