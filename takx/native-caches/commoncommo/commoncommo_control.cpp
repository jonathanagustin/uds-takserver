// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only

#include <cstring>
#include <iostream>
#include <string>

#include <curl/curl.h>
#include <libxml/parser.h>

#include <commo.h>

namespace {

class ControlLogger final : public atakmap::commoncommo::CommoLogger {
 public:
  void log(Level level, Type type, const char* message, void* detail) override {
    (void)level;
    (void)type;
    (void)message;
    (void)detail;
  }
};

}  // namespace

int main() {
  xmlInitParser();
  if (curl_global_init(CURL_GLOBAL_DEFAULT) != CURLE_OK) {
    std::cerr << "curl_global_init failed\n";
    xmlCleanupParser();
    return 1;
  }

  const char* version = atakmap::commoncommo::Commo::getVersionString();
  if (version == nullptr || std::strncmp(version, "1.17.2", 6) != 0) {
    std::cerr << "unexpected CommonCommo version: " << (version ? version : "<null>") << "\n";
    curl_global_cleanup();
    xmlCleanupParser();
    return 1;
  }

  ControlLogger logger;
  const char* uid_str = "TAKX-CTRL-UID";
  atakmap::commoncommo::ContactUID uid(
      reinterpret_cast<const uint8_t*>(uid_str), std::strlen(uid_str));

  {
    atakmap::commoncommo::Commo commo(&logger, &uid, "TAKX-CTRL");

    const char* cot_xml =
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<event version=\"2.0\" uid=\"TAKX-CTRL-UID\" type=\"a-f-G-U-C\" "
        "time=\"2026-01-01T00:00:00.000Z\" start=\"2026-01-01T00:00:00.000Z\" "
        "stale=\"2026-01-01T00:05:00.000Z\" how=\"m-g\">"
        "<point lat=\"38.8977\" lon=\"-77.0365\" hae=\"10.0\" ce=\"5.0\" le=\"5.0\"/>"
        "<detail><contact callsign=\"TAKX-CTRL\"/></detail>"
        "</event>";

    char* proto_data = nullptr;
    size_t proto_len = 0;
    if (commo.cotXmlToTakproto(&proto_data, &proto_len, cot_xml, 1) !=
            atakmap::commoncommo::COMMO_SUCCESS ||
        proto_data == nullptr || proto_len == 0) {
      std::cerr << "cotXmlToTakproto failed\n";
      commo.shutdown();
      curl_global_cleanup();
      xmlCleanupParser();
      return 1;
    }

    char* roundtrip_xml = nullptr;
    if (commo.takprotoToCotXml(&roundtrip_xml, proto_data, proto_len) !=
            atakmap::commoncommo::COMMO_SUCCESS ||
        roundtrip_xml == nullptr) {
      std::cerr << "takprotoToCotXml failed\n";
      commo.takmessageFree(proto_data);
      commo.shutdown();
      curl_global_cleanup();
      xmlCleanupParser();
      return 1;
    }

    const std::string out_xml(roundtrip_xml);
    commo.takmessageFree(roundtrip_xml);
    commo.takmessageFree(proto_data);

    if (out_xml.find("TAKX-CTRL-UID") == std::string::npos ||
        out_xml.find("a-f-G-U-C") == std::string::npos) {
      std::cerr << "round-trip CoT XML mismatch: " << out_xml << "\n";
      commo.shutdown();
      curl_global_cleanup();
      xmlCleanupParser();
      return 1;
    }

    commo.shutdown();
  }

  curl_global_cleanup();
  xmlCleanupParser();

  std::cout << "commoncommo_version=" << version << "\n";
  std::cout << "commoncommo_static_pic_control=true\n";
  return 0;
}
