/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#define _POSIX_C_SOURCE 200809L
#include <mdbtools.h>

#include <stdio.h>
#include <string.h>

int main(void) {
    size_t (*volatile ole_next)(MdbHandle *, MdbColumn *, void *) = mdb_ole_read_next;
    if (ole_next == NULL) {
        fprintf(stderr, "missing mdb_ole_read_next symbol\n");
        return 1;
    }

    const char *ver = mdb_get_version();
    if (ver == NULL || strcmp(ver, "1.0.0") != 0) {
        fprintf(stderr, "unexpected mdbtools version: %s\n", ver ? ver : "(null)");
        return 1;
    }

    if (mdb_open("/nonexistent-takx-control.mdb", MDB_NOFLAGS) != NULL) {
        fprintf(stderr, "expected mdb_open on nonexistent file to fail\n");
        return 1;
    }

    printf("mdbtools_static_pic_control=true version=%s\n", ver);
    return 0;
}
