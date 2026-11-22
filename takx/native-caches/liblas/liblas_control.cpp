// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <string>

#include <liblas/capi/liblas.h>
#include <liblas/liblas.hpp>
#include <liblas/version.hpp>

namespace {

struct SamplePoint {
  double x;
  double y;
  double z;
  uint16_t intensity;
  uint8_t classification;
};

constexpr SamplePoint kSamples[3] = {
    {500001.25, 4100002.50, 100.75, 23, 2},
    {499999.25, 4099990.50, 99.25, 65535, 5},
    {500003.00, 4100003.75, -12.50, 0, 1},
};

bool near(double a, double b) {
  return std::abs(a - b) < 0.001;
}

}  // namespace

int main() {
  const std::string version = liblas::GetVersion();
  if (version.empty()) {
    std::cerr << "empty libLAS version\n";
    return 1;
  }

  const std::string path = "/tmp/liblas_control_roundtrip.laz";
  std::remove(path.c_str());

  {
    std::ofstream ofs(path.c_str(), std::ios::out | std::ios::binary);
    if (!ofs) {
      std::cerr << "failed to open output LAZ\n";
      return 1;
    }
    liblas::Header header;
    header.SetDataFormatId(liblas::ePointFormat3);
    header.SetScale(0.01, 0.01, 0.01);
    header.SetOffset(500000.0, 4100000.0, 0.0);
    header.SetCompressed(true);
    liblas::Writer writer(ofs, header);
    for (const auto& s : kSamples) {
      liblas::Point p(&header);
      p.SetCoordinates(s.x, s.y, s.z);
      p.SetIntensity(s.intensity);
      p.SetClassification(s.classification);
      p.SetReturnNumber(1);
      p.SetNumberOfReturns(1);
      writer.WritePoint(p);
    }
  }

  {
    std::ifstream ifs(path.c_str(), std::ios::in | std::ios::binary);
    if (!ifs) {
      std::cerr << "failed to open input LAZ\n";
      return 1;
    }
    liblas::ReaderFactory factory;
    liblas::Reader reader = factory.CreateWithStream(ifs);
    if (!reader.GetHeader().Compressed() || reader.GetHeader().GetPointRecordsCount() != 3) {
      std::cerr << "unexpected LAZ header metadata\n";
      return 1;
    }
    for (int i = 0; i < 3; ++i) {
      if (!reader.ReadNextPoint()) {
        std::cerr << "failed to read point " << i << "\n";
        return 1;
      }
      const liblas::Point& p = reader.GetPoint();
      if (!near(p.GetX(), kSamples[i].x) || !near(p.GetY(), kSamples[i].y) ||
          !near(p.GetZ(), kSamples[i].z) || p.GetIntensity() != kSamples[i].intensity) {
        std::cerr << "point mismatch at index " << i << "\n";
        return 1;
      }
    }
    if (!reader.ReadPointAt(1) || !near(reader.GetPoint().GetX(), kSamples[1].x)) {
      std::cerr << "ReadPointAt(1) failed\n";
      return 1;
    }
  }

  LASReaderH creader = LASReader_Create(path.c_str());
  if (creader == nullptr) {
    std::cerr << "LASReader_Create failed\n";
    return 1;
  }
  LASPointH cpoint = LASReader_GetPointAt(creader, 2);
  if (cpoint == nullptr || !near(LASPoint_GetX(cpoint), kSamples[2].x) ||
      !near(LASPoint_GetZ(cpoint), kSamples[2].z)) {
    std::cerr << "C API LASReader_GetPointAt(2) failed\n";
    LASReader_Destroy(creader);
    return 1;
  }
  LASReader_Destroy(creader);
  std::remove(path.c_str());

  std::cout << "liblas_version=" << version << "\n";
  std::cout << "liblas_static_pic_control=true\n";
  return 0;
}
