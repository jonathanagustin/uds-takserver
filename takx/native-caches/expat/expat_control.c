/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX Expat 2.10.0 static PIC archive.
 */

#include <stdio.h>
#include <string.h>

#include "expat.h"

struct parse_state {
    int saw_root;
    int saw_item;
};

static void XMLCALL on_start(void *user_data, const XML_Char *name, const XML_Char **atts) {
    (void)atts;
    struct parse_state *st = (struct parse_state *)user_data;
    if (strcmp(name, "takx") == 0) {
        st->saw_root = 1;
    } else if (strcmp(name, "layer") == 0) {
        st->saw_item = 1;
    }
}

int main(void) {
    if (strstr(XML_ExpatVersion(), "2.10.0") == NULL) {
        fprintf(stderr, "unexpected Expat version: %s\n", XML_ExpatVersion());
        return 1;
    }

    XML_Parser parser = XML_ParserCreate(NULL);
    if (parser == NULL) {
        fprintf(stderr, "XML_ParserCreate failed\n");
        return 1;
    }

    struct parse_state st = {0, 0};
    XML_SetUserData(parser, &st);
    XML_SetElementHandler(parser, on_start, NULL);

    const char *doc = "<takx><layer id=\"1\"/></takx>";
    if (XML_Parse(parser, doc, (int)strlen(doc), XML_TRUE) != XML_STATUS_OK) {
        fprintf(stderr, "XML_Parse failed: %s\n", XML_ErrorString(XML_GetErrorCode(parser)));
        XML_ParserFree(parser);
        return 1;
    }
    XML_ParserFree(parser);

    if (!st.saw_root || !st.saw_item) {
        fprintf(stderr, "expected XML elements not observed\n");
        return 1;
    }

    printf("expat_version=%s\n", XML_ExpatVersion());
    printf("expat_static_pic_control=true\n");
    return 0;
}
