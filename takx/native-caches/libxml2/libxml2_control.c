/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX libxml2 2.13.3 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "libxml/parser.h"
#include "libxml/tree.h"
#include "libxml/xmlversion.h"

int main(void) {
#if LIBXML_VERSION != 21303
#error "unexpected libxml2 header version"
#endif

    LIBXML_TEST_VERSION

    const char *xml = "<takx version=\"2.13.3\"><item>ok</item></takx>";
    xmlDocPtr doc = xmlReadMemory(xml, (int)strlen(xml), "control.xml", NULL, 0);
    if (doc == NULL) {
        fprintf(stderr, "xmlReadMemory failed\n");
        return 1;
    }

    xmlNodePtr root = xmlDocGetRootElement(doc);
    if (root == NULL || xmlStrcmp(root->name, BAD_CAST "takx") != 0) {
        fprintf(stderr, "unexpected root element\n");
        xmlFreeDoc(doc);
        xmlCleanupParser();
        return 1;
    }

    xmlChar *ver = xmlGetProp(root, BAD_CAST "version");
    if (ver == NULL || xmlStrcmp(ver, BAD_CAST "2.13.3") != 0) {
        fprintf(stderr, "unexpected version attribute\n");
        xmlFree(ver);
        xmlFreeDoc(doc);
        xmlCleanupParser();
        return 1;
    }
    xmlFree(ver);

    xmlNodePtr child = root->children;
    if (child == NULL || xmlStrcmp(child->name, BAD_CAST "item") != 0) {
        fprintf(stderr, "unexpected child element\n");
        xmlFreeDoc(doc);
        xmlCleanupParser();
        return 1;
    }

    xmlChar *content = xmlNodeGetContent(child);
    if (content == NULL || xmlStrcmp(content, BAD_CAST "ok") != 0) {
        fprintf(stderr, "unexpected child content\n");
        xmlFree(content);
        xmlFreeDoc(doc);
        xmlCleanupParser();
        return 1;
    }
    xmlFree(content);

    xmlFreeDoc(doc);
    xmlCleanupParser();

    printf("libxml2_version=%d\n", LIBXML_VERSION);
    printf("libxml2_static_pic_control=true\n");
    return 0;
}
