// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX draco 1.5.7 static PIC archive.

#include <iostream>
#include <memory>
#include <utility>

#include "draco/compression/decode.h"
#include "draco/compression/encode.h"
#include "draco/mesh/triangle_soup_mesh_builder.h"

int main() {
    draco::TriangleSoupMeshBuilder mb;
    mb.Start(2);
    const int pos_att_id =
        mb.AddAttribute(draco::GeometryAttribute::POSITION, 3, draco::DT_FLOAT32);

    const float p0[3] = {0.0f, 0.0f, 0.0f};
    const float p1[3] = {1.0f, 0.0f, 0.0f};
    const float p2[3] = {1.0f, 1.0f, 0.0f};
    const float p3[3] = {0.0f, 1.0f, 0.0f};
    mb.SetAttributeValuesForFace(pos_att_id, draco::FaceIndex(0), p0, p1, p2);
    mb.SetAttributeValuesForFace(pos_att_id, draco::FaceIndex(1), p0, p2, p3);

    std::unique_ptr<draco::Mesh> mesh = mb.Finalize();
    if (!mesh || mesh->num_faces() != 2) {
        std::cerr << "failed to build test mesh\n";
        return 1;
    }

    draco::Encoder encoder;
    encoder.SetAttributeQuantization(draco::GeometryAttribute::POSITION, 11);
    draco::EncoderBuffer enc_buf;
    if (!encoder.EncodeMeshToBuffer(*mesh, &enc_buf).ok() || enc_buf.size() == 0) {
        std::cerr << "EncodeMeshToBuffer failed\n";
        return 1;
    }

    draco::DecoderBuffer dec_buf;
    dec_buf.Init(enc_buf.data(), enc_buf.size());
    auto geom_type = draco::Decoder::GetEncodedGeometryType(&dec_buf);
    if (!geom_type.ok() || geom_type.value() != draco::TRIANGULAR_MESH) {
        std::cerr << "unexpected encoded geometry type\n";
        return 1;
    }

    draco::Decoder decoder;
    auto decoded = decoder.DecodeMeshFromBuffer(&dec_buf);
    if (!decoded.ok()) {
        std::cerr << "DecodeMeshFromBuffer failed\n";
        return 1;
    }
    auto out_mesh = std::move(decoded).value();
    if (!out_mesh || out_mesh->num_faces() != 2 || out_mesh->num_points() < 4) {
        std::cerr << "unexpected decoded mesh geometry\n";
        return 1;
    }

    draco::DecoderBuffer truncated;
    truncated.Init(enc_buf.data(), 1);
    if (decoder.DecodeMeshFromBuffer(&truncated).ok()) {
        std::cerr << "truncated buffer unexpectedly decoded\n";
        return 1;
    }

    std::cout << "draco_encoded_bytes=" << enc_buf.size() << '\n';
    std::cout << "draco_static_pic_control=true\n";
    return 0;
}
