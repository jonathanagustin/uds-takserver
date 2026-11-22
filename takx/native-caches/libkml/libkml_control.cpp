// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#include <kml/base/uri_parser.h>
#include <kml/base/version.h>
#include <kml/dom.h>
#include <kml/engine.h>
#pragma GCC diagnostic pop

#include <cmath>
#include <cstdio>
#include <iostream>
#include <memory>
#include <string>

static bool check_geometry(const std::string &xml) {
  std::string error;
  kmldom::ElementPtr root = kmldom::ParseNS(xml, &error);
  kmldom::KmlPtr kml = kmldom::AsKml(root);
  if (!kml || !error.empty()) {
    return false;
  }
  kmldom::PlacemarkPtr placemark = kmldom::AsPlacemark(kml->get_feature());
  if (!placemark || placemark->get_name() != "actual-point") {
    return false;
  }
  kmldom::PointPtr point = kmldom::AsPoint(placemark->get_geometry());
  if (!point || !point->has_coordinates() ||
      point->get_coordinates()->get_coordinates_array_size() != 1) {
    return false;
  }
  const kmlbase::Vec3 v = point->get_coordinates()->get_coordinates_array_at(0);
  return std::abs(v.get_longitude() - 12.5) < 1e-12 &&
         std::abs(v.get_latitude() + 4.25) < 1e-12 &&
         std::abs(v.get_altitude() - 123.0) < 1e-12;
}

int main() {
  if (kmlbase::Version::GetString() != "1.3.0") {
    return 1;
  }
  const std::string xml =
      "<kml xmlns='http://www.opengis.net/kml/2.2'>"
      "<Placemark><name>actual-point</name>"
      "<Point><coordinates>12.5,-4.25,123</coordinates></Point>"
      "</Placemark></kml>";
  if (!check_geometry(xml)) {
    return 2;
  }
  std::string error;
  if (kmldom::Parse("<kml><broken>", &error) || error.empty()) {
    return 3;
  }

  const std::string kmz_path = "/tmp/takx_libkml_control.kmz";
  std::remove(kmz_path.c_str());
  if (!kmlengine::KmzFile::WriteKmz(kmz_path.c_str(), xml)) {
    return 4;
  }
  kmlengine::KmzFilePtr zip = kmlengine::KmzFile::OpenFromFile(kmz_path.c_str());
  std::string decoded;
  std::string missing;
  std::string bytes;
  const bool kmz_ok = zip && zip->ReadKml(&decoded) && decoded == xml &&
                      check_geometry(decoded) &&
                      !zip->ReadFile("absent.kml", &missing) &&
                      zip->SaveToString(&bytes) && !bytes.empty();
  std::remove(kmz_path.c_str());
  if (!kmz_ok) {
    return 5;
  }

  kmlengine::KmzFilePtr memory = kmlengine::KmzFile::OpenFromString(bytes);
  decoded.clear();
  if (!memory || !memory->ReadKml(&decoded) || decoded != xml) {
    return 6;
  }
  if (kmlengine::KmzFile::OpenFromString("not a zip")) {
    return 7;
  }

  std::unique_ptr<kmlbase::UriParser> uri(
      kmlbase::UriParser::CreateResolvedUri(
          "https://example.invalid/root/model.kml", "../other.kml"));
  std::string result;
  if (!uri || !uri->Normalize() || !uri->ToString(&result) ||
      result != "https://example.invalid/other.kml") {
    return 8;
  }
  std::string filename;
  if (!kmlbase::UriParser::UriToFilename("file:///tmp/kml%20control.kml",
                                         &filename) ||
      filename != "/tmp/kml control.kml") {
    return 9;
  }
  if (kmlbase::UriParser::CreateFromParse("http://[invalid")) {
    return 10;
  }

  std::cout << "libkml_static_pic_control=true\n";
  return 0;
}
