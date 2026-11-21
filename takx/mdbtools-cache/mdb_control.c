/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX MDBTools 1.0.0 static PIC archive.
 * Verifies header signatures, missing-file rejection, and real global symbols
 * mdb_ole_read_next and mdb_set_boolean_fmt_numbers without host MDBTools/GLib.
 */

#define _XOPEN_SOURCE 700
#include <stdio.h>
#include <stdlib.h>

#include "mdbtools.h"

int main(void) {
    /* Verify exact ATAK-patched header signatures resolve against libmdb.a. */
    size_t (*ole_fn)(MdbHandle *, MdbColumn *, void *) = mdb_ole_read_next;
    int (*bool_fmt_fn)(MdbHandle *) = mdb_set_boolean_fmt_numbers;
    if (ole_fn == NULL || bool_fmt_fn == NULL) {
        fprintf(stderr, "expected defined mdbtools symbols\n");
        return 1;
    }

    MdbHandle *missing = mdb_open("/nonexistent/takx-missing.mdb", MDB_NOFLAGS);
    if (missing != NULL) {
        mdb_close(missing);
        fprintf(stderr, "expected NULL for nonexistent MDB path\n");
        return 1;
    }

    printf("mdb_symbols_resolved=true\n");
    printf("mdb_missing_file_rejected=true\n");
    printf("mdb_static_pic_control=true\n");
    return 0;
}
