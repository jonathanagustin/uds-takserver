// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

#include <cstdio>
#include <cstring>

#include "gdal.h"
#include "gdal_priv.h"
#include "ogr_spatialref.h"

int main() {
    GDALAllRegister();

    const char* release = GDALVersionInfo("RELEASE_NAME");
    if (release == nullptr || std::strncmp(release, "2.4.4", 5) != 0) {
        std::fprintf(stderr, "unexpected GDAL release: %s\n", release ? release : "(null)");
        return 1;
    }

    GDALDriverManager* mgr = GetGDALDriverManager();
    if (mgr == nullptr) {
        std::fprintf(stderr, "missing GDALDriverManager\n");
        return 1;
    }

    const char* required_drivers[] = {"MEM", "GTiff", "KML", "LIBKML"};
    for (const char* name : required_drivers) {
        if (mgr->GetDriverByName(name) == nullptr) {
            std::fprintf(stderr, "missing required GDAL driver: %s\n", name);
            return 1;
        }
    }

    GDALDriver* mem_driver = mgr->GetDriverByName("MEM");
    GDALDataset* ds = mem_driver->Create("", 4, 4, 1, GDT_Byte, nullptr);
    if (ds == nullptr) {
        std::fprintf(stderr, "failed to create MEM dataset\n");
        return 1;
    }
    unsigned char write_buf[16];
    for (int i = 0; i < 16; ++i) {
        write_buf[i] = static_cast<unsigned char>(i + 1);
    }
    GDALRasterBand* band = ds->GetRasterBand(1);
    if (band == nullptr ||
        band->RasterIO(GF_Write, 0, 0, 4, 4, write_buf, 4, 4, GDT_Byte, 0, 0) != CE_None) {
        std::fprintf(stderr, "MEM RasterIO write failed\n");
        GDALClose(ds);
        return 1;
    }
    unsigned char read_buf[16] = {};
    if (band->RasterIO(GF_Read, 0, 0, 4, 4, read_buf, 4, 4, GDT_Byte, 0, 0) != CE_None ||
        std::memcmp(write_buf, read_buf, sizeof(write_buf)) != 0) {
        std::fprintf(stderr, "MEM RasterIO readback mismatch\n");
        GDALClose(ds);
        return 1;
    }
    GDALClose(ds);

    OGRSpatialReference srs;
    if (srs.SetWellKnownGeogCS("WGS84") != OGRERR_NONE || !srs.IsGeographic()) {
        std::fprintf(stderr, "WGS84 OGRSpatialReference check failed\n");
        return 1;
    }

    std::printf("gdal_static_pic_control=true\n");
    return 0;
}
