// Copyright 2026 uds-takserver authors
// SPDX-License-Identifier: GPL-3.0-only
//
// Genuine C++17 consumer for the prebuilt TAKX tinyxml2 10.0.0 static PIC archive.

#include <cstring>
#include <iostream>

#include "tinyxml2.h"

int main() {
    static_assert(
        TINYXML2_MAJOR_VERSION == 10 && TINYXML2_MINOR_VERSION == 0 &&
            TINYXML2_PATCH_VERSION == 0,
        "unexpected tinyxml2 version");

    const char* xml = "<takx version=\"10\"><item id=\"ok\">hello</item></takx>";
    tinyxml2::XMLDocument doc;
    if (doc.Parse(xml) != tinyxml2::XML_SUCCESS) {
        std::cerr << "tinyxml2 Parse failed\n";
        return 1;
    }

    const tinyxml2::XMLElement* root = doc.FirstChildElement("takx");
    if (root == nullptr || root->IntAttribute("version") != 10) {
        std::cerr << "unexpected root element\n";
        return 1;
    }

    const tinyxml2::XMLElement* item = root->FirstChildElement("item");
    if (item == nullptr || item->Attribute("id") == nullptr ||
        std::strcmp(item->Attribute("id"), "ok") != 0 ||
        item->GetText() == nullptr || std::strcmp(item->GetText(), "hello") != 0) {
        std::cerr << "unexpected item element\n";
        return 1;
    }

    std::cout << "tinyxml2_version=" << TINYXML2_MAJOR_VERSION << '.'
              << TINYXML2_MINOR_VERSION << '.' << TINYXML2_PATCH_VERSION << '\n';
    std::cout << "tinyxml2_static_pic_control=true\n";
    return 0;
}
