/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 */

#include <sqlite3.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const char *ver = sqlite3_libversion();
    if (ver == NULL || strncmp(ver, "3.46.0", 6) != 0) {
        fprintf(stderr, "unexpected sqlite3 version: %s\n", ver ? ver : "(null)");
        return 1;
    }

    sqlite3 *db = NULL;
    if (sqlite3_open(":memory:", &db) != SQLITE_OK || db == NULL) {
        fprintf(stderr, "sqlite3_open failed\n");
        if (db != NULL) {
            sqlite3_close(db);
        }
        return 1;
    }

    sqlite3_stmt *stmt = NULL;
    const char *sql = "SELECT json_extract('{\"status\":\"ok\"}', '$.status');";
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) != SQLITE_OK) {
        fprintf(stderr, "sqlite3_prepare_v2 failed: %s\n", sqlite3_errmsg(db));
        sqlite3_close(db);
        return 1;
    }

    int step_rc = sqlite3_step(stmt);
    const unsigned char *val = sqlite3_column_text(stmt, 0);
    if (step_rc != SQLITE_ROW || val == NULL || strcmp((const char *)val, "ok") != 0) {
        fprintf(stderr, "unexpected sqlite3 json_extract result\n");
        sqlite3_finalize(stmt);
        sqlite3_close(db);
        return 1;
    }

    sqlite3_finalize(stmt);
    sqlite3_close(db);
    printf("sqlite3_static_pic_control=true version=%s\n", ver);
    return 0;
}
