/* Copyright 2026 uds-takserver authors
 * SPDX-License-Identifier: GPL-3.0-only
 *
 * Genuine C consumer for the prebuilt TAKX SQLCipher 4.5.7 static PIC archive
 * linked against prebuilt OpenSSL 3.5.9 LTS. Verifies encrypted DB creation,
 * correct-key reopen, exact data roundtrip, and wrong-key rejection.
 */

#define SQLITE_HAS_CODEC 1
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "sqlite3.h"

static int query_text(sqlite3 *db, const char *sql, char *buf, size_t cap) {
    sqlite3_stmt *stmt = NULL;
    if (sqlite3_prepare_v2(db, sql, -1, &stmt, NULL) != SQLITE_OK) {
        return -1;
    }
    int rc = sqlite3_step(stmt);
    if (rc != SQLITE_ROW) {
        sqlite3_finalize(stmt);
        return -1;
    }
    const unsigned char *txt = sqlite3_column_text(stmt, 0);
    if (!txt) {
        sqlite3_finalize(stmt);
        return -1;
    }
    snprintf(buf, cap, "%s", (const char *)txt);
    sqlite3_finalize(stmt);
    return 0;
}

int main(void) {
    const char *db_path = "/tmp/takx-sqlcipher-control.db";
    remove(db_path);

    sqlite3 *db = NULL;
    char val[128] = {0};
    if (sqlite3_open(db_path, &db) != SQLITE_OK) {
        fprintf(stderr, "sqlite3_open create failed\n");
        return 1;
    }
    if (sqlite3_key(db, "takx-correct-key", 16) != SQLITE_OK ||
        sqlite3_exec(db, "CREATE TABLE kv(k TEXT PRIMARY KEY, v TEXT); INSERT INTO kv VALUES('a','ok-457');", NULL, NULL, NULL) != SQLITE_OK) {
        fprintf(stderr, "encrypted write failed\n");
        sqlite3_close(db);
        return 1;
    }
    if (query_text(db, "PRAGMA cipher_version;", val, sizeof(val)) != 0 || strstr(val, "4.5.7") == NULL) {
        fprintf(stderr, "unexpected cipher_version: %s\n", val);
        sqlite3_close(db);
        return 1;
    }
    printf("cipher_version=%s\n", val);
    sqlite3_close(db);

    /* Reopen with correct key and verify data. */
    if (sqlite3_open(db_path, &db) != SQLITE_OK ||
        sqlite3_key(db, "takx-correct-key", 16) != SQLITE_OK ||
        query_text(db, "SELECT v FROM kv WHERE k='a';", val, sizeof(val)) != 0 ||
        strcmp(val, "ok-457") != 0) {
        fprintf(stderr, "correct-key reopen failed: %s\n", val);
        if (db) sqlite3_close(db);
        return 1;
    }
    sqlite3_close(db);

    /* Reopen with wrong key and verify rejection. */
    if (sqlite3_open(db_path, &db) != SQLITE_OK ||
        sqlite3_key(db, "takx-wrong-key!!", 16) != SQLITE_OK) {
        fprintf(stderr, "wrong-key open failed\n");
        if (db) sqlite3_close(db);
        return 1;
    }
    if (query_text(db, "SELECT v FROM kv WHERE k='a';", val, sizeof(val)) == 0) {
        fprintf(stderr, "wrong key unexpectedly read encrypted DB\n");
        sqlite3_close(db);
        return 1;
    }
    sqlite3_close(db);
    remove(db_path);

    printf("sqlcipher_static_pic_control=true\n");
    return 0;
}
